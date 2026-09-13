#!/usr/bin/env python3
"""owlg — command line interface for Optimized Owl GeoTIFF."""
import argparse, os, sys, json, getpass, numpy as np
from . import _term as T

def _pw(a, need=False):
    if getattr(a, 'password', None): return a.password
    if os.environ.get('OWLG_KEY'): return os.environ['OWLG_KEY']
    if getattr(a, 'ask_password', False) or need: return getpass.getpass('OWLG passphrase: ')
    return None

AUTO_TILED_PIXELS = 16 << 20      # above 16 MPixel -> tiled layout


def _auto_layout(src):
    """Big rasters must be tiled: encode/read RAM must not scale with file size."""
    try:
        import rasterio, warnings; warnings.filterwarnings('ignore')
        with rasterio.open(src) as ds:
            n = ds.width * ds.height
    except Exception:
        try:
            from osgeo import gdal
            d = gdal.Open(src); n = d.RasterXSize * d.RasterYSize
        except Exception:
            return 'tiled'
    return 'tiled' if n > AUTO_TILED_PIXELS else 'flat'


def _next_delta(d):
    from .target import DELTAS
    later = [x for x in DELTAS if x > d]
    return later[0] if later else d + 8


def _search_target(a, layout):
    """Pick (delta, q) for --target. Prints the probes so the trade-off is visible."""
    from . import imgio as _io
    from .target import parse_target, search, estimate_flat, estimate_tiled
    want = parse_target(a.target)
    base = (a.base or 'webp')
    if base not in _io.QUALITY_LADDERS:
        sys.exit(f"--target works with --base webp or avif (got {base})")
    ladder = [int(a.q)] if a.q else _io.QUALITY_LADDERS[base]
    print(f"  searching the smallest delta that gives {want:g}x vs raw (base {base})")
    if layout == 'tiled':
        from .tiled import scan_source, _Sampler
        W, H, B, _meta, _const, coded = scan_source(a.src)
        raw = W * H * B
        est = estimate_tiled(_Sampler(a.src, coded, a.tile or 512), base, ladder, a.overviews)
    else:
        from .container import load_source
        A, _meta, _const, coded, groups = load_source(a.src)
        raw = A.nbytes
        est = estimate_flat(np.ascontiguousarray(A[coded]), coded, groups, base, ladder,
                            a.tile or 1024)
    log = lambda d, n, q, r: print(f"    delta {d:<2d} q={q:<3d} -> {n/1e6:.3f} MB  {r:5.1f}x")
    d, n, q, ok = search(est, raw, want, log=log)
    if ok:
        print(f"  -> delta {d} (+/-{d} DN) is the smallest bound that reaches {want:g}x")
    else:
        print(f"  -> even delta {d} only reaches {raw/max(n,1):.1f}x; encoding at +/-{d} DN anyway")
    return d, q


def _flat_idx(idxs):
    for j in idxs:
        if isinstance(j, (list, tuple)): yield from j
        else: yield j


