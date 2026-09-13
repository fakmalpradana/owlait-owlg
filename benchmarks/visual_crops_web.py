"""Crops for the owlait.com viewer: several locations, OWLG (flat, UTM window) and
OWLGT (EPSG:3857 tiles at the finest zoom) against the usual formats.

    python benchmarks/visual_crops_web.py data/FT2026_crop/FT2026_crop.tif \
        data/FT2026_crop/FT2026_crop_3857.tif data/bench_work -o ../site/assets/ft2026 \
        --labels terrace,rooftops,road            # or --loc 6000,8000 --loc ...

Per location and row it writes <loc>/<name>.png and <loc>/<name>_err.png (|err| 0..64 DN,
black..white, like the README) — no _zoom.png, the site zooms in CSS — plus manifest.json
with the whole-raster numbers from results/ft2026.json and the per-crop max err / RMSE, and results.json (a copy of results/ft2026.json).
"""
import argparse, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src'))
sys.path.insert(0, HERE)
from visual_crops import window, png, ERR_SCALE          # noqa: E402
from owlg import tiles                                  # noqa: E402

# (id, label, file in workdir, results/ft2026.json row name)
OWLG_ROWS = [
    ('jpeg75',       'GeoTIFF JPEG q75',     'ref_jpeg75.tif',      'GeoTIFF JPEG q75 (lossy)'),
    ('jp2q3',        'JPEG 2000 q3',         'ref_jp2q3.jp2',       'JP2 OpenJPEG q3 (lossy, ~33x)'),
    ('jp2q2',        'JPEG 2000 q2',         'ref_jp2q2.jp2',       'JP2 OpenJPEG q2 (lossy, ~50x)'),
    ('jxld4',        'JPEG XL distance 4',   'ref_jxld4.jxl',       'JPEG XL distance 4 (lossy)'),
    ('owlg_d8',      'OWLG δ8',              'owlg_webp_d8.owlg',   'OWLG delta=8, base WEBP'),
    ('owlg_d16',     'OWLG δ16',             'owlg_webp_d16.owlg',  'OWLG delta=16, base WEBP'),
    ('owlg_d32',     'OWLG δ32',             'owlg_webp_d32.owlg',  'OWLG delta=32, base WEBP'),
    ('owlg_avif_d32','OWLG δ32 AVIF',        'owlg_avif_d32.owlg',  'OWLG delta=32, base AVIF'),
]
OWLGT_ROWS = [
    ('g2t_jpg',      'gdal2tiles JPEG q75',  'g2t_jpg',                  'gdal2tiles JPEG q75 tree (3011 tiles)'),
    ('g2t_webp',     'gdal2tiles WEBP q75',  'g2t_webp',                 'gdal2tiles WEBP q75 tree (3011 tiles)'),
    ('owlgt_q72',    'OWLGT view q72',       'OWLGT_view_q72.owlgt',     'OWLGT view q72'),
    ('owlgt_q50',    'OWLGT view q50',       'OWLGT_view_q50.owlgt',     'OWLGT view q50'),
    ('owlgt_d8',     'OWLGT exact δ8',       'OWLGT_exact_delta8.owlgt', 'OWLGT exact delta=8'),
    ('owlgt_d16',    'OWLGT exact δ16',      'OWLGT_exact_delta16.owlgt','OWLGT exact delta=16'),
    ('owlgt_d32',    'OWLGT exact δ32',      'OWLGT_exact_delta32.owlgt','OWLGT exact delta=32'),
]
Z = 21          # finest zoom of the FT2026 pyramids


