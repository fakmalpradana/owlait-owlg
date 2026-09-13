"""A bounded-memory reader for OWLG v4.

Only the tiles actually asked for get decoded, and the cache is bounded by a tile
count (LRU) -- not by the size of the raster. Opening a 100 GB file uses the same
RAM as opening a 100 MB one.
"""
import hashlib
import numpy as np, os, struct, json
from collections import OrderedDict
from . import imgio as _io
from .codec import dec_tile
from . import crypto as cy
from . import tiled as _tiled
from .errors import OwlgError, NeedKey

MAX_TILES = 48          # decoded tiles held in memory


def _cap_gdal_cache(mb=256):
    if os.environ.get('GDAL_CACHEMAX'): return
    os.environ['GDAL_CACHEMAX'] = str(mb)
    try:
        from osgeo import gdal
        gdal.SetCacheMax(mb << 20)
    except Exception:
        pass


class TiledReader:
    def __init__(self, path, password=None, fast=False, max_tiles=MAX_TILES):
        self.path = path; self.fast = fast
        self.f = open(path, 'rb')
        raw = self.f.read(6)
        if raw[:4] != b'OWLG': raise OwlgError('not an OWLG file')
        ver, flags = raw[4], raw[5]
        if ver != 4: raise OwlgError(f'not OWLG v4 (version {ver})')
        p = 6; self.key = self.prefix = None
        if flags & 1:
            hd = self.f.read(1 + 4 + 16 + 8)
            if password is None: raise NeedKey('encrypted file: a passphrase is required')
            iters = struct.unpack('<I', hd[1:5])[0]
            salt = hd[5:21]; self.prefix = hd[21:29]
            self.key = cy.derive_key(password, salt, iters)
            p += 29
        hl = struct.unpack('<I', self.f.read(4))[0]; p += 4
        hj = self.f.read(hl); p += hl
        if self.key is not None:
            try: hj = cy.unseal(self.key, self.prefix, 0, hj)
            except Exception: raise OwlgError('wrong passphrase, or the file is corrupt')
        self.hdr = json.loads(hj)
        self.base_off = p
        self.dir = self.hdr['dir']
        self.levels = self.hdr['levels']
        self.tile = self.hdr['tile']
        self.nb = len(self.hdr['coded'])
        # Files written before band grouping have one blob per tile; treat
        # them as a single group so they keep opening.
        self.groups = self.hdr.get('groups') or [list(self.hdr['coded'])[:3]]
        self.cache = OrderedDict(); self.max_tiles = max_tiles

    # ---------------- blob access ----------------
    def _blob(self, idx):
        o, L = self.dir[idx]
        self.f.seek(self.base_off + o)
        b = self.f.read(L)
        return cy.unseal(self.key, self.prefix, idx + 1, b) if self.key is not None else b

    def _tile(self, lvl, ty, tx):
        k = (lvl, ty, tx)
        t = self.cache.get(k)
        if t is not None:
            self.cache.move_to_end(k); return t
        L = self.levels[lvl]
        i = ty * L['ntx'] + tx
        bi = L['base'][i]
        blobs = [self._blob(j) for j in (bi if isinstance(bi, (list, tuple)) else [bi])]
        arr = _tiled.dec_groups(blobs, self.hdr['codec'], self.groups)
        if (not self.fast) and L.get('corr') is not None and self.hdr['delta'] >= 0:
            th, tw = arr.shape[1], arr.shape[2]
            rec = arr.copy()
            dec_tile(np.frombuffer(self._blob(L['corr'][i]), np.uint8),
                     arr, 0, th, 0, tw, self.hdr['delta'], rec)
            arr = rec
        self.cache[k] = arr
        while len(self.cache) > self.max_tiles: self.cache.popitem(last=False)
        return arr

    # ---------------- reading ----------------
    def read_window(self, band, xoff, yoff, xsize, ysize, level=0):
        L = self.levels[level]; W, H = L['w'], L['h']
        xoff = max(0, int(xoff)); yoff = max(0, int(yoff))
        xsize = max(0, min(int(xsize), W - xoff)); ysize = max(0, min(int(ysize), H - yoff))
        out = np.zeros((ysize, xsize), np.uint8)
        if xsize == 0 or ysize == 0: return out
        const = self.hdr.get('const') or {}
        if str(band) in const:
            out[:] = const[str(band)]; return out
        if band not in self.hdr['coded']: return out
        bi = self.hdr['coded'].index(band)
        T = self.tile
        for ty in range(yoff // T, (yoff + ysize - 1) // T + 1):
            for tx in range(xoff // T, (xoff + xsize - 1) // T + 1):
                if ty >= L['nty'] or tx >= L['ntx']: continue
                t = self._tile(level, ty, tx)
                ty0, tx0 = ty * T, tx * T
                sy0 = max(yoff, ty0); sy1 = min(yoff + ysize, ty0 + t.shape[1])
                sx0 = max(xoff, tx0); sx1 = min(xoff + xsize, tx0 + t.shape[2])
                if sy1 <= sy0 or sx1 <= sx0: continue
                out[sy0-yoff:sy1-yoff, sx0-xoff:sx1-xoff] = \
                    t[bi, sy0-ty0:sy1-ty0, sx0-tx0:sx1-tx0]
        return out

    def read_all(self, level=0):
        L = self.levels[level]
        out = np.zeros((self.hdr['bands'], L['h'], L['w']), np.uint8)
        for b in range(self.hdr['bands']):
            T = self.tile
            for ty in range(L['nty']):
                for tx in range(L['ntx']):
                    y0, x0 = ty*T, tx*T
                    th = min(T, L['h']-y0); tw = min(T, L['w']-x0)
                    out[b, y0:y0+th, x0:x0+tw] = self.read_window(b, x0, y0, tw, th, level)
        return out

    def close(self):
        try: self.f.close()
        except Exception: pass


_RCACHE = {}


def _pw_tag(password):
    """Cache tag for a passphrase. The passphrase itself is never stored: only a
    digest of it takes part in the key. Without this, a file opened once with the
    correct passphrase would be served from cache to a later caller supplying the
    WRONG one."""
    if password is None:
        return None
    if isinstance(password, str):
        password = password.encode('utf-8')
    return hashlib.sha256(b'owlg-cache-tag\x00' + password).hexdigest()


def reader(path, password=None, fast=False):
    key = (os.path.abspath(path), os.path.getmtime(path), bool(fast), _pw_tag(password))
    r = _RCACHE.get(key)
    if r is None:
        for k in list(_RCACHE):
            try: _RCACHE.pop(k).close()
            except Exception: pass
        r = TiledReader(path, password, fast); _RCACHE[key] = r
    return r

def is_v4(path):
    try:
        with open(path, 'rb') as f:
            h = f.read(6)
        return h[:4] == b'OWLG' and h[4] == 4
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Streaming verification for large files (10-100 GB): never loads the full raster
# into memory. The digest is computed over exactly the same tile-order
# serialization the encoder uses (see owlg/tiled.py).
def _scan_preamble(hdr):
    return json.dumps(dict(w=hdr['w'], h=hdr['h'], bands=hdr['bands'],
                           tile=hdr['tile'], const=hdr['const'],
                           coded=hdr['coded']),
                      separators=(',', ':'), sort_keys=True).encode()


def scan_digest(path, password=None):
    """SHA-256 of the DECODED pixels, computed per tile without loading the full raster."""
    import hashlib
    r = TiledReader(path, password, fast=False)
    try:
        hdr = r.hdr; T = hdr['tile']; L = hdr['levels'][0]
        h = hashlib.sha256(); h.update(_scan_preamble(hdr))
        nb = len(hdr['coded'])
        for ty in range(L['nty']):
            for tx in range(L['ntx']):
                y0, x0 = ty*T, tx*T
                th, tw = min(T, hdr['h']-y0), min(T, hdr['w']-x0)
                t = r._tile(0, ty, tx)[:nb, :th, :tw]
                h.update(np.ascontiguousarray(t).tobytes())
        return h.hexdigest()
    finally:
        r.close()


def source_digest(orig, hdr):
    """Recompute the digest from the original GeoTIFF, in the same tile order.

    Used to prove that this .owlg really did come from that original file -- which
    holds in near-lossless mode too, where the decoded pixels are deliberately
    allowed to differ within the +/-delta bound.
    """
    import hashlib, rasterio, warnings; warnings.filterwarnings('ignore')
    T = hdr['tile']; coded = hdr['coded']
    h = hashlib.sha256(); h.update(_scan_preamble(hdr))
    nty = (hdr['h'] + T - 1)//T; ntx = (hdr['w'] + T - 1)//T
    with rasterio.open(orig) as ds:
        for ty in range(nty):
            for tx in range(ntx):
                y0, x0 = ty*T, tx*T
                th, tw = min(T, hdr['h']-y0), min(T, hdr['w']-x0)
                a = ds.read(indexes=[b+1 for b in coded],
                            window=((y0, y0+th), (x0, x0+tw)))
                h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def verify_scan(path, password=None):
    """(ok, expected, got).

    The stored digest is a digest of the ORIGINAL pixels, so comparing it against
    the decoded result is only meaningful in lossless mode (delta=0). In
    near-lossless mode ok=None: use source_digest() against the original GeoTIFF.
    """
    r = TiledReader(path, password, fast=False)
    exp = r.hdr.get('sha256_scan'); dl = int(r.hdr.get('delta', 0)); r.close()
    if not exp or dl != 0: return (None, exp, None)
    got = scan_digest(path, password)
    return (got == exp, exp, got)


def verify_bounds(path, orig, password=None, progress=None):
    """Compare tile by tile against the original GeoTIFF. Returns (maxerr, npix)."""
    import rasterio, warnings; warnings.filterwarnings('ignore')
    r = TiledReader(path, password, fast=False)
    try:
        hdr = r.hdr; T = hdr['tile']; L = hdr['levels'][0]
        coded = hdr['coded']; const = hdr.get('const') or {}
        maxerr = 0; npix = 0
        with rasterio.open(orig) as ds:
            for ty in range(L['nty']):
                for tx in range(L['ntx']):
                    y0, x0 = ty*T, tx*T
                    th, tw = min(T, hdr['h']-y0), min(T, hdr['w']-x0)
                    a = ds.read(indexes=[b+1 for b in coded],
                                window=((y0, y0+th), (x0, x0+tw))).astype(np.int16)
                    t = r._tile(0, ty, tx)[:len(coded), :th, :tw].astype(np.int16)
                    e = int(np.abs(a - t).max()) if a.size else 0
                    if e > maxerr: maxerr = e
                    npix += int(a.size)
                    for bs, v in const.items():
                        c = ds.read(int(bs)+1, window=((y0, y0+th), (x0, x0+tw)))
                        e = int(np.abs(c.astype(np.int16) - int(v)).max()) if c.size else 0
                        if e > maxerr: maxerr = e
                        npix += int(c.size)
                if progress: progress(ty + 1, L['nty'])
        return maxerr, npix
    finally:
        r.close()
