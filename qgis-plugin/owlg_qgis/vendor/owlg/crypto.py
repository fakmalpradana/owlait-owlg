"""OWLG encryption: AES-256-GCM, key derived with PBKDF2-HMAC-SHA256.

The backend is selected automatically:
  1. `cryptography` if installed (fastest, widely tested)
  2. the pure-Python fallback in ._aes (numpy-accelerated) - so that encrypted
     files can still be opened in environments without extra packages, like QGIS

The KDF always uses hashlib.pbkdf2_hmac from the standard library, so it can
never become a reason for an import to fail.

Each blob is encrypted separately with a unique nonce = prefix(8B) || counter_be(4B),
so random per-tile access stays possible without decrypting the whole file.
"""
import os, struct, hashlib

KDF_PBKDF2_SHA256 = 1
DEFAULT_ITERS = 600_000          # in line with OWASP guidance & practical in WebCrypto
TAG_LEN = 16

_BACKEND = None
def _aesgcm(key):
    """Return an AESGCM object from the best available backend."""
    global _BACKEND
    if _BACKEND is None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _A
            _BACKEND = ('cryptography', _A)
        except Exception:
            from ._aes import AESGCM as _A
            _BACKEND = ('pure-python', _A)
    return _BACKEND[1](key)

def backend_name():
    _aesgcm(b'\x00' * 32)
    return _BACKEND[0]

def derive_key(password: str, salt: bytes, iters: int = DEFAULT_ITERS) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iters, dklen=32)

def new_params():
    return os.urandom(16), os.urandom(8)      # salt, nonce_prefix

def _nonce(prefix: bytes, idx: int) -> bytes:
    return prefix + struct.pack('>I', idx & 0xFFFFFFFF)

def seal(key: bytes, prefix: bytes, idx: int, data: bytes) -> bytes:
    return _aesgcm(key).encrypt(_nonce(prefix, idx), data, None)

def unseal(key: bytes, prefix: bytes, idx: int, data: bytes) -> bytes:
    return _aesgcm(key).decrypt(_nonce(prefix, idx), data, None)
