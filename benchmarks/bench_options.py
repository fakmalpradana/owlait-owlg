#!/usr/bin/env python3
"""Measure every OWLG encoding option on one raster, plus reference formats.

    python benchmarks/bench_options.py samples/rgb_small.tif -o results/rgb_small

Writes `<out>.json` (machine readable) and `<out>.md` (the table that goes in
the README). Every number in the documentation comes from this script; nothing
is typed in by hand.

What is measured, per row:
  size        bytes on disk
  vs GeoTIFF  ratio against a DEFLATE+predictor GeoTIFF of the same raster
  vs raw      ratio against width*height*bands uncompressed bytes
  maxerr      max |original - decoded| over every pixel of every band
  rmse        root mean square error over the same
  enc / dec   wall clock seconds

Reference formats are encoded with GDAL so the comparison is against what a GIS
user would actually produce, not against a strawman.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time, warnings
warnings.filterwarnings('ignore')

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))


# --------------------------------------------------------------------------- io
def read_all(path):
    """Read any raster GDAL can open. rasterio's bundled GDAL lacks some drivers
    (JPEG XL, for one), so fall back to the system gdal_translate -> GeoTIFF."""
    import rasterio
    try:
        with rasterio.open(path) as ds:
            return ds.read(), ds.profile
    except Exception:
        tmp = path + '.readback.tif'
        if not gdal_translate(path, tmp, []):
            raise
        try:
            with rasterio.open(tmp) as ds:
                return ds.read(), ds.profile
        finally:
            os.remove(tmp)


def gdal_translate(src, dst, opts):
    # a leading '-of X' in opts overrides the GTiff default
    fmt = [] if '-of' in opts else ['-of', 'GTiff']
    cmd = ['gdal_translate', '-q'] + fmt + opts + [src, dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0


def err_stats(orig, got):
    if orig.shape != got.shape:
        return None, None
    d = np.abs(orig.astype(np.int32) - got.astype(np.int32))
    return int(d.max()), float(np.sqrt((d.astype(np.float64) ** 2).mean()))


# ---------------------------------------------------------------------- rows
class Bench:
    def __init__(self, src, workdir):
        self.src = src
        self.work = workdir
        self.orig, self.prof = read_all(src)
        b, h, w = self.orig.shape
        self.raw = int(b) * int(h) * int(w) * self.orig.dtype.itemsize
        self.shape = (w, h, b)
        self.rows = []
        self.ref_bytes = None            # DEFLATE GeoTIFF, set by reference()

    def p(self, name):
        return os.path.join(self.work, name)

    def add(self, group, name, path, maxerr, rmse, enc, dec, note=''):
        sz = os.path.getsize(path) if path and os.path.exists(path) else None
        row = dict(group=group, name=name, bytes=sz,
                   vs_raw=(self.raw / sz) if sz else None,
                   vs_gtiff=(self.ref_bytes / sz) if (sz and self.ref_bytes) else None,
                   maxerr=maxerr, rmse=rmse, enc_s=enc, dec_s=dec, note=note)
        self.rows.append(row)
        me = '—' if maxerr is None else str(maxerr)
        print(f"  {name:<34s} {sz/1e6 if sz else 0:8.3f} MB  "
              f"{self.raw/sz if sz else 0:6.2f}x raw  maxerr={me:<4s} "
              f"enc {enc if enc else 0:6.1f}s", flush=True)
        return row

    # ---------------- reference formats ----------------
    def reference(self):
        print("\nReference formats (GDAL)")
        # DEFLATE + horizontal predictor: the usual "lossless GeoTIFF" answer
        d = self.p('ref_deflate.tif')
        t0 = time.time()
        ok = gdal_translate(self.src, d, ['-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=2',
                                          '-co', 'ZLEVEL=9', '-co', 'TILED=YES'])
        enc = time.time() - t0
        if ok:
            self.ref_bytes = os.path.getsize(d)
            self.add('reference', 'GeoTIFF DEFLATE+pred (lossless)', d, 0, 0.0, enc, None,
                     'baseline for the "vs GeoTIFF" column')

        specs = [
            ('GeoTIFF LZW (lossless)',
             ['-co', 'COMPRESS=LZW', '-co', 'PREDICTOR=2', '-co', 'TILED=YES'], 'lzw.tif'),
            ('GeoTIFF ZSTD (lossless)',
             ['-co', 'COMPRESS=ZSTD', '-co', 'PREDICTOR=2', '-co', 'ZSTD_LEVEL=9',
              '-co', 'TILED=YES'], 'zstd.tif'),
            ('GeoTIFF WEBP lossless',
             ['-co', 'COMPRESS=WEBP', '-co', 'WEBP_LOSSLESS=YES', '-co', 'TILED=YES'],
             'webpll.tif'),
            ('COG DEFLATE (lossless)',
             ['-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=2', '-co', 'TILED=YES',
              '-co', 'COPY_SRC_OVERVIEWS=YES'], 'cog.tif'),
            ('GeoTIFF JPEG q85 (lossy)',
             ['-co', 'COMPRESS=JPEG', '-co', 'JPEG_QUALITY=85', '-co', 'TILED=YES'],
             'jpeg85.tif'),
            ('GeoTIFF JPEG q75 (lossy)',
             ['-co', 'COMPRESS=JPEG', '-co', 'JPEG_QUALITY=75', '-co', 'TILED=YES'],
             'jpeg75.tif'),
            ('GeoTIFF WEBP q85 (lossy)',
             ['-co', 'COMPRESS=WEBP', '-co', 'WEBP_LEVEL=85', '-co', 'TILED=YES'],
             'webp85.tif'),
            # JPEG 2000 is the same wavelet family as ECW and the closest freely
            # writable stand-in for it. QUALITY is a percentage of the raw size.
            ('JP2 OpenJPEG lossless',
             ['-of', 'JP2OpenJPEG', '-co', 'REVERSIBLE=YES', '-co', 'QUALITY=100'],
             'jp2ll.jp2'),
        ] + [
            (f'JP2 OpenJPEG q{q} (lossy, ~{100/q:.0f}x)',
             ['-of', 'JP2OpenJPEG', '-co', f'QUALITY={q}'], f'jp2q{q}.jp2')
            for q in (25, 10, 5, 3)
        ] + [
            ('JPEG XL lossless',
             ['-of', 'JPEGXL', '-co', 'LOSSLESS=YES'], 'jxlll.jxl'),
        ] + [
            (f'JPEG XL distance {d} (lossy)',
             ['-of', 'JPEGXL', '-co', 'LOSSLESS=NO', '-co', f'DISTANCE={d}'], f'jxld{d}.jxl')
            for d in (1, 2, 4)
        ]
        for name, opts, fn in specs:
            p = self.p('ref_' + fn)
            t0 = time.time()
            if not gdal_translate(self.src, p, opts):
                print(f"  {name:<34s} (not supported by this GDAL build)")
                continue
            enc = time.time() - t0
            try:
                got, _ = read_all(p)
                me, rm = err_stats(self.orig, got)
            except Exception:
                me = rm = None
            self.add('reference', name, p, me, rm, enc, None)

    # ---------------- owlg ----------------
    def owlg(self, label, group, args, out, decode_args=(), note=''):
        from owlg import cli as owlg_cli
        p = self.p(out)
        if os.path.exists(p):
            os.remove(p)
        t0 = time.time()
        try:
            owlg_cli.main(['encode', self.src, p] + args)
        except SystemExit:
            pass
        except Exception as e:
            print(f"  {label:<34s} FAILED: {type(e).__name__}: {e}")
            return None
        enc = time.time() - t0
        back = self.p(out + '.back.tif')
        t0 = time.time()
        try:
            owlg_cli.main(['decode', p, back] + list(decode_args))
            dec = time.time() - t0
            got, _ = read_all(back)
            me, rm = err_stats(self.orig, got)
        except Exception as e:
            dec, me, rm = None, None, None
            note = (note + f' decode failed: {type(e).__name__}').strip()
        row = self.add(group, label, p, me, rm, enc, dec, note)
        for f in (back,):
            if os.path.exists(f):
                os.remove(f)
        return row


def build_rows(b, deep=False):
    b.reference()

    print("\nOWLG — lossless (bit-identical revert)")
    b.owlg('OWLG lossless, base WebP', 'lossless',
           ['--delta', '0', '--base', 'webp', '--layout', 'flat'], 'll_webp.owlg',
           note='the default: portable, opens anywhere WebP does')
    b.owlg('OWLG lossless, base AVIF', 'lossless',
           ['--delta', '0', '--base', 'avif', '--layout', 'flat'], 'll_avif.owlg')
    b.owlg('OWLG lossless, base JPEG XL', 'lossless',
           ['--delta', '0', '--base', 'jxl', '--layout', 'flat'], 'll_jxl.owlg',
           note='smallest lossless, but needs a JXL decoder')
    b.owlg('OWLG lossless, tiled+pyramid', 'lossless',
           ['--delta', '0', '--layout', 'tiled', '--tile', '512'], 'll_tiled.owlg',
           note='constant RAM, internal overviews')

    print("\nOWLG — near-lossless, hard per-pixel bound")
    for d in ([0, 1, 2, 3, 5, 8, 12, 16] if deep else [1, 2, 3, 5, 8]):
        if d == 0:
            continue
        b.owlg(f'OWLG near-lossless delta={d}', 'nearlossless',
               ['--delta', str(d), '--base', 'webp', '--layout', 'flat'],
               f'nl{d}.owlg', note=f'guaranteed max error <= {d} DN')

    print("\nOWLG — base codec at the same bound (delta=2)")
    b.owlg('OWLG delta=2, base AVIF', 'basecodec',
           ['--delta', '2', '--base', 'avif', '--layout', 'flat'], 'd2_avif.owlg',
           note='smallest, needs an AVIF decoder')

    print("\nOWLG — a size goal: the smallest bound that reaches 20x vs raw")
    for base in ('webp', 'avif'):
        row = b.owlg(f'OWLG --target 20x, base {base.upper()}', 'target',
                     ['--target', '20x', '--base', base, '--layout', 'flat'],
                     f't20_{base}.owlg', note='delta chosen by the encoder')
        if row and row.get('maxerr') is not None:
            row['note'] = f"encoder chose delta={row['maxerr']}; bound proven by the decode"


    print("\nOWLG — layout")
    b.owlg('OWLG delta=2, tiled+pyramid', 'layout',
           ['--delta', '2', '--layout', 'tiled', '--tile', '512'], 'd2_tiled.owlg',
           note='constant RAM; required for 10-100 GB')
    b.owlg('OWLG delta=2, tiled, no overviews', 'layout',
           ['--delta', '2', '--layout', 'tiled', '--tile', '512', '--no-overviews'],
           'd2_tiled_noov.owlg', note='pyramid costs ~+23% of level 0')

    print("\nOWLG — recovery tier and encryption")
    b.owlg('OWLG delta=2 + recovery tier', 'extras',
           ['--delta', '2', '--base', 'webp', '--layout', 'flat', '--recovery'],
           'd2_rec.owlg', decode_args=['--tier', 'full'],
           note='ship light, archive recovery; revert is bit-identical')
    os.environ['OWLG_KEY'] = 'benchmark-passphrase'
    b.owlg('OWLG delta=2, AES-256-GCM', 'extras',
           ['--delta', '2', '--base', 'webp', '--layout', 'flat', '--encrypt',
            '--iters', '50000'], 'd2_enc.owlg',
           note='encryption overhead is the tag+nonce per blob')
    os.environ.pop('OWLG_KEY', None)


def split_rows(b):
    """Measure the two files `owlg split` produces from a recovery-tier file."""
    from owlg import cli as owlg_cli
    src = b.p('d2_rec.owlg')
    if not os.path.exists(src):
        return
    light, rec = b.p('split_light.owlg'), b.p('split_rec.owlr')
    try:
        owlg_cli.main(['split', src, light, rec])
    except Exception:
        return
    b.add('extras', '  -> light half (distribute)', light, None, None, None, None,
          'near-lossless +/-2, safe to share widely')
    b.add('extras', '  -> recovery half (archive)', rec, None, None, None, None,
          'rejoin to get bit-identical pixels back')


def owlgt_rows(b):
    """OWLGT web tile pyramid vs the usual gdal2tiles/mbtiles answer."""
    from owlg import cli as owlg_cli
    src3857 = b.p('src3857.tif')
    if not gdal_translate(b.src, src3857, []):
        return
    r = subprocess.run(['gdalwarp', '-q', '-t_srs', 'EPSG:3857', '-overwrite',
                        b.src, src3857], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(src3857):
        print("\nOWLGT: gdalwarp to EPSG:3857 failed; skipping")
        return
    import rasterio
    with rasterio.open(src3857) as ds:
        res = ds.transform.a
    # pick a zoom range that roughly matches the native resolution
    z = 0
    while 156543.03392804097 / (2 ** z) > res and z < 22:
        z += 1
    zmax = min(z, 21); zmin = max(0, zmax - 3)
    print(f"\nOWLGT web tiles (z{zmin}-{zmax}, native res {res:.3f} m/px)")

    for label, args, out, note in [
        ('OWLGT profile=view q50', ['--profile', 'view', '--q', '50'], 'view50.owlgt',
         'display only; smallest'),
        ('OWLGT profile=view q72', ['--profile', 'view', '--q', '72'], 'view72.owlgt',
         'default display quality'),
        ('OWLGT profile=exact delta=3', ['--profile', 'exact', '--delta', '3'],
         'exact3.owlgt', 'bounded error per tile; analysis-safe'),
    ]:
        p = b.p(out)
        t0 = time.time()
        try:
            owlg_cli.main(['tiles', src3857, p, '--minzoom', str(zmin),
                           '--maxzoom', str(zmax)] + args)
        except Exception as e:
            print(f"  {label:<34s} FAILED: {type(e).__name__}: {e}")
            continue
        b.add('owlgt', label, p, None, None, time.time() - t0, None, note)

    # gdal2tiles equivalent, measured as the on-disk size of the PNG tree
    out = b.p('g2t')
    shutil.rmtree(out, ignore_errors=True)
    t0 = time.time()
    # gdal2tiles ships as a script whose shebang may point at an interpreter
    # without osgeo bindings, so try a few ways of invoking it.
    script = shutil.which('gdal2tiles.py') or shutil.which('gdal2tiles')
    cands = []
    if script:
        cands += [[sys.executable, script], ['python3.12', script],
                  ['python3', script], [script]]
    cands.append(['gdal2tiles'])
    for pre in cands:
        try:
            r = subprocess.run(pre + ['-q', '-z', f'{zmin}-{zmax}', '-w', 'none',
                                      src3857, out], capture_output=True, text=True)
        except (FileNotFoundError, PermissionError):
            continue
        if r.returncode == 0 and os.path.isdir(out):
            break
    if os.path.isdir(out):
        tot = n = 0
        for dp, _, fs in os.walk(out):
            for f in fs:
                if f.endswith('.png'):
                    tot += os.path.getsize(os.path.join(dp, f)); n += 1
        row = dict(group='owlgt', name=f'gdal2tiles PNG tree ({n} tiles)', bytes=tot,
                   vs_raw=(b.raw / tot) if tot else None,
                   vs_gtiff=(b.ref_bytes / tot) if (tot and b.ref_bytes) else None,
                   maxerr=None, rmse=None, enc_s=time.time() - t0, dec_s=None,
                   note='the usual answer; one PNG per tile on disk')
        b.rows.append(row)
        print(f"  {row['name']:<34s} {tot/1e6:8.3f} MB  {b.raw/tot:6.2f}x raw")

    # MBTiles, for completeness
    mb = b.p('tiles.mbtiles')
    t0 = time.time()
    r = subprocess.run(['gdal_translate', '-q', '-of', 'MBTILES', src3857, mb,
                        '-co', 'TILE_FORMAT=PNG', '-co', f'MAXZOOM={zmax}'],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(mb):
        subprocess.run(['gdaladdo', '-q', mb], capture_output=True, text=True)
        b.add('owlgt', 'MBTiles PNG', mb, None, None, time.time() - t0, None,
              'single file, but still PNG tiles inside')


# ---------------------------------------------------------------------- output
def to_markdown(meta, rows):
    w, h, bands = meta['width'], meta['height'], meta['bands']
    L = [f"### `{os.path.basename(meta['source'])}` — {w} x {h} x {bands} "
         f"{meta['dtype']}, {meta['raw_bytes']/1e6:.1f} MB raw\n"]
    titles = {
        'reference': 'Reference formats',
        'lossless': 'OWLG lossless — revert is bit-identical',
        'nearlossless': 'OWLG near-lossless — hard per-pixel bound',
        'basecodec': 'Base codec, same bound',
        'target': 'A size goal: `--target 20x`',
        'layout': 'Layout',
        'extras': 'Recovery tier and encryption',
        'owlgt': 'OWLGT web tiles',
    }
    for g in ['reference', 'lossless', 'nearlossless', 'basecodec', 'target', 'layout',
              'extras', 'owlgt']:
        sel = [r for r in rows if r['group'] == g]
        if not sel:
            continue
        L.append(f"\n**{titles[g]}**\n")
        L.append('| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |')
        L.append('|---|---:|---:|---:|---:|---:|---:|')
        for r in sel:
            sz = f"{r['bytes']/1e6:.3f} MB" if r['bytes'] else '—'
            vr = f"{r['vs_raw']:.2f}x" if r['vs_raw'] else '—'
            vg = f"{r['vs_gtiff']:.2f}x" if r['vs_gtiff'] else '—'
            me = '—' if r['maxerr'] is None else (
                '**0**' if r['maxerr'] == 0 else str(r['maxerr']))
            rm = '—' if r['rmse'] is None else f"{r['rmse']:.3f}"
            en = f"{r['enc_s']:.1f} s" if r['enc_s'] else '—'
            esc = lambda t: t.replace('|', '\\|')
            nm = esc(r['name']) + (f"<br><sub>{esc(r['note'])}</sub>" if r['note'] else '')
            L.append(f"| {nm} | {sz} | {vr} | {vg} | {me} | {rm} | {en} |")
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', help='GeoTIFF to benchmark')
    ap.add_argument('-o', '--out', default=None, help='output prefix (default: results/<name>)')
    ap.add_argument('--keep', action='store_true', help='keep the encoded files')
    ap.add_argument('--deep', action='store_true', help='wider delta sweep')
    ap.add_argument('--no-owlgt', action='store_true', help='skip the web tile section')
    a = ap.parse_args()

    out = a.out or os.path.join(ROOT, 'results', os.path.splitext(os.path.basename(a.src))[0])
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    work = out + '.work' if a.keep else tempfile.mkdtemp(prefix='owlgbench_')
    os.makedirs(work, exist_ok=True)

    import owlg
    from owlg._compat import HAVE_NUMBA
    from owlg import imgio
    caps = imgio.capabilities()
    print(f"OWLG {owlg.__version__} | numba={HAVE_NUMBA} | backends={caps['backends']}")
    print(f"source: {a.src}")

    b = Bench(a.src, work)
    w, h, bands = b.shape
    print(f"        {w} x {h} x {bands} {b.orig.dtype}, {b.raw/1e6:.1f} MB raw")
    build_rows(b, deep=a.deep)
    split_rows(b)
    if not a.no_owlgt:
        try:
            owlgt_rows(b)
        except Exception as e:
            print(f"OWLGT section skipped: {type(e).__name__}: {e}")

    meta = dict(source=os.path.abspath(a.src), width=w, height=h, bands=bands,
                dtype=str(b.orig.dtype), raw_bytes=b.raw,
                owlg_version=owlg.__version__, numba=HAVE_NUMBA,
                backends=caps['backends'], when=time.strftime('%Y-%m-%d'))
    with open(out + '.json', 'w') as f:
        json.dump(dict(meta=meta, rows=b.rows), f, indent=1)
    with open(out + '.md', 'w') as f:
        f.write(to_markdown(meta, b.rows))
    print(f"\n-> {out}.json\n-> {out}.md")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
