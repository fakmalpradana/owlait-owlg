"""Side-by-side crops for the README: the same window of the original and of each
encoded file, a 4x zoom of its centre, and an |error| map on one fixed scale so
the maps are comparable across formats.

    python benchmarks/visual_crops.py data/FT2026_crop/FT2026_crop.tif data/bench_work \
        --xoff 6000 --yoff 8000 --size 320 -o docs/img/ft2026

Writes <name>.png, <name>_zoom.png, <name>_err.png per format plus a markdown
table on stdout. Any format GDAL can open goes through gdal_translate -srcwin;
.owlg files use the windowed reader, so nothing here decodes a whole raster.
"""
import argparse, os, subprocess, sys, tempfile
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

# what to show, in table order: (label, file in workdir)
ROWS = [
    ('Original', None),
    ('GeoTIFF JPEG q75', 'ref_jpeg75.tif'),
    ('JPEG 2000 q3', 'ref_jp2q3.jp2'),
    ('JPEG 2000 q2', 'ref_jp2q2.jp2'),
    ('JPEG XL distance 4', 'ref_jxld4.jxl'),
    ('OWLG delta 8', 'owlg_webp_d8.owlg'),
    ('OWLG delta 16', 'owlg_webp_d16.owlg'),
    ('OWLG delta 32', 'owlg_webp_d32.owlg'),
    ('OWLG delta 32, AVIF', 'owlg_avif_d32.owlg'),
]
ERR_SCALE = 64          # |error| 0..64 DN -> black..white, same for every row


def window(path, x, y, s):
    """RGB uint8 (s, s, 3) of a window, for any format."""
    if path.endswith('.owlg'):
        from owlg.tiled_read import is_v4
        if is_v4(path):
            from owlg.tiled_read import TiledReader
            r = TiledReader(path)
            return np.dstack([r.read_window(b, x, y, s, s) for b in range(3)])
        from owlg.container import read_window
        return np.dstack([read_window(path, b, x, y, s, s) for b in range(3)])
    import rasterio
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, 'w.tif')
        subprocess.run(['gdal_translate', '-q', '-of', 'GTiff', '-srcwin', str(x), str(y), str(s), str(s),
                        '-b', '1', '-b', '2', '-b', '3', path, tmp], check=True, capture_output=True)
        with rasterio.open(tmp) as ds:
            return ds.read().transpose(1, 2, 0)


def png(arr, path):
    from owlg import imgio
    open(path, 'wb').write(imgio.png_encode(np.ascontiguousarray(arr.astype(np.uint8)), level=9))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src'); ap.add_argument('work')
    ap.add_argument('--xoff', type=int, required=True); ap.add_argument('--yoff', type=int, required=True)
    ap.add_argument('--size', type=int, default=320); ap.add_argument('--zoom', type=int, default=4)
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    s, z = a.size, a.zoom
    zs = s // z                                  # zoomed patch: centre zs x zs pixels, shown at z x
    c0 = (s - zs) // 2
    orig = window(a.src, a.xoff, a.yoff, s).astype(np.int32)
    rel = os.path.relpath(a.out, os.getcwd())
    print('| | Crop (native) | Centre, 4x | \\|error\\| (0–64 DN) | Size | max err in this crop |')
    print('|---|---|---|---|---:|---:|')
    for label, fn in ROWS:
        path = a.src if fn is None else os.path.join(a.work, fn)
        if not os.path.exists(path):
            print(f"skip {label}: {path} missing", file=sys.stderr); continue
        img = window(path, a.xoff, a.yoff, s).astype(np.int32)
        name = 'original' if fn is None else os.path.splitext(fn)[0].replace('ref_', '')
        png(img, os.path.join(a.out, f'{name}.png'))
        zoom = np.repeat(np.repeat(img[c0:c0 + zs, c0:c0 + zs], z, 0), z, 1)
        png(zoom, os.path.join(a.out, f'{name}_zoom.png'))
        err = np.abs(img - orig).max(axis=2)
        png(np.clip(err * (255 // ERR_SCALE), 0, 255), os.path.join(a.out, f'{name}_err.png'))
        size = f"{os.path.getsize(path)/1e6:.1f} MB" if fn else '674.5 MB raw'
        print(f"| **{label}** | ![]({rel}/{name}.png) | ![]({rel}/{name}_zoom.png) | ![]({rel}/{name}_err.png) "
              f"| {size} | {int(err.max()) if fn else 0} |")


if __name__ == '__main__':
    main()
