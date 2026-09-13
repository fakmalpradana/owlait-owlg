"""The .owlg file container (v2).

Layout:
    "OWLG" | u8 version | u8 flags
    [if encrypted: u8 kdf_id | u32 iters | 16B salt | 8B nonce_prefix]
    u32 hdr_len | hdr_bytes            <- JSON; if encrypted: AES-GCM (blob index 0)
    blob_region                        <- offsets & lengths recorded in the header

All of the metadata (CRS, transform, dimensions, blob directory) is encrypted too;
all that is left in the clear is the small envelope, so the file stays recognizable.
"""
import numpy as np, json, struct, os, time, warnings, hashlib
warnings.filterwarnings('ignore')
from . import imgio as _io
from .codec import enc_tile, dec_tile
from . import crypto as cy

MAGIC = b'OWLG'; VERSION = 3
REC_MAGIC = b'OWLR'
LEGACY_MAGIC = b'GTZ1'
AVIF_Q = _io.QUALITY_LADDERS['avif']
WEBP_Q = _io.QUALITY_LADDERS['webp']
# The default base is WebP: decodable by practically every GDAL, Pillow, Qt, and
# browser build out there. On the test data it costs only +4.8% over AVIF.
DEFAULT_BASE = 'webp'

from .errors import OwlgError, NeedKey      # re-exported for compatibility


def _pad_rgb(a):
    """Pad a 1- or 2-band tile up to three channels for an RGB base codec.

    The real channels must be KEPT and only the remainder filled. An earlier
    version wrote `np.repeat(a[:, :, :1], 3, 2)`, which replaced every channel
    with band 0 and so silently destroyed the second band of any trailing
    two-band group -- invisible at delta > 0, because the correction layer
    repairs it, but a real data loss in lossless mode.
    """
    if a.shape[2] >= 3:
        return np.ascontiguousarray(a)
    pad = np.repeat(a[:, :, :1], 3 - a.shape[2], 2)
    return np.ascontiguousarray(np.concatenate([a, pad], 2))

def _enc_base(a, codec, q):
    if codec == 'avif': return _io.avif_encode(a, level=int(q), speed=6)
    if codec == 'webp': return _io.webp_encode(a, level=int(q))
    if codec == 'jxl':  return _io.jxl_encode(a, lossless=False, distance=float(q))
    if codec == 'jxl_lossless': return _io.jxl_encode(a, lossless=True)
    raise OwlgError(f'unknown codec: {codec}')

def _dec_base(buf, codec):
    d = _io.decode_by_codec(codec, buf)
    return np.ascontiguousarray(np.asarray(d)[..., :3].astype(np.uint8))

# ------------------------------- WRITE -------------------------------
def _build(C, coded, groups, cod, qq, delta, tile, wbuf=None):
    """Base blobs + correction blobs for the coded bands C at (codec, q, delta).
    Returns (base_blobs, corr_blobs, total_bytes). Exact, not an estimate."""
    nb, H, W = C.shape
    if wbuf is None:
        wbuf = np.zeros(nb*tile*tile*3 + 65536, np.uint8)
    bl, decs = [], []
    for g in groups:
        idx = [coded.index(x) for x in g]
        a = np.ascontiguousarray(C[idx].transpose(1, 2, 0))
        if a.shape[2] < 3: a = _pad_rgb(a)
        blob = _enc_base(a, cod, qq); bl.append(blob)
        decs.append(_dec_base(blob, cod)[:, :, :len(g)])
    BASE = np.ascontiguousarray(np.concatenate([d.transpose(2,0,1) for d in decs], 0)[:nb])
    tb = []
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            n = enc_tile(C, BASE, y0, min(y0+tile, H), x0, min(x0+tile, W), delta, wbuf)
            tb.append(wbuf[:n].tobytes())
    return bl, tb, sum(map(len, bl)) + sum(map(len, tb))


def _rio():
    import rasterio; return rasterio

