# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Version numbers restart at 0.1.0 with the first public release: the 2.x–4.x
entries below are the pre-release development history, kept because the
format versions in file headers (`v: 3` flat, `v: 4` tiled) still refer to
them. Every file written by those versions opens unchanged in 0.1.0.

## [Unreleased]

- `owlg diff a b [--bound N]`: streaming per-band error statistics of any two rasters;
  `owlg.cli.diff_stats()` is the shared measurement behind it and the benchmarks.
- `benchmarks/bench_large.py`: streaming, resumable benchmark for rasters beyond the
  flat layout — GDAL reference formats with measured error, OWLG delta 2–32 proven by a
  full decode, OWLGT against gdal2tiles trees and MBTiles, and tile latency.
- README table D: a 169 MPixel orthophoto, delta 2–32 against JPEG, JPEG 2000 and JPEG
  XL with the error each one actually made; OWLGT size and latency tables.
- `owlg serve` encodes PNG responses at zlib level 1: 2.5x faster per tile for 4 % more
  bytes.
- VRT bridge honours GDAL's buffer size in `read_band` (fixes a QGIS hang on open); QGIS
  plugin no longer blocks on a stale `EXPECTED_VERSION`.

## [0.1.0] - 2026-09-13

First public release. The efficiency work that led here is written up in
[results/codec_experiments_2026-09.md](results/codec_experiments_2026-09.md).

### Added
- **`owlg encode --target RATIO`** (e.g. `--target 20x`): searches the
  smallest delta that makes the file that many times smaller than the raw
  pixels, then encodes with it. The ratio is the goal, the delta it lands on
  is the promise — written to the header and proven by `owlg verify` like any
  other. A drone orthophoto reaches 20x at +/-14 DN (22.5x achieved).
- Quality ladders extended downwards (AVIF q45/q30, WebP q50/q40): at
  delta >= 8 the best base is a lower quality one, and these rungs are where
  20x lives. One `QUALITY_LADDERS` table now serves both layouts.
- `--overview-q` (default 50): pyramid levels carry no bound, so they are
  encoded at a lower quality. ~35% off the pyramid, ~7% off a tiled file.
- Colour, when stdout is a terminal: progress bars for the quality search
  and tile rows, a green/red verdict in `verify`, `info` as a key/value
  sheet with a pyramid table, YES/NO in `check`, endpoints in `serve`.
  Off when piped, under `NO_COLOR`, or with `--no-color`;
  `OWLG_COLOR=always` forces it. `info --json` prints the raw header.
- `benchmarks/bench_codec_lab.py`: in-memory harness that isolates one
  encoder variable at a time and reports the smallest delta reaching 20x.
- JPEG 2000 (OpenJPEG) and JPEG XL reference rows in `bench_options.py`,
  every one with its measured max error.
- `docs/getting-started.md`: guided first hour with the CLI, the Python API
  and the npm package.

### Changed
- WebP base blobs use libwebp `method=6`: ~4% smaller, ~2x slower to
  encode, identical to read.
- Quality selection in the tiled layout now includes the correction cost at
  delta 0 too. It used to skip it there, although with a lossy base that is
  where the correction layer is the *dominant* cost, so tiled lossless files
  picked the lowest quality on the ladder and came out larger than flat ones.
- Non-uint8 input and an unknown base codec raise `OwlgError` in both layouts.
- Plugin metadata points at the GitHub repository.

### Not changed, deliberately
- The correction bitstream. Two-rate adaptation, intensity and neighbour
  contexts, and block-skip flags were prototyped and measured at -1.5% to
  +2%; closed-loop and pre-denoised bases made files larger. The coder
  already pays within a few bits of the theoretical cost of locating the
  scattered outliers it corrects. Details in the results write-up.

## [4.0.0] - 2026-09-13 (pre-release)

The release that makes 10-100 GB rasters practical, and stops a failure from
hiding itself.

### Added
- **OWLG v4, the tiled layout** (`--layout tiled`, the default above 16 MPixel).
  Each tile is an independent blob with its own base and correction stream, and
  an internal overview pyramid is built during encoding. Encode and read memory
  is now flat with respect to raster size: 0.220 GB at 4096 px square,
  0.239 GB at 16384 px square, a 16x increase in data for an 8% increase in RAM.
