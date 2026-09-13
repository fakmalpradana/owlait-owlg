"""Band-count handling, in both layouts.

A base blob is an ordinary RGB image, so it holds at most three bands. Rasters
with other band counts therefore exercise two pieces of machinery that are easy
to get wrong and that fail SILENTLY when they are wrong:

  * splitting bands into groups of three, one base blob per group;
  * padding a trailing group of one or two bands up to three channels.

Both had real defects. The padding replaced every channel with band 0, which
destroyed the second band of any trailing two-band group -- invisible at
delta > 0 because the correction layer repairs it, but a silent data loss in
lossless mode. And the tiled layout truncated to three bands outright, so a
four-band raster raised a shape error rather than encoding.

These tests assert the bound over every pixel of every band, so a band that
comes back as a copy of another band fails loudly.
"""
import numpy as np
import pytest

from conftest import skip_unless_supported
from owlg.container import read_owlg

BAND_COUNTS = [1, 2, 3, 4, 5, 6, 8]
LAYOUTS = ["flat", "tiled"]
DELTAS = [0, 2]


def _varied(bands, height=140, width=180, seed=11):
    """A raster where every band genuinely differs from every other.

    Constant bands are dropped into the header and never reach the codec, so a
    test raster whose extra bands are constant would hide exactly the bug this
    module is here to catch.
    """
    rng = np.random.default_rng(seed)
    a = np.clip(rng.normal(128, 45, (bands, height, width)), 0, 255).astype(np.uint8)
    for b in range(bands):
        # a distinct gradient per band: no two bands can be confused
        ramp = np.linspace(0, 60, width, dtype=np.float32)[None, :] * (b + 1) / bands
        a[b] = np.clip(a[b].astype(np.float32) + ramp + b * 9, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(a)


@pytest.mark.parametrize("bands", BAND_COUNTS)
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", DELTAS)
def test_every_band_count_honours_the_bound(bands, layout, delta, make_raster, encode):
    skip_unless_supported(layout, delta)
    src = make_raster(f"bands{bands}.tif", _varied(bands))
    dst = encode(layout, delta, src=src, tile=64)

    got, hdr = read_owlg(str(dst))
    want = _varied(bands)
    assert got.shape == want.shape, f"{got.shape} != {want.shape}"

    err = np.abs(got.astype(np.int32) - want.astype(np.int32))
    per_band = [int(err[b].max()) for b in range(bands)]
    assert err.max() <= delta, (
        f"bound violated: per-band max error {per_band}, bound +/-{delta}")
    if delta == 0:
        assert np.array_equal(got, want), f"per-band max error {per_band}"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_a_trailing_two_band_group_keeps_its_second_band(layout, make_raster, encode):
    """The specific shape that used to lose data: bands % 3 == 2.

    Band 1 must not come back as a copy of band 0. The two bands here are made
    deliberately far apart so a copy shows up as a huge error, not a subtle one.
    """
    a = np.zeros((2, 100, 120), np.uint8)
    a[0] = 30
    a[1] = 200
    a[0, ::3, :] = 60          # structure, so the codec cannot trivially predict
    a[1, :, ::4] = 170
    skip_unless_supported(layout, 0)
    src = make_raster("trailing2.tif", a)
    dst = encode(layout, 0, src=src, tile=64)

    got, _ = read_owlg(str(dst))
    assert not np.array_equal(got[0], got[1]), "band 1 came back as a copy of band 0"
    assert np.array_equal(got, a)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_constant_bands_are_dropped_and_restored(layout, make_raster, encode):
    """A constant band is stored as one number in the header, not as pixels."""
    a = _varied(4)
    a[3] = 255                                   # fully constant alpha
    skip_unless_supported(layout, 0)
    src = make_raster("constband.tif", a)
    dst = encode(layout, 0, src=src, tile=64)

    got, hdr = read_owlg(str(dst))
    assert hdr["const"] == {"3": 255}, hdr["const"]
    assert np.array_equal(got, a)
