"""The recovery tier: split a layered .owlg into a light half plus an .owlr, and
put it back together again.

The point of the split is that you can ship the light half (near-lossless, small)
and keep the recovery half in the archive. So: join must be exact, the light half
alone must still honour its bound, and a recovery file from another parent must
be refused rather than silently produce corrupt pixels.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_encoder, skip_unless_supported

DELTA = 3


@pytest.fixture(scope="session")
def layered(encode):
    """A flat .owlg carrying both tiers (recovery is flat-layout only)."""
    skip_unless_supported("flat", DELTA)
    return encode("flat", DELTA, tag="rec", recovery=True)


@pytest.fixture(scope="session")
def other_layered(encode, make_raster, read_geotiff, sample_tif):
    rgb = read_geotiff(sample_tif)
    arr = np.ascontiguousarray((255 - rgb.astype(np.int32)).astype(np.uint8))
    src = make_raster("tiering_other.tif", arr)
    skip_unless_supported("flat", DELTA)
    return encode("flat", DELTA, src=src, tag="rec", recovery=True)


@needs_encoder
def test_layered_file_has_both_tiers(layered):
    from owlg.container import open_owlg

    hdr, _get = open_owlg(str(layered))
    assert hdr["tiers"] == ["light", "full"]
    assert hdr["n_rec"] > 0
    assert hdr["delta"] == DELTA


@needs_encoder
def test_full_tier_is_bit_identical(layered, sample_tif, read_geotiff):
    from owlg.container import read_owlg

    arr, hdr = read_owlg(str(layered), tier="full")
    orig = read_geotiff(sample_tif)
    assert arr.shape == orig.shape
    assert np.array_equal(arr, orig)
    assert hdr["sha256_match"] is True


@needs_encoder
def test_split_then_join_is_byte_identical(layered, workspace):
    from owlg.tiering import join, split

    light = workspace / "light.owlg"
    recovery = workspace / "recovery.owlr"
    rejoined = workspace / "rejoined.owlg"

    split(str(layered), str(light), str(recovery))
    assert light.is_file() and recovery.is_file()
    assert light.stat().st_size > 0 and recovery.stat().st_size > 0
    assert light.stat().st_size < layered.stat().st_size, "the light half must be smaller"
    assert recovery.read_bytes()[:4] == b"OWLR"

    join(str(light), str(recovery), str(rejoined))
    assert rejoined.read_bytes() == layered.read_bytes(), (
        "join did not reproduce the original layered file byte for byte"
    )


@needs_encoder
def test_light_half_alone_still_honours_the_bound(layered, workspace, sample_tif,
                                                  read_geotiff):
    from owlg.container import open_owlg, read_owlg
    from owlg.tiering import split

    light = workspace / "light.owlg"
    split(str(layered), str(light), str(workspace / "recovery.owlr"))

    hdr, _get = open_owlg(str(light))
    assert hdr["tiers"] == ["light"]
    assert hdr.get("n_rec", 0) == 0

    arr, _ = read_owlg(str(light))
    orig = read_geotiff(sample_tif)
    assert arr.shape == orig.shape
    assert arr.any()
    err = np.abs(orig.astype(np.int32) - arr.astype(np.int32))
    assert int(err.max()) <= DELTA
    assert int(err.max()) > 0, "the light half should be the near-lossless one"


@needs_encoder
def test_light_half_cannot_pretend_to_be_exact(layered, workspace):
    from owlg.container import read_owlg
    from owlg.tiering import split

    light = workspace / "light.owlg"
    split(str(layered), str(light), str(workspace / "recovery.owlr"))
    with pytest.raises(Exception) as exc:
        read_owlg(str(light), tier="full")
    assert "recovery" in str(exc.value).lower()


@needs_encoder
def test_rejoined_file_decodes_bit_identically(layered, workspace, sample_tif,
                                               read_geotiff):
    from owlg.container import read_owlg
    from owlg.tiering import join, split

    light = workspace / "light.owlg"
    recovery = workspace / "recovery.owlr"
    rejoined = workspace / "rejoined.owlg"
    split(str(layered), str(light), str(recovery))
    join(str(light), str(recovery), str(rejoined))

    arr, hdr = read_owlg(str(rejoined), tier="full")
    assert hdr["tiers"] == ["light", "full"]
    assert np.array_equal(arr, read_geotiff(sample_tif))


@needs_encoder
def test_recovery_from_a_different_parent_is_rejected(layered, other_layered, workspace):
    from owlg.tiering import join, split

    mine_light = workspace / "mine_light.owlg"
    mine_rec = workspace / "mine.owlr"
    theirs_light = workspace / "theirs_light.owlg"
    theirs_rec = workspace / "theirs.owlr"
    split(str(layered), str(mine_light), str(mine_rec))
    split(str(other_layered), str(theirs_light), str(theirs_rec))

    # sanity: each pair joins fine on its own
    join(str(mine_light), str(mine_rec), str(workspace / "ok.owlg"))

    with pytest.raises(Exception) as exc:
        join(str(mine_light), str(theirs_rec), str(workspace / "mismatch.owlg"))
    assert "does not belong" in str(exc.value)
    assert not (workspace / "mismatch.owlg").exists()


@needs_encoder
def test_split_refuses_a_file_without_a_recovery_tier(encode, workspace):
    from owlg.tiering import split

    skip_unless_supported("flat", DELTA)
    plain = encode("flat", DELTA)
    with pytest.raises(Exception) as exc:
        split(str(plain), str(workspace / "l.owlg"), str(workspace / "r.owlr"))
    assert "recovery" in str(exc.value).lower()


@needs_encoder
def test_join_refuses_a_file_that_is_not_an_owlr(layered, workspace):
    from owlg.tiering import join, split

    light = workspace / "light.owlg"
    split(str(layered), str(light), str(workspace / "recovery.owlr"))
    bogus = workspace / "not_recovery.owlr"
    bogus.write_bytes(b"NOPE" + b"\x00" * 64)

    with pytest.raises(Exception) as exc:
        join(str(light), str(bogus), str(workspace / "out.owlg"))
    assert ".owlr" in str(exc.value)
