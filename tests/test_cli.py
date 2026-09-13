"""The command line, driven in-process through owlg.cli.main().

`verify` and `check` end with sys.exit(), so their contract is the exit code:
0 = the bound is proven over every pixel, 2 = it is violated. Both the code and
the human-readable verdict are asserted, because the exit code is what CI would
gate on and the text is what a user reads.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import LAYOUTS, needs_encoder, skip_unless_supported


def _main(capsys, argv):
    """Run owlg.cli.main and return (exit_code_or_None, stdout)."""
    from owlg import cli

    code = None
    try:
        cli.main([str(a) for a in argv])
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr().out


# ------------------------------------------------------------- encode / decode
@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", (0, 2))
def test_encode_then_decode_respects_the_bound(layout, delta, sample_tif, workspace,
                                               capsys, read_geotiff):
    skip_unless_supported(layout, delta)
    owlg = workspace / f"{layout}_{delta}.owlg"
    tif = workspace / f"{layout}_{delta}.tif"

    code, out = _main(capsys, ["encode", sample_tif, owlg, "--delta", delta,
                               "--layout", layout])
    assert code is None
    assert owlg.is_file() and owlg.stat().st_size > 0
    assert owlg.stat().st_size < sample_tif.stat().st_size

    code, out = _main(capsys, ["decode", owlg, tif])
    assert code is None
    assert str(tif) in out
    assert tif.is_file()

    orig = read_geotiff(sample_tif)
    got = read_geotiff(tif)
    assert got.shape == orig.shape
    assert int(np.abs(orig.astype(np.int32) - got.astype(np.int32)).max()) <= delta


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_decode_fast_skips_the_correction_layer(layout, sample_tif, workspace, capsys,
                                                read_geotiff):
    skip_unless_supported(layout, 3)
    owlg = workspace / "fast.owlg"
    tif = workspace / "fast.tif"
    _main(capsys, ["encode", sample_tif, owlg, "--delta", 3, "--layout", layout])
    code, _out = _main(capsys, ["decode", owlg, tif, "--fast"])
    assert code is None
    got = read_geotiff(tif)
    assert got.shape == (3, 718, 791)
    assert got.any()


@needs_encoder
def test_decode_check_sha_passes_for_a_lossless_file(sample_tif, workspace, capsys):
    skip_unless_supported("flat", 0)
    owlg = workspace / "loss0.owlg"
    _main(capsys, ["encode", sample_tif, owlg, "--delta", 0, "--layout", "flat"])
    code, _out = _main(capsys, ["decode", owlg, workspace / "loss0.tif", "--check-sha"])
    assert code is None


# -------------------------------------------------------------------- verify
@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("delta", (0, 2))
def test_verify_proves_the_bound(layout, delta, sample_tif, encode, capsys):
    skip_unless_supported(layout, delta)
    code, out = _main(capsys, ["verify", encode(layout, delta), sample_tif])

    assert code == 0, out
    assert "BOUND PROVEN" in out
    assert "VIOLATED" not in out
    assert f"bound +/-{delta}" in out
    assert "shape  : same" in out
    assert "transform same" in out and "crs same" in out


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_verify_says_bit_identical_for_delta_zero(layout, sample_tif, encode, capsys):
    skip_unless_supported(layout, 0)
    code, out = _main(capsys, ["verify", encode(layout, 0), sample_tif])
    assert code == 0
    assert "bit-identical" in out
    assert "BOUND PROVEN" in out


@needs_encoder
def test_verify_reports_the_source_digest_for_a_tiled_file(sample_tif, encode, capsys):
    skip_unless_supported("tiled", 2)
    code, out = _main(capsys, ["verify", encode("tiled", 2), sample_tif])
    assert code == 0
    assert "digest : original pixels MATCH" in out
    assert "MPixel" in out and "samples checked" in out


@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_verify_exits_2_when_the_original_is_a_different_raster(layout, encode, capsys,
                                                                make_raster, read_geotiff,
                                                                sample_tif):
    skip_unless_supported(layout, 0)
    rgb = read_geotiff(sample_tif)
    other = make_raster("cli_other.tif",
                        np.ascontiguousarray((255 - rgb.astype(np.int32)).astype(np.uint8)))
    code, out = _main(capsys, ["verify", encode(layout, 0), other])
    assert code == 2, out
    assert "*** VIOLATED ***" in out
    assert "BOUND PROVEN" not in out


# ---------------------------------------------------------------------- info
@needs_encoder
def test_info_flat(sample_tif, encode, capsys):
    skip_unless_supported("flat", 2)
    code, out = _main(capsys, ["info", encode("flat", 2)])
    assert code is None
    assert "OWLG v3" in out and "flat" in out
    assert "791 x 718 x 3" in out
    assert "bound +/-2 DN" in out
    assert "encrypted      : no" in out
    assert "base layer" in out and "correction" in out


@needs_encoder
def test_info_json_prints_the_raw_header(sample_tif, encode, capsys):
    import json

    skip_unless_supported("flat", 2)
    code, out = _main(capsys, ["info", encode("flat", 2), "--json"])
    assert code is None
    hdr = json.loads(out)
    assert hdr["v"] == 3 and hdr["w"] == 791 and hdr["h"] == 718
    assert hdr["bands"] == 3 and hdr["delta"] == 2 and hdr["mode"] == "nearlossless"
    assert "dir" not in hdr


@needs_encoder
def test_info_tiled_shows_the_pyramid(sample_tif, encode, capsys):
    skip_unless_supported("tiled", 2)
    code, out = _main(capsys, ["info", encode("tiled", 2)])
    assert code is None
    assert "pyramid" in out
    assert "level 0  791 x 718" in out
    assert "512 px" in out
    assert "tile digest    : stored" in out
    assert "base + correction" in out and "base only" in out
    assert "bound +/-2 DN" in out


@needs_encoder
def test_info_tiled_lossless_promises_bit_identical(encode, capsys):
    skip_unless_supported("tiled", 0)
    code, out = _main(capsys, ["info", encode("tiled", 0)])
    assert code is None
    assert "LOSSLESS" in out and "bit-identical" in out


# ----------------------------------------------------------------------- vrt
@needs_encoder
@pytest.mark.parametrize("layout", LAYOUTS)
def test_vrt_sidecar_is_written(layout, encode, workspace, capsys):
    skip_unless_supported(layout, 2)
    vrt = workspace / f"{layout}.vrt"
    code, out = _main(capsys, ["vrt", encode(layout, 2), vrt])
    assert code is None
    assert str(vrt) in out
    assert "no GeoTIFF copy" in out

    xml = vrt.read_text()
    assert xml.startswith("<VRTDataset")
    assert 'rasterXSize="791"' in xml and 'rasterYSize="718"' in xml
    assert xml.count("<VRTRasterBand") == 3
    assert "owlg.vrt.read_band" in xml
    assert "<PixelFunctionLanguage>Python</PixelFunctionLanguage>" in xml

    import xml.etree.ElementTree as ET

    ET.fromstring(xml)  # must be well-formed XML


@needs_encoder
def test_vrt_exposes_the_tiled_pyramid_as_overviews(encode, workspace, capsys):
    skip_unless_supported("tiled", 2)
    vrt = workspace / "ov.vrt"
    _main(capsys, ["vrt", encode("tiled", 2), vrt])
    xml = vrt.read_text()
    assert "<Overview>" in xml
    assert (workspace / "ov.vrt.ov1.vrt").is_file()


# ----------------------------------------------------------------------- npy
@needs_encoder
@pytest.mark.parametrize("fmt,layout_opt", [("npy", "CHW"), ("npy", "HWC"),
                                            ("npz", "CHW"), ("memmap", "CHW")])
def test_npy_export(fmt, layout_opt, encode, workspace, capsys, decode_array):
    skip_unless_supported("tiled", 2)
    src = encode("tiled", 2)
    out = workspace / f"export_{fmt}_{layout_opt}.{'npz' if fmt == 'npz' else 'npy'}"
    code, text = _main(capsys, ["npy", src, out, "--format", fmt,
                                "--layout", layout_opt])
    assert code is None
    expected = "(3, 718, 791)" if layout_opt == "CHW" else "(718, 791, 3)"
    assert f"shape={expected}" in text, text
    assert f"layout={layout_opt}" in text
    assert out.is_file()

    ref = decode_array(src)
    if fmt == "npz":
        with np.load(str(out), allow_pickle=False) as z:
            got = z["image"]
    else:
        got = np.load(str(out), mmap_mode="r" if fmt == "memmap" else None)
    want = ref if layout_opt == "CHW" else np.ascontiguousarray(ref.transpose(1, 2, 0))
    assert np.array_equal(np.asarray(got), want)


# --------------------------------------------------------------------- check
@needs_encoder
def test_check_reports_this_environment(capsys):
    code, out = _main(capsys, ["check"])
    assert code in (0, 2)
    assert "image backends" in out
    assert "GeoTIFF writer" in out
    assert "real decode probes:" in out
    for codec in ("avif", "webp", "jxl"):
        assert codec in out


@needs_encoder
def test_check_reports_whether_a_specific_file_can_be_opened(encode, capsys):
    skip_unless_supported("tiled", 2)
    code, out = _main(capsys, ["check", encode("tiled", 2)])
    assert code == 0
    assert "uses base webp" in out
    assert "CAN be opened here" in out


# ------------------------------------------------------------------ arguments
def test_cli_requires_a_subcommand():
    from owlg import cli

    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


@needs_encoder
def test_recovery_is_refused_for_the_tiled_layout(sample_tif, workspace):
    from owlg import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["encode", str(sample_tif), str(workspace / "x.owlg"),
                  "--layout", "tiled", "--recovery"])
    assert "--recovery" in str(exc.value.code)
