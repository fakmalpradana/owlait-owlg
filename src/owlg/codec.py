"""GTZ v1 -- GeoTIFF Zero-loss-budget container.
Architecture: a base layer (AVIF/JXL, natively decodable by browsers) + a
hard-bounded correction layer (|orig-rec| <= delta guaranteed per pixel), coded
with a binary adaptive range coder + CABAC-style binarization.
"""
import numpy as np, json, struct, io
from ._compat import njit
from .rc import (_enc_bit, _enc_bypass, _enc_shift_low, _dec_init, _dec_bit,
                _dec_bypass, PINIT)

MAGIC = b'GTZ1'
NA = 7                     # activity bins
C0_N   = 3 * NA * 3 * 3 * 3
CS_OFF = C0_N
CS_N   = 3 * NA
CG_OFF = CS_OFF + CS_N
CG_N   = 3 * NA * 3
CE_OFF = CG_OFF + CG_N
CE_N   = 3 * 16
NPROB  = CE_OFF + CE_N

@njit(cache=True, inline='always')
def _act(base, y, x, H, W):
    xm = x-1 if x > 0 else 0; xp = x+1 if x < W-1 else W-1
    ym = y-1 if y > 0 else 0; yp = y+1 if y < H-1 else H-1
    g = abs(np.int32(base[y, xp]) - np.int32(base[y, xm])) + \
        abs(np.int32(base[yp, x]) - np.int32(base[ym, x]))
    if   g <= 2:  return 0
    elif g <= 6:  return 1
    elif g <= 12: return 2
    elif g <= 24: return 3
    elif g <= 48: return 4
    elif g <= 96: return 5
    return 6

@njit(cache=True, inline='always')
def _n3(q):
    if q == 0: return 0
    if q == 1 or q == -1: return 1
    return 2

