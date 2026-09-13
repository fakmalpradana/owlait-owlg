# OWLG Python API reference

The Python package is the reference implementation: it encodes, decodes, verifies and
serves OWLG, and everything the [command line](cli.md) does is a thin wrapper over the
functions below. There are three layers to it — the container functions
(`write_owlg`/`read_owlg`/…) which move whole rasters in and out of memory, the tiled v4
reader (`TiledReader`) which reads windows out of a file of any size in bounded memory,
and a set of bridges to the rest of the ecosystem (GDAL via `.vrt`, NumPy and PyTorch via
`owlg.ml`). Every signature, return value and snippet on this page was executed against
`samples/rgb_small.tif` (791 x 718 x 3 uint8, EPSG:32618) and the outputs are pasted as
they came back. See also the [CLI reference](cli.md), the
[JavaScript/Node API](node-api.md), and the [project README](../README.md).

## Contents

- [Installation and imports](#installation-and-imports)
- [Array conventions](#array-conventions)
- [Core container API](#core-container-api)
  - [`write_owlg`](#write_owlg) · [`read_owlg`](#read_owlg) · [`open_owlg`](#open_owlg) · [`info`](#info) · [`to_tif`](#to_tif)
  - [`OwlgError` and `NeedKey`](#owlgerror-and-needkey)
- [Tiled encoding: `owlg.tiled.write_tiled`](#tiled-encoding-owlgtiledwrite_tiled)
- [Bounded-memory reading: `owlg.tiled_read`](#bounded-memory-reading-owlgtiled_read)
  - [`TiledReader`](#tiledreader) · [`reader`](#reader) · [`is_v4`](#is_v4)
- [Integrity and verification](#integrity-and-verification)
- [Windowed reads: `owlg.container.read_window`](#windowed-reads-owlgcontainerread_window)
- [GDAL bridge: `owlg.vrt.make_vrt`](#gdal-bridge-owlgvrtmake_vrt)
- [The ML bridge: `owlg.ml`](#the-ml-bridge-owlgml)
- [Tiering and rebasing](#tiering-and-rebasing)
- [Environment capabilities: `owlg.imgio`](#environment-capabilities-owlgimgio)
- [Worked example: the memory-bounded read pattern](#worked-example-the-memory-bounded-read-pattern)
- [Worked example: the ML pipeline](#worked-example-the-ml-pipeline)

## Installation and imports

```
pip install owlg[full]
```

`owlg` alone pulls in only NumPy, which is enough to *read* a file when an image codec is
already present. The extras map to capabilities: `codecs` (imagecodecs), `encode`
(rasterio + imagecodecs, required to write anything), `crypto` (cryptography, for
AES-256-GCM), `fast` (numba), `pillow`, and `full` for all of them.

The package root re-exports only the container functions:

```python
>>> import owlg
>>> owlg.__version__
'4.0.0'
>>> [n for n in dir(owlg) if not n.startswith('_')]
['OwlgError', 'codec', 'container', 'crypto', 'imgio', 'info', 'open_owlg', 'rc',
 'read_owlg', 'to_tif', 'write_owlg']
```

Everything else is reached through its submodule — `owlg.tiled`, `owlg.tiled_read`,
`owlg.ml`, `owlg.vrt`, `owlg.tiering`, `owlg.rebase`, `owlg.imgio`.

> `NeedKey` is **not** re-exported from the package root, despite being the exception you
> most want to catch. Import it as `from owlg.container import NeedKey`.

## Array conventions

Unless a function explicitly says otherwise, **every raster array in this API is
band-first `(bands, height, width)` and `uint8`.** This matches rasterio's `ds.read()` and
PyTorch's CHW convention. OWLG v3 and v4 both handle uint8 only; `write_owlg` and
`write_tiled` raise on any other dtype.

Two deliberate exceptions:

- `owlg.ml` functions accept `layout='HWC'`, which returns `(height, width, bands)`.
- Window reads (`TiledReader.read_window`, `owlg.container.read_window`) return a
  **single band** as `(ysize, xsize)`, because a window is per-band by construction.

Constant bands (a band where every sample is the same value, typically an all-opaque
alpha) are not stored as pixels at all; they are recorded in the header's `const` map and
re-materialised on read, so the array you get back always has the full band count.

## Core container API

### `write_owlg`

```python
owlg.write_owlg(src, dst, delta=0, codec='auto', q=None, tile=1024, base=None,
                password=None, kdf_iters=600000, recovery=False, verbose=True)
```

Encode a uint8 GeoTIFF into a **flat (v3)** `.owlg`. This is the layout that holds one
base image for the whole raster; for anything above roughly 16 MPixel use
[`write_tiled`](#tiled-encoding-owlgtiledwrite_tiled) instead.

| Parameter | Meaning |
| --- | --- |
| `src` | Path to the source GeoTIFF, opened with rasterio. CRS, transform, nodata, colour interpretation and tags are copied into the header. |
| `dst` | Output path. |
| `delta` | Hard per-pixel bound in DN. `0` selects the lossless path (JPEG XL lossless base, no correction layer). |
| `base` | `'webp'` (default), `'avif'` or `'auto'`. Ignored when `delta == 0`. |
| `q` | Base-layer quality. `None` searches a quality ladder and keeps the smallest total. |
| `codec` | Legacy. Anything other than `'auto'` forces exactly one (codec, q) pair and requires `q` to be set. |
| `tile` | Correction-layer tile edge in pixels. |
| `password` | Passphrase; enables AES-256-GCM over every blob and the header. |
| `kdf_iters` | PBKDF2-SHA256 iterations. |
| `recovery` | Add a recovery tier so a near-lossless file can still revert bit-identically. |
| `verbose` | Print progress to stdout. |

Returns `dict` with `total` (file bytes), `ratio` (raw / total), `base` (base-layer bytes)
and `corr` (correction-layer bytes).

```python
import owlg
stats = owlg.write_owlg("rgb_small.tif", "api.owlg", delta=2, base="webp", verbose=False)
print(stats)
```

```
{'total': 352830, 'ratio': 4.828994133151943, 'base': 135946, 'corr': 215652}
```

### `read_owlg`

```python
owlg.read_owlg(path, password=None, fast=False, tier='auto', verify_sha=False)
```

Decode a whole `.owlg` — v3 or v4, the function dispatches on the magic — into memory.

| Parameter | Meaning |
| --- | --- |
| `path` | Input `.owlg`. |
| `password` | Passphrase for an encrypted file. |
| `fast` | Base layer only; skips the correction stream and steps outside the error bound. |
| `tier` | `'auto'` uses the recovery tier when present, `'light'` stops at the near-lossless reconstruction, `'full'` demands a bit-identical result and raises `OwlgError` if there is no recovery tier. |
| `verify_sha` | Raise `OwlgError` unless the decoded pixels hash to the SHA-256 stored at encode time. |

Returns `(array, hdr)` where `array` is `(bands, height, width)` uint8 and `hdr` is the
header dict, augmented with `encrypted` and — when a digest is stored — `sha256_match`.

```python
arr, hdr = owlg.read_owlg("api.owlg")
print(arr.shape, arr.dtype, "| delta", hdr["delta"], "| codec", hdr["codec"],
      "| sha256_match", hdr["sha256_match"])
```

```
(3, 718, 791) uint8 | delta 2 | codec webp | sha256_match False
```

`sha256_match` is `False` here and that is correct: the file is near-lossless at ±2 DN, so
the decoded pixels are deliberately not the original pixels. On a `delta=0` file it is
`True`.

This function loads the entire raster. For large files, read windows through
[`TiledReader`](#tiledreader) instead.

### `open_owlg`

```python
owlg.open_owlg(path, password=None)
```

Read the header **without decoding any pixels**, and get a callable for raw blob access.
Returns `(hdr, getblob)` where `getblob(i) -> bytes` returns blob `i`, decrypted if
necessary. This is the cheap way to ask a question about a file — dimensions, band count,
delta, codec, whether it is encrypted, how the bytes are split across tiers.

For a v3 file the header carries `n_base` / `n_corr` / `n_rec` blob counts and a `dir`
list of `[offset, length]` pairs. For a v4 file it carries `levels` instead, and the three
counts are present but zero.

```python
hdr, getblob = owlg.open_owlg("api.owlg")
print("w,h,bands:", hdr["w"], hdr["h"], hdr["bands"],
      "| n_base", hdr["n_base"], "| n_corr", hdr["n_corr"])
print("blob 0   :", len(getblob(0)), "bytes (the base image)")
```

```
w,h,bands: 791 718 3 | n_base 1 | n_corr 1
blob 0   : 135946 bytes (the base image)
```

### `info`

```python
owlg.info(path, password=None)
```

Header plus a two-way byte split. Returns `(hdr, base_bytes, corr_bytes)`, where
`base_bytes` is the total size of the base blobs and `corr_bytes` is everything after them
(correction plus recovery).

```python
hdr, base_bytes, corr_bytes = owlg.info("api.owlg")
print("base", base_bytes, "corr", corr_bytes, "| q", hdr["q"], "| mode", hdr["mode"])
```

```
base 135946 corr 215652 | q 90 | mode nearlossless
```

### `to_tif`

```python
owlg.to_tif(src, dst, password=None, fast=False, tier='auto', verify_sha=False)
```

Decode straight to a GeoTIFF, restoring CRS, transform, colour interpretation and tags.
Same decoding parameters as `read_owlg`. Returns `dst`.

```python
print(owlg.to_tif("api.owlg", "api.tif"))
```

```
api.tif
```

### `OwlgError` and `NeedKey`

`OwlgError` is the base class for everything this package raises deliberately;
`NeedKey(OwlgError)` is the specific case of an encrypted file opened with no passphrase.
Catching `OwlgError` catches both.

```python
from owlg import open_owlg, OwlgError
from owlg.container import NeedKey          # NOT re-exported from the package root

for path, pw in (("secret.owlg", None), ("secret.owlg", "wrong"), ("rgb_small.tif", None)):
    try:
        open_owlg(path, pw)
    except NeedKey as e:
        print(f"{path!r:16} pw={pw!r:8} -> NeedKey: {e}")
    except OwlgError as e:
        print(f"{path!r:16} pw={pw!r:8} -> OwlgError: {e}")
```

```
'secret.owlg'    pw=None     -> NeedKey: encrypted file: a passphrase is required
'secret.owlg'    pw='wrong'  -> OwlgError: wrong passphrase, or the file is corrupt
'rgb_small.tif'  pw=None     -> OwlgError: not an OWLG file
```

The distinction matters in an application: `NeedKey` means "ask the user for a
passphrase", every other `OwlgError` means "this file will not open".

`TiledReader` is the exception to this scheme — it raises plain `ValueError` for the same
conditions, with these exact messages:

```
TiledReader("secret_v4.owlg")            ValueError: encrypted file: a passphrase is required
TiledReader("secret_v4.owlg", "wrong")   ValueError: wrong passphrase, or the file is corrupt
TiledReader("rgb_small.tif")             ValueError: not an OWLG file
TiledReader("flat_v3.owlg")              ValueError: not OWLG v4 (version 3)
```

The last one is the one to watch for: `TiledReader` handles v4 files only. Branch on
[`is_v4`](#is_v4) first, or use [`owlg.container.read_window`](#windowed-reads-owlgcontainerread_window),
which dispatches for you.

## Tiled encoding: `owlg.tiled.write_tiled`

```python
owlg.tiled.write_tiled(src, dst, delta=0, base='webp', q=None, tile=512,
                       overviews=True, min_overview=256, password=None,
                       kdf_iters=600000, verbose=True, gdal_cache_mb=256)
```

Encode into the **tiled (v4)** layout. Each tile is an independent blob with its own base
and correction stream, an overview pyramid is built inside the file, and the source is
read tile by tile so RAM never holds the whole raster. This is the layout for rasters in
the tens or hundreds of GB.

| Parameter | Meaning |
| --- | --- |
| `src`, `dst` | Source GeoTIFF and output path. |
| `delta` | Hard per-pixel bound in DN; `0` is lossless (a lossy base plus a correction stream that restores every sample exactly). |
| `base` | `'webp'` or `'avif'`. **`'auto'` is not supported here** and fails with `ValueError: auto`. |
| `q` | Base quality; `None` samples up to six scattered tiles and picks the best from a ladder. |
| `tile` | Tile edge in pixels, default 512. |
| `overviews` | Build the internal pyramid. |
| `min_overview` | Stop halving once the level's longest edge is at or below this. The last level produced is therefore the first at or below the threshold. |
| `password`, `kdf_iters` | AES-256-GCM as for `write_owlg`. |
| `gdal_cache_mb` | Caps `GDAL_CACHEMAX` (if not already set in the environment) so GDAL's own cache does not defeat the streaming design. |

Returns `dict` with `total`, `ratio` and `levels` (the number of pyramid levels, level 0
included).

```python
from owlg.tiled import write_tiled
print(write_tiled("rgb_small.tif", "t.owlg", delta=0, tile=256, base="webp", verbose=False))
```

```
{'total': 745852, 'ratio': 2.284386178491175, 'levels': 3}
```

Only level 0 carries the error guarantee. Levels 1 and up are 2x2 box averages of the
already-corrected data, stored base-only, for display when zoomed out.

## Bounded-memory reading: `owlg.tiled_read`

### `TiledReader`

```python
owlg.tiled_read.TiledReader(path, password=None, fast=False, max_tiles=48)
```

An open handle on a v4 file that decodes only the tiles a read actually touches and holds
them in an LRU cache bounded by **tile count**, not by raster size. Opening a 100 GB file
costs the same RAM as opening a 100 MB one. Raises `ValueError` if the file is not OWLG v4.

Useful attributes: `hdr` (the header dict), `tile` (tile edge), `levels` (list of level
descriptors with `w`, `h`, `nty`, `ntx`), `cache` (the `OrderedDict` of decoded tiles),
`max_tiles`, `key` (`None` when the file is not encrypted).

#### `read_window(band, xoff, yoff, xsize, ysize, level=0)`

Read a rectangle from one band. `band` is zero-based. The window is clamped to the level's
extent, so an oversized request returns what exists rather than raising. Returns a
`(ysize, xsize)` uint8 array — note the shape is row-major, height first.

A band listed in the header's `const` map returns a filled array without touching any
blob; a band that is neither coded nor constant returns zeros.

#### `read_all(level=0)`

Materialise a whole level as `(bands, height, width)` uint8. Convenient, but it defeats
the point of the class on a large file — the result is the full raster in memory.

#### `close()`

Close the underlying file handle. Safe to call more than once.

```python
from owlg.tiled_read import TiledReader

r = TiledReader("t.owlg", max_tiles=16)
print("levels:", [(l["w"], l["h"]) for l in r.levels])
print("window:", r.read_window(0, 300, 200, 256, 128).shape)
print("all(0):", r.read_all(0).shape)
print("all(1):", r.read_all(1).shape)
r.close()
```

```
levels: [(791, 718), (396, 359), (198, 180)]
window: (128, 256)
all(0): (3, 718, 791)
all(1): (3, 359, 396)
```

### `reader`

```python
owlg.tiled_read.reader(path, password=None, fast=False)
```

Return a cached `TiledReader` for `(abspath, mtime, fast, has_password)`. Asking for a
different file evicts and closes the previous one, so the cache holds exactly one reader.
This is what the GDAL pixel function uses, so that a pan across a QGIS canvas does not
reopen and re-parse the file for every block.

```python
from owlg.tiled_read import reader
print(reader("t.owlg") is reader("t.owlg"))
```

```
True
```

Do not `close()` a reader you obtained this way unless you are finished with the file
entirely; the next caller will get the closed object.

### `is_v4`

```python
owlg.tiled_read.is_v4(path)
```

`True` if the first six bytes are the OWLG magic with version 4. Never raises — a missing
or unreadable file returns `False`. Use it to branch between the tiled reader and the flat
container path.

```python
from owlg.tiled_read import is_v4
print("t.owlg  ->", is_v4("t.owlg"), "| rgb.owlg ->", is_v4("rgb.owlg"))
```

```
t.owlg  -> True | rgb.owlg -> False
```

## Integrity and verification

A v4 file stores `sha256_scan`: a SHA-256 over a defined tile-order serialisation of the
**original** pixels, computed while the encoder streams. Three functions recompute it from
three different starting points, and comparing them answers different questions.

### `scan_digest(path, password=None)`

Digest of the **decoded** pixels, computed tile by tile without ever holding the whole
raster. Returns a hex string.

### `source_digest(orig, hdr)`

Digest recomputed from an **original GeoTIFF**, in the same tile order, using `hdr` (from
`open_owlg` or `TiledReader.hdr`) for the geometry. Requires rasterio. Matching the stored
digest proves that this `.owlg` really was made from that GeoTIFF — and that holds in
near-lossless mode too, where the decoded pixels are legitimately allowed to differ.

### `verify_scan(path, password=None)`

Returns `(ok, expected, got)`. The stored digest is of the *original* pixels, so comparing
it against the decoded result is only meaningful in lossless mode. On a near-lossless file
`ok` is `None` and `got` is `None` — that is not a failure, it means "use `source_digest`
against the original instead".

### `verify_bounds(path, orig, password=None, progress=None)`

Stream the `.owlg` and the original GeoTIFF tile by tile and return `(maxerr, nsamples)`.
`maxerr` is the largest absolute difference in DN found anywhere; the bound holds exactly
when `maxerr <= hdr['delta']`. `nsamples` counts band samples, not pixels. `progress`, if
given, is called as `progress(tile_row_index, total_tile_rows)`.

```python
from owlg.tiled_read import (TiledReader, scan_digest, source_digest,
                             verify_scan, verify_bounds)

hdr = TiledReader("t.owlg").hdr
print("stored  :", hdr["sha256_scan"][:32], "...")
print("decoded :", scan_digest("t.owlg")[:32], "...")
print("original:", source_digest("rgb_small.tif", hdr)[:32], "...")

ok, exp, got = verify_scan("t.owlg")
print("verify_scan ok =", ok, "| equal =", exp == got)

maxerr, nsamples = verify_bounds("t.owlg", "rgb_small.tif")
print("maxerr =", maxerr, "| samples compared =", nsamples)
```

```
stored  : fabf8a3c954571423e86f1b2e03e62b2 ...
decoded : fabf8a3c954571423e86f1b2e03e62b2 ...
original: fabf8a3c954571423e86f1b2e03e62b2 ...
verify_scan ok = True | equal = True
maxerr = 0 | samples compared = 1703814
```

On a near-lossless v4 file, `verify_scan` reports the `None` case:

```python
>>> verify_scan("t_delta3.owlg")
(None, 'fabf8a3c954571423e86f1b2e03e62b20c0dee1cf843f8c139112d9779fd75fa', None)
```

## Windowed reads: `owlg.container.read_window`

```python
owlg.container.read_window(path, band, xoff, yoff, xsize, ysize,
                           password=None, fast=False, level=0)
```

The layout-agnostic window read. For a v4 file it delegates to the cached `TiledReader`;
for a v3 file it opens the file once, keeps the decoded base layer, and applies correction
tiles only for the tiles a window actually intersects — so a QGIS pan decodes what is on
screen rather than the whole raster. Returns `(ysize, xsize)` uint8 for one band.

`level` selects a pyramid level and is meaningful only for v4 files; a v3 file ignores it.

```python
from owlg.container import read_window
print("flat v3 :", read_window("rgb.owlg", 1, 200, 150, 64, 32).shape)
print("tiled v4:", read_window("t.owlg", 1, 200, 150, 64, 32, level=0).shape)
print("level 1 :", read_window("t.owlg", 1, 100, 75, 64, 32, level=1).shape)
```

```
flat v3 : (32, 64)
tiled v4: (32, 64)
level 1 : (32, 64)
```

Note that the v3 path caches the decoded base layer for the whole raster, so it is bounded
by raster size, not by window size. Only the v4 path is genuinely constant-RAM.

## GDAL bridge: `owlg.vrt.make_vrt`

```python
owlg.vrt.make_vrt(owlg_path, vrt_path=None, password=None, fast=False, blocksize=None)
```

Write a `.vrt` sidecar whose bands are VRT derived bands backed by a Python pixel function
that calls `read_window`. Every GDAL-based application then opens the `.owlg` directly —
no GeoTIFF copy, so the disk footprint stays the size of the `.owlg` plus a few kilobytes.

| Parameter | Meaning |
| --- | --- |
| `owlg_path` | Input `.owlg`. Stored in the VRT as an absolute path. |
| `vrt_path` | Output path; defaults to `<owlg_path>.vrt`. |
| `password` | Passphrase; also read from `OWLG_KEY` if not given. |
| `fast` | Sidecar reads the base layer only. |
| `blocksize` | Block edge; defaults to the file's tile size. |

Returns the sidecar path. For a v4 file with more than one level, one extra sidecar per
overview level is written next to the main one and referenced from it as a GDAL overview.

```python
import os
from owlg.vrt import make_vrt

p = make_vrt("rgb.owlg", "rgb.vrt")
print(os.path.basename(p), os.path.getsize(p), "bytes")

p2 = make_vrt("t.owlg")
print(os.path.basename(p2), "+",
      sorted(f for f in os.listdir(".") if f.startswith("t.owlg.vrt.ov")))
```

```
rgb.vrt 2565 bytes
t.owlg.vrt + ['t.owlg.vrt.ov1.vrt', 't.owlg.vrt.ov2.vrt']
```

GDAL will only execute the pixel function when it is allowed to:

```
export GDAL_VRT_ENABLE_PYTHON=YES
export GDAL_VRT_PYTHON_TRUSTED_MODULES=owlg.vrt
```

and the Python interpreter embedded in that GDAL build must be able to `import owlg`.
Reading the VRT's metadata works without either. `owlg.vrt.selftest_fn` is a trivial pixel
function that fills the buffer with `42`; point GDAL at a VRT using it to establish
whether GDAL will run Python at all, before blaming the `.owlg`.

## The ML bridge: `owlg.ml`

Three tiers, matched to how big the data is: load it all (`to_array`, `to_npy`, `to_npz`),
memory-map it (`to_memmap`), or iterate windows (`patches`, `OwlgDataset`).

Every function here takes `password` and `fast`, with the meanings they have in
`read_owlg`.

### `to_array(path, password=None, fast=False, layout='CHW')`

Returns `(array, meta)`. `layout='CHW'` gives `(bands, height, width)`; `'HWC'` gives
`(height, width, bands)`. `meta` is a plain dict with `width`, `height`, `bands`, `dtype`,
`crs` (WKT), `transform` (six elements), `nodata`, `delta`, `colorinterp` and `layout` —
JSON-serialisable, so it survives a trip through a dataset manifest.

### `to_npy(path, out, password=None, layout='CHW', fast=False)`

Write a `.npy` plus a sidecar `<out stem>.meta.json`. Returns `(out, meta)`.

### `to_npz(path, out, password=None, layout='CHW', compressed=True, fast=False)`

Write a `.npz` holding `image` and `meta` (the metadata as a JSON string). No sidecar file.
Returns `(out, meta)`.

### `to_memmap(path, out, password=None, layout='CHW', fast=False)`

Write a `.npy` in a form that `np.load(out, mmap_mode='r')` opens without reading it into
RAM, plus the same `.meta.json` sidecar. Returns `(out, meta)`. Note that *writing* it
still decodes the whole raster into memory first; the benefit is on the read side.

### `patch_grid(path, size=256, stride=None, password=None)`

Count the patches **without decoding any pixels** — header read only. Returns
`(count, (rows, cols))`. `stride` defaults to `size` (non-overlapping).

### `patches(path, size=256, stride=None, password=None, bands=None, drop_partial=True, fast=False, skip_empty_alpha=True)`

Generator of `(patch, (row, col))`. `patch` is `(bands, size, size)` uint8 and C-contiguous;
`row`/`col` are the top-left pixel coordinates. `bands` selects a subset by index.
`drop_partial=True` discards edge patches smaller than `size`. `skip_empty_alpha=True`
skips patches whose alpha band is entirely zero, for four-band rasters with a real (non
constant) alpha.

### `OwlgDataset(paths, size=256, stride=None, password=None, transform=None, bands=None, fast=False)`

A map-style dataset over one or many `.owlg` files, suitable as a
`torch.utils.data.Dataset` (it implements `__len__` and `__getitem__` and has no torch
dependency). The index is built from headers alone at construction time; a file's raster
is decoded on first access and cached, and the cache holds one file at a time, so
sequential sampling is cheap and shuffling across many large files is not. `transform`, if
given, is applied to each patch before it is returned.

```python
import numpy as np
from owlg import ml

arr, meta = ml.to_array("rgb.owlg")
print("array :", arr.shape, arr.dtype, "| layout", meta["layout"])
print("meta  :", {k: meta[k] for k in ("width", "height", "bands", "dtype", "delta")})

n, (rows, cols) = ml.patch_grid("rgb.owlg", size=256)
print("grid  :", n, "patches,", rows, "rows x", cols, "cols")

batch = [p for p, (r, c) in ml.patches("rgb.owlg", size=256, stride=256)]
print("patch :", batch[0].shape, batch[0].dtype, "| count", len(batch))

ds = ml.OwlgDataset("rgb.owlg", size=256, transform=lambda p: p.astype("float32") / 255)
print("dataset:", len(ds), "items | ds[0]", ds[0].shape, ds[0].dtype)
```

```
array : (3, 718, 791) uint8 | layout CHW
meta  : {'width': 791, 'height': 718, 'bands': 3, 'dtype': 'uint8', 'delta': 3}
grid  : 6 patches, 2 rows x 3 cols
patch : (3, 256, 256) uint8 | count 6
dataset: 6 items | ds[0] (3, 256, 256) float32
```

> **Memory note.** `to_array`, `patches` and `OwlgDataset` all decode the full raster
> through `read_owlg` before slicing, on v4 files as well as v3. They are convenience
> wrappers, not a constant-RAM path. For a raster larger than RAM, drive
> [`TiledReader.read_window`](#tiledreader) yourself — the pattern is below. Only
> `patch_grid` is genuinely header-only.

## Tiering and rebasing

### `owlg.tiering.split(src, light_out, rec_out, password=None)`

Split a file carrying a recovery tier into a light `.owlg` and a `.owlr` recovery file.
Returns `(light_out, rec_out)`. Raises `OwlgError('this file has no recovery tier')` if
there is nothing to split. The `.owlr` records the parent's SHA-256.

### `owlg.tiering.join(light, recovery, out, password=None)`

Rejoin the pair. Returns `out`. If the recovery file's `parent_sha256` does not match the
light file's `sha256`, it raises rather than producing corrupt pixels.

```python
import os
from owlg.tiering import split, join

l, r = split("rgb_rec.owlg", "py_light.owlg", "py_rec.owlr")
print(l, os.path.getsize(l), "|", r, os.path.getsize(r))
print(join("py_light.owlg", "py_rec.owlr", "py_full.owlg"),
      os.path.getsize("py_full.owlg"))
```

```
py_light.owlg 288521 | py_rec.owlr 402293
py_full.owlg 690651
```

### `owlg.rebase.rebase(src, dst, base='webp', delta=0, password=None, verbose=True)`

Re-encode a file onto a different base codec. The correction layer is computed relative to
the decoded base, so the base cannot be substituted in place — the raster is decoded and
re-encoded. `delta` is measured **against the decoded content of `src`**, not against the
original GeoTIFF; the returned dict adds `guarantee_vs_original`, which is
`src_delta + delta` and is the number to quote.

```python
from owlg.rebase import rebase
print(rebase("rgb_avif.owlg", "py_rebase.owlg", base="webp", delta=1, verbose=False))
```

```
{'total': 463066, 'ratio': 3.6794193484298137, 'base': 110366, 'corr': 351465, 'guarantee_vs_original': 3}
```

> With the default `delta=0`, `write_owlg` takes its lossless path and uses a JPEG XL
> lossless base regardless of the `base` argument — so a `delta=0` rebase to `'webp'`
> produces a `jxl_lossless` file. Pass a non-zero `delta` when the point is to land on a
> specific codec. See the [CLI note on `rebase`](cli.md#rebase).

## Environment capabilities: `owlg.imgio`

### `can_decode(codec)`

```python
owlg.imgio.can_decode(codec)
```

A real test, not an import check: decode a tiny embedded 8x8 probe blob through the full
backend chain and confirm the shape. `codec` is `'avif'`, `'webp'` or `'jxl'`. Returns
`(bool, detail)` — on failure, `detail` is the underlying error, truncated.

### `capabilities()`

```python
owlg.imgio.capabilities()
```

A dict summarising this environment: `backends` (which of imagecodecs, Pillow and GDAL are
importable), `numba`, `crypto`, `rasterio`, `geotiff_writer`, `decode` (per-codec results
from `can_decode`), optionally `decode_why`, and the two roll-ups `can_read_owlg` and
`can_write_geotiff`.

```python
from owlg.imgio import capabilities, can_decode

c = capabilities()
print("backends:", c["backends"])
print("decode  :", c["decode"], "| can_read_owlg", c["can_read_owlg"])
print("can_decode('avif') ->", can_decode("avif"))
print("can_decode('nope') ->", can_decode("nope"))
```

```
backends: ['imagecodecs', 'pillow+avif']
decode  : {'avif': True, 'webp': True, 'jxl': True} | can_read_owlg True
can_decode('avif') -> (True, 'proven able to decode')
can_decode('nope') -> (False, 'unknown codec')
```

Call `capabilities()` on startup in any application that will open user-supplied files,
and tell the user what is missing before they hand you a file you cannot open.

## Worked example: the memory-bounded read pattern

Open a tiled file, read the windows you need, close. The cache is bounded by tile count,
so peak memory is roughly `max_tiles * tile * tile * bands` bytes plus one output array —
independent of how large the file is. With the defaults (48 tiles of 512 px, 3 bands) that
is about 38 MB whether the raster is 100 MB or 100 GB.

```python
from owlg.tiled_read import TiledReader

r = TiledReader("rgb_tiled.owlg", max_tiles=16)
print("size      :", r.hdr["w"], "x", r.hdr["h"], "x", r.hdr["bands"])
print("tile      :", r.tile, "px")
print("levels    :", [(l["w"], l["h"]) for l in r.levels])
print("delta     :", r.hdr["delta"])

# one window: only the tiles it intersects are decoded
win = r.read_window(band=0, xoff=300, yoff=200, xsize=256, ysize=128)
print("window    :", win.shape, win.dtype)

# sweep the whole raster in 64 px windows; the cache never grows past max_tiles
for y in range(0, r.hdr["h"], 64):
    for x in range(0, r.hdr["w"], 64):
        r.read_window(0, x, y, 64, 64)
print("cache held:", len(r.cache), "tiles after 156 window reads (max_tiles=16)")

r.close()
```

```
size      : 791 x 718 x 3
tile      : 512 px
levels    : [(791, 718), (396, 359), (198, 180)]
delta     : 0
window    : (128, 256) uint8
cache held: 4 tiles after 156 window reads (max_tiles=16)
```

Four tiles, because this raster only *has* four at level 0 — the point is that the number
is bounded by the cache and the working set, never by the file. Three practical notes:

- Align your window loop to the tile grid (`r.tile`) when you can. A window that straddles
  four tiles decodes four tiles.
- Read one band at a time. Decoding a tile produces all bands at once and caches them, so
  reading bands 0, 1, 2 of the same window costs one decode, not three.
- Use `level=1` or higher for overviews and thumbnails; a zoomed-out view should never
  touch level 0.
- Always `close()`, or use the reader inside `try`/`finally`. `TiledReader` holds an open
  file handle and is not a context manager.

## Worked example: the ML pipeline

OWLG to ndarray to patches to a float tensor, band-first throughout.

```python
import numpy as np
from owlg import ml

# 1. decode once, band-first uint8
arr, meta = ml.to_array("rgb.owlg")
print("array :", arr.shape, arr.dtype, "| layout", meta["layout"])
print("meta  :", {k: meta[k] for k in ("width", "height", "bands", "dtype", "delta")})

# 2. how many patches, without decoding anything
n, (rows, cols) = ml.patch_grid("rgb.owlg", size=256)
print("grid  :", n, "patches,", rows, "rows x", cols, "cols")

# 3. iterate the patches
batch = []
for patch, (row, col) in ml.patches("rgb.owlg", size=256, stride=256):
    batch.append(patch)
print("patch :", batch[0].shape, batch[0].dtype, "| count", len(batch))

# 4. stack and normalise -> ready for a model
x = np.stack(batch).astype("float32") / 255.0
print("tensor:", x.shape, x.dtype, "range", float(x.min()), "-", float(x.max()))

# 5. or let the dataset do steps 2-4
ds = ml.OwlgDataset("rgb.owlg", size=256, transform=lambda p: p.astype("float32") / 255)
print("dataset:", len(ds), "items | ds[0]", ds[0].shape, ds[0].dtype)
```

```
array : (3, 718, 791) uint8 | layout CHW
meta  : {'width': 791, 'height': 718, 'bands': 3, 'dtype': 'uint8', 'delta': 3}
grid  : 6 patches, 2 rows x 3 cols
patch : (3, 256, 256) uint8 | count 6
tensor: (6, 3, 256, 256) float32 range 0.0 - 1.0
dataset: 6 items | ds[0] (3, 256, 256) float32
```

Hand `ds` straight to `torch.utils.data.DataLoader`; the patches are already CHW, which is
what a `Conv2d` expects. For a raster too large to decode in one piece, build the same
pipeline on `TiledReader.read_window` using the pattern above, and use
`ml.patch_grid` to enumerate the coordinates without touching pixels.
