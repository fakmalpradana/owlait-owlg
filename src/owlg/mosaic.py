"""Reassemble .owlgt tiles back into a raster: a full mosaic, or a render of an
arbitrary bbox (which is what WMS GetMap and OGC API - Maps need)."""
import numpy as np, math
from .tiles import open_owlgt, read_tile, tile_bounds, TS, WORLD

def level_tiles(hdr, z):
    out = []
    for k in hdr['index']:
        zz, x, y = k.split('/')
        if int(zz) == z: out.append((int(x), int(y)))
    return sorted(out)

def level_extent(hdr, z):
    t = level_tiles(hdr, z)
    if not t: return None
    xs = [a for a, _ in t]; ys = [b for _, b in t]
    return min(xs), min(ys), max(xs), max(ys)

def mosaic(path_or_open, z, cache=None):
    """Assemble every tile at zoom z -> (HWC uint8 array, 3857 affine transform)."""
    hdr, raw, base = open_owlgt(path_or_open) if isinstance(path_or_open, str) else path_or_open
    ext = level_extent(hdr, z)
    if ext is None: raise ValueError(f'no tiles at zoom {z}')
    x0, y0, x1, y1 = ext
    W = (x1-x0+1)*TS; H = (y1-y0+1)*TS
    out = np.zeros((H, W, 3), np.uint8)
    alpha = np.zeros((H, W), np.uint8)
    cache = {} if cache is None else cache
    for (x, y) in level_tiles(hdr, z):
        t, _ = read_tile(hdr, raw, base, z, x, y, cache)
        if t is None: continue
        r0 = (y-y0)*TS; c0 = (x-x0)*TS
        out[r0:r0+TS, c0:c0+TS] = t[:, :, :3]
        alpha[r0:r0+TS, c0:c0+TS] = 255
    n = 2**z; s = 2*WORLD/n
    tf = (s/TS, 0.0, -WORLD + x0*s, 0.0, -s/TS, WORLD - y0*s)
    return out, alpha, tf

def to_tif(src, dst, z=None, t_srs=None, resampling='bilinear'):
    """Revert .owlgt -> GeoTIFF. Defaults to EPSG:3857 as-is (no extra resampling)."""
    hdr, raw, base = open_owlgt(src)
    z = hdr['maxzoom'] if z is None else z
    arr, alpha, tf = mosaic((hdr, raw, base), z)
    bands = np.concatenate([arr.transpose(2, 0, 1), alpha[None]], 0)
    if t_srs is None:
        from .geotiff import write as _gwrite
        _gwrite(dst, bands, crs_wkt='EPSG:3857', transform6=tf,
                tags=dict(OWLGT_ZOOM=z, OWLGT_PROFILE=hdr['profile'],
                          OWLGT_DELTA=hdr.get('delta')),
                colorinterp=['red', 'green', 'blue', 'alpha'])
        return dst, dict(crs='EPSG:3857', zoom=z, warped=False)
    # These imports must precede the first use of Affine below: binding them
    # afterwards makes `Affine` a local name that is still unbound here, which
    # made every --t-srs revert raise UnboundLocalError.
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    prof = dict(driver='GTiff', width=arr.shape[1], height=arr.shape[0], count=4,
                dtype='uint8', crs='EPSG:3857', transform=Affine(*tf),
                tiled=True, blockxsize=512, blockysize=512,
                compress='DEFLATE', predictor=2)
    rs = getattr(Resampling, resampling, Resampling.bilinear)
    dtf, dw, dh = calculate_default_transform('EPSG:3857', t_srs, arr.shape[1], arr.shape[0],
                                              *_bounds_from(tf, arr.shape[1], arr.shape[0]))
    dprof = dict(prof); dprof.update(crs=t_srs, transform=dtf, width=dw, height=dh)
    with rasterio.open(dst, 'w', **dprof) as ds:
        for b in range(4):
            reproject(bands[b], rasterio.band(ds, b+1),
                      src_transform=Affine(*tf), src_crs='EPSG:3857',
                      dst_transform=dtf, dst_crs=t_srs, resampling=rs)
        ds.update_tags(OWLGT_ZOOM=str(z), OWLGT_WARPED_FROM='EPSG:3857')
    return dst, dict(crs=str(t_srs), zoom=z, warped=True)

