"""OWLG v4 -- a tiled layout for large rasters (tens to hundreds of GB).

Three changes from v3, all of them at the format level:

  1. The base layer is TILED. Each tile is its own WebP/AVIF blob, so reading a
     single window only touches the tiles that intersect it. In v3 the base was
     one whole image that had to be decoded in its entirety.
  2. An OVERVIEW PYRAMID inside the file. A zoomed-out view reads a coarse level
     instead of the whole raster. Level 0 carries the error guarantee; the overview
     levels are averages of the already-corrected data, stored base-only for display.
  3. STREAMING encoding. The source is read tile by tile and each blob is written
     straight out to disk, so RAM never holds the whole raster.

The entropy coder's context is confined to the tile (encoder and decoder alike see
nothing outside it), so each tile really does stand on its own.
"""
import hashlib
import numpy as np, json, struct, os, time, hashlib, tempfile, warnings
warnings.filterwarnings('ignore')
from . import imgio as _io
from .codec import enc_tile, dec_tile
from . import crypto as cy

MAGIC = b'OWLG'; VERSION = 4
DEFAULT_TILE = 512
AVIF_Q = _io.QUALITY_LADDERS['avif']
WEBP_Q = _io.QUALITY_LADDERS['webp']


def _enc_base(a, codec, q):
    if codec == 'avif': return _io.avif_encode(a, level=int(q), speed=6)
    if codec == 'webp': return _io.webp_encode(a, level=int(q))
    if codec == 'jxl_lossless': return _io.jxl_encode(a, lossless=True)
    raise ValueError(codec)

def _dec_base(buf, codec, nb):
    d = _io.decode_by_codec(codec, buf)
    return np.ascontiguousarray(np.asarray(d)[..., :3].astype(np.uint8))[:, :, :nb]


def band_groups(coded):
    """Split band indices into groups of three.

    A base blob is an ordinary RGB image, so it carries at most three bands. With
    four or more bands we write several blobs per tile, exactly as the flat
    layout does. Encoding only the first three would silently drop the rest."""
    return [list(coded[i:i + 3]) for i in range(0, len(coded), 3)]


def enc_groups(hwc, codec, q, groups):
    """Encode one tile's pixels as one base blob per group of three bands."""
    out = []
    for gi, g in enumerate(groups):
        sub = hwc[:, :, 3 * gi:3 * gi + len(g)]
        # Pad a 1- or 2-band group up to RGB while KEEPING the real channels.
        if sub.shape[2] < 3:
            pad = np.repeat(sub[:, :, :1], 3 - sub.shape[2], 2)
            sub = np.concatenate([sub, pad], 2)
        out.append(_enc_base(np.ascontiguousarray(sub), codec, q))
    return out


def dec_groups(blobs, codec, groups):
    """Inverse of enc_groups: returns (bands, h, w) uint8 for all coded bands."""
    if not isinstance(blobs, (list, tuple)):
        blobs = [blobs]                 # a file written before grouping existed
    decs = [_dec_base(b, codec, len(g)) for b, g in zip(blobs, groups)]
    hwc = decs[0] if len(decs) == 1 else np.concatenate(decs, 2)
    return np.ascontiguousarray(hwc.transpose(2, 0, 1))


class _Writer:
    """Writes blobs to a temporary data file, recording each one's offset."""
    def __init__(self, path, key=None, prefix=None):
        self.f = open(path, 'wb'); self.off = 0; self.dir = []
        self.key = key; self.prefix = prefix
    def add(self, blob):
        if self.key is not None:
            blob = cy.seal(self.key, self.prefix, len(self.dir) + 1, blob)
        self.f.write(blob)
        self.dir.append([self.off, len(blob)]); self.off += len(blob)
        return len(self.dir) - 1
    def close(self): self.f.close()