def load_source(src):
    """Read a uint8 GeoTIFF whole: (A (B,H,W), meta, const, coded, groups)."""
    rasterio = _rio()
    with rasterio.open(src) as ds:
        A = ds.read()
        if ds.dtypes[0] != 'uint8':
            raise OwlgError(f'OWLG handles uint8; this file is {ds.dtypes[0]}')
        meta = dict(crs=ds.crs.to_wkt() if ds.crs else None,
                    transform=list(ds.transform)[:6], nodata=list(ds.nodatavals),
                    colorinterp=[c.name for c in ds.colorinterp], tags=ds.tags())
    const, coded = {}, []
    for b in range(A.shape[0]):
        u = np.unique(A[b])
        (const.__setitem__(str(b), int(u[0])) if u.size == 1 else coded.append(b))
    groups = [coded[i:i+3] for i in range(0, len(coded), 3)]
    return A, meta, const, coded, groups


def write_owlg(src, dst, delta=0, codec='auto', q=None, tile=1024, base=None,
               password=None, kdf_iters=cy.DEFAULT_ITERS, recovery=False, verbose=True,
               extra_header=None):
    t0 = time.time()
    A, meta, const, coded, groups = load_source(src)
    B, H, W = A.shape; RAW = A.nbytes
    C = np.ascontiguousarray(A[coded]); nb = len(coded)
    sha = hashlib.sha256(np.ascontiguousarray(A).tobytes()).hexdigest()
    if verbose:
        print(f"  {W}x{H}x{B} uint8  RAW={RAW/1e6:.2f} MB")
        if const: print(f"  constant bands dropped: {const} (-{len(const)*H*W/1e6:.2f} MB)")

    # A base codec that is itself lossless (JPEG XL) needs no correction layer
    # at all, which is the smallest lossless result when the decoder is present.
    # But JXL is the LEAST portable codec here, so it is only used when it was
    # actually asked for. Any other base reaches bit-exactness the normal way:
    # a lossy base plus a correction layer bounded at zero.
    _b = (base or DEFAULT_BASE).lower()
    if _b == 'auto':
        _b = 'jxl' if _io.can_decode('jxl')[0] else DEFAULT_BASE
    _pure_lossless = (delta == 0 and _b == 'jxl')
    if _pure_lossless:
        bblobs = []
        for g in groups:
            idx = [coded.index(x) for x in g]
            a = np.ascontiguousarray(C[idx].transpose(1, 2, 0))
            if a.shape[2] < 3: a = _pad_rgb(a)
            bblobs.append(_enc_base(a, 'jxl_lossless', 0))
        tblobs = []; rblobs = []; used = ('jxl_lossless', None); nty = ntx = 0; mode = 'lossless'
    else:
        nty, ntx = (H+tile-1)//tile, (W+tile-1)//tile
        wbuf = np.zeros(nb*tile*tile*3 + 65536, np.uint8)
        def build(cod, qq):
            return _build(C, coded, groups, cod, qq, delta, tile, wbuf)
        if codec not in (None, 'auto'):
            if q is None:
                raise OwlgError("--codec also needs --q (it names an exact codec and "
                                "quality); prefer --base, which picks the quality for you")
            cands = [(codec, q)]
        elif q is not None:
            cands = [(_b if _b != 'jxl' else DEFAULT_BASE, int(q))]
        else:
            if _b == 'avif':   cands = [('avif', x) for x in AVIF_Q]
            elif _b == 'webp': cands = [('webp', x) for x in WEBP_Q]
            elif _b == 'jxl':  cands = [('webp', x) for x in WEBP_Q]
            else: raise OwlgError(f'unknown base codec: {_b}')
        best = None
        for cod, qq in cands:
            bl, tb, sz = build(cod, qq)
            if verbose: print(f"    try {cod} q={qq}: {sz/1e6:.3f} MB")
            if best is None or sz < best[2]: best = (bl, tb, sz, cod, qq)
        bblobs, tblobs, _, cod, qq = best; used = (cod, qq)
        mode = 'lossless' if delta == 0 else 'nearlossless'
        rblobs = []
        if recovery:
            decs = [_dec_base(bl, cod)[:, :, :len(g)] for bl, g in zip(bblobs, groups)]
            BASE = np.ascontiguousarray(np.concatenate([d.transpose(2,0,1) for d in decs], 0)[:nb])
            rec = BASE.copy(); k = 0
            for ty in range(nty):
                for tx in range(ntx):
                    y0,y1 = ty*tile, min((ty+1)*tile,H); x0,x1 = tx*tile, min((tx+1)*tile,W)
                    dec_tile(np.frombuffer(tblobs[k], np.uint8), BASE, y0,y1,x0,x1, delta, rec); k += 1
            for ty in range(nty):
                for tx in range(ntx):
                    y0,y1 = ty*tile, min((ty+1)*tile,H); x0,x1 = tx*tile, min((tx+1)*tile,W)
                    n = enc_tile(C, rec, y0, y1, x0, x1, 0, wbuf)
                    rblobs.append(wbuf[:n].tobytes())
            if verbose:
                print(f"  recovery tier: +{sum(map(len,rblobs))/1e6:.3f} MB -> revert becomes bit-identical")

    hdr = dict(v=VERSION, mode=mode, w=W, h=H, bands=B, delta=int(delta),
               codec=used[0], q=used[1], tile=tile, const=const, coded=coded,
               groups=groups, nty=nty, ntx=ntx, sha256=sha,
               tiers=['light'] + (['full'] if rblobs else []), **meta)
    if extra_header:
        hdr.update(extra_header)
    _write_file(dst, hdr, bblobs, tblobs, rblobs, password, kdf_iters)
    tot = os.path.getsize(dst)
    if verbose:
        enc = ' ENCRYPTED' if password else ''
        print(f"  -> {dst}: {tot/1e6:.3f} MB  {RAW/tot:.2f}x  "
              f"{'LOSSLESS' if delta==0 else f'bound=+/-{delta} DN'}{enc}  ({time.time()-t0:.1f}s)")
    return dict(total=tot, ratio=RAW/tot, base=sum(map(len,bblobs)), corr=sum(map(len,tblobs)))