@njit(cache=True, inline='always')
def _nlq(e, delta, s):
    if delta == 0: return e
    if e >= 0: return (e + delta) // s
    return -((-e + delta) // s)

# ------------------------------- ENCODE -------------------------------
@njit(cache=True)
def enc_tile(orig, base, y0, y1, x0, x1, delta, out):
    """orig/base: (B,H,W) uint8. Codes the correction for tile [y0:y1, x0:x1]."""
    B, H, W = orig.shape
    s = np.int32(2 * delta + 1); dl = np.int32(delta)
    th = y1 - y0; tw = x1 - x0
    probs = np.full(NPROB, PINIT, np.uint16)
    rng = np.uint32(0xFFFFFFFF); low = np.uint64(0); cache = np.uint8(0)
    cs = np.int64(1); pos = 0
    Q = np.zeros((B, th, tw), np.int32)
    for b in range(B):
        for y in range(th):
            for x in range(tw):
                e = np.int32(orig[b, y0+y, x0+x]) - np.int32(base[b, y0+y, x0+x])
                Q[b, y, x] = _nlq(e, dl, s)
    for b in range(B):
        bb = b if b < 3 else 2
        for y in range(th):
            for x in range(tw):
                a = _act(base[b], y0+y, x0+x, H, W)
                nl = _n3(Q[b, y, x-1]) if x > 0 else 0
                nu = _n3(Q[b, y-1, x]) if y > 0 else 0
                npv = _n3(Q[b-1, y, x]) if b > 0 else 0
                c0 = ((((bb * NA + a) * 3 + nl) * 3 + nu) * 3 + npv)
                q = Q[b, y, x]
                nz = 0 if q == 0 else 1
                rng, low, cache, cs, pos = _enc_bit(rng, low, cache, cs, out, pos, probs, c0, nz)
                if nz == 1:
                    sg = 0 if q > 0 else 1
                    rng, low, cache, cs, pos = _enc_bit(rng, low, cache, cs, out, pos, probs,
                                                        CS_OFF + bb * NA + a, sg)
                    m = (q if q > 0 else -q) - 1
                    k = 0
                    while k < 3:
                        gt = 1 if m > k else 0
                        rng, low, cache, cs, pos = _enc_bit(rng, low, cache, cs, out, pos, probs,
                                                            CG_OFF + (bb * NA + a) * 3 + k, gt)
                        if gt == 0: break
                        k += 1
                    if m >= 3:
                        v = m - 3 + 1          # exp-Golomb order 0 on v>=1
                        nb = 0; t = v
                        while t > 1: t >>= 1; nb += 1
                        for i in range(nb):
                            rng, low, cache, cs, pos = _enc_bit(rng, low, cache, cs, out, pos, probs,
                                                                CE_OFF + bb * 16 + (i if i < 16 else 15), 1)
                        rng, low, cache, cs, pos = _enc_bit(rng, low, cache, cs, out, pos, probs,
                                                            CE_OFF + bb * 16 + (nb if nb < 16 else 15), 0)
                        for i in range(nb - 1, -1, -1):
                            rng, low, cache, cs, pos = _enc_bypass(rng, low, cache, cs, out, pos,
                                                                   (v >> i) & 1)
    for _ in range(5):
        low, cache, cs, pos = _enc_shift_low(low, cache, cs, out, pos)
    return pos

# ------------------------------- DECODE -------------------------------
@njit(cache=True)
def dec_tile(buf, base, y0, y1, x0, x1, delta, rec):
    B, H, W = base.shape
    s = np.int32(2 * delta + 1)
    th = y1 - y0; tw = x1 - x0
    probs = np.full(NPROB, PINIT, np.uint16)
    rng, code, pos = _dec_init(buf)
    Q = np.zeros((B, th, tw), np.int32)
    for b in range(B):
        bb = b if b < 3 else 2
        for y in range(th):
            for x in range(tw):
                a = _act(base[b], y0+y, x0+x, H, W)
                nl = _n3(Q[b, y, x-1]) if x > 0 else 0
                nu = _n3(Q[b, y-1, x]) if y > 0 else 0
                npv = _n3(Q[b-1, y, x]) if b > 0 else 0
                c0 = ((((bb * NA + a) * 3 + nl) * 3 + nu) * 3 + npv)
                rng, code, pos, nz = _dec_bit(rng, code, buf, pos, probs, c0)
                q = np.int32(0)
                if nz == 1:
                    rng, code, pos, sg = _dec_bit(rng, code, buf, pos, probs, CS_OFF + bb * NA + a)
                    k = 0; m = np.int32(0); esc = False
                    while k < 3:
                        rng, code, pos, gt = _dec_bit(rng, code, buf, pos, probs,
                                                      CG_OFF + (bb * NA + a) * 3 + k)
                        if gt == 0: m = np.int32(k); break
                        k += 1
                    if k == 3: esc = True
                    if esc:
                        nb = 0
                        while True:
                            rng, code, pos, cb = _dec_bit(rng, code, buf, pos, probs,
                                                          CE_OFF + bb * 16 + (nb if nb < 16 else 15))
                            if cb == 0: break
                            nb += 1
                        v = np.int32(1)
                        for _ in range(nb):
                            rng, code, pos, bt = _dec_bypass(rng, code, buf, pos)
                            v = (v << 1) | np.int32(bt)
                        m = v - 1 + 3
                    q = (m + 1) if sg == 0 else -(m + 1)
                Q[b, y, x] = q
                r = np.int32(base[b, y0+y, x0+x]) + q * s
                if r < 0: r = np.int32(0)
                if r > 255: r = np.int32(255)
                rec[b, y0+y, x0+x] = np.uint8(r)
    return pos


# --------------------------------------------------------------------------
# Single dispatch point. Without numba the code above runs as ordinary Python
# and is very slow, precisely because its shape is optimized for numba.
# codec_py supplies a flat variant whose output is identical bit-for-bit.
_DISPATCH_INSTALLED = True
from ._compat import HAVE_NUMBA as _HN
dec_tile_numba = dec_tile
if not _HN:
    from .codec_py import dec_tile_py as _dtp
    def dec_tile(buf, base, y0, y1, x0, x1, delta, rec):      # noqa: F811
        return _dtp(buf, base, y0, y1, x0, x1, delta, rec)