def _info(a):
    from .container import open_owlg
    hdr, _ = open_owlg(a.src, _pw(a))
    if a.json:
        print(json.dumps({k: v for k, v in hdr.items() if k != 'dir'}, indent=1, ensure_ascii=False))
        return
    sz = os.path.getsize(a.src); raw = hdr['w'] * hdr['h'] * hdr['bands']
    dirs = hdr.get('dir', []); delta = int(hdr.get('delta', 0))
    v4 = hdr.get('v') == 4
    print(T.hr(os.path.basename(a.src)))
    print(T.kv('format', f"OWLG v{hdr.get('v')} {T.dim('tiled + pyramid' if v4 else 'flat')}"))
    dims = f"{hdr['w']} x {hdr['h']} x {hdr['bands']}"
    print(T.kv('raster', f"{T.num(dims)} uint8  {T.dim(f'raw {raw/1e6:.2f} MB')}"))
    print(T.kv('size', f"{T.num(T.mb(sz))}  {T.ratio(raw/sz)} vs raw"))
    print(T.kv('guarantee', T.bound(delta) + T.dim('  every pixel of every band') if delta
               else T.bound(0) + T.dim('  revert is bit-identical')))
    if hdr.get('bound_vs_original') is not None and hdr['bound_vs_original'] != delta:
        print(T.kv('vs original', T.warn(f"+/-{hdr['bound_vs_original']} DN")
                   + T.dim(f"  (rebased from {hdr.get('rebased_from', {}).get('codec')} "
                           f"+/-{hdr.get('rebased_from', {}).get('delta')})")))
    print(T.kv('base codec', f"{T.key(hdr.get('codec'))} q={hdr.get('q')}"))
    if hdr.get('const'):
        print(T.kv('constant bands', T.dim(str(hdr['const']))))
    crs = hdr.get('crs') or ''
    m = None
    for pat in ('ID["EPSG",', 'AUTHORITY["EPSG","'):
        if pat in crs:
            tail = crs[crs.rindex(pat) + len(pat):]
            m = 'EPSG:' + ''.join(ch for ch in tail[:8] if ch.isdigit()); break
    if crs:
        name = crs.split('"')[1] if '"' in crs else crs[:40]
        print(T.kv('crs', f"{name} {T.dim(m or '')}"))
    tf = hdr.get('transform') or []
    if tf: print(T.kv('pixel size', T.dim(f"{abs(tf[0]):g} x {abs(tf[4]):g}  origin ({tf[2]:.3f}, {tf[5]:.3f})")))
    print(T.kv('encrypted', T.tag('yes, AES-256-GCM') if hdr.get('encrypted') else 'no'))
    if v4:
        lv = hdr['levels']
        print(T.kv('tiles', f"{hdr['tile']} px, {sum(l['nty']*l['ntx'] for l in lv)} tiles, {len(dirs)} blobs"))
        rows = []
        for i, l in enumerate(lv):
            b = sum(dirs[j][1] for j in _flat_idx(l['base']))
            cbytes = sum(dirs[j][1] for j in _flat_idx(l['corr'])) if l.get('corr') else 0
            rows.append([f"level {i}", f"{l['w']} x {l['h']}", f"{l['nty']} x {l['ntx']}",
                         T.mb(b + cbytes), T.warn('base + correction') if l.get('corr') else T.dim('base only')])
        print(T.table(rows, header=['pyramid', 'size', 'tiles', 'bytes', 'content'], align='llrrl'))
        dg = hdr.get('sha256_scan')
        print(T.kv('tile digest', (T.ok('stored') + T.dim(f' {dg[:16]}...')) if dg else T.dim('absent')))
    else:
        nb, nc, nr = hdr.get('n_base', 0), hdr.get('n_corr', 0), hdr.get('n_rec', 0)
        base = sum(L for _, L in dirs[:nb]); corr = sum(L for _, L in dirs[nb:nb+nc])
        rec = sum(L for _, L in dirs[nb+nc:])
        rows = [['base layer', T.mb(base), f'{nb} blob' + ('s' if nb != 1 else '')],
                ['correction', T.mb(corr), f'{nc} tile' + ('s' if nc != 1 else '')]]
        if nr: rows.append(['recovery tier', T.mb(rec), T.ok('bit-identical revert available')])
        print(T.table(rows, header=['layer', 'bytes', ''], align='lrl'))
        if hdr.get('sha256'): print(T.kv('sha256', T.dim(hdr['sha256'][:16] + '...')))


def _geo_line(hdr, otr, oc):
    same_t = np.allclose(otr, hdr['transform']); same_c = (oc.to_wkt() if oc else None) == hdr['crs']
    return T.kv('geo', f"transform {T.ok('same') if same_t else T.bad('DIFFERENT')}, "
                       f"crs {T.ok('same') if same_c else T.bad('DIFFERENT')}", 6)


def _verdict(mx, bound, dl):
    v = T.ok('BOUND PROVEN') if mx <= bound else T.bad('*** VIOLATED ***')
    extra = '' if bound == dl else T.dim(f"  [rebased file: header delta={dl}, guarantee vs the original is +/-{bound}]")
    return T.kv('error', f"max={T.num(mx)} (bound +/-{bound}) -> {v}{extra}", 6)