def _pick_quality(src, coded, tile, codec, delta, qlist, nsample=6, groups=None):
    """Sample a handful of scattered tiles to pick the best base quality."""
    import rasterio
    with rasterio.open(src) as ds:
        H, W = ds.height, ds.width
        nty, ntx = (H + tile - 1)//tile, (W + tile - 1)//tile
        picks = []
        step = max(1, (nty * ntx) // max(nsample, 1))
        for k in range(0, nty * ntx, step):
            picks.append((k // ntx, k % ntx))
            if len(picks) >= nsample: break
        if groups is None: groups = band_groups(coded)
        buf = np.zeros(len(coded)*tile*tile*3 + 65536, np.uint8)
        best = None
        for q in qlist:
            tot = 0
            for (ty, tx) in picks:
                y0, x0 = ty*tile, tx*tile
                th, tw = min(tile, H-y0), min(tile, W-x0)
                if th <= 0 or tw <= 0: continue
                a = ds.read(indexes=[b+1 for b in coded],
                            window=((y0, y0+th), (x0, x0+tw)))
                hwc = np.ascontiguousarray(a.transpose(1, 2, 0))
                blobs = enc_groups(hwc, codec, q, groups)
                tot += sum(len(bl) for bl in blobs)
                if delta > 0:
                    Bs = dec_groups(blobs, codec, groups)
                    C = np.ascontiguousarray(a)
                    tot += enc_tile(C, Bs, 0, th, 0, tw, delta, buf)
            if best is None or tot < best[1]: best = (q, tot)
        return best[0]


def _cap_gdal_cache(mb=256):
    """Cap the GDAL block cache. Without this, peak encode RAM is dominated by
    GDAL's cache (not by our own data), and it grows with the size of the raster."""
    if os.environ.get('GDAL_CACHEMAX'): return
    os.environ['GDAL_CACHEMAX'] = str(mb)
    try:
        from osgeo import gdal
        gdal.SetCacheMax(mb << 20)
    except Exception:
        pass


def write_tiled(src, dst, delta=0, base='webp', q=None, tile=DEFAULT_TILE,
                overviews=True, min_overview=256, password=None,
                kdf_iters=cy.DEFAULT_ITERS, verbose=True, gdal_cache_mb=256,
                overview_q=None):
    _cap_gdal_cache(gdal_cache_mb)
    import rasterio
    t0 = time.time()
    with rasterio.open(src) as ds:
        if ds.dtypes[0] != 'uint8':
            raise ValueError(f'OWLG v4 handles uint8; this file is {ds.dtypes[0]}')
        W, H, B = ds.width, ds.height, ds.count
        meta = dict(crs=ds.crs.to_wkt() if ds.crs else None,
                    transform=list(ds.transform)[:6], nodata=list(ds.nodatavals),
                    colorinterp=[c.name for c in ds.colorinterp], tags=ds.tags())
        # constant bands: detect them from per-band statistics, without loading it all
        const, coded = {}, []
        for b in range(B):
            mn, mx = None, None
            for _, win in ds.block_windows(b + 1):
                a = ds.read(b + 1, window=win)
                m0, m1 = int(a.min()), int(a.max())
                mn = m0 if mn is None else min(mn, m0)
                mx = m1 if mx is None else max(mx, m1)
                if mn != mx: break
            if mn == mx: const[str(b)] = int(mn)
            else: coded.append(b)
    nb = len(coded)
    groups = band_groups(coded)
    if base == 'auto':
        # 'auto' means: use the smallest base this machine can actually encode
        # AND decode. AVIF is usually smaller, but a file nobody can open is
        # worthless, so fall back to WebP when the AVIF probe fails.
        base = 'webp'
        try:
            ok, _why = _io.can_decode('avif')
            if ok:
                _io.avif_encode(np.zeros((8, 8, 3), np.uint8), level=60, speed=8)
                base = 'avif'
        except Exception:
            base = 'webp'
        if verbose:
            print(f"  --base auto resolved to {base}")
    if base not in ('webp', 'avif', 'jxl'):
        raise ValueError(f"unknown base codec: {base!r} (use webp, avif, jxl or auto)")
    codec = 'jxl_lossless' if (delta == 0 and base == 'jxl') else base
    if delta == 0 and base in ('webp', 'avif'):
        codec = base            # lossy base + delta=0 correction -> still bit-exact
    if q is None:
        qlist = WEBP_Q if codec == 'webp' else AVIF_Q
        q = _pick_quality(src, coded, tile, codec, delta, qlist, groups=groups)
    if verbose:
        print(f"  {W}x{H}x{B} uint8  tile {tile}px  base={codec} q={q}  delta=+/-{delta}")
        if const: print(f"  constant bands dropped: {const}")

    key = prefix = salt = None
    if password:
        salt, prefix = cy.new_params(); key = cy.derive_key(password, salt, kdf_iters)
    tmp = dst + '.part'
    w = _Writer(tmp, key, prefix)
    levels = []
    buf = np.zeros(nb*tile*tile*3 + 65536, np.uint8)

    # ---------- level 0: streamed straight from the source ----------
    nty, ntx = (H + tile - 1)//tile, (W + tile - 1)//tile
    base_idx = [0]*(nty*ntx); corr_idx = [0]*(nty*ntx) if delta >= 0 else None
    nbytes_b = nbytes_c = 0
    # Streaming digest over the tile-order serialization (see SHA_SCAN_DOC).
    _h = hashlib.sha256()
    _h.update(json.dumps(dict(w=W, h=H, bands=B, tile=tile, const=const,
                              coded=coded), separators=(',', ':'),
                         sort_keys=True).encode())
    with rasterio.open(src) as ds:
        for ty in range(nty):
            for tx in range(ntx):
                y0, x0 = ty*tile, tx*tile
                th, tw = min(tile, H-y0), min(tile, W-x0)
                a = np.ascontiguousarray(
                    ds.read(indexes=[b+1 for b in coded],
                            window=((y0, y0+th), (x0, x0+tw))))
                _h.update(a.tobytes())
                hwc = np.ascontiguousarray(a.transpose(1, 2, 0))
                blobs = enc_groups(hwc, codec, q, groups)
                k = ty*ntx + tx
                base_idx[k] = [w.add(bl) for bl in blobs]
                nbytes_b += sum(len(bl) for bl in blobs)
                Bs = dec_groups(blobs, codec, groups)
                C = np.ascontiguousarray(a)
                n = enc_tile(C, Bs, 0, th, 0, tw, delta, buf)
                corr_idx[k] = w.add(buf[:n].tobytes()); nbytes_c += n
            if verbose and nty > 4 and (ty % max(1, nty//10) == 0):
                print(f"    level 0: tile row {ty+1}/{nty}", flush=True)
    levels.append(dict(w=W, h=H, nty=nty, ntx=ntx, base=base_idx, corr=corr_idx))
    if verbose:
        print(f"  level 0: {nty*ntx} tile" + ("s" if nty*ntx != 1 else "") + f", base {nbytes_b/1e6:.2f} MB + correction {nbytes_c/1e6:.2f} MB")

    # ---------- overview: read the previous level back, downsample 2x ----------
    if overviews:
        w.close()
        # Overviews are display-only (no bound), so a lower quality is free.
        # A lossless JXL base keeps its own quality: there is no knob to turn.
        oq = q if codec == 'jxl_lossless' else min(int(q), overview_q or _io.OVERVIEW_Q)
        lv = 0
        while max(levels[lv]['w'], levels[lv]['h']) > min_overview:
            prev = levels[lv]
            pw_, ph = prev['w'], prev['h']
            cw, ch = (pw_+1)//2, (ph+1)//2
            cnty, cntx = (ch+tile-1)//tile, (cw+tile-1)//tile
            cbase = [0]*(cnty*cntx); tot = 0
            f = open(tmp, 'ab')            # keep appending, do not truncate
            off_base = os.path.getsize(tmp)
            newdir = []
            rd = _LevelReader(tmp, prev, codec, nb, key, prefix, w.dir, tile, delta,
                              groups=groups)
            for ty in range(cnty):
                for tx in range(cntx):
                    y0, x0 = ty*tile, tx*tile
                    th, tw = min(tile, ch-y0), min(tile, cw-x0)
                    src_arr = rd.read(2*y0, 2*x0, min(2*tw, pw_-2*x0), min(2*th, ph-2*y0))
                    a = _box2(src_arr, th, tw)
                    hwc = np.ascontiguousarray(a.transpose(1, 2, 0))
                    idxs = []
                    for blob in enc_groups(hwc, codec, oq, groups):
                        if key is not None:
                            blob = cy.seal(key, prefix, len(w.dir) + len(newdir) + 1, blob)
                        f.write(blob)
                        newdir.append([off_base, len(blob)]); off_base += len(blob)
                        idxs.append(len(w.dir) + len(newdir) - 1)
                        tot += len(blob)
                    cbase[ty*cntx+tx] = idxs
            f.close()
            w.dir.extend(newdir)
            levels.append(dict(w=cw, h=ch, nty=cnty, ntx=cntx, base=cbase, corr=None))
            if verbose:
                print(f"  overview {lv+1}: {cw}x{ch}, {cnty*cntx} tile" + ("s" if cnty*cntx != 1 else "") + f", {tot/1e6:.2f} MB")
            lv += 1
    else:
        w.close()

    hdr = dict(v=VERSION, mode='nearlossless' if delta else 'lossless',
               w=W, h=H, bands=B, delta=int(delta), codec=codec, q=int(q),
               tile=tile, const=const, coded=coded, groups=groups, levels=levels,
               sha256_scan=_h.hexdigest(), dir=w.dir, **meta)
    hj = json.dumps(hdr, separators=(',', ':')).encode()
    if key is not None: hj = cy.seal(key, prefix, 0, hj)
    with open(dst, 'wb') as out:
        out.write(MAGIC); out.write(bytes([VERSION, 1 if password else 0]))
        if password:
            out.write(bytes([cy.KDF_PBKDF2_SHA256])); out.write(struct.pack('<I', kdf_iters))
            out.write(salt); out.write(prefix)
        out.write(struct.pack('<I', len(hj))); out.write(hj)
        with open(tmp, 'rb') as src_f:
            while True:
                chunk = src_f.read(8 << 20)
                if not chunk: break
                out.write(chunk)
    os.remove(tmp)
    tot = os.path.getsize(dst)
    raw = W*H*B
    if verbose:
        print(f"  -> {dst}: {tot/1e6:.3f} MB  {raw/tot:.2f}x  "
              f"{len(levels)} levels  ({time.time()-t0:.1f}s)")
    return dict(total=tot, ratio=raw/tot, levels=len(levels))


def _box2(a, th, tw):
    """2x2 box average -> (B,th,tw)."""
    B, h, w = a.shape
    hh, ww = th*2, tw*2
    if h < hh or w < ww:
        pad = np.zeros((B, hh, ww), a.dtype)
        pad[:, :h, :w] = a
        if h < hh: pad[:, h:, :w] = a[:, h-1:h, :w]
        if w < ww: pad[:, :, w:] = pad[:, :, w-1:w]
        a = pad
    r = a[:, :hh, :ww].reshape(B, th, 2, tw, 2).mean(axis=(2, 4))
    return np.ascontiguousarray(np.rint(r).astype(np.uint8))


class _LevelReader:
    """Reads tiles of one level back out of the data file still being written."""
    def __init__(self, path, level, codec, nb, key, prefix, dirs, tile, delta,
                 groups=None):
        self.path = path; self.lv = level; self.codec = codec; self.nb = nb
        self.groups = groups if groups is not None else band_groups(list(range(nb)))
        self.key = key; self.prefix = prefix; self.dir = dirs
        self.tile = tile; self.delta = delta
        self.f = open(path, 'rb'); self.cache = {}
    def _blob(self, idx):
        o, L = self.dir[idx]
        self.f.seek(o); b = self.f.read(L)
        return cy.unseal(self.key, self.prefix, idx+1, b) if self.key is not None else b
    def _tile(self, ty, tx):
        k = (ty, tx)
        if k in self.cache: return self.cache[k]
        lv = self.lv; i = ty*lv['ntx']+tx
        bi = lv['base'][i]
        blobs = [self._blob(j) for j in (bi if isinstance(bi, (list, tuple)) else [bi])]
        arr = dec_groups(blobs, self.codec, self.groups)
        if lv.get('corr') is not None and self.delta >= 0:
            th, tw = arr.shape[1], arr.shape[2]
            rec = arr.copy()
            dec_tile(np.frombuffer(self._blob(lv['corr'][i]), np.uint8),
                     arr, 0, th, 0, tw, self.delta, rec)
            arr = rec
        if len(self.cache) > 8: self.cache.clear()
        self.cache[k] = arr
        return arr
    def read(self, y, x, w_, h_):
        lv = self.lv; T = self.tile
        out = np.zeros((self.nb, h_, w_), np.uint8)
        for ty in range(y//T, (y+h_-1)//T + 1):
            for tx in range(x//T, (x+w_-1)//T + 1):
                if ty >= lv['nty'] or tx >= lv['ntx']: continue
                t = self._tile(ty, tx)
                ty0, tx0 = ty*T, tx*T
                sy0 = max(y, ty0); sy1 = min(y+h_, ty0+t.shape[1])
                sx0 = max(x, tx0); sx1 = min(x+w_, tx0+t.shape[2])
                if sy1 <= sy0 or sx1 <= sx0: continue
                out[:, sy0-y:sy1-y, sx0-x:sx1-x] = t[:, sy0-ty0:sy1-ty0, sx0-tx0:sx1-tx0]
        return out
