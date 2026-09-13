"""The central guarantee: |original - decoded| <= delta for EVERY pixel.

Nothing in here samples, subsets or tolerates: the comparison is over the whole
array, band by band, and each assertion is guarded so it cannot pass vacuously
(right shape, right dtype, decoded content that is actually there).
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from conftest import LAYOUTS, needs_encoder, skip_unless_supported

DELTAS = (0, 1, 2, 3, 5)


def _abs_err(orig: np.ndarray, dec: np.ndarray) -> np.ndarray:
    return np.abs(orig.astype(np.int32) - dec.astype(np.int32))


def _assert_not_vacuous(dec: np.ndarray, orig: np.ndarray) -> None:
    """Guard rails: a decoder that returned zeros, or the wrong shape, must not
    be able to satisfy the bound assertions below."""
    assert dec.shape == orig.shape, f"shape {dec.shape} != original {orig.shape}"
    assert dec.dtype == np.uint8
    assert dec.size == orig.size > 0
    assert dec.any(), "decoded raster is entirely zero"
    assert int(dec.max()) > int(dec.min()), "decoded raster is a single constant value"
    # the sample has real content in every band
    for b in range(dec.shape[0]):
        assert np.unique(dec[b]).size > 1, f"band {b} decoded to a constant"


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", DELTAS)
def test_every_pixel_within_bound(layout, delta, sample_tif, encode, decode_array, read_geotiff):
    skip_unless_supported(layout, delta)
    orig = read_geotiff(sample_tif)
    dec = decode_array(encode(layout, delta))

    _assert_not_vacuous(dec, orig)

    err = _abs_err(orig, dec)
    worst = int(err.max())
    assert worst <= delta, (
        f"{layout} delta={delta}: max per-pixel error {worst} exceeds the bound; "
        f"{int((err > delta).sum())} of {err.size} pixels violate it"
    )
    # ...and per band, so a single clean band cannot hide a broken one.
    for b in range(orig.shape[0]):
        assert int(err[b].max()) <= delta, f"band {b} exceeds the bound"
    assert np.count_nonzero(err > delta) == 0


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_delta_zero_is_bit_identical(layout, sample_tif, encode, decode_array, read_geotiff):
    """delta=0 is not 'almost lossless' -- it is byte-for-byte the original."""
    skip_unless_supported(layout, 0)
    orig = read_geotiff(sample_tif)
    dec = decode_array(encode(layout, 0))

    _assert_not_vacuous(dec, orig)
    assert np.array_equal(orig, dec)
    assert int(_abs_err(orig, dec).max()) == 0

    want = hashlib.sha256(np.ascontiguousarray(orig).tobytes()).hexdigest()
    got = hashlib.sha256(np.ascontiguousarray(dec).tobytes()).hexdigest()
    assert got == want, "SHA-256 of the decoded pixels differs from the original"


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_delta_zero_sha_recorded_in_file(layout, sample_tif, encode, read_geotiff):
    """The file itself carries a digest of the original pixels, and it checks out."""
    skip_unless_supported(layout, 0)
    from owlg.container import read_owlg

    path = encode(layout, 0)
    arr, hdr = read_owlg(str(path))
    orig = read_geotiff(sample_tif)
    _assert_not_vacuous(arr, orig)

    if layout == "flat":
        assert hdr["sha256"] == hashlib.sha256(
            np.ascontiguousarray(orig).tobytes()
        ).hexdigest()
        assert hdr["sha256_match"] is True
    else:
        from owlg.tiled_read import verify_scan

        ok, expected, got = verify_scan(str(path))
        assert ok is True
        assert expected and got == expected


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", (1, 3))
def test_bound_is_tight_not_trivially_satisfied(layout, delta, sample_tif, encode,
                                                decode_array, read_geotiff):
    """A larger delta must actually be used for compression -- otherwise the bound
    test above would pass simply because nothing is ever lossy."""
    skip_unless_supported(layout, delta)
    orig = read_geotiff(sample_tif)
    dec = decode_array(encode(layout, delta))
    err = _abs_err(orig, dec)
    assert int(err.max()) <= delta
    assert int(err.max()) > 0, "near-lossless mode produced a bit-identical result"
    assert err.mean() <= delta


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", (0, 2))
def test_bound_survives_the_geotiff_writer(layout, delta, sample_tif, encode,
                                           read_geotiff, workspace):
    """The guarantee has to hold for the file a user actually gets back, not just
    for the in-memory array."""
    skip_unless_supported(layout, delta)
    from owlg.container import to_tif

    out = workspace / f"{layout}_{delta}.tif"
    to_tif(str(encode(layout, delta)), str(out))
    orig = read_geotiff(sample_tif)
    dec = read_geotiff(out)

    _assert_not_vacuous(dec, orig)
    assert int(_abs_err(orig, dec).max()) <= delta


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_header_records_the_promised_bound(layout, encode):
    from owlg.container import open_owlg

    for delta in DELTAS:
        skip_unless_supported(layout, delta)
        hdr, _get = open_owlg(str(encode(layout, delta)))
        assert int(hdr["delta"]) == delta
        assert hdr["mode"] == ("lossless" if delta == 0 else "nearlossless")
        assert hdr["w"] == 791 and hdr["h"] == 718 and hdr["bands"] == 3