def _diff(a):
    """Streaming comparison of two rasters of the same shape: what `verify` does
    for an .owlg, for any pair GDAL can open. Meant for measuring what other
    lossy formats actually did to the pixels."""
    import rasterio, warnings; warnings.filterwarnings('ignore')
    with rasterio.open(a.a) as A, rasterio.open(a.b) as B:
        if (A.width, A.height) != (B.width, B.height):
            sys.exit(f"Error: shapes differ: {A.width}x{A.height} vs {B.width}x{B.height}")
        nb = min(A.count, B.count)
        if A.count != B.count:
            print(T.warn(f"band count differs ({A.count} vs {B.count}); comparing the first {nb}"))
        raw = B.width * B.height * B.count
        sa, sb = os.path.getsize(a.a), os.path.getsize(a.b)
        print(T.kv('a', f"{T.path(os.path.basename(a.a))}  {T.dim(T.mb(sa))}", 6))
        print(T.kv('b', f"{T.path(os.path.basename(a.b))}  {T.dim(T.mb(sb) + f', raw {raw/1e6:.1f} MB')}", 6))
        print(T.kv('size', f"a is {T.ratio(raw/sa)} vs raw, {T.ratio(sb/sa)} vs b", 6))
        mx = [0]*nb; se = [0.0]*nb; changed = [0]*nb; n = [0]*nb
        hist = [np.zeros(256, np.int64) for _ in range(nb)]
        wins = list(B.block_windows(1)); prog = T.Progress('comparing', len(wins)) if len(wins) > 8 else None
        for i, (_, win) in enumerate(wins):
            x = A.read(indexes=list(range(1, nb+1)), window=win).astype(np.int32)
            y = B.read(indexes=list(range(1, nb+1)), window=win).astype(np.int32)
            e = np.abs(x - y)
            for b in range(nb):
                eb = e[b]
                if eb.size == 0: continue
                mx[b] = max(mx[b], int(eb.max())); se[b] += float((eb.astype(np.float64)**2).sum())
                changed[b] += int((eb > 0).sum()); n[b] += int(eb.size)
                hist[b] += np.bincount(eb.ravel(), minlength=256)[:256]
            if prog and (i % max(1, len(wins)//50) == 0 or i == len(wins)-1): prog.update(i+1)
    rows = []
    for b in range(nb):
        h = hist[b]; c = np.cumsum(h); tot = max(1, n[b])
        p999 = int(np.searchsorted(c, 0.999 * tot)); p9999 = int(np.searchsorted(c, 0.9999 * tot))
        rows.append([f"band {b+1}", T.num(mx[b]), f"{p999}", f"{p9999}",
                     f"{np.sqrt(se[b]/tot):.3f}", f"{100*changed[b]/tot:.2f}%"])
    print(T.table(rows, header=['', 'max err', 'p99.9', 'p99.99', 'RMSE', 'changed'], align='lrrrrr'))
    M = max(mx)
    if a.bound is not None:
        ok = M <= a.bound
        print(T.kv('bound', f"+/-{a.bound} -> " + (T.ok('HOLDS') if ok else T.bad('*** VIOLATED ***')), 6))
        sys.exit(0 if ok else 2)
    print(T.kv('worst', f"{T.num(M)} DN in some pixel of some band " + T.dim('(no bound is promised by this pair; that is the point)'), 6))


def _verify(a):
    import rasterio, warnings; warnings.filterwarnings('ignore')
    from .tiled_read import is_v4
    from .container import open_owlg
    hdr, _ = open_owlg(a.owlg, _pw(a)); dl = int(hdr['delta'])
    bound = int(hdr.get('bound_vs_original', dl))
    if is_v4(a.owlg):                      # streaming path: safe for 100 GB
        from .tiled_read import verify_bounds, source_digest, scan_digest
        L0 = hdr['levels'][0]
        prog = T.Progress('checking tile rows', L0['nty']) if L0['nty'] > 4 else None
        mx, npix = verify_bounds(a.owlg, a.orig, _pw(a), progress=(lambda i, n: prog.update(i)) if prog else None)
        with rasterio.open(a.orig) as ds:
            otr = list(ds.transform)[:6]; oc = ds.crs
            same = (ds.width == hdr['w'] and ds.height == hdr['h'] and ds.count == hdr['bands'])
        nb_ = max(1, int(hdr['bands']))
        print(T.kv('shape', (T.ok('same') if same else T.bad('DIFFERENT'))
                   + T.dim(f"  ({npix/nb_/1e6:.1f} MPixel x {nb_} bands = {npix/1e6:.1f} M samples checked)"), 6))
        print(_verdict(mx, bound, dl))
        exp = hdr.get('sha256_scan')
        if exp:
            got = source_digest(a.orig, hdr)
            print(T.kv('digest', f"original pixels {T.ok('MATCH') if got == exp else T.bad('MISMATCH')} "
                       + T.dim(f"({exp[:16]}...) -> this file really came from {os.path.basename(a.orig)}"), 6))
            if bound == 0:
                d2 = scan_digest(a.owlg, _pw(a))
                print(f"         decoded pixels {T.ok('MATCH') if d2 == exp else T.bad('MISMATCH')} "
                      + T.dim('-> revert is bit-identical'))
        if bound == 0:
            print(T.kv('lossless', f"bit-identical to the original = {T.ok(True) if mx == 0 else T.bad(False)}", 6))
        print(_geo_line(hdr, otr, oc))
        sys.exit(0 if mx <= bound else 2)
    from .container import read_owlg
    with rasterio.open(a.orig) as ds: O = ds.read(); otr = list(ds.transform)[:6]; oc = ds.crs
    R, hdr = read_owlg(a.owlg, _pw(a))
    er = np.abs(R.astype(np.int32) - O.astype(np.int32)); mx = int(er.max())
    print(T.kv('shape', T.ok('same') if R.shape == O.shape else T.bad('DIFFERENT'), 6))
    if bound == 0 or hdr.get('n_rec'):
        print(T.kv('lossless', f"bit-identical to the original = {T.ok(True) if mx == 0 else T.bad(False)}"
                   f"  | SHA-256 match = {hdr.get('sha256_match')}", 6))
    print(_verdict(mx, bound, dl))
    print(T.dim(f"         mean|e|={er.mean():.4f}  rmse={np.sqrt((er.astype(np.float64)**2).mean()):.4f}"
                f"  pixels changed={100*(er>0).mean():.2f}%"))
    print(_geo_line(hdr, otr, oc))
    sys.exit(0 if mx <= bound else 2)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='owlg', description='Optimized Owl GeoTIFF')
    ap.add_argument('--password', '-P', help='passphrase (or env OWLG_KEY)')
    ap.add_argument('--ask-password', '-A', action='store_true', help='prompt for a passphrase')
    ap.add_argument('--no-color', action='store_true',
                    help='plain output (also: NO_COLOR=1; OWLG_COLOR=always forces colour)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    e = sub.add_parser('encode', help='GeoTIFF -> .owlg')
    e.add_argument('src'); e.add_argument('dst')
    e.add_argument('--delta', type=int, default=0,
                   help='hard per-pixel error bound in DN; 0 (default) = lossless, '
                        'revert is bit-identical')
    e.add_argument('--target', default=None, metavar='RATIO',
                   help='size goal vs raw pixels, e.g. 20 or 20x: search the smallest '
                        'delta that reaches it, then encode with that delta (the '
                        'chosen bound is written to the header and printed)')
    e.add_argument('--codec', default='auto',
                   help='pin an exact base codec and skip the quality search; '
                        'requires --q, flat layout only. Prefer --base.')
    e.add_argument('--q', default=None, help='base layer quality; default is auto-picked')
    e.add_argument('--base', default=None, choices=['webp', 'avif', 'jxl', 'auto'],
                   help='base layer codec: webp (default, portable) | avif (smaller) | '
                        'jxl (lossless-only, smallest but least portable) | auto')
    e.add_argument('--tile', type=int, default=None,
                   help='tile size in pixels; default 512 (tiled) / 1024 (flat)')
    e.add_argument('--layout', choices=['auto', 'tiled', 'flat'], default='auto',
                   help="'tiled' (v4) = constant RAM + internal pyramid, required for "
                        "large rasters; 'flat' (v3) = one block; 'auto' (default) = "
                        "tiled above 16 MPixel")
    e.add_argument('--no-overviews', dest='overviews', action='store_false',
                   help='do not build the internal pyramid (tiled layout only)')
    e.add_argument('--min-overview', type=int, default=256,
                   help='stop halving the pyramid below this size')
    e.add_argument('--overview-q', type=int, default=None,
                   help='base quality for the display-only overview levels '
                        '(default 50, never above the level-0 quality)')
    e.add_argument('--encrypt', action='store_true', help='encrypt with AES-256-GCM')
    e.add_argument('--iters', type=int, default=600_000, help='PBKDF2 iterations')
    e.add_argument('--recovery', action='store_true',
                   help='include a recovery tier so revert can be bit-identical '
                        '(flat layout only)')

    d = sub.add_parser('decode', help='.owlg -> GeoTIFF')
    d.add_argument('src'); d.add_argument('dst')
    d.add_argument('--fast', action='store_true', help='base layer only (skip corrections)')
    d.add_argument('--tier', choices=['auto','light','full'], default='auto',
                   help="'full' demands a bit-identical result (needs a recovery tier)")
    d.add_argument('--check-sha', action='store_true',
                   help='fail unless the result matches the SHA-256 of the original pixels')

    i = sub.add_parser('info', help='show the header'); i.add_argument('src')
    i.add_argument('--json', action='store_true', help='print the raw header JSON only')
    v = sub.add_parser('verify', help='prove the error bound over every pixel')
    v.add_argument('owlg'); v.add_argument('orig')

    df = sub.add_parser('diff', help='compare any two rasters (e.g. an ECW decoded to '
                                     'GeoTIFF vs the original): max error, RMSE, per band')
    df.add_argument('a', help='the lossy/derived raster'); df.add_argument('b', help='the original')
    df.add_argument('--bound', type=int, default=None,
                    help='exit 2 if any sample differs by more than this')

    r = sub.add_parser('vrt', help='write a .vrt sidecar so GDAL/QGIS read .owlg DIRECTLY')
    r.add_argument('src'); r.add_argument('dst', nargs='?')
    r.add_argument('--fast', action='store_true', help='sidecar reads the base layer only')

    sp = sub.add_parser('split', help='split a layered .owlg -> light + .owlr')
    sp.add_argument('src'); sp.add_argument('light'); sp.add_argument('recovery')
    jn = sub.add_parser('join', help='rejoin light + .owlr')
    jn.add_argument('light'); jn.add_argument('recovery'); jn.add_argument('dst')

    rb = sub.add_parser('rebase', help='change the base layer codec (e.g. avif -> webp)')
    rb.add_argument('src'); rb.add_argument('dst')
    rb.add_argument('--base', default='webp', choices=['webp', 'avif', 'jxl', 'auto'])
    rb.add_argument('--delta', type=int, default=0,
                    help='0 (default) = keep the existing guarantee exactly')

    ck = sub.add_parser('check', help='test whether this environment can decode OWLG')
    ck.add_argument('src', nargs='?')

    rv = sub.add_parser('revert', help='.owlgt -> GeoTIFF (mosaic the tiles)')
    rv.add_argument('src'); rv.add_argument('dst')
    rv.add_argument('--zoom', type=int, default=None, help='default: the maximum zoom')
    rv.add_argument('--t-srs', dest='t_srs', default=None,
                    help='warp back to this CRS (e.g. EPSG:32749); default stays EPSG:3857')

    sv = sub.add_parser('serve', help='serve .owlgt over OGC API - Tiles/Maps and WMS 1.3.0')
    sv.add_argument('src', nargs='+'); sv.add_argument('--host', default='127.0.0.1')
    sv.add_argument('--port', type=int, default=8080)

    n = sub.add_parser('npy', help='.owlg -> ndarray (.npy/.npz/memmap)')
    n.add_argument('src'); n.add_argument('dst')
    n.add_argument('--format', choices=['npy','npz','memmap'], default='npy')
    n.add_argument('--layout', choices=['CHW','HWC'], default='CHW')
    n.add_argument('--fast', action='store_true', help='base layer only')

    t = sub.add_parser('tiles', help='EPSG:3857 source -> .owlgt tile pyramid')
    t.add_argument('src'); t.add_argument('dst')
    t.add_argument('--minzoom', type=int, required=True); t.add_argument('--maxzoom', type=int, required=True)
    t.add_argument('--profile', choices=['view','exact'], default='view')
    t.add_argument('--delta', type=int, default=3); t.add_argument('--q', type=int, default=72)

    a = ap.parse_args(argv)
    if a.no_color: os.environ['OWLG_COLOR'] = 'never'
    if a.cmd == 'encode':
        pw = _pw(a, need=a.encrypt)
        layout = a.layout
        if layout == 'auto':
            layout = 'flat' if a.recovery else _auto_layout(a.src)
        if layout == 'tiled' and a.recovery:
            sys.exit("--recovery is only available with --layout flat "
                     "(in the tiled layout, --delta 0 is already bit-identical)")
        delta, q = a.delta, a.q
        if a.target:
            delta, q = _search_target(a, layout)
        if layout == 'tiled':
            from .tiled import write_tiled
            r = write_tiled(a.src, a.dst, delta=delta,
                            base=(a.base or 'webp'), q=(int(q) if q else None),
                            tile=(a.tile or 512), overviews=a.overviews,
                            min_overview=a.min_overview, overview_q=a.overview_q,
                            password=pw if a.encrypt else None, kdf_iters=a.iters)
        else:
            from .container import write_owlg
            r = write_owlg(a.src, a.dst, delta=delta, codec=a.codec, q=q,
                           tile=(a.tile or 1024), base=a.base,
                           password=pw if a.encrypt else None, kdf_iters=a.iters,
                           recovery=a.recovery)
        if a.target:
            from .target import parse_target
            want = parse_target(a.target)
            got = r['ratio']
            if got >= want:
                print(f"  target {want:g}x reached: {got:.1f}x with a guaranteed bound of +/-{delta} DN")
            else:
                print(f"  target {want:g}x NOT reached: {got:.1f}x at +/-{delta} DN "
                      f"(the search estimate was optimistic; re-run with --delta {_next_delta(delta)})")
    elif a.cmd == 'decode':
        from .container import to_tif
        to_tif(a.src, a.dst, password=_pw(a), fast=a.fast, tier=a.tier,
               verify_sha=a.check_sha)
        note = T.warn(' (base layer only, outside the bound)') if a.fast else ''
        print(f"-> {T.path(a.dst)}  {T.dim(f'{os.path.getsize(a.dst)/1e6:.3f} MB')}{note}")
    elif a.cmd == 'info':
        _info(a)
    elif a.cmd == 'verify':
        _verify(a)
    elif a.cmd == 'diff':
        _diff(a)
    elif a.cmd == 'split':
        from .tiering import split
        l, r = split(a.src, a.light, a.recovery, _pw(a))
        print(f"-> {T.path(l)} ({os.path.getsize(l)/1e6:.3f} MB, distribute)  +  "
              f"{T.path(r)} ({os.path.getsize(r)/1e6:.3f} MB, archive)")
    elif a.cmd == 'join':
        from .tiering import join
        o = join(a.light, a.recovery, a.dst, _pw(a))
        print(f"-> {T.path(o)} ({os.path.getsize(o)/1e6:.3f} MB, {T.ok('revert is now bit-identical')})")
    elif a.cmd == 'rebase':
        from .rebase import rebase
        rebase(a.src, a.dst, base=a.base, delta=a.delta, password=_pw(a))
    elif a.cmd == 'check':
        from .imgio import capabilities, can_decode
        c = capabilities()
        yn = lambda v, good='present', badw='absent': T.ok(good) if v else T.bad(badw)
        print(T.kv('image backends', ', '.join(c['backends']) or T.bad('NONE')))
        print(T.kv('GeoTIFF writer', c.get('geotiff_writer') or T.bad('NONE')))
        print(T.kv('encryption', c.get('crypto')))
        print(T.kv('numba', yn(c.get('numba'))) + ('' if c.get('numba') else
              T.dim('   (exact decode is ~26x slower without it: pip install owlg[fast])')))
        print(T.key('real decode probes') + T.dim(':'))
        for cc in ('avif', 'webp', 'jxl'):
            okc, why = can_decode(cc)
            print(f"  {cc:<5}: {yn(okc, 'YES', 'NO')}" + ('' if okc else T.dim(f"  ({why[:90]})")))
        if a.src:
            from .container import open_owlg
            hdr, _ = open_owlg(a.src, _pw(a))
            need = hdr['codec'].replace('_lossless', '')
            okc, why = can_decode(need)
            print(f"\nfile {T.path(os.path.basename(a.src))} uses base {T.key(hdr['codec'])} -> "
                  + (T.ok('CAN be opened here') if okc else T.bad('CANNOT be opened here')))
            if not okc:
                print(T.dim(f"  reason: {why[:140]}"))
                print(T.warn(f"  fix   : owlg rebase {a.src} portable.owlg --base webp   ")
                      + T.dim("(on a machine that can decode it), or re-encode from the original "
                              "GeoTIFF with --base webp"))
        sys.exit(0 if c['can_read_owlg'] else 2)
    elif a.cmd == 'revert':
        from . import mosaic
        d, info = mosaic.to_tif(a.src, a.dst, z=a.zoom, t_srs=a.t_srs)
        print(f"-> {T.path(d)}  crs={T.key(info['crs'])} zoom={T.num(info['zoom'])} warped={info['warped']}")
    elif a.cmd == 'serve':
        from .server import serve
        serve(a.src, host=a.host, port=a.port)
    elif a.cmd == 'vrt':
        from .vrt import make_vrt
        pw = _pw(a)
        if pw: os.environ['OWLG_KEY'] = pw
        out = make_vrt(a.src, a.dst, password=pw, fast=a.fast)
        print(T.path(out))
        print(f"  disk footprint: {T.num(f'{os.path.getsize(a.src)/1e6:.3f} MB')} (.owlg) + "
              f"{os.path.getsize(out)/1e3:.1f} kB (.vrt) — {T.ok('no GeoTIFF copy')}")
        print(T.dim("  usage: export GDAL_VRT_ENABLE_PYTHON=YES  then open the .vrt in QGIS/gdalinfo"))
    elif a.cmd == 'npy':
        from . import ml
        fn = {'npy': ml.to_npy, 'npz': ml.to_npz, 'memmap': ml.to_memmap}[a.format]
        out, m = fn(a.src, a.dst, password=_pw(a), layout=a.layout, fast=a.fast)
        shp = ((m['bands'], m['height'], m['width']) if m['layout'] == 'CHW'
               else (m['height'], m['width'], m['bands']))
        print(f"-> {T.path(out)}  shape={T.num(shp)} layout={m['layout']}")
    elif a.cmd == 'tiles':
        from .tiles import write_owlgt
        write_owlgt(a.src, a.dst, a.minzoom, a.maxzoom, delta=a.delta, q=a.q, profile=a.profile)

def run():
    try:
        main()
    except BrokenPipeError:
        # `owlg check | head -3` closes the pipe early; that is not an error.
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
    except KeyboardInterrupt:
        print('interrupted', file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        name = type(e).__name__
        if name == 'NeedKey':
            print(T.warn('This file is encrypted.') + ' Supply a passphrase with -P, -A, or env OWLG_KEY.',
                  file=sys.stderr)
        else:
            print(f"{T.bad('Error:')} {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    run()
