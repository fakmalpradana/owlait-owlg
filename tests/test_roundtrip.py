"""Everything that is not pixels: CRS, geotransform, band count, colour
interpretation, nodata, tags -- and the constant bands the format drops."""
from __future__ import annotations

import numpy as np
import pytest

from conftest import LAYOUTS, needs_encoder, skip_unless_supported


@pytest.fixture(scope="session")
def const_band_raster(make_raster, read_geotiff, sample_tif):
    """A 4-band raster whose band 3 is constant -- the format drops it and stores
    only the value in the header."""
    rgb = read_geotiff(sample_tif)
    arr = np.zeros((4, rgb.shape[1], rgb.shape[2]), np.uint8)
    arr[:3] = rgb
    arr[3] = 200
    return make_raster(
        "const_band.tif",
        arr,
        nodata=255,
        colorinterp=["red", "green", "blue", "alpha"],
    )


# ---------------------------------------------------------------- georeferencing
@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", (0, 2))
def test_header_keeps_georeferencing(layout, delta, sample_tif, encode, geotiff_meta):
    skip_unless_supported(layout, delta)
    from owlg.container import open_owlg

    src = geotiff_meta(sample_tif)
    hdr, _get = open_owlg(str(encode(layout, delta)))

    assert hdr["w"] == src["width"]
    assert hdr["h"] == src["height"]
    assert hdr["bands"] == src["count"]
    assert hdr["crs"] == src["crs_wkt"]
    assert "EPSG" in (hdr["crs"] or "") or "UTM" in (hdr["crs"] or "")
    assert np.allclose(hdr["transform"], src["transform"])
    assert hdr["colorinterp"] == src["colorinterp"] == ["red", "green", "blue"]
    assert list(hdr["nodata"]) == list(src["nodata"])
    assert hdr["tags"].get("AREA_OR_POINT") == src["tags"].get("AREA_OR_POINT")


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", (0, 2))
def test_geotiff_roundtrip_keeps_georeferencing(layout, delta, sample_tif, encode,
                                                geotiff_meta, workspace):
    skip_unless_supported(layout, delta)
    from owlg.container import to_tif

    out = workspace / f"rt_{layout}_{delta}.tif"
    to_tif(str(encode(layout, delta)), str(out))

    src = geotiff_meta(sample_tif)
    got = geotiff_meta(out)

    assert (got["width"], got["height"]) == (src["width"], src["height"])
    assert got["count"] == src["count"]
    assert got["dtype"] == "uint8"
    assert got["crs_wkt"] == src["crs_wkt"]
    assert np.allclose(got["transform"], src["transform"])
    assert got["colorinterp"] == src["colorinterp"]
    assert got["tags"].get("AREA_OR_POINT") == src["tags"].get("AREA_OR_POINT")


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_geotiff_roundtrip_keeps_nodata(layout, const_band_raster, encode, geotiff_meta,
                                        workspace):
    skip_unless_supported(layout, 2)
    from owlg.container import to_tif

    src_path = encode(layout, 2, src=const_band_raster, tag="const")
    out = workspace / f"nodata_{layout}.tif"
    to_tif(str(src_path), str(out))

    assert geotiff_meta(out)["nodata"] == geotiff_meta(const_band_raster)["nodata"]


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_nodata_is_preserved_in_the_owlg_header(layout, const_band_raster, encode,
                                                geotiff_meta):
    """The value does survive into the container -- only the GeoTIFF writer loses it."""
    skip_unless_supported(layout, 2)
    from owlg.container import open_owlg

    hdr, _get = open_owlg(str(encode(layout, 2, src=const_band_raster, tag="const")))
    assert list(hdr["nodata"]) == list(geotiff_meta(const_band_raster)["nodata"]) == [255.0] * 4


# ------------------------------------------------------------------ band count
@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_constant_band_is_dropped_and_restored(layout, const_band_raster, encode,
                                               decode_array, read_geotiff):
    skip_unless_supported(layout, 2)
    from owlg.container import open_owlg

    path = encode(layout, 2, src=const_band_raster, tag="const")
    hdr, _get = open_owlg(str(path))

    # the band really was dropped from the coded payload...
    assert hdr["const"] == {"3": 200}
    assert hdr["coded"] == [0, 1, 2]
    assert hdr["bands"] == 4

    # ...and comes back exactly, alongside the three coded bands.
    orig = read_geotiff(const_band_raster)
    dec = decode_array(path)
    assert dec.shape == orig.shape == (4, 718, 791)
    assert np.array_equal(dec[3], np.full(dec[3].shape, 200, np.uint8))
    assert np.unique(dec[3]).tolist() == [200]
    assert int(np.abs(orig.astype(np.int32) - dec.astype(np.int32)).max()) <= 2
    assert dec[:3].any()


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_constant_band_roundtrips_through_geotiff(layout, const_band_raster, encode,
                                                  read_geotiff, geotiff_meta, workspace):
    skip_unless_supported(layout, 2)
    from owlg.container import to_tif

    out = workspace / f"const_{layout}.tif"
    to_tif(str(encode(layout, 2, src=const_band_raster, tag="const")), str(out))

    got = read_geotiff(out)
    assert got.shape == (4, 718, 791)
    assert np.unique(got[3]).tolist() == [200]
    assert geotiff_meta(out)["count"] == 4
    assert geotiff_meta(out)["colorinterp"] == ["red", "green", "blue", "alpha"]


@needs_encoder
def test_single_band_raster_roundtrips(make_raster, read_geotiff, encode, decode_array,
                                       sample_tif):
    """A 1-band raster is padded to three channels internally; it must come back
    as one band, not three."""
    gray = read_geotiff(sample_tif)[:1]
    path = make_raster("gray.tif", gray, colorinterp=["gray"])
    for layout in LAYOUTS:
        skip_unless_supported(layout, 2)
        dec = decode_array(encode(layout, 2, src=path, tag="gray"))
        assert dec.shape == gray.shape == (1, 718, 791)
        assert int(np.abs(dec.astype(np.int32) - gray.astype(np.int32)).max()) <= 2


@needs_encoder
def test_tiled_layout_handles_four_varying_bands(make_raster, read_geotiff, sample_tif,
                                                 shared_dir):
    from owlg.tiled import write_tiled

    rgb = read_geotiff(sample_tif)
    arr = np.zeros((4, rgb.shape[1], rgb.shape[2]), np.uint8)
    arr[:3] = rgb
    arr[3] = ((rgb[0].astype(np.int32) + rgb[1]) // 2).astype(np.uint8)
    src = make_raster("rgba_varying.tif", arr)
    write_tiled(str(src), str(shared_dir / "rgba_varying.owlg"), delta=2, verbose=False)
