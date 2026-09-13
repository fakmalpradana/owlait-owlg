"""Benchmark for rasters that do not fit the flat path: everything streams.

    python benchmarks/bench_large.py data/FT2026_crop/FT2026_crop.tif \
        --src3857 data/FT2026_crop/FT2026_crop_3857.tif --resume

Three sections, each honest about what it measures:

* reference   GDAL formats (GeoTIFF codecs, JPEG 2000, JPEG XL) with the error
              every lossy one actually made, measured pixel by pixel.
* owlg        tiled layout, delta 2/4/8/16/32, two base codecs; the bound is
              proven by decoding the whole file back and diffing it.
* owlgt       web tile pyramid vs gdal2tiles trees and MBTiles, same zoom range,
              plus tile latency: how long one tile takes to reach a client.

Rows are written to the .json after each measurement so `--resume` skips what
is already done; a full run on a 169 MPixel raster takes a few hours.
"""
import argparse, json, os, random, shutil, sqlite3, statistics, subprocess, sys, tempfile
import threading, time, urllib.request, warnings
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
warnings.filterwarnings('ignore')

DELTAS = [2, 4, 8, 16, 32]
NLAT = 200            # random tiles per latency measurement


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.returncode == 0


def gdal_translate(src, dst, opts):
    fmt = [] if '-of' in opts else ['-of', 'GTiff']
    return sh(['gdal_translate', '-q'] + fmt + opts + [src, dst])


def ms(samples):
    s = sorted(samples)
    return dict(p50=1e3 * statistics.median(s), p95=1e3 * s[int(0.95 * (len(s) - 1))])


