"""Colour must never change the words, and must be off whenever the output is
not a terminal, so `owlg ... | grep` and CI logs see plain text."""
from __future__ import annotations

import pytest

from owlg import _term as T


def test_piped_output_has_no_escape_codes(monkeypatch, capsys):
    monkeypatch.delenv("OWLG_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert T.enabled() is False                 # pytest's capture is not a TTY
    assert T.ok("BOUND PROVEN") == "BOUND PROVEN"
    assert "\033" not in T.table([["a", "1"]], header=["k", "v"])


def test_owlg_color_always_forces_codes_and_no_color_wins(monkeypatch):
    monkeypatch.setenv("OWLG_COLOR", "always")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert T.enabled() is True
    styled = T.bad("x")
    assert styled.startswith("\033[") and styled.endswith("\033[0m") and "x" in styled
    monkeypatch.setenv("OWLG_COLOR", "never")
    assert T.enabled() is False
    monkeypatch.delenv("OWLG_COLOR")
    monkeypatch.setenv("NO_COLOR", "1")
    assert T.enabled() is False


def test_table_aligns_on_visible_width(monkeypatch):
    monkeypatch.setenv("OWLG_COLOR", "always")
    rows = [[T.ok("short"), "1"], ["a much longer cell", "22"]]
    lines = T.table(rows, align="lr").splitlines()
    # both rows end at the same column once escape codes are ignored
    assert len(T._plain(lines[0])) == len(T._plain(lines[1]))


def test_cli_no_color_flag(monkeypatch, capsys):
    monkeypatch.setenv("OWLG_COLOR", "always")
    from owlg import cli

    with pytest.raises(SystemExit):
        cli.main(["--no-color", "check"])
    out = capsys.readouterr().out
    assert "\033" not in out and "real decode probes" in out
