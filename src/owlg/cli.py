#!/usr/bin/env python3
"""owlg — command line interface for Optimized Owl GeoTIFF."""
import argparse, os, sys, json, getpass, numpy as np

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog='owlg', description='Optimized Owl GeoTIFF')
    ap.add_argument('--password', '-P', help='passphrase (or env OWLG_KEY)')
    ap.add_argument('--ask-password', '-A', action='store_true', help='prompt for a passphrase')
    sub = ap.add_subparsers(dest='cmd', required=True)

    e = sub.add_parser('encode', help='GeoTIFF -> .owlg')
    e.add_argument('src'); e.add_argument('dst')
    e.add_argument('--delta', type=int, default=0,
                   help='hard per-pixel error bound in DN; 0 (default) = lossless, '
                        'revert is bit-identical')
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
    v = sub.add_parser('verify', help='prove the error bound over every pixel')
    v.add_argument('owlg'); v.add_argument('orig')

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
    if a.cmd == 'encode':
        pw = _pw(a, need=a.encrypt)
        layout = a.layout
        if layout == 'auto':
            layout = 'flat' if a.recovery else _auto_layout(a.src)
        if layout == 'tiled':
            if a.recovery:
                sys.exit("--recovery is only available with --layout flat "
                         "(in the tiled layout, --delta 0 is already bit-identical)")
            from .tiled import write_tiled
            write_tiled(a.src, a.dst, delta=a.delta,
                        base=(a.base or 'webp'), q=(int(a.q) if a.q else None),
                        tile=(a.tile or 512), overviews=a.overviews,
                        min_overview=a.min_overview, overview_q=a.overview_q,
                        password=pw if a.encrypt else None, kdf_iters=a.iters)
        else:
            from .container import write_owlg
            write_owlg(a.src, a.dst, delta=a.delta, codec=a.codec, q=a.q,
                       tile=(a.tile or 1024), base=a.base,
                       password=pw if a.encrypt else None, kdf_iters=a.iters,
                       recovery=a.recovery)
    elif a.cmd == 'decode':
        from .container import to_tif
        to_tif(a.src, a.dst, password=_pw(a), fast=a.fast, tier=a.tier,
               verify_sha=a.check_sha)
        print(f"-> {a.dst}")
    elif a.cmd == 'info':
        from .tiled_read import is_v4
        if is_v4(a.src):
            from .container import open_owlg
            hdr, _ = open_owlg(a.src, _pw(a))
            lv = hdr['levels']
            show = {k: v for k, v in hdr.items()
                    if k not in ('crs', 'dir', 'levels', 'coded', 'tags')}
            print(json.dumps(show, indent=1, ensure_ascii=False)[:1400])
            sz = os.path.getsize(a.src)
            nt = sum(l['nty']*l['ntx'] for l in lv)
            raw = hdr['w']*hdr['h']*hdr['bands']
            print(f"file {sz/1e6:.3f} MB | ratio {raw/sz:.2f}x | tile {hdr['tile']}px "
                  f"| {nt} tiles | blobs {len(hdr['dir'])} | encrypted {hdr.get('encrypted', False)}")
            print("pyramid:")
            for i, l in enumerate(lv):
                kind = 'base+correction' if l.get('corr') else 'base only'
                def _flat(idxs):
                    for j in idxs:
                        if isinstance(j, (list, tuple)):
                            yield from j
                        else:
                            yield j
                byts = sum(hdr['dir'][j][1] for j in _flat(l['base']))
                if l.get('corr'):
                    byts += sum(hdr['dir'][j][1] for j in _flat(l['corr']))
                print(f"  level {i}: {l['w']}x{l['h']}  {l['nty']}x{l['ntx']} tiles  "
                      f"{byts/1e6:8.3f} MB  {kind}")
            print("bit-identical revert: " + ('YES (lossless mode)' if hdr['delta'] == 0
                  else f"NO - this file is near-lossless +/-{hdr['delta']} DN"))
            print("tile-scan digest: " + ('stored' if hdr.get('sha256_scan') else 'absent')
                  + (f" ({hdr['sha256_scan'][:16]}...)" if hdr.get('sha256_scan') else ''))
            return
        from .container import info
        hdr, base, corr = info(a.src, _pw(a))
        nb, nc, nr = hdr.get('n_base',0), hdr.get('n_corr',0), hdr.get('n_rec',0)
        dirs = hdr.get('dir', [])
        corr = sum(L for _, L in dirs[nb:nb+nc]); rec = sum(L for _, L in dirs[nb+nc:])
        show = {k: v for k, v in hdr.items() if k not in ('crs', 'dir')}
        print(json.dumps(show, indent=1, ensure_ascii=False)[:1600])
        sz = os.path.getsize(a.src)
        print(f"file {sz/1e6:.3f} MB | base {base/1e6:.3f} | correction {corr/1e6:.3f}"
              + (f" | recovery {rec/1e6:.3f}" if nr else "")
              + f" | blobs {len(dirs)} | encrypted {hdr.get('encrypted')}")
        if nr: print("bit-identical revert: AVAILABLE (recovery tier present)")
        elif hdr.get('delta') == 0: print("bit-identical revert: YES (lossless mode)")
        else: print(f"bit-identical revert: NO - this file is near-lossless +/-{hdr.get('delta')} DN")
    elif a.cmd == 'verify':
        import rasterio, warnings; warnings.filterwarnings('ignore')
        from .tiled_read import is_v4
        if is_v4(a.owlg):                      # streaming path: safe for 100 GB
            from .tiled_read import verify_bounds
            from .container import open_owlg
            hdr, _ = open_owlg(a.owlg, _pw(a)); dl = int(hdr['delta'])
            def _p(i, n):
                if n > 4 and i % max(1, n//10) == 0:
                    print(f"    tile row {i}/{n}", flush=True)
            mx, npix = verify_bounds(a.owlg, a.orig, _pw(a), progress=_p)
            with rasterio.open(a.orig) as ds:
                otr = list(ds.transform)[:6]; oc = ds.crs
                same = (ds.width == hdr['w'] and ds.height == hdr['h']
                        and ds.count == hdr['bands'])
            bound = int(hdr.get('bound_vs_original', dl))
            nb_ = max(1, int(hdr['bands']))
            print(f"shape  : {'same' if same else 'DIFFERENT'}  "
                  f"({npix/nb_/1e6:.1f} MPixel x {nb_} bands = {npix/1e6:.1f} M samples checked)")
            print(f"error  : max={mx} (bound +/-{bound}) -> "
                  f"{'BOUND PROVEN' if mx <= bound else '*** VIOLATED ***'}"
                  + ('' if bound == dl else f"  [rebased file: header delta={dl}, "
                                            f"guarantee vs the original is +/-{bound}]"))
            exp = hdr.get('sha256_scan')
            if exp:
                from .tiled_read import source_digest, scan_digest
                got = source_digest(a.orig, hdr)
                print(f"digest : original pixels {'MATCH' if got == exp else 'MISMATCH'} "
                      f"({exp[:16]}...) -> this file really came from {os.path.basename(a.orig)}")
                if bound == 0:
                    d2 = scan_digest(a.owlg, _pw(a))
                    print(f"         decoded pixels {'MATCH' if d2 == exp else 'MISMATCH'} "
                          "-> revert is bit-identical")
            if bound == 0:
                print(f"lossless: bit-identical to the original = {mx == 0}")
            print(f"geo    : transform {'same' if np.allclose(otr, hdr['transform']) else 'DIFFERENT'}, "
                  f"crs {'same' if (oc.to_wkt() if oc else None) == hdr['crs'] else 'DIFFERENT'}")
            sys.exit(0 if mx <= bound else 2)
        from .container import read_owlg
        with rasterio.open(a.orig) as ds: O = ds.read(); otr = list(ds.transform)[:6]; oc = ds.crs
        R, hdr = read_owlg(a.owlg, _pw(a))
        er = np.abs(R.astype(np.int32) - O.astype(np.int32)); dl = hdr['delta']
        bound = int(hdr.get('bound_vs_original', dl))
        print(f"shape  : {'same' if R.shape==O.shape else 'DIFFERENT'}")
        if bound == 0 or hdr.get('n_rec'):
            print(f"lossless: bit-identical to the original = {er.max() == 0}"
                  f"  | SHA-256 match = {hdr.get('sha256_match')}")
        print(f"error  : max={er.max()} (bound +/-{bound}) -> "
              f"{'BOUND PROVEN' if er.max()<=bound else '*** VIOLATED ***'}"
              + ('' if bound == dl else f"  [rebased file: header delta={dl}, "
                                        f"guarantee vs the original is +/-{bound}]"))
        print(f"         mean|e|={er.mean():.4f}  rmse={np.sqrt((er.astype(np.float64)**2).mean()):.4f}"
              f"  pixels changed={100*(er>0).mean():.2f}%")
        print(f"geo    : transform {'same' if np.allclose(otr,hdr['transform']) else 'DIFFERENT'}, "
              f"crs {'same' if (oc.to_wkt() if oc else None)==hdr['crs'] else 'DIFFERENT'}")
        sys.exit(0 if er.max() <= bound else 2)
    elif a.cmd == 'split':
        from .tiering import split
        l, r = split(a.src, a.light, a.recovery, _pw(a))
        print(f"-> {l} ({os.path.getsize(l)/1e6:.3f} MB)  +  {r} ({os.path.getsize(r)/1e6:.3f} MB)")
    elif a.cmd == 'join':
        from .tiering import join
        o = join(a.light, a.recovery, a.dst, _pw(a))
        print(f"-> {o} ({os.path.getsize(o)/1e6:.3f} MB, revert is now bit-identical)")
    elif a.cmd == 'rebase':
        from .rebase import rebase
        rebase(a.src, a.dst, base=a.base, delta=a.delta, password=_pw(a))
    elif a.cmd == 'check':
        from .imgio import capabilities, can_decode
        c = capabilities()
        print(f"image backends : {', '.join(c['backends']) or 'NONE'}")
        print(f"GeoTIFF writer : {c.get('geotiff_writer') or 'NONE'}")
        print(f"encryption     : {c.get('crypto')}")
        print(f"numba          : {'present' if c.get('numba') else 'absent'}")
        print("real decode probes:")
        for cc in ('avif', 'webp', 'jxl'):
            okc, why = can_decode(cc)
            print(f"  {cc:<5}: {'YES' if okc else 'NO'}" + ('' if okc else f"  ({why[:90]})"))
        if a.src:
            from .container import open_owlg
            hdr, _ = open_owlg(a.src, _pw(a))
            need = hdr['codec'].replace('_lossless', '')
            okc, why = can_decode(need)
            print(f"\nfile {os.path.basename(a.src)} uses base '{hdr['codec']}' -> "
                  f"{'CAN be opened here' if okc else 'CANNOT be opened here'}")
            if not okc:
                print(f"  reason: {why[:140]}")
                print(f"  fix   : owlg rebase {a.src} portable.owlg --base webp   "
                      "(on a machine that can decode it), or re-encode from the original "
                      "GeoTIFF with --base webp")
        sys.exit(0 if c['can_read_owlg'] else 2)
    elif a.cmd == 'revert':
        from . import mosaic
        d, info = mosaic.to_tif(a.src, a.dst, z=a.zoom, t_srs=a.t_srs)
        print(f"-> {d}  crs={info['crs']} zoom={info['zoom']} warped={info['warped']}")
    elif a.cmd == 'serve':
        from .server import serve
        serve(a.src, host=a.host, port=a.port)
    elif a.cmd == 'vrt':
        from .vrt import make_vrt
        pw = _pw(a)
        if pw: os.environ['OWLG_KEY'] = pw
        out = make_vrt(a.src, a.dst, password=pw, fast=a.fast)
        print(out)
        print(f"  disk footprint: {os.path.getsize(a.src)/1e6:.3f} MB (.owlg) + "
              f"{os.path.getsize(out)/1e3:.1f} kB (.vrt) — no GeoTIFF copy")
        print("  usage: export GDAL_VRT_ENABLE_PYTHON=YES  then open the .vrt in QGIS/gdalinfo")
    elif a.cmd == 'npy':
        from . import ml
        fn = {'npy': ml.to_npy, 'npz': ml.to_npz, 'memmap': ml.to_memmap}[a.format]
        out, m = fn(a.src, a.dst, password=_pw(a), layout=a.layout, fast=a.fast)
        shp = ((m['bands'], m['height'], m['width']) if m['layout'] == 'CHW'
               else (m['height'], m['width'], m['bands']))
        print(f"-> {out}  shape={shp} layout={m['layout']}")
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
            print('This file is encrypted. Supply a passphrase with -P, -A, or env OWLG_KEY.',
                  file=sys.stderr)
        else:
            print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    run()