class Bench:
    def __init__(self, src, work, out, resume):
        import rasterio
        self.src, self.work, self.out = src, work, out
        with rasterio.open(src) as ds:
            self.w, self.h, self.b = ds.width, ds.height, ds.count
            self.dtype = str(ds.dtypes[0])
        self.raw = self.w * self.h * self.b
        self.rows = []
        if resume and os.path.exists(out + '.json'):
            self.rows = json.load(open(out + '.json'))['rows']
            print(f"resuming: {len(self.rows)} rows already measured")
        self.ref_bytes = next((r['bytes'] for r in self.rows if r['name'].startswith('GeoTIFF DEFLATE')), None)

    def p(self, name): return os.path.join(self.work, name)
    def done(self, name): return any(r['name'].startswith(name) for r in self.rows)

    def add(self, group, name, nbytes, maxerr=None, rmse=None, enc=None, dec=None, note='', **extra):
        row = dict(group=group, name=name, bytes=nbytes, vs_raw=self.raw / nbytes if nbytes else None,
                   vs_gtiff=(self.ref_bytes / nbytes) if (nbytes and self.ref_bytes) else None,
                   maxerr=maxerr, rmse=rmse, enc_s=enc, dec_s=dec, note=note, **extra)
        self.rows.append(row)
        self.save()
        me = '—' if maxerr is None else str(maxerr)
        print(f"  {name:<36s} {nbytes/1e6:9.3f} MB {self.raw/nbytes if nbytes else 0:7.2f}x raw  "
              f"maxerr={me:<4s} enc {enc or 0:7.1f}s dec {dec or 0:6.1f}s", flush=True)
        return row

    def save(self):
        import owlg
        meta = dict(source=os.path.abspath(self.src), width=self.w, height=self.h, bands=self.b,
                    dtype=self.dtype, raw_bytes=self.raw, owlg_version=owlg.__version__,
                    when=time.strftime('%Y-%m-%d'))
        json.dump(dict(meta=meta, rows=self.rows), open(self.out + '.json', 'w'), indent=1)

    def err(self, path):
        """max err / RMSE of `path` against the source, streaming; non-GeoTIFF
        formats go through a temporary GeoTIFF readback first."""
        from owlg.cli import diff_stats
        tmp = None
        if not path.lower().endswith(('.tif', '.tiff')):
            tmp = path + '.readback.tif'
            if not gdal_translate(path, tmp, ['-co', 'TILED=YES']):
                return None, None
            path = tmp
        try:
            st = diff_stats(path, self.src)
        finally:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        n = sum(1 for _ in st)
        return max(s['max'] for s in st), float(np.sqrt(sum(s['rmse'] ** 2 for s in st) / n))

    # ------------------------------------------------------------ reference
    def reference(self):
        print("\nReference formats (GDAL)")
        specs = [
            ('GeoTIFF DEFLATE+pred (lossless)', ['-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=2',
             '-co', 'ZLEVEL=9', '-co', 'TILED=YES'], 'deflate.tif', 'baseline for the "vs GeoTIFF" column'),
            ('GeoTIFF LZW (lossless)', ['-co', 'COMPRESS=LZW', '-co', 'PREDICTOR=2', '-co', 'TILED=YES'], 'lzw.tif', ''),
            ('GeoTIFF ZSTD (lossless)', ['-co', 'COMPRESS=ZSTD', '-co', 'PREDICTOR=2', '-co', 'ZSTD_LEVEL=9',
             '-co', 'TILED=YES'], 'zstd.tif', ''),
            ('GeoTIFF WEBP lossless', ['-co', 'COMPRESS=WEBP', '-co', 'WEBP_LOSSLESS=YES', '-co', 'TILED=YES'], 'webpll.tif', ''),
            ('GeoTIFF JPEG q85 (lossy)', ['-co', 'COMPRESS=JPEG', '-co', 'JPEG_QUALITY=85', '-co', 'TILED=YES',
             '-b', '1', '-b', '2', '-b', '3'], 'jpeg85.tif', 'RGB only: JPEG cannot carry the 4th band'),
            ('GeoTIFF JPEG q75 (lossy)', ['-co', 'COMPRESS=JPEG', '-co', 'JPEG_QUALITY=75', '-co', 'TILED=YES',
             '-b', '1', '-b', '2', '-b', '3'], 'jpeg75.tif', 'RGB only'),
            ('GeoTIFF WEBP q85 (lossy)', ['-co', 'COMPRESS=WEBP', '-co', 'WEBP_LEVEL=85', '-co', 'TILED=YES'], 'webp85.tif', ''),
            ('GeoTIFF WEBP q75 (lossy)', ['-co', 'COMPRESS=WEBP', '-co', 'WEBP_LEVEL=75', '-co', 'TILED=YES'], 'webp75.tif', ''),
            ('JP2 OpenJPEG lossless', ['-of', 'JP2OpenJPEG', '-co', 'REVERSIBLE=YES', '-co', 'QUALITY=100'], 'jp2ll.jp2',
             'JPEG 2000: the same wavelet family as ECW'),
        ] + [
            (f'JP2 OpenJPEG q{q} (lossy, ~{100/q:.0f}x)', ['-of', 'JP2OpenJPEG', '-co', f'QUALITY={q}'], f'jp2q{q}.jp2', '')
            for q in (25, 10, 5, 3, 2)
        ] + [
            ('JPEG XL lossless', ['-of', 'JPEGXL', '-co', 'LOSSLESS=YES'], 'jxlll.jxl', ''),
        ] + [
            (f'JPEG XL distance {d} (lossy)', ['-of', 'JPEGXL', '-co', 'LOSSLESS=NO', '-co', f'DISTANCE={d}'], f'jxld{d}.jxl', '')
            for d in (1, 2, 4)
        ]
        for name, opts, fn, note in specs:
            if self.done(name): continue
            p = self.p('ref_' + fn)
            t0 = time.time()
            if not os.path.exists(p) and not gdal_translate(self.src, p, opts):
                print(f"  {name:<36s} (not supported by this GDAL build)"); continue
            enc = time.time() - t0
            me, rm = self.err(p)
            if name.startswith('GeoTIFF DEFLATE'): self.ref_bytes = os.path.getsize(p)
            self.add('reference', name, os.path.getsize(p), me, rm, enc, None, note)

    # ------------------------------------------------------------ owlg
    def owlg(self):
        from owlg import cli
        print("\nOWLG tiled + pyramid — hard per-pixel bound, proven by a full decode")
        for base in ('webp', 'avif'):
            for d in ([0] if base == 'webp' else []) + DELTAS:
                name = f'OWLG delta={d}, base {base.upper()}' if d else 'OWLG lossless (delta=0), base WEBP'
                if self.done(name): continue
                p = self.p(f'owlg_{base}_d{d}.owlg'); back = p + '.back.tif'
                for f in (p, back):
                    if os.path.exists(f): os.remove(f)
                t0 = time.time()
                cli.main(['encode', self.src, p, '--delta', str(d), '--base', base, '--layout', 'tiled'])
                enc = time.time() - t0
                t0 = time.time()
                cli.main(['decode', p, back])
                dec = time.time() - t0
                me, rm = self.err(back)
                os.remove(back)
                self.add('owlg', name, os.path.getsize(p), me, rm, enc, dec,
                         'bit-identical revert' if d == 0 else f'guaranteed max error <= {d} DN')

    # ------------------------------------------------------------ owlgt
    def owlgt(self, src3857):
        from owlg import tiles, cli
        import rasterio
        with rasterio.open(src3857) as ds:
            res = ds.transform.a
        # gdal2tiles' convention: the finest zoom whose tile resolution is still
        # coarser than or equal to the native pixel (never oversample)
        zmax = 0
        while 156543.03392804097 / (2 ** zmax) >= res and zmax < 23: zmax += 1
        zmax -= 1; zmin = max(0, zmax - 7)
        print(f"\nOWLGT web tiles z{zmin}-{zmax} (native {res:.3f} m/px)")

        # ponytail: build the pyramid once and hand it to every write_owlgt;
        # it is also the ground truth the OWLGT bound is measured against.
        t0 = time.time(); LV = tiles.build_pyramid(src3857, zmin, zmax); pyr_s = time.time() - t0
        ntiles = sum(len(v) for v in LV.values())
        print(f"  pyramid: {ntiles} tiles in {pyr_s:.0f}s")
        tiles.build_pyramid = lambda *a, **k: LV

        def owlgt_err(path):
            hdr, raw, base = tiles.open_owlgt(path)
            cache = {}; mx = 0; se = 0.0; n = 0
            for z in sorted(LV):
                for (x, y), arr in LV[z].items():
                    rec, _ = tiles.read_tile(hdr, raw, base, z, x, y, cache)
                    e = np.abs(rec.astype(np.int32) - arr[:, :, :3].astype(np.int32))
                    mx = max(mx, int(e.max())); se += float((e.astype(np.float64) ** 2).sum()); n += e.size
                    if len(cache) > 64: cache.clear()
            return mx, float(np.sqrt(se / n))

        specs = [('OWLGT view q50', ['--profile', 'view', '--q', '50'], 'display only, no bound'),
                 ('OWLGT view q72', ['--profile', 'view', '--q', '72'], 'default display quality')]
        specs += [(f'OWLGT exact delta={d}', ['--profile', 'exact', '--delta', str(d)],
                   f'guaranteed max error <= {d} DN on every tile') for d in DELTAS]
        for name, args, note in specs:
            if self.done(name): continue
            p = self.p(name.replace(' ', '_').replace('=', '') + '.owlgt')
            t0 = time.time()
            tiles.write_owlgt(src3857, p, zmin, zmax, delta=int(args[3]) if 'exact' in args else 3,
                              q=int(args[3]) if 'view' in args else 72, profile=args[1], verbose=False)
            enc = time.time() - t0 + pyr_s
            me, rm = owlgt_err(p)
            self.add('owlgt', name, os.path.getsize(p), me, rm, enc, None, note, ntiles=ntiles)

        # gdal2tiles trees: PNG is the lossless twin the lossy trees are measured against
        script = shutil.which('gdal2tiles.py') or shutil.which('gdal2tiles')
        trees = [('gdal2tiles PNG tree', ['--tiledriver=PNG'], 'png'),
                 ('gdal2tiles JPEG q75 tree', ['--tiledriver=JPEG', '--jpeg-quality=75'], 'jpg'),
                 ('gdal2tiles WEBP q75 tree', ['--tiledriver=WEBP', '--webp-quality=75'], 'webp')]
        for name, args, ext in trees:
            out = self.p('g2t_' + ext)
            if self.done(name) and os.path.isdir(out): continue
            if not os.path.isdir(out):
                t0 = time.time()
                # the script's own shebang interpreter is the one with osgeo bindings
                ok = script and sh([script, '-q', '-z', f'{zmin}-{zmax}', '-w', 'none',
                                    '--processes=8', '--xyz'] + args + [src3857, out])
                enc = time.time() - t0
                if not ok: print(f"  {name:<36s} gdal2tiles failed"); continue
            else: enc = None
            tot = n = 0
            for dp, _, fs in os.walk(out):
                for f in fs:
                    if f.endswith('.' + ext): tot += os.path.getsize(os.path.join(dp, f)); n += 1
            me, rm = (0, 0.0) if ext == 'png' else tree_err(self.p('g2t_png'), out, ext, zmax)
            if not self.done(name):
                self.add('owlgt', f'{name} ({n} tiles)', tot, me, rm, enc, None,
                         'one file per tile on disk', ntiles=n)

        # MBTiles: same three codecs in one SQLite file
        for name, fmt, extra in [('MBTiles PNG', 'PNG', []), ('MBTiles JPEG q75', 'JPEG', ['-co', 'QUALITY=75']),
                                 ('MBTiles WEBP q75', 'WEBP', ['-co', 'QUALITY=75'])]:
            mb = self.p(f'tiles_{fmt.lower()}.mbtiles')
            if self.done(name) and os.path.exists(mb): continue
            t0 = time.time()
            if not os.path.exists(mb):
                if not sh(['gdal_translate', '-q', '-of', 'MBTILES', src3857, mb, '-co', f'TILE_FORMAT={fmt}',
                           '-co', f'MAXZOOM={zmax}', '-co', f'MINZOOM={zmin}'] + extra): continue
                sh(['gdaladdo', '-q', mb] + [str(2 ** i) for i in range(1, zmax - zmin + 1)])
            enc = time.time() - t0
            me, rm = (0, 0.0) if fmt == 'PNG' else mbtiles_err(self.p('tiles_png.mbtiles'), mb, zmax)
            if not self.done(name):
                self.add('owlgt', name, os.path.getsize(mb), me, rm, enc, None, 'single SQLite file of tiles')

        self.latency(zmin, zmax, LV)

    # ------------------------------------------------------------ latency
    def latency(self, zmin, zmax, LV):
        """How long one tile takes to reach a client, p50/p95 over NLAT random
        tiles of the finest zoom: in-process (decode + PNG encode for OWLGT; the
        file read or SQLite fetch for the others) and end-to-end over HTTP."""
        from owlg import tiles, server
        print("\nTile latency (finest zoom, %d random tiles)" % NLAT)
        rng = random.Random(7)
        keys = rng.sample(sorted(LV[zmax]), min(NLAT, len(LV[zmax])))
        lat = {}

        for f in sorted(os.listdir(self.work)):
            if not f.endswith('.owlgt'): continue
            hdr, raw, base = tiles.open_owlgt(self.p(f))
            cold, warm, dec = [], [], []
            for (x, y) in keys:
                cache = {}
                t0 = time.perf_counter(); rec, _ = tiles.read_tile(hdr, raw, base, zmax, x, y, cache)
                t1 = time.perf_counter(); dec.append(t1 - t0)
                server._enc_png(rec, None); cold.append(time.perf_counter() - t0)
                t0 = time.perf_counter(); tiles.read_tile(hdr, raw, base, zmax, x, y, cache)
                server._enc_png(rec, None); warm.append(time.perf_counter() - t0)
            lat[f[:-6].replace('_', ' ')] = dict(cold=ms(cold), warm=ms(warm), dec=ms(dec), open_s=None)

        png = self.p('g2t_png')
        if os.path.isdir(png):
            t = []
            for (x, y) in keys:
                fp = os.path.join(png, str(zmax), str(x), f'{y}.png')
                t0 = time.perf_counter(); open(fp, 'rb').read() if os.path.exists(fp) else None
                t.append(time.perf_counter() - t0)
            lat['gdal2tiles PNG tree'] = dict(cold=ms(t), warm=None)
        mb = self.p('tiles_png.mbtiles')
        if os.path.exists(mb):
            con = sqlite3.connect(mb); t = []
            for (x, y) in keys:
                t0 = time.perf_counter()
                con.execute('select tile_data from tiles where zoom_level=? and tile_column=? and tile_row=?',
                            (zmax, x, (1 << zmax) - 1 - y)).fetchone()
                t.append(time.perf_counter() - t0)
            lat['MBTiles PNG'] = dict(cold=ms(t), warm=None)

        # end to end over HTTP: owlg serve vs a static file server on the PNG tree
        ex = next((f for f in os.listdir(self.work) if f.startswith('OWLGT_exact_delta4')), None)
        if ex:
            lat['HTTP: owlg serve (exact delta=4)'] = self.http_lat(
                [os.path.join(os.path.dirname(sys.executable), 'owlg'), 'serve', self.p(ex), '--port', '8765'],
                lambda x, y: f'http://127.0.0.1:8765/xyz/{ex[:-6]}/{zmax}/{x}/{y}.png', keys)
        if os.path.isdir(png):
            lat['HTTP: python -m http.server (PNG tree)'] = self.http_lat(
                [sys.executable, '-m', 'http.server', '8766', '--bind', '127.0.0.1', '-d', png],
                lambda x, y: f'http://127.0.0.1:8766/{zmax}/{x}/{y}.png', keys)
        for k, v in lat.items():
            print(f"  {k:<40s} p50 {v['cold']['p50']:7.2f} ms  p95 {v['cold']['p95']:7.2f} ms"
                  + (f"   decode {v['dec']['p50']:6.2f} ms  warm {v['warm']['p50']:6.2f} ms" if v.get('warm') else ''))
        self.lat = lat
        self.save_lat()

    def http_lat(self, cmd, url, keys):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'), OWLG_COLOR='never')
        pr = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        t0 = time.time(); u0 = url(*keys[0])
        while time.time() - t0 < 120:
            try: urllib.request.urlopen(u0, timeout=5).read(); break
            except Exception: time.sleep(0.5)
        open_s = time.time() - t0
        t = []
        try:
            for (x, y) in keys:
                t1 = time.perf_counter()
                try: urllib.request.urlopen(url(x, y), timeout=30).read()
                except urllib.error.HTTPError: pass
                t.append(time.perf_counter() - t1)
        finally:
            pr.terminate(); pr.wait()
        return dict(cold=ms(t), warm=None, open_s=open_s)

    def save_lat(self):
        d = json.load(open(self.out + '.json')); d['latency'] = self.lat
        json.dump(d, open(self.out + '.json', 'w'), indent=1)


