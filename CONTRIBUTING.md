# Contributing

## Setup

```bash
git clone https://github.com/fakmalpradana/owlait-owlg.git
cd owlait-owlg
pip install -e .[dev]
pytest -q
```

## The one rule

This format makes exactly one promise: for `--delta d`, every pixel of every band
satisfies `|original - decoded| <= d`. Any change that touches the codec, the container,
band handling or the tiled layout must come with a test that asserts that bound **over
every pixel**, not over a sample. `tests/test_bounds.py` and `tests/test_bands.py` show
the pattern.

Bugs found in this codebase have mostly been silent ones — a band quietly replaced by a
copy of another, a cache serving decrypted pixels to the wrong passphrase, a failed code
path falling back to a different one without saying so. Tests that only check "it ran
without raising" would have passed for all of them. Prefer assertions on the actual
values.

## Before opening a pull request

- `pytest -q` passes (200+ tests).
- New user-facing behaviour is documented in `docs/`, and any CLI change is reflected in
  `docs/cli.md` with output captured from a real run rather than written from memory.
- If you changed anything affecting file size or speed, re-run
  `python benchmarks/bench_options.py samples/rgb_small.tif -o results/rgb_small` and
  commit the regenerated tables. Every number in the README comes from that script.
- If you changed anything under `src/owlg/`, re-vendor the QGIS plugin copy:
  `python scripts/build_all.py`. The vendored tree is generated, never edited by hand.

## On-disk compatibility

Changing the byte layout of an existing version is a breaking change. Either keep the
reader able to open both shapes (the tiled reader accepts a bare integer where it now
expects a list of blob indices, for exactly this reason), or bump the version and say so
in `CHANGELOG.md`.

## Reporting a bug

A reproducer beats a description. If it involves a raster, the band count, dtype, whether
any band is constant, and the `--delta` and `--layout` you used are usually enough to
reconstruct it — a file is rarely needed, and please do not attach imagery you cannot
share publicly.

## Licence

Contributions are accepted under AGPL-3.0-or-later, the licence of the project.