- **Streaming integrity digest** (`sha256_scan`). SHA-256 over a defined
  tile-scan serialization, computed as the encoder streams and recomputed the
  same way during `owlg verify`, so a 100 GB file can be verified without ever
  holding more than one tile in memory. The stored digest is of the *original*
  pixels, so `owlg verify` proves both that the error bound holds and that this
  `.owlg` really came from that GeoTIFF.
- `owlg info` reports the pyramid level by level for v4 files.
- `owlg encode --layout / --no-overviews / --min-overview` flags.
- The QGIS plugin's **Decoder diagnostics** now runs a real self-test: a 4x4
  pixel VRT with a trivial Python pixel function. If GDAL refuses that, the
  problem is GDAL, not your file.
- Layer metadata written on load names the format, version, mode, base codec and
  on-disk `.owlg` size, so the Information panel does not only show the GDAL
  bridge driver.

### Fixed
- **The GDAL option name was wrong.** The plugin registered its decoder under
  `GDAL_VRT_TRUSTED_MODULES`; GDAL reads `GDAL_VRT_PYTHON_TRUSTED_MODULES`.
  Direct reads therefore failed every single time, and the plugin quietly fell
  back to decoding the whole raster into a GeoTIFF cache. Users saw a layer
  reporting `GTiff` and wondered why. Both option names are now set.
- **A failed direct read is never silent again.** The plugin shows the cause and
  asks; a GeoTIFF copy is only made if you accept, and that layer is labelled
  `[GeoTIFF copy]`. Decoding to GeoTIFF is refused outright for rasters too
  large for it, with the reason stated.
- **A 22x decode regression.** `tiled.py` and `tiled_read.py` imported `dec_tile`
  straight from `codec.py`, getting the numba-shaped function. Without numba
  that shape runs as plain Python, roughly 60x slower, bypassing the flat
  pure-Python decoder written for exactly this case. A single dispatch point in
  `codec.py` now routes every caller. A 512 px window went from 81 s to 3.7 s
  without numba, and 140 ms with it.
- GDAL block cache is capped during encoding, so peak RSS reflects the codec
  rather than GDAL's cache. `GDAL_CACHEMAX` set by the user is still honoured.
- **Windowed reads of a flat lossless file served the bare base layer.** The
  v3 window reader (the path behind `owlg vrt` and the QGIS plugin) skipped the
  correction layer whenever the header said `mode: lossless`. A `--delta 0` file
  on a WebP base -- the default -- is described as lossless and *does* carry a
  correction layer, so QGIS in exact mode showed lossy pixels, up to 48 DN off
  on the sample, while `owlg decode` was exact. The reader now keys off
  `n_corr`, as `read_owlg` already did. `tests/test_windowed.py` covers it.

### Changed
- All user-facing output, comments and documentation are now in English.
- Licensed under AGPL-3.0-or-later.

## [3.3.0] - 2026-09-12

### Added
- Direct reads: QGIS loads the `.owlg` itself through a small `.vrt` sidecar
  with a Python pixel function, decoding only the tiles on screen. No GeoTIFF
  copy, so the disk footprint stays the size of the `.owlg`.
- Pure-Python AES-256-GCM fallback, verified bit-identical to `cryptography`,
  so encrypted files open in a QGIS Python that lacks it.
- GDAL path for writing GeoTIFF when `rasterio` is absent.
- `owlg rebase` to change the base codec while preserving the guarantee, and
  `owlg check` to probe whether an environment can actually decode a file.

### Fixed
- Capability reporting performed a nominal import check and could report
  "all ready" in an environment with no working AVIF decoder. It now performs
  a real probe decode of an embedded blob.
- The decoder is vendored as the subpackage `owlg_qgis.vendor.owlg` rather than
  a top-level `owlg`, so reinstalling the plugin cannot leave a stale copy in
  `sys.modules`.
- Windowed reads were wrong for layered files: the recovery layer's context
  reads one pixel outside the tile, so all eight neighbours must reach the
  delta stage first.

## [3.0.0] - 2026-09-12

### Added
- `.owlgt` web tile pyramid, OGC API - Tiles/Maps and WMS 1.3.0 server.
- Zero-dependency Node package with a decoder that is bit-exact against Python.
- `owlg npy` pipeline to ndarray for ML work.
- AES-256-GCM encryption of both payload and metadata.

## [2.0.0] - 2026-09-11

### Added
- The hybrid architecture: a lossy base layer plus a hard-bounded correction
  layer, giving a guaranteed per-pixel error bound that lossy codecs cannot
  offer. Binary adaptive range coder with 567 contexts.