def _read_img(path):
    import imagecodecs
    return imagecodecs.imread(path)


def _tile_err(png, other):
    """|png - other| on RGB, only where the PNG is fully opaque: the RGB under
    a transparent pixel is undefined and every codec fills it differently."""
    a = png[:, :, :3].astype(np.int32); b = other[:, :, :3].astype(np.int32)
    e = np.abs(a - b)
    if png.ndim == 3 and png.shape[2] == 4:
        e = e[png[:, :, 3] == 255]
    return int(e.max()) if e.size else 0, float((e.astype(np.float64) ** 2).sum()), int(e.size)


def tree_err(png_dir, other_dir, ext, zmax):
    """max err / RMSE of a lossy gdal2tiles tree against its PNG twin at zmax."""
    mx = 0; se = 0.0; n = 0
    for dp, _, fs in os.walk(os.path.join(png_dir, str(zmax))):
        for f in fs:
            if not f.endswith('.png'): continue
            o = os.path.join(other_dir, os.path.relpath(dp, png_dir), f[:-3] + ext)
            if not os.path.exists(o): continue
            m, s2, k = _tile_err(_read_img(os.path.join(dp, f)), _read_img(o))
            mx = max(mx, m); se += s2; n += k
    return (mx, float(np.sqrt(se / n))) if n else (None, None)