def _write_file(dst, hdr, bblobs, tblobs, rblobs, password, iters):
    blobs = list(bblobs) + list(tblobs) + list(rblobs)
    key = prefix = salt = None
    if password:
        salt, prefix = cy.new_params()
        key = cy.derive_key(password, salt, iters)
        blobs = [cy.seal(key, prefix, i+1, b) for i, b in enumerate(blobs)]
    off = 0; dirs = []
    for b in blobs: dirs.append([off, len(b)]); off += len(b)
    hdr = dict(hdr); hdr['n_base'] = len(bblobs); hdr['n_corr'] = len(tblobs)
    hdr['n_rec'] = len(rblobs); hdr['dir'] = dirs
    hj = json.dumps(hdr).encode()
    if password: hj = cy.seal(key, prefix, 0, hj)
    with open(dst, 'wb') as f:
        f.write(MAGIC); f.write(bytes([VERSION, 1 if password else 0]))
        if password:
            f.write(bytes([cy.KDF_PBKDF2_SHA256])); f.write(struct.pack('<I', iters))
            f.write(salt); f.write(prefix)
        f.write(struct.pack('<I', len(hj))); f.write(hj)
        for b in blobs: f.write(b)

# ------------------------------- READ -------------------------------
def _v4(path):
    from .tiled_read import is_v4
    return is_v4(path)


