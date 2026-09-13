"""owlg.ml -- the ndarray bridge.

Every export path has to agree with an ordinary decode: same pixels, same order,
same dtype. A transpose bug here is silent in every viewer but ruins training.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from conftest import LAYOUTS, needs_encoder, skip_unless_supported

LAYOUT_OPTS = ("CHW", "HWC")


def _expected(ref: np.ndarray, layout: str) -> np.ndarray:
    return ref if layout == "CHW" else np.ascontiguousarray(ref.transpose(1, 2, 0))


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("order", LAYOUT_OPTS)
@pytest.mark.parametrize("delta", (0, 2))
def test_to_array_matches_a_normal_decode(layout, order, delta, encode, decode_array):
    skip_unless_supported(layout, delta)
    from owlg import ml

    path = encode(layout, delta)
    ref = decode_array(path)
    arr, meta = ml.to_array(str(path), layout=order)

    assert arr.dtype == np.uint8
    assert arr.shape == _expected(ref, order).shape
    assert arr.shape == ((3, 718, 791) if order == "CHW" else (718, 791, 3))
    assert np.array_equal(arr, _expected(ref, order))
    assert arr.any(), "decoded array is entirely zero"

    assert meta["layout"] == order
    assert (meta["width"], meta["height"], meta["bands"]) == (791, 718, 3)
    assert meta["dtype"] == "uint8"
    assert meta["delta"] == delta
    assert meta["colorinterp"] == ["red", "green", "blue"]
    assert "32618" in (meta["crs"] or "") or "UTM zone 18N" in (meta["crs"] or "")
    assert np.allclose(meta["transform"][:6],
                       [300.0379266750948, 0.0, 101985.0, 0.0, -300.041782729805, 2826915.0])


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("order", LAYOUT_OPTS)
def test_to_npy_roundtrips(layout, order, encode, decode_array, workspace):
    skip_unless_supported(layout, 2)
    from owlg import ml

    path = encode(layout, 2)
    out = workspace / f"{layout}_{order}.npy"
    written, meta = ml.to_npy(str(path), str(out), layout=order)

    assert os.path.abspath(written) == os.path.abspath(str(out))
    assert out.is_file()
    got = np.load(str(out))
    assert got.dtype == np.uint8
    assert np.array_equal(got, _expected(decode_array(path), order))

    sidecar = workspace / f"{layout}_{order}.meta.json"
    assert sidecar.is_file()
    on_disk = json.loads(sidecar.read_text())
    assert on_disk["layout"] == order == meta["layout"]
    assert on_disk["bands"] == 3 and on_disk["width"] == 791 and on_disk["height"] == 718


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("order", LAYOUT_OPTS)
def test_to_npz_roundtrips(layout, order, encode, decode_array, workspace):
    skip_unless_supported(layout, 2)
    from owlg import ml

    path = encode(layout, 2)
    out = workspace / f"{layout}_{order}.npz"
    _written, meta = ml.to_npz(str(path), str(out), layout=order)
    assert out.is_file()

    with np.load(str(out), allow_pickle=False) as z:
        assert sorted(z.keys()) == ["image", "meta"]
        image = z["image"]
        stored = json.loads(str(z["meta"]))

    assert image.dtype == np.uint8
    assert np.array_equal(image, _expected(decode_array(path), order))
    assert stored["layout"] == order == meta["layout"]
    assert stored["delta"] == 2


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_to_memmap_is_loadable_without_reading_it_all(layout, encode, decode_array,
                                                      workspace):
    skip_unless_supported(layout, 2)
    from owlg import ml

    path = encode(layout, 2)
    out = workspace / f"{layout}_mm.npy"
    ml.to_memmap(str(path), str(out))

    mm = np.load(str(out), mmap_mode="r")
    assert mm.shape == (3, 718, 791)
    assert np.array_equal(np.asarray(mm), decode_array(path))
    assert np.array_equal(np.asarray(mm[1, 100:120, 200:220]),
                          decode_array(path)[1, 100:120, 200:220])
    del mm


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_to_array_fast_skips_the_correction_layer(layout, encode, decode_array,
                                                  read_geotiff, sample_tif):
    skip_unless_supported(layout, 3)
    from owlg import ml

    path = encode(layout, 3)
    fast, _meta = ml.to_array(str(path), fast=True)
    full = decode_array(path)
    assert fast.shape == full.shape
    assert fast.any()
    # the base layer alone is not corrected, so it may exceed the bound...
    orig = read_geotiff(sample_tif)
    assert int(np.abs(orig.astype(np.int32) - full.astype(np.int32)).max()) <= 3
    # ...but it is still recognisably the same image
    assert float(np.abs(fast.astype(np.float64) - full.astype(np.float64)).mean()) < 8.0


@needs_encoder
def test_patch_grid_and_patches_agree(encode):
    skip_unless_supported("flat", 2)
    from owlg import ml

    path = encode("flat", 2)
    count, (nr, nc) = ml.patch_grid(str(path), size=256, stride=256)
    assert (nr, nc) == ((718 - 256) // 256 + 1, (791 - 256) // 256 + 1) == (2, 3)
    assert count == nr * nc == 6

    got = list(ml.patches(str(path), size=256, stride=256))
    assert len(got) == count
    for patch, (r, c) in got:
        assert patch.shape == (3, 256, 256)
        assert patch.dtype == np.uint8
        assert 0 <= r <= 718 - 256 and 0 <= c <= 791 - 256


@needs_encoder
def test_dataset_indexes_patches(encode, decode_array):
    skip_unless_supported("flat", 2)
    from owlg import ml

    path = encode("flat", 2)
    ds = ml.OwlgDataset(str(path), size=256, stride=256)
    assert len(ds) == 6
    ref = decode_array(path)
    first = ds[0]
    assert first.shape == (3, 256, 256)
    assert np.array_equal(first, ref[:, :256, :256])