def mbtiles_err(png_mb, other_mb, zmax):
    import imagecodecs
    a = sqlite3.connect(png_mb); b = sqlite3.connect(other_mb)
    q = 'select tile_column, tile_row, tile_data from tiles where zoom_level=?'
    other = {(c, r): d for c, r, d in b.execute(q, (zmax,))}
    mx = 0; se = 0.0; n = 0
    for c, r, d in a.execute(q, (zmax,)):
        if (c, r) not in other: continue
        m, s2, k = _tile_err(imagecodecs.imread(d), imagecodecs.imread(other[(c, r)]))
        mx = max(mx, m); se += s2; n += k
    return (mx, float(np.sqrt(se / n))) if n else (None, None)


# ------------------------------------------------------------------ output
def to_markdown(d):
    m, rows = d['meta'], d['rows']
    L = [f"### `{os.path.basename(m['source'])}` — {m['width']} x {m['height']} x {m['bands']} "
         f"{m['dtype']}, {m['raw_bytes']/1e6:.1f} MB raw ({m['width']*m['height']/1e6:.0f} MPixel)\n"]
    titles = {'reference': 'Reference formats (GDAL)',
              'owlg': 'OWLG tiled + pyramid — hard per-pixel bound, proven by decoding every pixel',
              'owlgt': 'OWLGT web tiles vs tile trees and MBTiles (same zoom range)'}
    for g, title in titles.items():
        sel = [r for r in rows if r['group'] == g]
        if not sel: continue
        L += [f"\n**{title}**\n", '| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc | dec |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
        for r in sel:
            me = '—' if r['maxerr'] is None else ('**0**' if r['maxerr'] == 0 else str(r['maxerr']))
            nm = r['name'].replace('|', '\\|') + (f"<br><sub>{r['note']}</sub>" if r['note'] else '')
            L.append(f"| {nm} | {r['bytes']/1e6:.3f} MB | {r['vs_raw']:.2f}x | "
                     f"{f'{r['vs_gtiff']:.2f}x' if r['vs_gtiff'] else '—'} | {me} | "
                     f"{'—' if r['rmse'] is None else f'{r['rmse']:.3f}'} | "
                     f"{f'{r['enc_s']:.0f} s' if r['enc_s'] else '—'} | {f'{r['dec_s']:.0f} s' if r['dec_s'] else '—'} |")
    if d.get('latency'):
        L += ["\n**Tile latency** — one finest-zoom tile, p50 / p95 over 200 random tiles. In-process rows "
              "are decode + PNG encode for OWLGT, a file read for the PNG tree, a SQLite fetch for MBTiles; "
              "HTTP rows are end to end on localhost.\n",
              '| Source | p50 | p95 | decode only p50 | warm p50 | server start |', '|---|---:|---:|---:|---:|---:|']
        for k, v in d['latency'].items():
            L.append(f"| {k} | {v['cold']['p50']:.2f} ms | {v['cold']['p95']:.2f} ms | "
                     f"{f'{v['dec']['p50']:.2f} ms' if v.get('dec') else '—'} | "
                     f"{f'{v['warm']['p50']:.2f} ms' if v.get('warm') else '—'} | "
                     f"{f'{v['open_s']:.1f} s' if v.get('open_s') else '—'} |")
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src'); ap.add_argument('--src3857', help='EPSG:3857 copy for the OWLGT section')
    ap.add_argument('-o', '--out', default=None); ap.add_argument('--work', default=None)
    ap.add_argument('--resume', action='store_true'); ap.add_argument('--only', choices=['reference', 'owlg', 'owlgt', 'latency'])
    a = ap.parse_args()
    out = a.out or os.path.join(ROOT, 'results', os.path.splitext(os.path.basename(a.src))[0])
    work = a.work or out + '.work'
    os.makedirs(work, exist_ok=True)
    import owlg
    print(f"OWLG {owlg.__version__}  source {a.src}")
    b = Bench(a.src, work, out, a.resume)
    print(f"        {b.w} x {b.h} x {b.b} {b.dtype}, {b.raw/1e6:.1f} MB raw")
    if a.only in (None, 'reference'): b.reference()
    if a.only in (None, 'owlg'): b.owlg()
    if a.only in (None, 'owlgt') and a.src3857: b.owlgt(a.src3857)
    if a.only == 'latency' and a.src3857:       # re-measure latency on existing files
        from owlg import tiles
        hdr = tiles.open_owlgt(next(b.p(f) for f in os.listdir(work) if f.endswith('.owlgt')))[0]
        zmin, zmax = hdr['minzoom'], hdr['maxzoom']
        b.latency(zmin, zmax, tiles.build_pyramid(a.src3857, zmax, zmax))
    open(out + '.md', 'w').write(to_markdown(json.load(open(out + '.json'))))
    print(f"\n-> {out}.json\n-> {out}.md")


if __name__ == '__main__':
    main()
