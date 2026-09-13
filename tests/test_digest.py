"""The streaming sha256_scan digest of the v4 (tiled) layout.

The digest is taken over the ORIGINAL pixels in tile order, so it proves
provenance -- which file this .owlg was made from -- even in near-lossless mode,
where the decoded pixels are deliberately allowed to differ.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_encoder, skip_unless_supported


@pytest.fixture(scope="session")
def other_raster(make_raster, read_geotiff, sample_tif):
    """Same shape and georeferencing as the sample, different pixels."""
    rgb = read_geotiff(sample_tif)
    arr = np.ascontiguousarray((255 - rgb.astype(np.int32)).astype(np.uint8))
    return make_raster("other.tif", arr)


@needs_encoder
def test_verify_scan_true_for_lossless_file(encode):
    skip_unless_supported("tiled", 0)
    from owlg.tiled_read import verify_scan

    ok, expected, got = verify_scan(str(encode("tiled", 0)))
    assert ok is True
    assert isinstance(expected, str) and len(expected) == 64
    assert got == expected


@needs_encoder
def test_scan_digest_matches_the_stored_value(encode):
    skip_unless_supported("tiled", 0)
    from owlg.container import open_owlg
    from owlg.tiled_read import scan_digest

    path = encode("tiled", 0)
    hdr, _get = open_owlg(str(path))
    assert hdr["sha256_scan"]
    assert scan_digest(str(path)) == hdr["sha256_scan"]


@needs_encoder
def test_verify_scan_is_undecidable_for_near_lossless(encode):
    """In near-lossless mode the decoded pixels legitimately differ, so
    verify_scan reports None rather than a false negative."""
    skip_unless_supported("tiled", 2)
    from owlg.tiled_read import verify_scan

    ok, expected, got = verify_scan(str(encode("tiled", 2)))
    assert ok is None
    assert isinstance(expected, str) and len(expected) == 64
    assert got is None


@needs_encoder
def test_source_digest_matches_the_original_for_near_lossless(sample_tif, encode):
    skip_unless_supported("tiled", 2)
    from owlg.container import open_owlg
    from owlg.tiled_read import source_digest

    hdr, _get = open_owlg(str(encode("tiled", 2)))
    assert hdr["delta"] == 2
    assert source_digest(str(sample_tif), hdr) == hdr["sha256_scan"]


@needs_encoder
def test_digest_of_a_different_raster_does_not_match(sample_tif, other_raster, encode):
    """Provenance has to be falsifiable: a file encoded from another raster must
    not validate against this one, in either direction."""
    skip_unless_supported("tiled", 2)
    from owlg.container import open_owlg
    from owlg.tiled_read import source_digest

    mine, _ = open_owlg(str(encode("tiled", 2)))
    theirs, _ = open_owlg(str(encode("tiled", 2, src=other_raster, tag="other")))

    assert mine["sha256_scan"] != theirs["sha256_scan"]
    # the other file's digest is not the digest of our original...
    assert source_digest(str(sample_tif), theirs) != theirs["sha256_scan"]
    # ...and our file's digest is not the digest of their original.
    assert source_digest(str(other_raster), mine) != mine["sha256_scan"]
    # each still matches its own source
    assert source_digest(str(other_raster), theirs) == theirs["sha256_scan"]


@needs_encoder
def test_digest_covers_every_tile(sample_tif, encode, make_raster, read_geotiff):
    """A single changed pixel in the last tile must change the digest -- otherwise
    the scan is only reading part of the raster."""
    skip_unless_supported("tiled", 2)
    from owlg.container import open_owlg
    from owlg.tiled_read import source_digest

    hdr, _get = open_owlg(str(encode("tiled", 2)))
    arr = read_geotiff(sample_tif).copy()
    arr[-1, -1, -1] = np.uint8(255 - int(arr[-1, -1, -1]))
    tweaked = make_raster("one_pixel_changed.tif", arr)

    assert source_digest(str(tweaked), hdr) != hdr["sha256_scan"]
    assert source_digest(str(sample_tif), hdr) == hdr["sha256_scan"]


@needs_encoder
def test_verify_bounds_streams_the_whole_raster(sample_tif, encode):
    """The streaming verifier and the in-memory comparison must agree."""
    skip_unless_supported("tiled", 3)
    from owlg.tiled_read import verify_bounds

    maxerr, npix = verify_bounds(str(encode("tiled", 3)), str(sample_tif))
    assert maxerr <= 3
    assert npix == 791 * 718 * 3
