#!/usr/bin/env python3
"""Codec laboratory: measure one encoding idea at a time, in memory.

    python benchmarks/bench_codec_lab.py raster.tif [--exp base,tile,...] [--deltas 2,3,5,8]

Unlike bench_options.py, which drives the CLI and measures whole files, this
script calls the codec pieces directly (base encoder + correction coder) so an
idea can be isolated: same pixels, same deltas, one variable changed. Sizes are
base bytes + correction bytes; the container overhead (a few hundred bytes of
JSON) is the same for every row and is ignored.

Every row reports max|err| measured over every sample after a real decode, so
the bound is checked, never assumed. The headline metric per experiment is the
smallest delta at which the file is >= TARGET_X times smaller than raw.

Writes results/lab_<name>.json and .md.
"""
import argparse, json, os, sys, time, warnings
warnings.filterwarnings('ignore')
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

from bench_options import read_all, err_stats                       # noqa: E402
from owlg import imgio as _io                                       # noqa: E402
from owlg.codec import enc_tile, dec_tile                           # noqa: E402
from owlg.container import _pad_rgb                                 # noqa: E402

TARGET_X = 20.0


# ------------------------------------------------------------------ base layer
def enc_base(hwc, codec, q, **kw):
    if kw.get('enc_override'):
        return kw['enc_override'](hwc, q)
    if codec == 'webp':
        return _io.webp_encode(hwc, level=int(q))
    if codec == 'avif':
        return _io.avif_encode(hwc, level=int(q), speed=kw.get('speed', 6))
    if codec == 'avif444':
        import imagecodecs
        return imagecodecs.avif_encode(hwc, level=int(q), speed=kw.get('speed', 6),
                                       pixelformat='YUV444')
    if codec == 'jxl':
        return _io.jxl_encode(hwc, lossless=False, distance=float(q),
                              effort=kw.get('effort', 7))
    raise ValueError(codec)


def dec_base(blob, codec):
    c = 'avif' if codec.startswith('avif') else codec
    return np.ascontiguousarray(np.asarray(_io.decode_by_codec(c, blob))[..., :3].astype(np.uint8))


def base_layer(C, codec, q, **kw):
    """C: (nb,H,W) coded bands -> (blobs, BASE (nb,H,W) decoded)."""
    nb = C.shape[0]
    groups = [list(range(i, min(i + 3, nb))) for i in range(0, nb, 3)]
    blobs, decs = [], []
    for g in groups:
        a = np.ascontiguousarray(C[g].transpose(1, 2, 0))
        if a.shape[2] < 3:
            a = _pad_rgb(a)
        bl = enc_base(a, codec, q, **kw)
        blobs.append(bl)
        decs.append(dec_base(bl, codec)[:, :, :len(g)])
    BASE = np.ascontiguousarray(np.concatenate([d.transpose(2, 0, 1) for d in decs], 0)[:nb])
    return blobs, BASE


# ------------------------------------------------------------- correction layer
def correction(C, BASE, delta, tile, enc=enc_tile, dec=dec_tile):
    """Encode + decode the correction layer. Returns (bytes, reconstruction)."""
    nb, H, W = C.shape
    buf = np.zeros(nb * tile * tile * 3 + 65536, np.uint8)
    rec = BASE.copy()
    total = 0
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            y1, x1 = min(y0 + tile, H), min(x0 + tile, W)
            n = enc(C, BASE, y0, y1, x0, x1, delta, buf)
            total += n
            dec(np.frombuffer(buf[:n].tobytes(), np.uint8), BASE, y0, y1, x0, x1, delta, rec)
    return total, rec


