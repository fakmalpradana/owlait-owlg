"""`owlg encode --target 20x`: choose the smallest delta that reaches a size
ratio against the raw pixels, then encode with that delta.

The ratio is a goal; the delta is the promise. Whatever delta the search lands
on is written to the header and proven by `owlg verify`, exactly as if the user
had typed it. The search never trades the bound for the ratio silently: when
even the largest delta on the ladder falls short, that is reported and the
largest delta is used.
"""
import re
import numpy as np
from .errors import OwlgError

# Container overhead (magic, header JSON, blob directory) that the payload
# estimates below do not see. Small, but it is the difference between 7.99x
# and 8.0x when the goal is exactly met.
HEADER_BYTES = 4096

# Candidate bounds, in DN. Coarse at the top because the size curve flattens:
# beyond ~16 DN the base layer, not the correction, is what is left.
DELTAS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 32]


def parse_target(text):
    """'20', '20x', '20:1' -> 20.0"""
    m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:x|:1)?\s*', str(text), re.I)
    if not m or float(m.group(1)) <= 1:
        raise OwlgError(f"--target wants a ratio above 1, like 20 or 20x (got {text!r})")
    return float(m.group(1))


def search(estimate, raw_bytes, target, deltas=DELTAS, log=None):
    """Smallest delta whose estimated size is <= raw/target.

    estimate(delta) -> (bytes, quality). Size is monotone non-increasing in
    delta, so this is a binary search over the ladder: ~4 probes instead of 14.
    Returns (delta, bytes, quality, reached).
    """
    budget = raw_bytes / target
    lo, hi = 0, len(deltas) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        d = deltas[mid]
        nbytes, q = estimate(d)
        if log:
            log(d, nbytes, q, raw_bytes / max(nbytes, 1))
        if nbytes <= budget:
            best = (d, nbytes, q)
            hi = mid - 1
        else:
            lo = mid + 1
    if best is None:                      # not even the coarsest bound gets there
        d = deltas[-1]
        nbytes, q = estimate(d)
        return d, nbytes, q, False
    return best + (True,)


def estimate_flat(C, coded, groups, base, ladder, tile):
    """Exact sizes: the flat encoder builds the whole thing per probe."""
    from .container import _build
    nb = len(coded)
    wbuf = np.zeros(nb * tile * tile * 3 + 65536, np.uint8)

    def est(delta):
        best = None
        for q in ladder:
            _, _, sz = _build(C, coded, groups, base, q, delta, tile, wbuf)
            if best is None or sz < best[0]:
                best = (sz, q)
        return best[0] + HEADER_BYTES, best[1]
    return est


def estimate_tiled(sampler, base, ladder, overviews):
    """Sampled sizes scaled to the raster; the pyramid adds ~15% on top."""
    pyr = 1.15 if overviews else 1.0

    def est(delta):
        q = sampler.best_q(base, ladder, delta)
        return sampler.estimate(base, q, delta) * pyr + HEADER_BYTES, q
    return est
