"""AES-256-GCM: the pure-Python fallback against the reference implementation,
and the encrypted-container behaviour users depend on.

The pure-Python AESGCM exists so encrypted .owlg files stay readable inside
environments (QGIS, say) that have no `cryptography` wheel. If it ever diverges
by a single byte those files become unreadable there, so the comparison is exact.
"""
from __future__ import annotations

import os

import pytest

from conftest import (LAYOUTS, needs_cryptography, needs_encoder,
                      skip_unless_supported)

KEY = bytes(range(32))
NONCE = bytes(range(12))
LENGTHS = (0, 1, 15, 16, 17, 100, 4096)

# A fast KDF: these tests prove the crypto, not PBKDF2's cost factor.
FAST_ITERS = 1000
PASSPHRASE = "correct horse battery staple"


def _pure():
    from owlg._aes import AESGCM

    return AESGCM


def _ref():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM


# ---------------------------------------------- pure Python vs. `cryptography`
@needs_cryptography
@pytest.mark.parametrize("n", LENGTHS)
def test_pure_python_aesgcm_is_byte_identical(n):
    msg = os.urandom(n)
    pure = _pure()(KEY).encrypt(NONCE, msg, None)
    ref = _ref()(KEY).encrypt(NONCE, msg, None)

    assert pure == ref, f"ciphertext+tag differs at length {n}"
    assert len(pure) == n + 16, "GCM output must be the message plus a 16-byte tag"
    # ...and each decrypts the other's output.
    assert _pure()(KEY).decrypt(NONCE, ref, None) == msg
    assert _ref()(KEY).decrypt(NONCE, pure, None) == msg


@needs_cryptography
@pytest.mark.parametrize("n", (0, 17, 4096))
def test_pure_python_aesgcm_matches_with_aad(n):
    msg = os.urandom(n)
    aad = b"owlg-header"
    assert _pure()(KEY).encrypt(NONCE, msg, aad) == _ref()(KEY).encrypt(NONCE, msg, aad)


@needs_cryptography
def test_pure_python_aesgcm_matches_for_several_keys_and_nonces():
    for _ in range(4):
        key = os.urandom(32)
        nonce = os.urandom(12)
        msg = os.urandom(257)
        assert _pure()(key).encrypt(nonce, msg, None) == _ref()(key).encrypt(nonce, msg, None)


def test_pure_python_aesgcm_roundtrips_without_the_reference():
    """Works even in an environment with no `cryptography` at all."""
    for n in LENGTHS:
        msg = os.urandom(n)
        ct = _pure()(KEY).encrypt(NONCE, msg, None)
        assert _pure()(KEY).decrypt(NONCE, ct, None) == msg


@pytest.mark.parametrize("n", (1, 16, 100))
def test_tampered_tag_is_rejected(n):
    ct = bytearray(_pure()(KEY).encrypt(NONCE, os.urandom(n), None))
    ct[-1] ^= 0x01
    with pytest.raises(Exception) as exc:
        _pure()(KEY).decrypt(NONCE, bytes(ct), None)
    assert "authentication" in str(exc.value).lower()


@pytest.mark.parametrize("n", (1, 16, 100))
def test_tampered_ciphertext_is_rejected(n):
    ct = bytearray(_pure()(KEY).encrypt(NONCE, os.urandom(n), None))
    ct[0] ^= 0x80
    with pytest.raises(Exception):
        _pure()(KEY).decrypt(NONCE, bytes(ct), None)


def test_truncated_ciphertext_and_wrong_key_are_rejected():
    ct = _pure()(KEY).encrypt(NONCE, b"payload", None)
    with pytest.raises(Exception):
        _pure()(KEY).decrypt(NONCE, ct[:8], None)
    with pytest.raises(Exception):
        _pure()(bytes(32)).decrypt(NONCE, ct, None)  # wrong key
    with pytest.raises(Exception):
        _pure()(KEY).decrypt(bytes(12), ct, None)  # wrong nonce
    with pytest.raises(ValueError):
        _pure()(b"tooshort")


