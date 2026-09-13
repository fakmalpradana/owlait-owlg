# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [4.0.0] - 2026-09-13

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