# ------------------------------------------------------------------- the lab
class Lab:
    def __init__(self, src, out):
        self.src = src
        self.out = out
        A, _ = read_all(src)
        self.A = A
        B = A.shape[0]
        # constant bands are free in the real format: drop them here too
        self.coded = [b for b in range(B) if np.unique(A[b]).size > 1]
        self.C = np.ascontiguousarray(A[self.coded])
        self.raw = int(A.nbytes)
        self.rows = []
        w, h = A.shape[2], A.shape[1]
        print(f"{os.path.basename(src)}: {w}x{h}x{B} uint8, raw {self.raw/1e6:.2f} MB, "
              f"coded bands {self.coded}")

    def add(self, exp, name, delta, nbytes, rec, t, note=''):
        me, rm = err_stats(self.C, rec)
        ok = me <= delta
        row = dict(exp=exp, name=name, delta=delta, bytes=int(nbytes),
                   vs_raw=self.raw / nbytes, maxerr=me, rmse=rm, bound_ok=ok, s=t, note=note)
        self.rows.append(row)
        flag = '' if ok else '  *** BOUND VIOLATED ***'
        print(f"  {name:<40s} d={delta:<2d} {nbytes/1e6:7.3f} MB {self.raw/nbytes:6.2f}x "
              f"maxerr={me:<3d} rmse={rm:5.2f} {t:5.1f}s{flag}", flush=True)
        return row

    def hybrid(self, exp, name, codec, q, delta, tile=1024, enc=enc_tile, dec=dec_tile,
               note='', **kw):
        t0 = time.time()
        blobs, BASE = base_layer(self.C, codec, q, **kw)
        nb = sum(map(len, blobs))
        nc, rec = correction(self.C, BASE, delta, tile, enc, dec)
        return self.add(exp, name, delta, nb + nc, rec, time.time() - t0,
                        note or f'base {nb/1e6:.3f} + corr {nc/1e6:.3f} MB')

    def best_over_ladder(self, exp, label, codec, ladder, delta, **kw):
        """The encoder's own rule: try every quality, keep the smallest total."""
        best = None
        for q in ladder:
            r = self.hybrid(exp, f'{label} q={q}', codec, q, delta, **kw)
            if best is None or r['bytes'] < best['bytes']:
                best = r
        best['note'] = (best['note'] + ' <- best of ladder').strip()
        return best

    # ---------------------------------------------------------------- report
    def headline(self):
        """Per experiment: smallest delta whose best row reaches TARGET_X."""
        out = {}
        for r in self.rows:
            if not r['bound_ok']:
                continue
            k = r['exp']
            if r['vs_raw'] >= TARGET_X and (k not in out or r['delta'] < out[k]['delta']
                                            or (r['delta'] == out[k]['delta'] and r['bytes'] < out[k]['bytes'])):
                out[k] = r
        return out

    def write(self):
        os.makedirs(os.path.dirname(self.out) or '.', exist_ok=True)
        hl = self.headline()
        meta = dict(source=self.src, raw_bytes=self.raw, coded_bands=self.coded,
                    target_x=TARGET_X, when=time.strftime('%Y-%m-%d'))
        json.dump(dict(meta=meta, headline=hl, rows=self.rows),
                  open(self.out + '.json', 'w'), indent=1)
        L = [f"### {os.path.basename(self.src)} — raw {self.raw/1e6:.2f} MB, target {TARGET_X:.0f}x\n"]
        L.append("**Smallest delta reaching the target, per experiment**\n")
        L.append("| Experiment | delta | best row | size | vs raw | max err |")
        L.append("|---|---:|---|---:|---:|---:|")
        exps = []
        for r in self.rows:
            if r['exp'] not in exps:
                exps.append(r['exp'])
        for e in exps:
            r = hl.get(e)
            if r:
                L.append(f"| {e} | {r['delta']} | {r['name']} | {r['bytes']/1e6:.3f} MB | "
                         f"{r['vs_raw']:.2f}x | {r['maxerr']} |")
            else:
                L.append(f"| {e} | — | not reached | | | |")
        L.append("\n**All rows**\n")
        L.append("| Experiment | Row | delta | size | vs raw | max err | RMSE | s |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for r in self.rows:
            v = '' if r['bound_ok'] else ' **VIOLATED**'
            L.append(f"| {r['exp']} | {r['name']} | {r['delta']} | {r['bytes']/1e6:.3f} MB | "
                     f"{r['vs_raw']:.2f}x | {r['maxerr']}{v} | {r['rmse']:.2f} | {r['s']:.1f} |")
        open(self.out + '.md', 'w').write('\n'.join(L) + '\n')
        print(f"\n-> {self.out}.json / .md")
        for e, r in hl.items():
            print(f"   {e:<12s} reaches {TARGET_X:.0f}x at delta={r['delta']}  ({r['name']}, {r['vs_raw']:.1f}x)")


# ------------------------------------------------------------- experiments
WEBP_Q = [95, 90, 85, 75, 60]           # what 4.0.0 tries
WEBP_Q_X = [75, 60, 50, 40, 30, 20]     # extended downwards
AVIF_Q = [95, 90, 85, 75, 60, 45]
AVIF_Q_X = [75, 60, 45, 35, 25, 15]
JXL_D = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0]


