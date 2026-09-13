"""Pure-Python AES-256-GCM (numpy-accelerated) - the fallback for when the
`cryptography` package is unavailable, for example inside QGIS's own Python.

Used ONLY to decrypt .owlg files. Verified bit-for-bit against the reference
`cryptography` implementation. Not a replacement for a real crypto library for
other purposes: it is not designed to resist timing (side-channel) attacks,
which are in any case not relevant when opening your own local files.
"""
import numpy as np

# --------------------------------------------------------------- AES tables
def _mk_tables():
    p = q = 1; sbox = [0]*256
    while True:                                   # build the S-box from generator 3
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1; q ^= q << 2; q ^= q << 4; q &= 0xFF
        if q & 0x80: q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) \
              ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1: break
    sbox[0] = 0x63
    return np.array(sbox, np.uint8)

SBOX = _mk_tables()
_x = np.arange(256, dtype=np.uint16)
MUL2 = np.uint8(((_x << 1) ^ np.where(_x & 0x80, 0x11B, 0)) & 0xFF)
MUL3 = np.uint8(MUL2 ^ np.uint8(_x))
RCON = np.array([0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1B,0x36,0x6C,0xD8,0xAB,0x4D], np.uint8)
SR = np.array([0,5,10,15, 4,9,14,3, 8,13,2,7, 12,1,6,11])     # ShiftRows (flat layout)


def _expand_key(key: bytes):
    nk = len(key) // 4                 # 8 for AES-256
    nr = nk + 6                        # 14 rounds
    w = np.zeros(((nr + 1) * 4, 4), np.uint8)
    w[:nk] = np.frombuffer(key, np.uint8).reshape(nk, 4)
    for i in range(nk, (nr + 1) * 4):
        t = w[i - 1].copy()
        if i % nk == 0:
            t = SBOX[np.roll(t, -1)]
            t[0] ^= RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = SBOX[t]
        w[i] = w[i - nk] ^ t
    return w.reshape(nr + 1, 16), nr


def _aes_ecb(rk, nr, blocks):
    """blocks: (N,16) uint8 -> (N,16) uint8. All blocks are processed at once."""
    s = blocks ^ rk[0]
    for r in range(1, nr):
        s = SBOX[s][:, SR]
        c = s.reshape(-1, 4, 4)
        a0, a1, a2, a3 = c[:, :, 0], c[:, :, 1], c[:, :, 2], c[:, :, 3]
        s = np.stack([MUL2[a0] ^ MUL3[a1] ^ a2 ^ a3,
                      a0 ^ MUL2[a1] ^ MUL3[a2] ^ a3,
                      a0 ^ a1 ^ MUL2[a2] ^ MUL3[a3],
                      MUL3[a0] ^ a1 ^ a2 ^ MUL2[a3]], axis=2).reshape(-1, 16)
        s = s ^ rk[r]
    s = SBOX[s][:, SR] ^ rk[nr]
    return s


def _ctr(rk, nr, nonce12, n_blocks, start=2):
    ctr = np.zeros((n_blocks, 16), np.uint8)
    ctr[:, :12] = np.frombuffer(nonce12, np.uint8)
    idx = (np.arange(n_blocks, dtype=np.uint64) + start)
    ctr[:, 12] = (idx >> np.uint64(24)) & np.uint64(0xFF)
    ctr[:, 13] = (idx >> np.uint64(16)) & np.uint64(0xFF)
    ctr[:, 14] = (idx >> np.uint64(8)) & np.uint64(0xFF)
    ctr[:, 15] = idx & np.uint64(0xFF)
    return _aes_ecb(rk, nr, ctr)


# ---------------------------------------------------------------- GHASH
def _b2i(b): return int.from_bytes(bytes(b), 'big')
def _i2b(v): return v.to_bytes(16, 'big')

def _gmul(x, y):
    """GF(2^128) multiplication following the GCM convention (reversed bits)."""
    z = 0; v = y
    for i in range(127, -1, -1):
        if (x >> i) & 1: z ^= v
        if v & 1: v = (v >> 1) ^ 0xE1000000000000000000000000000000
        else:     v >>= 1
    return z

class _GHash:
    """4-bit tables: 32 tables x 16 entries -> 32 lookups per block."""
    def __init__(self, h_bytes):
        H = _b2i(h_bytes)
        self.T = []
        for nib in range(32):
            tab = [0] * 16
            for v in range(16):
                x = v << (4 * (31 - nib))
                tab[v] = _gmul(x, H)
            self.T.append(tab)
    def __call__(self, data: bytes, aad: bytes = b''):
        y = 0
        for chunk, pad in ((aad, True), (data, True)):
            for i in range(0, len(chunk), 16):
                blk = chunk[i:i+16]
                if len(blk) < 16: blk = blk + b'\x00' * (16 - len(blk))
                y ^= _b2i(blk)
                acc = 0
                for nib in range(32):
                    acc ^= self.T[nib][(y >> (4 * (31 - nib))) & 0xF]
                y = acc
        y ^= (len(aad) * 8) << 64 | (len(data) * 8)
        acc = 0
        for nib in range(32):
            acc ^= self.T[nib][(y >> (4 * (31 - nib))) & 0xF]
        return _i2b(acc)


class AESGCM:
    """Minimal API compatible with cryptography.hazmat's AESGCM."""
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32): raise ValueError('invalid key length')
        self._rk, self._nr = _expand_key(bytes(key))
        h = _aes_ecb(self._rk, self._nr, np.zeros((1, 16), np.uint8))[0]
        self._gh = _GHash(bytes(h))

    def _crypt(self, nonce, data):
        n = (len(data) + 15) // 16
        if n == 0: return b''
        ks = _ctr(self._rk, self._nr, nonce, n).reshape(-1)[:len(data)]
        return (np.frombuffer(data, np.uint8) ^ ks).tobytes()

    def _tag(self, nonce, ct, aad):
        s = self._gh(ct, aad or b'')
        j0 = _aes_ecb(self._rk, self._nr,
                      np.frombuffer(bytes(nonce) + b'\x00\x00\x00\x01', np.uint8).reshape(1, 16))[0]
        return bytes(np.frombuffer(s, np.uint8) ^ j0)

    def encrypt(self, nonce, data, aad=None):
        ct = self._crypt(nonce, bytes(data))
        return ct + self._tag(nonce, ct, aad)

    def decrypt(self, nonce, data, aad=None):
        data = bytes(data)
        if len(data) < 16: raise ValueError('ciphertext too short')
        ct, tag = data[:-16], data[-16:]
        exp = self._tag(nonce, ct, aad)
        if not _ct_eq(exp, tag): raise ValueError('GCM authentication failed')
        return self._crypt(nonce, ct)

def _ct_eq(a, b):
    if len(a) != len(b): return False
    r = 0
    for x, y in zip(a, b): r |= x ^ y
    return r == 0