def open_owlg(path, password=None):
    """Return (hdr, getblob) without decoding any pixels. getblob(i) -> bytes."""
    if _v4(path):
        from .tiled_read import reader
        r = reader(path, password)
        hdr = dict(r.hdr); hdr['encrypted'] = r.key is not None
        hdr['n_base'] = 0; hdr['n_corr'] = 0; hdr['n_rec'] = 0
        return hdr, r._blob
    raw = open(path, 'rb').read()
    if raw[:4] == LEGACY_MAGIC: return _open_legacy(raw)
    if raw[:4] != MAGIC: raise OwlgError('not an OWLG file')
    ver, flags = raw[4], raw[5]; p = 6
    key = prefix = None
    if flags & 1:
        if password is None: raise NeedKey('encrypted file: a passphrase is required')
        kdf = raw[p]; p += 1
        iters = struct.unpack('<I', raw[p:p+4])[0]; p += 4
        salt = raw[p:p+16]; p += 16
        prefix = raw[p:p+8]; p += 8
        if kdf != cy.KDF_PBKDF2_SHA256: raise OwlgError('unknown KDF')
        key = cy.derive_key(password, salt, iters)
    hl = struct.unpack('<I', raw[p:p+4])[0]; p += 4
    hj = raw[p:p+hl]; p += hl
    if key is not None:
        try: hj = cy.unseal(key, prefix, 0, hj)
        except Exception: raise OwlgError('wrong passphrase, or the file is corrupt')
    hdr = json.loads(hj)
    base_off = p
    def getblob(i):
        o, L = hdr['dir'][i]
        b = raw[base_off+o: base_off+o+L]
        return cy.unseal(key, prefix, i+1, b) if key is not None else b
    hdr['encrypted'] = bool(flags & 1)
    return hdr, getblob

def _open_legacy(raw):
    p = 4; hl = struct.unpack('<I', raw[p:p+4])[0]; p += 4
    hdr = json.loads(raw[p:p+hl]); p += hl
    ng = struct.unpack('<I', raw[p:p+4])[0]; p += 4
    bl = [struct.unpack('<I', raw[p+4*i:p+4*i+4])[0] for i in range(ng)]; p += 4*ng
    blobs = []
    for L in bl: blobs.append(raw[p:p+L]); p += L
    nt = struct.unpack('<I', raw[p:p+4])[0]; p += 4
    tl = [struct.unpack('<I', raw[p+4*i:p+4*i+4])[0] for i in range(nt)]; p += 4*nt
    for L in tl: blobs.append(raw[p:p+L]); p += L
    hdr['n_base'] = ng; hdr['encrypted'] = False
    return hdr, (lambda i: blobs[i])

def read_owlg(path, password=None, fast=False, tier='auto', verify_sha=False):
    """tier: 'auto' (use recovery if present), 'light' (near-lossless), 'full' (bit-exact)."""
    if _v4(path):
        from .tiled_read import reader
        r = reader(path, password, fast)
        arr = r.read_all(0)
        hdr = dict(r.hdr); hdr['encrypted'] = r.key is not None
        if hdr.get('sha256'):
            got = hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()
            hdr['sha256_match'] = (got == hdr['sha256'])
        return arr, hdr
    hdr, get = open_owlg(path, password)
    W, H, coded, groups = hdr['w'], hdr['h'], hdr['coded'], hdr['groups']
    decs = [_dec_base(get(i), hdr['codec'])[:, :, :len(g)] for i, g in enumerate(groups)]
    BASE = np.ascontiguousarray(np.concatenate([d.transpose(2,0,1) for d in decs], 0)[:len(coded)])
    arr = BASE
    # Apply the correction layer whenever one is present. Keying this off
    # hdr['mode'] was wrong: a delta=0 file built from a lossy base DOES carry a
    # correction layer (bounded at zero) and is described as lossless, so the
    # mode string cannot decide whether corrections exist.
    if not fast and hdr.get('n_corr', 0) > 0:
        rec = BASE.copy(); tile = hdr['tile']; k = hdr['n_base']
        for ty in range(hdr['nty']):
            for tx in range(hdr['ntx']):
                y0,y1 = ty*tile, min((ty+1)*tile,H); x0,x1 = tx*tile, min((tx+1)*tile,W)
                dec_tile(np.frombuffer(get(k), np.uint8), BASE, y0, y1, x0, x1, hdr['delta'], rec)
                k += 1
        arr = rec
        nrec = hdr.get('n_rec', 0)
        want_full = (tier in ('auto', 'full')) and nrec > 0
        if tier == 'full' and nrec == 0:
            raise OwlgError('this file has no recovery tier; the result cannot be bit-identical')
        if want_full:
            src_rec = rec.copy(); out2 = rec.copy(); k = hdr['n_base'] + hdr['n_corr']
            for ty in range(hdr['nty']):
                for tx in range(hdr['ntx']):
                    y0,y1 = ty*tile, min((ty+1)*tile,H); x0,x1 = tx*tile, min((tx+1)*tile,W)
                    dec_tile(np.frombuffer(get(k), np.uint8), src_rec, y0,y1,x0,x1, 0, out2); k += 1
            arr = out2
    out = np.zeros((hdr['bands'], H, W), np.uint8)
    for i, b in enumerate(coded): out[b] = arr[i]
    for k2, v in hdr['const'].items(): out[int(k2)] = v
    if hdr.get('sha256'):
        got = hashlib.sha256(np.ascontiguousarray(out).tobytes()).hexdigest()
        hdr['sha256_match'] = (got == hdr['sha256'])
        if verify_sha and not hdr['sha256_match']:
            raise OwlgError('SHA-256 mismatch: the decoded result is not the original pixels')
    return out, hdr