def exp_current(lab, deltas):
    """4.0.0 behaviour: WebP ladder, flat 1024 tiles, best total."""
    for d in deltas:
        lab.best_over_ladder('current', 'webp', 'webp', WEBP_Q, d)


def exp_base(lab, deltas):
    """E1: base codecs and extended ladders at each delta."""
    for d in deltas:
        lab.best_over_ladder('base:webp-x', 'webp', 'webp', WEBP_Q_X, d)
        lab.best_over_ladder('base:avif', 'avif', 'avif', AVIF_Q_X, d)
        lab.best_over_ladder('base:avif444', 'avif444', 'avif444', AVIF_Q_X, d)
        lab.best_over_ladder('base:jxl', 'jxl', 'jxl', JXL_D, d)


def exp_ratio(lab, deltas):
    """Size-vs-delta curve for the two shipped bases, so the smallest delta that
    reaches TARGET_X can be read off per raster."""
    for d in sorted(set(deltas) | {12, 16, 20}):
        lab.best_over_ladder('ratio:webp', 'webp', 'webp', WEBP_Q + [50, 40], d)
        lab.best_over_ladder('ratio:avif', 'avif', 'avif', AVIF_Q + [30], d)


def exp_effort(lab, deltas):
    """Encoder effort knobs that do not change what a reader needs."""
    import imagecodecs
    for d in deltas:
        for m in (4, 6):
            lab.best_over_ladder(f'effort:webp-m{m}', f'webp method={m}', 'webp', WEBP_Q, d,
                                 enc_override=lambda a, q, m=m: imagecodecs.webp_encode(
                                     a, level=int(q), method=m, lossless=False))
        for sp in (6, 3):
            lab.best_over_ladder(f'effort:avif-s{sp}', f'avif speed={sp}', 'avif', AVIF_Q, d, speed=sp)


def exp_tile(lab, deltas):
    """E5: tile size (context confinement cost) with the current WebP ladder."""
    H, W = lab.C.shape[1:]
    for d in deltas:
        for t in (256, 512, 1024, max(H, W)):
            lab.best_over_ladder(f'tile:{t}', f'webp tile={t}', 'webp', WEBP_Q, d, tile=t)


EXPS = dict(current=exp_current, base=exp_base, tile=exp_tile, ratio=exp_ratio, effort=exp_effort)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src')
    ap.add_argument('-o', '--out', default=None, help='output prefix (default results/lab_<name>)')
    ap.add_argument('--exp', default='current,ratio', help=f'comma list of {sorted(EXPS)}')
    ap.add_argument('--deltas', default='2,3,5,8')
    a = ap.parse_args(argv)
    name = os.path.splitext(os.path.basename(a.src))[0]
    out = a.out or os.path.join(ROOT, 'results', f'lab_{name}')
    deltas = [int(x) for x in a.deltas.split(',')]
    lab = Lab(a.src, out)
    for e in a.exp.split(','):
        print(f"\n== {e} ==")
        EXPS[e](lab, deltas)
    lab.write()


if __name__ == '__main__':
    main()
