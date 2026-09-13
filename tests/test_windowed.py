"""Windowed reads must be indistinguishable from slicing a full decode.

The windows below are chosen against the sample's real geometry (791x718): the
flat fixture is written with 256 px tiles and the tiled fixture with 512 px, so
the list straddles interior tile seams in both, and clips at the right and
bottom edges where the last tile is a partial one.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import SAMPLE, needs_encoder, skip_unless_supported

W, H = 791, 718

# (xoff, yoff, xsize, ysize, description)
WINDOWS = [
    (0, 0, 64, 64, "origin"),
    (100, 100, 33, 17, "interior, odd size"),
    (250, 250, 20, 20, "straddles the 256 px seam in both axes"),
    (255, 255, 3, 3, "three pixels across the 256 px seam"),
    (500, 500, 64, 64, "straddles the 512 px seam in both axes"),
    (511, 0, 4, H, "full-height strip across the vertical 512 px seam"),
    (0, 511, W, 4, "full-width strip across the horizontal 512 px seam"),
    (760, 690, 64, 64, "clipped at the right AND bottom edge"),
    (W - 1, H - 1, 1, 1, "the very last pixel"),
    (0, 0, W, H, "the whole raster"),
]
IDS = [f"{x}_{y}_{w}x{h}" for (x, y, w, h, _d) in WINDOWS]


def _expected(full: np.ndarray, band: int, x: int, y: int, w: int, h: int) -> np.ndarray:
    """What a full decode says the window contains, after the same clipping the
    readers apply."""
    x = max(0, x)
    y = max(0, y)
    w = max(0, min(w, full.shape[2] - x))
    h = max(0, min(h, full.shape[1] - y))
    return np.ascontiguousarray(full[band, y:y + h, x:x + w])


# ------------------------------------------------------------------ flat (v3)
@needs_encoder
@pytest.mark.parametrize("x,y,w,h,desc", WINDOWS, ids=IDS)
def test_flat_read_window_matches_full_decode(x, y, w, h, desc, encode, decode_array):
    skip_unless_supported("flat", 2)
    from owlg import container

    path = encode("flat", 2, tile=256, tag="t256")
    full = decode_array(path)
    for band in range(full.shape[0]):
        got = container.read_window(str(path), band, x, y, w, h)
        want = _expected(full, band, x, y, w, h)
        assert got.shape == want.shape, desc
        assert got.dtype == np.uint8
        assert np.array_equal(got, want), f"band {band}, window {desc}"


@needs_encoder
@pytest.mark.parametrize("x,y,w,h,desc", WINDOWS, ids=IDS)
def test_flat_lossless_lossy_base_window_is_exact(x, y, w, h, desc, encode, decode_array):
    """A delta=0 file on a WebP base is described as `lossless` but still carries a
    correction layer. The windowed reader once keyed off the mode string and served
    the bare base layer -- 48 DN off on the sample -- while the full decode was
    exact. The window must match the original pixels, not just the full decode."""
    skip_unless_supported("flat", 0)
    import rasterio
    from owlg import container

    path = encode("flat", 0, tile=256, tag="ll_webp")
    with rasterio.open(SAMPLE) as ds:
        orig = ds.read()
    _hdr, _get = container.open_owlg(str(path))
    assert _hdr["mode"] == "lossless" and _hdr["n_corr"] > 0, "fixture must exercise the trap"
    for band in range(orig.shape[0]):
        got = container.read_window(str(path), band, x, y, w, h)
        want = _expected(orig, band, x, y, w, h)
        assert np.array_equal(got, want), f"band {band}, window {desc}"


# ----------------------------------------------------------------- tiled (v4)
@needs_encoder
@pytest.mark.parametrize("x,y,w,h,desc", WINDOWS, ids=IDS)
def test_tiled_read_window_matches_full_decode(x, y, w, h, desc, encode, decode_array):
    skip_unless_supported("tiled", 2)
    from owlg.tiled_read import TiledReader

    path = encode("tiled", 2)
    full = decode_array(path)
    reader = TiledReader(str(path))
    try:
        for band in range(full.shape[0]):
            got = reader.read_window(band, x, y, w, h)
            want = _expected(full, band, x, y, w, h)
            assert got.shape == want.shape, desc
            assert np.array_equal(got, want), f"band {band}, window {desc}"
    finally:
        reader.close()


@needs_encoder
@pytest.mark.parametrize("layout", ("flat", "tiled"))
def test_container_read_window_dispatches_to_both_layouts(layout, encode, decode_array):
    """container.read_window is the single entry point the GDAL bridge calls."""
    skip_unless_supported(layout, 2)
    from owlg import container

    path = encode(layout, 2, **({"tile": 256, "tag": "t256"} if layout == "flat" else {}))
    full = decode_array(path)
    for x, y, w, h, desc in WINDOWS:
        for band in range(full.shape[0]):
            got = container.read_window(str(path), band, x, y, w, h)
            assert np.array_equal(got, _expected(full, band, x, y, w, h)), (layout, desc)


@needs_encoder
@pytest.mark.parametrize("layout", ("flat", "tiled"))
def test_read_window_clips_instead_of_overrunning(layout, encode):
    skip_unless_supported(layout, 2)
    from owlg import container

    path = encode(layout, 2, **({"tile": 256, "tag": "t256"} if layout == "flat" else {}))
    # entirely outside the raster
    assert container.read_window(str(path), 0, W + 10, H + 10, 32, 32).shape == (0, 0)
    # half outside
    assert container.read_window(str(path), 0, W - 10, H - 10, 100, 100).shape == (10, 10)
    # a negative offset is clamped to the origin
    assert container.read_window(str(path), 0, -50, -50, 20, 20).shape == (20, 20)


@needs_encoder
def test_windowed_read_is_not_vacuous(encode, decode_array):
    """Guard: if read_window returned zeros everywhere the comparisons above
    would still pass on an all-zero region, so assert real content here."""
    skip_unless_supported("tiled", 2)
    from owlg import container

    path = encode("tiled", 2)
    full = decode_array(path)
    win = container.read_window(str(path), 0, 300, 300, 128, 128)
    assert win.shape == (128, 128)
    assert win.any()
    assert np.unique(win).size > 4
    assert np.array_equal(win, full[0, 300:428, 300:428])


# --------------------------------------------------------------- overview level
@needs_encoder
def test_overview_levels_are_readable(encode):
    skip_unless_supported("tiled", 2)
    from owlg.tiled_read import TiledReader

    path = encode("tiled", 2)
    reader = TiledReader(str(path))
    try:
        assert len(reader.levels) > 1, "the tiled layout should build a pyramid"
        lvl0 = reader.levels[0]
        assert (lvl0["w"], lvl0["h"]) == (W, H)

        for level in range(1, len(reader.levels)):
            meta = reader.levels[level]
            prev = reader.levels[level - 1]
            assert meta["w"] == (prev["w"] + 1) // 2
            assert meta["h"] == (prev["h"] + 1) // 2

            whole = reader.read_all(level)
            assert whole.shape == (3, meta["h"], meta["w"])
            assert whole.any(), f"overview level {level} decoded to all zeros"

            for band in range(3):
                for (x, y, w, h) in [(0, 0, 32, 32),
                                     (meta["w"] // 2, meta["h"] // 2, 40, 40),
                                     (meta["w"] - 5, meta["h"] - 5, 32, 32)]:
                    got = reader.read_window(band, x, y, w, h, level=level)
                    want = _expected(whole, band, x, y, w, h)
                    assert got.shape == want.shape
                    assert np.array_equal(got, want)
    finally:
        reader.close()


@needs_encoder
def test_overview_is_a_downsample_of_level_zero(encode, decode_array):
    """An overview is a 2x2 box average of the level below, stored base-only.

    Overviews carry NO error bound -- they exist for display, so the lossy base
    codec is all that backs them. What must hold is that level 1 really is this
    raster shrunk by two: on average it tracks the box average closely and it
    correlates with it almost perfectly. A pyramid built from the wrong level,
    from uncorrected data, or from garbage fails both.
    """
    skip_unless_supported("tiled", 2)
    from owlg.tiled_read import TiledReader

    path = encode("tiled", 2)
    full = decode_array(path).astype(np.float64)
    reader = TiledReader(str(path))
    try:
        ov = reader.read_all(1).astype(np.float64)
    finally:
        reader.close()

    # drop the final row/column of the overview: it is built from edge padding
    hh = (full.shape[1] // 2) * 2
    ww = (full.shape[2] // 2) * 2
    ref = full[:, :hh, :ww].reshape(3, hh // 2, 2, ww // 2, 2).mean(axis=(2, 4))
    got = ov[:, : hh // 2, : ww // 2]
    assert got.shape == ref.shape

    diff = np.abs(got - ref)
    assert diff.mean() < 6.0, f"overview drifts from the box average (mean {diff.mean():.2f})"
    assert np.percentile(diff, 99) < 32.0
    for band in range(3):
        r = np.corrcoef(got[band].ravel(), ref[band].ravel())[0, 1]
        assert r > 0.99, f"overview band {band} barely correlates with level 0 (r={r:.3f})"