def to_tif(src, dst, password=None, fast=False, tier='auto', verify_sha=False):
    a, hdr = read_owlg(src, password, fast, tier=tier, verify_sha=verify_sha)
    from .geotiff import write as _gwrite
    _gwrite(dst, a, crs_wkt=hdr.get('crs'), transform6=hdr.get('transform'),
            tags=hdr.get('tags'), colorinterp=hdr.get('colorinterp'),
            nodata=hdr.get('nodata'))
    return dst

def info(path, password=None):
    hdr, _ = open_owlg(path, password)
    nb = hdr['n_base']
    base = sum(L for _, L in hdr['dir'][:nb]); corr = sum(L for _, L in hdr['dir'][nb:])
    return hdr, base, corr


# ------------------- windowed reads (for the GDAL/QGIS bridge) -------------------
_WCACHE = {}

def _win_state(path, password=None, fast=False):
    """Open the file once and hold on to the decoded base layer. Corrections are
    decoded per tile only when a window asks for them, so QGIS never decodes the
    whole raster."""
    from .tiled_read import _pw_tag
    key = (os.path.abspath(path), os.path.getmtime(path), bool(fast), _pw_tag(password))
    st = _WCACHE.get(key)
    if st is not None: return st
    _WCACHE.clear()
    hdr, get = open_owlg(path, password)
    decs = [_dec_base(get(i), hdr['codec'])[:, :, :len(g)] for i, g in enumerate(hdr['groups'])]
    BASE = np.ascontiguousarray(np.concatenate([d.transpose(2, 0, 1) for d in decs], 0)[:len(hdr['coded'])])
    st = dict(hdr=hdr, get=get, base=BASE, fast=fast,
              scratch=None,      # delta-level reconstruction accumulator
              scratch2=None,     # recovery-layer output
              d1=set())          # tiles whose correction layer has been applied
    _WCACHE[key] = st
    return st

def _ensure_d1(st, ty, tx):
    """Apply one tile's correction layer to the accumulator, once and only once."""
    hdr = st['hdr']
    nty, ntx = hdr['nty'], hdr['ntx']
    if not (0 <= ty < nty and 0 <= tx < ntx): return
    if (ty, tx) in st['d1']: return
    if st['scratch'] is None: st['scratch'] = st['base'].copy()
    T = hdr['tile']; H, W = hdr['h'], hdr['w']
    y0, y1 = ty * T, min((ty + 1) * T, H)
    x0, x1 = tx * T, min((tx + 1) * T, W)
    idx = hdr['n_base'] + ty * ntx + tx
    dec_tile(np.frombuffer(st['get'](idx), np.uint8), st['base'],
             y0, y1, x0, x1, hdr['delta'], st['scratch'])
    st['d1'].add((ty, tx))