def pick_locations(src, n, size, min_dist):
    """Highest-variance size x size windows, at least min_dist apart. Reads an overview."""
    import rasterio
    with rasterio.open(src) as ds:
        f = 8
        a = ds.read([1, 2, 3], out_shape=(3, ds.height // f, ds.width // f)).astype(np.float32).mean(0)
    c = size // f
    ny, nx = a.shape[0] // c, a.shape[1] // c
    cells = a[:ny * c, :nx * c].reshape(ny, c, nx, c).var(axis=(1, 3))
    order = np.dstack(np.unravel_index(np.argsort(-cells, axis=None), cells.shape))[0]
    out = []
    for iy, ix in order:
        x, y = int(ix * size), int(iy * size)
        if all(max(abs(x - px), abs(y - py)) >= min_dist for px, py in out):
            out.append((x, y))
        if len(out) == n: break
    return out


def utm_to_tile(src, x, y, s):
    """Top-left tile (at Z) of the 3x3 block that holds the window, and the pixel offset
    of the window's top-left corner inside that block."""
    import rasterio
    from rasterio.warp import transform
    with rasterio.open(src) as ds:
        cx, cy = ds.transform * (x, y)
        mx, my = transform(ds.crs, 'EPSG:3857', [cx], [cy])
    res = 2 * tiles.WORLD / (2 ** Z) / tiles.TS
    gx = (mx[0] + tiles.WORLD) / res; gy = (tiles.WORLD - my[0]) / res     # global pixel at Z
    bx, by = int(gx) // tiles.TS, int(gy) // tiles.TS
    return bx, by, int(gx) - bx * tiles.TS, int(gy) - by * tiles.TS


def truth_tile(src3857, z, x, y):
    """One tile exactly as tiles.build_pyramid makes it (bilinear reproject), RGBA (256,256,4)."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_bounds
    from rasterio.windows import from_bounds as win
    tb = tiles.tile_bounds(z, x, y)
    with rasterio.open(src3857) as ds:
        w = win(*tb, transform=ds.transform).round_offsets().round_lengths()
        w = rasterio.windows.Window(w.col_off - 4, w.row_off - 4, w.width + 8, w.height + 8)
        src = ds.read(window=w, boundless=True, fill_value=0)
        dst = np.zeros((src.shape[0], tiles.TS, tiles.TS), np.uint8)
        reproject(src, dst, src_transform=ds.window_transform(w), src_crs=ds.crs,
                  dst_transform=from_bounds(*tb, tiles.TS, tiles.TS), dst_crs=ds.crs,
                  resampling=Resampling.bilinear)
    return dst.transpose(1, 2, 0)


def block(reader, bx, by, n=3):
    """n x n tiles from reader(x, y) -> (256,256,3|4) or None, as one array."""
    out = None
    for dy in range(n):
        for dx in range(n):
            t = reader(bx + dx, by + dy)
            if t is None: continue
            if out is None: out = np.zeros((256 * n, 256 * n, t.shape[2]), np.uint8)
            out[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256, :t.shape[2]] = t
    return out


def owlgt_reader(path):
    hdr, raw, base = tiles.open_owlgt(path); cache = {}
    return lambda x, y: tiles.read_tile(hdr, raw, base, Z, x, y, cache)[0]


def tree_reader(d, ext):
    import imagecodecs
    def r(x, y):
        p = os.path.join(d, str(Z), str(x), f'{y}.{ext}')
        return imagecodecs.imread(p) if os.path.exists(p) else None
    return r


def err_stats(img, orig, mask=None):
    e = np.abs(img[:, :, :3].astype(np.int32) - orig[:, :, :3].astype(np.int32))
    emap = e.max(axis=2)
    if mask is not None: e = e[mask]
    return emap, int(e.max()), float(np.sqrt((e.astype(np.float64) ** 2).mean()))


def write_row(out, name, img, emap):
    png(img[:, :, :3], os.path.join(out, f'{name}.png'))
    png(np.clip(emap * (255 // ERR_SCALE), 0, 255), os.path.join(out, f'{name}_err.png'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src'); ap.add_argument('src3857'); ap.add_argument('work')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--results', default=os.path.join(os.path.dirname(HERE), 'results', 'ft2026.json'))
    ap.add_argument('--loc', action='append', help='x,y of a window; repeat. Default: variance scan')
    ap.add_argument('--labels', default='', help='comma-separated, one per location')
    ap.add_argument('-n', type=int, default=3); ap.add_argument('--size', type=int, default=320)
    a = ap.parse_args()
    s = a.size
    locs = [tuple(map(int, l.split(','))) for l in a.loc] if a.loc else pick_locations(a.src, a.n, s, 3000)
    labels = [l.strip() for l in a.labels.split(',') if l.strip()]
    labels += [f'site {i + 1}' for i in range(len(labels), len(locs))]
    res = {r['name']: r for r in json.load(open(a.results))['rows']}

    def meta(rid, label, name, bound):
        r = res[name]
        return dict(id=rid, label=label, bytes=r['bytes'], vs_raw=round(r['vs_raw'], 1),
                    maxerr=r['maxerr'], rmse=round(r['rmse'], 2), bound=bound, crop={})

    man = dict(source='FT2026_crop.tif', raw_bytes=674544784, width=12986, height=12986, size=s, zoom=Z,
               locations=[], owlg=[meta(i, l, n, 'delta=' in n) for i, l, _, n in OWLG_ROWS],
               owlgt=[meta(i, l, n, 'exact' in n) for i, l, _, n in OWLGT_ROWS])

    for (x, y), label in zip(locs, labels):
        lid = label.lower().replace(' ', '-')
        out = os.path.join(a.out, lid); os.makedirs(out, exist_ok=True)
        print(f'== {label}: xoff={x} yoff={y}')
        man['locations'].append(dict(id=lid, label=label, xoff=x, yoff=y))

        # ---- OWLG: UTM window straight from each file
        orig = window(a.src, x, y, s)
        write_row(out, 'original', orig, np.zeros(orig.shape[:2], np.int32))
        for rid, label_, fn, name in OWLG_ROWS:
            img = window(os.path.join(a.work, fn), x, y, s)
            emap, mx, rm = err_stats(img, orig)
            m = next(m for m in man['owlg'] if m['id'] == rid)
            if m['bound']: assert mx <= res[name]['maxerr'], (rid, mx)
            m['crop'][lid] = dict(maxerr=mx, rmse=round(rm, 2))
            write_row(out, rid, img, emap)
            print(f'  {label_:<22s} crop max err {mx:3d}  rmse {rm:.2f}')

        # ---- OWLGT: 3x3 tile block at Z, same crop size
        bx, by, ox, oy = utm_to_tile(a.src, x, y, s)
        crop = lambda b: b[oy:oy + s, ox:ox + s]
        truth = block(lambda tx, ty: truth_tile(a.src3857, Z, tx, ty), bx, by)
        torig = crop(truth); mask = torig[:, :, 3] == 255
        write_row(out, 'original_tile', torig, np.zeros((s, s), np.int32))
        print(f'  tiles {Z}/{bx}-{bx+2}/{by}-{by+2} offset {ox},{oy}  opaque {mask.mean():.0%}')
        for rid, label_, fn, name in OWLGT_ROWS:
            p = os.path.join(a.work, fn)
            rd = tree_reader(p, fn.split('_')[1]) if fn.startswith('g2t_') else owlgt_reader(p)
            img = crop(block(rd, bx, by))
            emap, mx, rm = err_stats(img, torig, mask)
            emap[~mask] = 0
            m = next(m for m in man['owlgt'] if m['id'] == rid)
            if m['bound']: assert mx <= res[name]['maxerr'], (rid, mx)
            m['crop'][lid] = dict(maxerr=mx, rmse=round(rm, 2))
            write_row(out, rid, img, emap)
            print(f'  {label_:<22s} crop max err {mx:3d}  rmse {rm:.2f}')

    json.dump(man, open(os.path.join(a.out, 'manifest.json'), 'w'), indent=1, ensure_ascii=False)
    import shutil; shutil.copy(a.results, os.path.join(a.out, 'results.json'))   # the chart/table data
    print('wrote', os.path.join(a.out, 'manifest.json'))


if __name__ == '__main__':
    main()