def test_owlg_crypto_seal_unseal_roundtrip():
    from owlg import crypto as cy

    salt, prefix = cy.new_params()
    assert len(salt) == 16 and len(prefix) == 8
    key = cy.derive_key(PASSPHRASE, salt, FAST_ITERS)
    assert len(key) == 32
    assert cy.derive_key(PASSPHRASE, salt, FAST_ITERS) == key
    assert cy.derive_key("other", salt, FAST_ITERS) != key

    blob = os.urandom(500)
    sealed = cy.seal(key, prefix, 7, blob)
    assert sealed != blob
    assert cy.unseal(key, prefix, 7, sealed) == blob
    with pytest.raises(Exception):
        cy.unseal(key, prefix, 8, sealed)  # nonce is bound to the blob index


# ------------------------------------------------------- encrypted .owlg files
@pytest.fixture(scope="session")
def encrypted(encode):
    def _make(layout):
        skip_unless_supported(layout, 2)
        return encode(layout, 2, tag="enc", password=PASSPHRASE, kdf_iters=FAST_ITERS)

    return _make


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_encrypted_file_opens_with_the_right_passphrase(layout, encrypted, read_geotiff,
                                                        sample_tif):
    import numpy as np
    from owlg.container import read_owlg

    arr, hdr = read_owlg(str(encrypted(layout)), PASSPHRASE)
    assert hdr["encrypted"] is True
    assert arr.shape == (3, 718, 791)
    assert arr.any()
    orig = read_geotiff(sample_tif)
    assert int(np.abs(orig.astype(np.int32) - arr.astype(np.int32)).max()) <= 2


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_encrypted_header_reveals_nothing_in_the_clear(layout, encrypted):
    raw = open(str(encrypted(layout)), "rb").read()
    assert raw[:4] == b"OWLG"
    assert raw[5] & 1, "the encrypted flag must be set"
    for secret in (b"colorinterp", b"transform", b"sha256", b"AREA_OR_POINT"):
        assert secret not in raw, f"{secret!r} is readable without the key"


@needs_encoder
def test_flat_encrypted_rejects_a_wrong_passphrase(encrypted):
    from owlg.container import read_owlg

    with pytest.raises(Exception) as exc:
        read_owlg(str(encrypted("flat")), "definitely wrong")
    assert "passphrase" in str(exc.value).lower()


@needs_encoder
def test_tiled_encrypted_rejects_a_wrong_passphrase(encrypted):
    from owlg.tiled_read import TiledReader

    with pytest.raises(Exception) as exc:
        TiledReader(str(encrypted("tiled")), "definitely wrong")
    assert "passphrase" in str(exc.value).lower()


@needs_encoder
def test_flat_encrypted_without_a_passphrase_raises_needkey(encrypted):
    from owlg.container import read_owlg

    with pytest.raises(Exception) as exc:
        read_owlg(str(encrypted("flat")), None)
    assert type(exc.value).__name__ == "NeedKey"


@needs_encoder
def test_tiled_encrypted_without_a_passphrase_raises_needkey(encrypted):
    from owlg.container import read_owlg

    with pytest.raises(Exception) as exc:
        read_owlg(str(encrypted("tiled")), None)
    assert type(exc.value).__name__ == "NeedKey"


@needs_encoder
def test_tiled_reader_cache_does_not_accept_a_wrong_passphrase(encrypted):
    from owlg.container import read_owlg

    path = str(encrypted("tiled"))
    read_owlg(path, PASSPHRASE)          # populate the module-level reader cache
    with pytest.raises(Exception):
        read_owlg(path, "definitely wrong")


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_encrypted_and_plain_files_decode_to_the_same_pixels(layout, encode, encrypted,
                                                             decode_array):
    import numpy as np

    skip_unless_supported(layout, 2)
    plain = decode_array(encode(layout, 2))
    secret = decode_array(encrypted(layout), password=PASSPHRASE)
    assert np.array_equal(plain, secret)