def _tile_slice(st, ty, tx):
    """Return the fully decoded (B,th,tw) slice for a single tile."""
    hdr = st['hdr']; T = hdr['tile']; H, W = hdr['h'], hdr['w']
    y0, y1 = ty * T, min((ty + 1) * T, H)
    x0, x1 = tx * T, min((tx + 1) * T, W)
    # A delta=0 file on a lossy base still carries a correction layer, so the
    # mode string cannot decide this; only n_corr can (see read_owlg).
    if st['fast'] or not hdr.get('n_corr', 0):
        return np.ascontiguousarray(st['base'][:, y0:y1, x0:x1])
    _ensure_d1(st, ty, tx)
    if not hdr.get('n_rec', 0):
        return np.ascontiguousarray(st['scratch'][:, y0:y1, x0:x1])
    # The recovery layer uses the delta-level reconstruction as its context, and that
    # context reaches one pixel outside the tile -> the neighbours must be ready first.
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            _ensure_d1(st, ty + dy, tx + dx)
    if st['scratch2'] is None: st['scratch2'] = st['base'].copy()
    idx2 = hdr['n_base'] + hdr['n_corr'] + ty * hdr['ntx'] + tx
    dec_tile(np.frombuffer(st['get'](idx2), np.uint8), st['scratch'],
             y0, y1, x0, x1, 0, st['scratch2'])
    return np.ascontiguousarray(st['scratch2'][:, y0:y1, x0:x1])

def read_window(path, band, xoff, yoff, xsize, ysize, password=None, fast=False, level=0):
    if _v4(path):
        from .tiled_read import reader
        return reader(path, password, fast).read_window(band, xoff, yoff, xsize, ysize, level)
    return _read_window_v3(path, band, xoff, yoff, xsize, ysize, password, fast)


def _read_window_v3(path, band, xoff, yoff, xsize, ysize, password=None, fast=False):
    """Return a (ysize,xsize) uint8 array for one band. Only the tiles that
    intersect the window are decoded."""
    st = _win_state(path, password, fast)
    hdr = st['hdr']; H, W = hdr['h'], hdr['w']
    xoff = max(0, int(xoff)); yoff = max(0, int(yoff))
    xsize = max(0, min(int(xsize), W - xoff)); ysize = max(0, min(int(ysize), H - yoff))
    out = np.zeros((ysize, xsize), np.uint8)
    if xsize == 0 or ysize == 0: return out
    const = hdr.get('const') or {}
    if str(band) in const:
        out[:] = const[str(band)]; return out
    if band not in hdr['coded']: return out
    bi = hdr['coded'].index(band)
    if st['fast'] or not hdr.get('n_corr', 0):
        return np.ascontiguousarray(st['base'][bi, yoff:yoff+ysize, xoff:xoff+xsize])
    T = hdr['tile']
    for ty in range(yoff // T, (yoff + ysize - 1) // T + 1):
        for tx in range(xoff // T, (xoff + xsize - 1) // T + 1):
            t = _tile_slice(st, ty, tx)
            ty0, tx0 = ty * T, tx * T
            sy0 = max(yoff, ty0); sy1 = min(yoff + ysize, ty0 + t.shape[1])
            sx0 = max(xoff, tx0); sx1 = min(xoff + xsize, tx0 + t.shape[2])
            if sy1 <= sy0 or sx1 <= sx0: continue
            out[sy0-yoff:sy1-yoff, sx0-xoff:sx1-xoff] = t[bi, sy0-ty0:sy1-ty0, sx0-tx0:sx1-tx0]
    return out
