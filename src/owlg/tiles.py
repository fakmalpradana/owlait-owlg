"""OWLGT — a web tile pyramid (XYZ / EPSG:3857) built from an OWLG/GeoTIFF source.

The core idea: in a slippy map the client ALWAYS already holds the parent tile
before it asks for the children (zooming in goes coarse to fine). A child tile
may therefore be coded as a residual against the upsampled parent — the
inter-level redundancy that makes an ordinary pyramid balloon disappears
without adding a single request.

The mode is picked automatically per tile: residual-against-parent or
standalone, whichever is smaller. That way an OWLGT is never larger than
standalone tiles would be.
"""
import numpy as np, json, struct, os, hashlib, time, warnings
warnings.filterwarnings('ignore')
from . import imgio as _io
from .codec import enc_tile, dec_tile

MAGIC = b'OWLGT1'
WORLD = 20037508.342789244
TS = 256

def tile_bounds(z, x, y):
    n = 2 ** z; s = 2 * WORLD / n
    return (-WORLD + x*s, WORLD - (y+1)*s, -WORLD + (x+1)*s, WORLD - y*s)

def lonlat_tile(z, lon, lat):
    import math
    n = 2 ** z
    xt = int((lon + 180.0) / 360.0 * n)
    la = math.radians(lat)
    yt = int((1 - math.log(math.tan(la) + 1/math.cos(la)) / math.pi) / 2 * n)
    return xt, yt

def _grid(bounds, z):
    n = 2 ** z; s = 2 * WORLD / n
    x0 = int((bounds[0] + WORLD) / s); x1 = int((bounds[2] + WORLD - 1e-9) / s)
    y0 = int((WORLD - bounds[3]) / s); y1 = int((WORLD - bounds[1] - 1e-9) / s)
    return range(max(0,x0), min(n-1,x1)+1), range(max(0,y0), min(n-1,y1)+1)

def _avif(a, q):  return _io.avif_encode(np.ascontiguousarray(a), level=int(q), speed=6)
def _davif(b):    return np.ascontiguousarray(np.asarray(_io.avif_decode(b))[..., :3].astype(np.uint8))

def _up2(p):
    """Deterministic 2x bilinear upsample from (128,128,3) -> (256,256,3)."""
    h, w, c = p.shape
    out = np.repeat(np.repeat(p.astype(np.float32), 2, 0), 2, 1)
    # smooth with a shifted average (2x2 box on the fine grid) - stable & easy to reproduce
    o = out.copy()
    o[1:-1, :, :] = (out[:-2, :, :] + 2*out[1:-1, :, :] + out[2:, :, :]) / 4.0
    o2 = o.copy()
    o2[:, 1:-1, :] = (o[:, :-2, :] + 2*o[:, 1:-1, :] + o[:, 2:, :]) / 4.0
    return np.clip(np.rint(o2), 0, 255).astype(np.uint8)

