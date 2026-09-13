"""`--target RATIO`: the ratio is a goal, the delta it lands on is a promise.

The search must return the SMALLEST delta on the ladder that meets the goal,
never silently exceed the goal by more than the ladder step, and the file it
produces must carry that delta and satisfy it over every pixel.
"""
from __future__ import annotations

import os

import pytest

from conftest import needs_encoder

from test_cli import _main


# ------------------------------------------------------------------ unit
def test_parse_target_accepts_the_usual_spellings():
    from owlg.target import parse_target

    assert parse_target("20") == 20.0
    assert parse_target("20x") == 20.0
    assert parse_target("20X") == 20.0
    assert parse_target("20:1") == 20.0
    assert parse_target(" 12.5x ") == 12.5


@pytest.mark.parametrize("bad", ["0", "1", "-3", "big", "20y", ""])
def test_parse_target_rejects_nonsense(bad):
    from owlg.errors import OwlgError
    from owlg.target import parse_target

    with pytest.raises(OwlgError):
        parse_target(bad)


def test_search_returns_the_smallest_delta_that_fits():
    from owlg.target import search

    # a synthetic monotone size curve: bytes = 1000 / delta
    probes = []

    def est(d):
        probes.append(d)
        return 1000.0 / d, 60

    deltas = [1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 32]
    # budget = raw/target = 100 bytes -> need delta >= 10
    d, n, q, ok = search(est, raw_bytes=2000, target=20, deltas=deltas)
    assert ok and d == 10 and n == 100.0 and q == 60
    # a binary search, not a scan
    assert len(probes) <= 5


def test_search_reports_when_the_goal_is_out_of_reach():
    from owlg.target import search

    d, n, q, ok = search(lambda d: (5000.0, 30), raw_bytes=1000, target=2, deltas=[1, 2, 4])
    assert not ok and d == 4 and n == 5000.0


# ------------------------------------------------------------------ CLI
@needs_encoder
@pytest.mark.parametrize("layout", ["flat", "tiled"])
def test_encode_target_lands_on_the_smallest_delta_and_keeps_the_bound(
        layout, sample_tif, workspace, capsys, read_geotiff):
    """Encode with --target, then prove two things: the header delta is the
    smallest on the ladder that reaches the goal (by encoding the previous rung
    and checking it falls short), and the pixels honour that delta."""
    from owlg import container
    from owlg.target import DELTAS

    out = workspace / f"target_{layout}.owlg"
    code, text = _main(capsys, ["encode", sample_tif, out, "--target", "4x",
                                "--layout", layout, "--base", "webp"])
    assert code in (None, 0), text
    assert "target 4x reached" in text
    hdr, _ = container.open_owlg(str(out))
    d = hdr["delta"]
    raw = hdr["w"] * hdr["h"] * hdr["bands"]
    assert raw / os.path.getsize(out) >= 4.0

    # the previous rung must NOT reach the goal (otherwise the search was not minimal)
    prev = [x for x in DELTAS if x < d]
    if prev:
        smaller = workspace / f"target_{layout}_prev.owlg"
        _main(capsys, ["encode", sample_tif, smaller, "--delta", prev[-1],
                       "--layout", layout, "--base", "webp"])
        assert raw / os.path.getsize(smaller) < 4.0 * 1.02   # sampling slack for tiled

    # and the bound must hold over every pixel
    code, text = _main(capsys, ["verify", out, sample_tif])
    assert code == 0 and "BOUND PROVEN" in text
