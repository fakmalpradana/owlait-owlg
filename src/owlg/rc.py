"""Binary adaptive range coder (LZMA-style) + CABAC-style residual binarization.
   Encoder & decoder are symmetric; used for the GTZ correction layer."""
import numpy as np
from ._compat import njit

KTOP = np.uint32(1 << 24)
PBITS = 15
PINIT = np.uint16(1 << (PBITS - 1))
MOVE  = 5

# ----------------------------- ENCODER --------------------------------
@njit(cache=True)
def _enc_shift_low(low, cache, cache_size, out, pos):
    if low < np.uint64(0xFF000000) or low > np.uint64(0xFFFFFFFF):
        c = cache
        carry = np.uint8(low >> np.uint64(32))
        while True:
            out[pos] = np.uint8((c + carry) & np.uint8(0xFF)); pos += 1
            c = np.uint8(0xFF)
            cache_size -= 1
            if cache_size == 0: break
        cache = np.uint8((low >> np.uint64(24)) & np.uint64(0xFF))
    cache_size += 1
    low = (low << np.uint64(8)) & np.uint64(0xFFFFFFFF)
    return low, cache, cache_size, pos

@njit(cache=True)
def _enc_bit(rng, low, cache, cache_size, out, pos, probs, ci, bit):
    bound = (rng >> np.uint32(PBITS)) * np.uint32(probs[ci])
    if bit == 0:
        rng = bound
        probs[ci] = np.uint16(probs[ci] + (((1 << PBITS) - probs[ci]) >> MOVE))
    else:
        low = low + np.uint64(bound)
        rng = rng - bound
        probs[ci] = np.uint16(probs[ci] - (probs[ci] >> MOVE))
    while rng < KTOP:
        rng = rng << np.uint32(8)
        low, cache, cache_size, pos = _enc_shift_low(low, cache, cache_size, out, pos)
    return rng, low, cache, cache_size, pos

@njit(cache=True)
def _enc_bypass(rng, low, cache, cache_size, out, pos, bit):
    rng = rng >> np.uint32(1)
    if bit != 0: low = low + np.uint64(rng)
    while rng < KTOP:
        rng = rng << np.uint32(8)
        low, cache, cache_size, pos = _enc_shift_low(low, cache, cache_size, out, pos)
    return rng, low, cache, cache_size, pos

# ----------------------------- DECODER --------------------------------
@njit(cache=True)
def _dec_init(buf):
    code = np.uint32(0); pos = 1          # buf[0] is the cache priming byte
    for _ in range(4):
        code = (code << np.uint32(8)) | np.uint32(buf[pos]); pos += 1
    return np.uint32(0xFFFFFFFF), code, pos

@njit(cache=True)
def _dec_bit(rng, code, buf, pos, probs, ci):
    bound = (rng >> np.uint32(PBITS)) * np.uint32(probs[ci])
    if code < bound:
        rng = bound; bit = 0
        probs[ci] = np.uint16(probs[ci] + (((1 << PBITS) - probs[ci]) >> MOVE))
    else:
        code = code - bound; rng = rng - bound; bit = 1
        probs[ci] = np.uint16(probs[ci] - (probs[ci] >> MOVE))
    while rng < KTOP:
        rng = rng << np.uint32(8)
        code = (code << np.uint32(8)) | np.uint32(buf[pos] if pos < buf.size else 0); pos += 1
    return rng, code, pos, bit

@njit(cache=True)
def _dec_bypass(rng, code, buf, pos):
    rng = rng >> np.uint32(1)
    if code >= rng:
        code = code - rng; bit = 1
    else:
        bit = 0
    while rng < KTOP:
        rng = rng << np.uint32(8)
        code = (code << np.uint32(8)) | np.uint32(buf[pos] if pos < buf.size else 0); pos += 1
    return rng, code, pos, bit