def _bounds_from(tf, w, h):
    return (tf[2], tf[5] + tf[4]*h, tf[2] + tf[0]*w, tf[5])

# ---------------- render an arbitrary bbox (WMS GetMap / OGC API - Maps) ----------------
def pick_zoom(hdr, bbox3857, width):
    res = (bbox3857[2]-bbox3857[0]) / max(width, 1)
    best, bz = None, hdr['minzoom']
    for z in range(hdr['minzoom'], hdr['maxzoom']+1):
        r = 2*WORLD/(TS*2**z)
        d = abs(math.log(max(r, 1e-12)) - math.log(max(res, 1e-12)))
        if best is None or d < best: best, bz = d, z
    return bz

def render_bbox(hdr, raw, base, bbox, width, height, crs='EPSG:3857', cache=None):
    """Return (rgb HWC uint8, alpha HW uint8) for the bbox in the requested CRS."""
    if crs.upper() in ('EPSG:4326', 'CRS:84', 'OGC:CRS84'):
        b3857 = _bbox4326_to_3857(bbox, crs.upper())
    else:
        b3857 = bbox
    z = pick_zoom(hdr, b3857, width)
    n = 2**z; s = 2*WORLD/n; ppm = TS/s          # pixels per metre at level z
    gx0 = (b3857[0]+WORLD)*ppm; gx1 = (b3857[2]+WORLD)*ppm
    gy0 = (WORLD-b3857[3])*ppm; gy1 = (WORLD-b3857[1])*ppm
    tx0, tx1 = int(math.floor(gx0/TS)), int(math.ceil(gx1/TS))
    ty0, ty1 = int(math.floor(gy0/TS)), int(math.ceil(gy1/TS))
    cw = (tx1-tx0)*TS; ch = (ty1-ty0)*TS
    if cw <= 0 or ch <= 0 or cw*ch > 64_000_000:
        return np.zeros((height, width, 3), np.uint8), np.zeros((height, width), np.uint8)
    canvas = np.zeros((ch, cw, 3), np.uint8); ca = np.zeros((ch, cw), np.uint8)
    cache = {} if cache is None else cache
    for ty in range(ty0, ty1):
        for tx in range(tx0, tx1):
            t, _ = read_tile(hdr, raw, base, z, tx, ty, cache)
            if t is None: continue
            r0 = (ty-ty0)*TS; c0 = (tx-tx0)*TS
            canvas[r0:r0+TS, c0:c0+TS] = t[:, :, :3]; ca[r0:r0+TS, c0:c0+TS] = 255
    # map output pixels -> canvas pixels (Mercator: the axes are separable, so this is exact)
    if crs.upper() in ('EPSG:4326', 'CRS:84', 'OGC:CRS84'):
        lon = np.linspace(bbox[0], bbox[2], width, endpoint=False) + (bbox[2]-bbox[0])/(2*width)
        lat = np.linspace(bbox[3], bbox[1], height, endpoint=False) - (bbox[3]-bbox[1])/(2*height)
        if crs.upper() in ('EPSG:4326',):
            pass  # the caller has already normalized the (minlat,minlon,maxlat,maxlon) bbox
        mx = lon*WORLD/180.0
        my = np.log(np.tan((90+lat)*math.pi/360.0))*WORLD/math.pi
    else:
        mx = np.linspace(bbox[0], bbox[2], width, endpoint=False) + (bbox[2]-bbox[0])/(2*width)
        my = np.linspace(bbox[3], bbox[1], height, endpoint=False) - (bbox[3]-bbox[1])/(2*height)
    sx = np.clip(((mx+WORLD)*ppm - tx0*TS).astype(np.int64), 0, cw-1)
    sy = np.clip(((WORLD-my)*ppm - ty0*TS).astype(np.int64), 0, ch-1)
    rgb = canvas[np.ix_(sy, sx)]
    al = ca[np.ix_(sy, sx)]
    return rgb, al

def _bbox4326_to_3857(b, crs):
    lon0, lat0, lon1, lat1 = b
    f = lambda lat: math.log(math.tan((90+max(min(lat,85.05112878),-85.05112878))*math.pi/360.0))*WORLD/math.pi
    return (lon0*WORLD/180.0, f(lat0), lon1*WORLD/180.0, f(lat1))
