"""Correction-layer decoder optimized for pure Python (without numba).

The code in codec.py is written in the form that is ideal for numba: many small
functions and per-element arithmetic. That very form is the slowest one when
interpreted. This version merges it all into a single flat function with every
lookup cached in a local variable. Its output is identical bit-for-bit.
"""
import numpy as np

PBITS = 15; MOVE = 5; KTOP = 1 << 24
NA = 7
C0_N = 3 * NA * 3 * 3 * 3
CS_OFF = C0_N
CG_OFF = CS_OFF + 3 * NA
CE_OFF = CG_OFF + 3 * NA * 3
NPROB = CE_OFF + 3 * 16
PINIT = 1 << (PBITS - 1)

# activity bin table: g (0..1020) -> 0..6, computed once
_ACT = [0 if g <= 2 else 1 if g <= 6 else 2 if g <= 12 else 3 if g <= 24
        else 4 if g <= 48 else 5 if g <= 96 else 6 for g in range(2041)]


def dec_tile_py(buf, base, y0, y1, x0, x1, delta, rec):
    """base/rec: (B,H,W) uint8 (rec is modified in place). buf: bytes/Uint8Array."""
    B, H, W = base.shape
    s = 2 * delta + 1
    th = y1 - y0; tw = x1 - x0
    probs = [PINIT] * NPROB
    b_ = buf if isinstance(buf, (bytes, bytearray)) else bytes(buf)
    nbuf = len(b_)
    # --- range decoder state, held in locals ---
    p = 1
    r = 0xFFFFFFFF
    code = 0
    for _ in range(4):
        code = ((code << 8) | b_[p]) & 0xFFFFFFFF; p += 1
    act = _ACT
    Q = [None] * B
    for b in range(B):
        bb = b if b < 3 else 2
        bp = base[b].tolist(); rp = rec[b]
        qcur = [0] * (th * tw)
        qprev = Q[b - 1] if b > 0 else None
        bb_na = bb * NA
        for y in range(th):
            gy = y0 + y
            ym = gy - 1 if gy > 0 else 0
            yp = gy + 1 if gy < H - 1 else H - 1
            rowc = bp[gy]; rowu = bp[ym]; rowd = bp[yp]
            orow = rp[gy]
            base_row = y * tw
            prow = base_row - tw
            for x in range(tw):
                gx = x0 + x
                xm = gx - 1 if gx > 0 else 0
                xp_ = gx + 1 if gx < W - 1 else W - 1
                g = abs(rowc[xp_] - rowc[xm]) + abs(rowd[gx] - rowu[gx])
                a = act[g]
                if x > 0:
                    qq = qcur[base_row + x - 1]
                    nl = 0 if qq == 0 else (1 if (qq == 1 or qq == -1) else 2)
                else: nl = 0
                if y > 0:
                    qq = qcur[prow + x]
                    nu = 0 if qq == 0 else (1 if (qq == 1 or qq == -1) else 2)
                else: nu = 0
                if qprev is not None:
                    qq = qprev[base_row + x]
                    pv = 0 if qq == 0 else (1 if (qq == 1 or qq == -1) else 2)
                else: pv = 0
                ci = ((((bb_na + a) * 3 + nl) * 3 + nu) * 3 + pv)
                # --- zero / non-zero bit ---
                pr = probs[ci]; bound = (r >> PBITS) * pr
                if code < bound:
                    r = bound; probs[ci] = pr + (((1 << PBITS) - pr) >> MOVE); nz = 0
                else:
                    code -= bound; r -= bound; probs[ci] = pr - (pr >> MOVE); nz = 1
                while r < KTOP:
                    r = (r << 8) & 0xFFFFFFFF
                    code = ((code << 8) | (b_[p] if p < nbuf else 0)) & 0xFFFFFFFF; p += 1
                q = 0
                if nz:
                    ci = CS_OFF + bb_na + a
                    pr = probs[ci]; bound = (r >> PBITS) * pr
                    if code < bound:
                        r = bound; probs[ci] = pr + (((1 << PBITS) - pr) >> MOVE); sg = 0
                    else:
                        code -= bound; r -= bound; probs[ci] = pr - (pr >> MOVE); sg = 1
                    while r < KTOP:
                        r = (r << 8) & 0xFFFFFFFF
                        code = ((code << 8) | (b_[p] if p < nbuf else 0)) & 0xFFFFFFFF; p += 1
                    k = 0; m = 0; cgb = CG_OFF + (bb_na + a) * 3
                    while k < 3:
                        ci = cgb + k
                        pr = probs[ci]; bound = (r >> PBITS) * pr
                        if code < bound:
                            r = bound; probs[ci] = pr + (((1 << PBITS) - pr) >> MOVE); gt = 0
                        else:
                            code -= bound; r -= bound; probs[ci] = pr - (pr >> MOVE); gt = 1
                        while r < KTOP:
                            r = (r << 8) & 0xFFFFFFFF
                            code = ((code << 8) | (b_[p] if p < nbuf else 0)) & 0xFFFFFFFF; p += 1
                        if gt == 0: m = k; break
                        k += 1
                    if k == 3:
                        nb = 0; ceb = CE_OFF + bb * 16
                        while True:
                            ci = ceb + (nb if nb < 16 else 15)
                            pr = probs[ci]; bound = (r >> PBITS) * pr
                            if code < bound:
                                r = bound; probs[ci] = pr + (((1 << PBITS) - pr) >> MOVE); cb = 0
                            else:
                                code -= bound; r -= bound; probs[ci] = pr - (pr >> MOVE); cb = 1
                            while r < KTOP:
                                r = (r << 8) & 0xFFFFFFFF
                                code = ((code << 8) | (b_[p] if p < nbuf else 0)) & 0xFFFFFFFF; p += 1
                            if cb == 0: break
                            nb += 1
                        v = 1
                        for _ in range(nb):
                            r >>= 1
                            if code >= r: code -= r; bt = 1
                            else: bt = 0
                            while r < KTOP:
                                r = (r << 8) & 0xFFFFFFFF
                                code = ((code << 8) | (b_[p] if p < nbuf else 0)) & 0xFFFFFFFF; p += 1
                            v = (v << 1) | bt
                        m = v - 1 + 3
                    q = (m + 1) if sg == 0 else -(m + 1)
                qcur[base_row + x] = q
                val = rowc[gx] + q * s
                orow[gx] = 0 if val < 0 else (255 if val > 255 else val)
        Q[b] = qcur
    return p