def build_pyramid(src_tif, minz, maxz, resampling='bilinear'):
    """Return {z: {(x,y): array(256,256,4)}}; coarse levels are an exact 2x2 average."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_bounds
    rs = Resampling.bilinear if resampling == 'bilinear' else Resampling.nearest
    lv = {}
    with rasterio.open(src_tif) as ds:
        assert ds.crs.to_epsg() == 3857, 'source must be EPSG:3857'
        b = ds.bounds; src = ds.read(); stf = ds.transform; scrs = ds.crs
        nb = src.shape[0]
        xs, ys = _grid((b.left, b.bottom, b.right, b.top), maxz)
        t = {}
        for x in xs:
            for y in ys:
                tb = tile_bounds(maxz, x, y)
                dst = np.zeros((nb, TS, TS), np.uint8)
                reproject(src, dst, src_transform=stf, src_crs=scrs,
                          dst_transform=from_bounds(*tb, TS, TS), dst_crs=scrs,
                          resampling=rs)
                if nb >= 4 and dst[3].max() == 0: continue
                t[(x, y)] = np.ascontiguousarray(dst.transpose(1, 2, 0))
        lv[maxz] = t
    for z in range(maxz-1, minz-1, -1):
        t = {}
        for (x, y), _ in lv[z+1].items():
            px, py = x//2, y//2
            if (px, py) in t: continue
            can = np.zeros((TS*2, TS*2, lv[z+1][(x,y)].shape[2]), np.uint8)
            any_ = False
            for dx in (0,1):
                for dy in (0,1):
                    c = lv[z+1].get((px*2+dx, py*2+dy))
                    if c is None: continue
                    can[dy*TS:(dy+1)*TS, dx*TS:(dx+1)*TS] = c; any_ = True
            if not any_: continue
            a = can.reshape(TS, 2, TS, 2, can.shape[2]).mean(axis=(1, 3))
            t[(px, py)] = np.ascontiguousarray(np.rint(a).astype(np.uint8))
        lv[z] = t
    return lv

def write_owlgt(src_tif, dst, minz, maxz, delta=3, q=72, profile='exact', verbose=True):
    t0 = time.time()
    lv = build_pyramid(src_tif, minz, maxz)
    ntot = sum(len(v) for v in lv.values())
    if verbose: print(f"  pyramid z{minz}-{maxz}: {ntot} tiles " +
                      " ".join(f"z{z}:{len(lv[z])}" for z in sorted(lv)))
    blobs, index, seen = [], {}, {}
    stats = {z: dict(res=0, ind=0, bres=0, bind=0) for z in lv}
    dec_cache = {}
    wbuf = np.zeros(3*TS*TS*4 + 65536, np.uint8)
    for z in sorted(lv):
        for (x, y), arr in sorted(lv[z].items()):
            rgb = np.ascontiguousarray(arr[:, :, :3])
            alpha = arr[:, :, 3] if arr.shape[2] >= 4 else None
            # --- candidate A: standalone ---
            base = _avif(rgb, q)
            payloadA = b'\x00' + struct.pack('<I', len(base)) + base
            recA = _davif(base)
            if profile == 'exact':
                C = np.ascontiguousarray(recA.transpose(2, 0, 1))
                O = np.ascontiguousarray(rgb.transpose(2, 0, 1))
                n = enc_tile(O, C, 0, TS, 0, TS, delta, wbuf)
                corr = wbuf[:n].tobytes()
                payloadA = b'\x02' + struct.pack('<I', len(base)) + base + corr
                r = C.copy(); dec_tile(np.frombuffer(corr, np.uint8), C, 0, TS, 0, TS, delta, r)
                recA = np.ascontiguousarray(r.transpose(1, 2, 0))
            best, rec = payloadA, recA
            stats[z]['ind'] += 1; stats[z]['bind'] += len(payloadA)
            # --- candidate B: residual against the parent (exact profile only) ---
            if profile == 'exact' and z > minz and (z-1, x//2, y//2) in dec_cache:
                par = dec_cache[(z-1, x//2, y//2)]
                qx, qy = x % 2, y % 2
                pred = _up2(np.ascontiguousarray(par[qy*128:(qy+1)*128, qx*128:(qx+1)*128, :]))
                P = np.ascontiguousarray(pred.transpose(2, 0, 1))
                O = np.ascontiguousarray(rgb.transpose(2, 0, 1))
                n = enc_tile(O, P, 0, TS, 0, TS, delta, wbuf)
                payloadB = b'\x01' + wbuf[:n].tobytes()
                if len(payloadB) < len(best):
                    r = P.copy(); dec_tile(np.frombuffer(payloadB[1:], np.uint8), P, 0, TS, 0, TS, delta, r)
                    best = payloadB; rec = np.ascontiguousarray(r.transpose(1, 2, 0))
                    stats[z]['ind'] -= 1; stats[z]['bind'] -= len(payloadA)
                    stats[z]['res'] += 1; stats[z]['bres'] += len(payloadB)
            if alpha is not None and alpha.min() < 255:
                am = _io.png_encode(np.ascontiguousarray(alpha), level=9)
                best = best + b'\xFF' + struct.pack('<I', len(am)) + am
            dec_cache[(z, x, y)] = rec
            h = hashlib.sha256(best).digest()
            if h in seen: index[f"{z}/{x}/{y}"] = seen[h]
            else:
                off = sum(len(b) for b in blobs); blobs.append(best)
                seen[h] = [off, len(best)]; index[f"{z}/{x}/{y}"] = [off, len(best)]
    import rasterio
    with rasterio.open(src_tif) as ds: bnds = list(ds.bounds)
    hdr = dict(v=1, profile=profile, minzoom=minz, maxzoom=maxz, tilesize=TS,
               delta=int(delta), q=int(q), crs='EPSG:3857', bounds=bnds,
               ntiles=ntot, index=index)
    hj = json.dumps(hdr, separators=(',', ':')).encode()
    with open(dst, 'wb') as f:
        f.write(MAGIC); f.write(struct.pack('<I', len(hj))); f.write(hj)
        for b in blobs: f.write(b)
    if verbose:
        tot = os.path.getsize(dst); payload = sum(len(b) for b in blobs)
        print(f"  -> {dst}: {tot/1e6:.3f} MB (tiles {payload/1e6:.3f} + index {len(hj)/1e6:.3f})"
              f"  dedup {ntot-len(blobs)} tiles  ({time.time()-t0:.0f}s)")
        for z in sorted(stats):
            s = stats[z]
            print(f"     z{z}: residual {s['res']:4d} tiles/{s['bres']/1e3:8.1f} kB | "
                  f"standalone {s['ind']:4d}/{s['bind']/1e3:8.1f} kB")
    return dst, stats

def open_owlgt(path):
    raw = open(path, 'rb').read()
    assert raw[:6] == MAGIC, 'not an OWLGT file'
    hl = struct.unpack('<I', raw[6:10])[0]
    hdr = json.loads(raw[10:10+hl]); base = 10 + hl
    return hdr, raw, base

def read_tile(hdr, raw, base, z, x, y, cache=None):
    """Decode one tile; in residual mode the parent is decoded first (recursively)."""
    cache = {} if cache is None else cache
    k = (z, x, y)
    if k in cache: return cache[k], 0
    ent = hdr['index'].get(f"{z}/{x}/{y}")
    if ent is None: return None, 0
    off, L = ent; buf = raw[base+off: base+off+L]
    mode = buf[0]; nbytes = L
    if mode in (0, 2):
        bl = struct.unpack('<I', buf[1:5])[0]
        rec = _davif(buf[5:5+bl])
        if mode == 2:
            C = np.ascontiguousarray(rec.transpose(2, 0, 1)); r = C.copy()
            dec_tile(np.frombuffer(buf[5+bl:], np.uint8), C, 0, TS, 0, TS, hdr['delta'], r)
            rec = np.ascontiguousarray(r.transpose(1, 2, 0))
    else:
        par, pb = read_tile(hdr, raw, base, z-1, x//2, y//2, cache)
        nbytes += pb
        qx, qy = x % 2, y % 2
        pred = _up2(np.ascontiguousarray(par[qy*128:(qy+1)*128, qx*128:(qx+1)*128, :]))
        P = np.ascontiguousarray(pred.transpose(2, 0, 1)); r = P.copy()
        dec_tile(np.frombuffer(buf[1:], np.uint8), P, 0, TS, 0, TS, hdr['delta'], r)
        rec = np.ascontiguousarray(r.transpose(1, 2, 0))
    cache[k] = rec
    return rec, nbytes
