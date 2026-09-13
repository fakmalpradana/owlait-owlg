# Getting started with OWLG

A guided first hour: install, turn a GeoTIFF into an `.owlg`, prove the bound, open it in
QGIS without decoding a copy, publish web tiles, and read the same file from Python and
from JavaScript. Every command below was run for real against a drone orthophoto
(1471 x 1128 x 4, RGBA, 6.6 MB raw); swap in your own file. The full references are
[cli.md](cli.md), [python-api.md](python-api.md), [node-api.md](node-api.md).

## Contents

- [1. Install](#1-install)
- [2. Your first `.owlg`](#2-your-first-owlg)
- [3. Choose the bound — or choose the size](#3-choose-the-bound--or-choose-the-size)
- [4. Prove it](#4-prove-it)
- [5. Open it in QGIS and GDAL without a copy](#5-open-it-in-qgis-and-gdal-without-a-copy)
- [6. Large rasters](#6-large-rasters)
- [7. Web tiles and a server](#7-web-tiles-and-a-server)
- [8. Python](#8-python)
- [9. JavaScript](#9-javascript)
- [10. Encryption, recovery, portability](#10-encryption-recovery-portability)
- [Cheat sheet](#cheat-sheet)

---

## 1. Install

Python 3.9 or newer. Until the package is on PyPI, install from the repository:

```bash
pip install "owlg[full] @ git+https://github.com/fakmalpradana/owlait-owlg.git"
```

or, if you have cloned it:

```bash
pip install -e ".[full]"
```

`full` brings rasterio (reading GeoTIFFs), imagecodecs (WebP/AVIF/JXL), numba (26x faster
exact decoding), cryptography and Pillow. A reader-only install is just `pip install owlg`
with numpy as its single dependency.

Check what your machine can actually do — this decodes tiny probe images rather than
checking whether modules import:

```
$ owlg check
image backends : imagecodecs, pillow+avif
GeoTIFF writer : rasterio
encryption     : cryptography
numba          : present
real decode probes:
  avif : YES
  webp : YES
  jxl  : YES
```

Colour: every `owlg` command styles its output when stdout is a terminal (green verdicts,
red violations, progress bars). Piped or redirected, it prints plain text. `--no-color`
or `NO_COLOR=1` turns it off; `OWLG_COLOR=always` forces it.

## 2. Your first `.owlg`

```
$ owlg encode ortho.tif ortho.owlg --delta 2
  1471x1128x4 uint8  raw 6.637 MB  layout flat
  constant bands dropped: {'3': 255} (-1.66 MB)
    searching webp quality 1/7 q=95: 1.089 MB
    searching webp quality 2/7 q=90: 1.124 MB
    ...
    searching webp quality 7/7 q=40: 1.432 MB
  base webp q=95  base 0.610 MB + correction 0.479 MB
  -> ortho.owlg: 1.090 MB  6.09x  bound +/-2 DN  (2.4s)
```

Read that last line as the contract: the file is 6.1x smaller than the raw pixels, and
**no sample in it differs from the original by more than 2 DN**. Three things happened
on the way:

- the alpha band was all 255, so it was recorded as a constant and costs nothing;
- the encoder tried seven WebP qualities and kept the one with the smallest
  base + correction total (a better base needs less correction, so there is a real
  optimum, and it is usually a high quality);
- the correction layer is what turns a lossy WebP into a bounded file.

`--delta 0` gives a bit-identical revert. `--layout` is chosen automatically: flat below
16 MPixel, tiled above (see [6](#6-large-rasters)).

## 3. Choose the bound — or choose the size

Two ways to ask, one promise either way.

**Ask for a bound.** `--delta N` means every pixel within N DN. Rough guide, from the
[benchmarks](../README.md#efficiency-option-by-option):

| you want | use | drone ortho |
|---|---|---:|
| a master copy, byte-for-byte | `--delta 0` | 2.8x |
| a working copy for analysis | `--delta 1` or `2` | 4.5–6.1x |
| a distribution copy for viewing | `--delta 3` to `8` | 7.6–14x |
| the smallest file with a still-written bound | `--delta 12` to `16` | 18.7–23x |

**Ask for a size.** `--target 20x` searches the smallest delta that makes the file at
least 20x smaller than raw, shows its probes, and encodes with that delta:

```
$ owlg encode ortho.tif ortho20.owlg --target 20x --base avif
  searching the smallest delta that gives 20x vs raw (base avif)
    delta 8  q=75  -> 0.449 MB   14.8x
    delta 16 q=45  -> 0.271 MB   24.5x
    delta 12 q=60  -> 0.336 MB   19.7x
    delta 14 q=60  -> 0.298 MB   22.2x
  -> delta 14 (+/-14 DN) is the smallest bound that reaches 20x
  1471x1128x4 uint8  raw 6.637 MB  layout flat
  constant bands dropped: {'3': 255} (-1.66 MB)
  base avif q=60  base 0.223 MB + correction 0.071 MB
  -> ortho20.owlg: 0.296 MB  22.46x  bound +/-14 DN  (0.6s)
  target 20x reached: 22.5x with a guaranteed bound of +/-14 DN
```

The ratio is the goal; the delta it lands on is the promise, written to the header
exactly as if you had typed `--delta 14`. If even the coarsest bound on the ladder cannot
reach the goal, it says so instead of pretending.

`--base webp` (default) opens everywhere. `--base avif` is 5–10% smaller at the same
bound and needs an AVIF decoder on the reading side — `owlg check` tells you whether a
machine has one.

## 4. Prove it

Never take the bound on faith. `verify` compares against the original, every sample of
every band, and exits 0 only when the bound holds:

```
$ owlg verify ortho20.owlg ortho.tif
shape  : same
error  : max=14 (bound +/-14) -> BOUND PROVEN
         mean|e|=2.4337  rmse=3.9215  pixels changed=64.68%
geo    : transform same, crs same
$ echo $?
0
```

Exit code 2 means the bound is violated — usable as a CI gate with no output parsing.
Tiled files also carry a streaming digest of the *original* pixels, so `verify` proves at
the same time that this `.owlg` really came from that GeoTIFF.

`info` tells you what a file promises without decoding anything:

```
$ owlg info ortho20.owlg
── ortho20.owlg ────────────────────────────────────────────
format         : OWLG v3 flat
raster         : 1471 x 1128 x 4 uint8  raw 6.64 MB
size           : 0.296 MB  22.46x vs raw
guarantee      : bound +/-14 DN  every pixel of every band
base codec     : avif q=60
constant bands : {'3': 255}
crs            : WGS 84 / UTM zone 49S EPSG:32749
pixel size     : 0.100014 x 0.100009  origin (432432.295, 9134040.545)
encrypted      : no
layer          bytes
──────────  ────────  ───────
base layer  0.223 MB  1 blob
correction  0.071 MB  4 tiles
sha256         : 1f98aab9264674b1...
```

## 5. Open it in QGIS and GDAL without a copy

```
$ owlg vrt ortho.owlg
/data/ortho.owlg.vrt
  disk footprint: 1.090 MB (.owlg) + 3.2 kB (.vrt) — no GeoTIFF copy
  usage: export GDAL_VRT_ENABLE_PYTHON=YES  then open the .vrt in QGIS/gdalinfo
```

The sidecar routes every block request GDAL makes to the OWLG decoder, so what QGIS,
`gdalinfo`, rasterio or ArcGIS read *is* the `.owlg`, one window at a time:

```bash
export GDAL_VRT_ENABLE_PYTHON=YES
export GDAL_VRT_PYTHON_TRUSTED_MODULES=owlg.vrt
gdalinfo -stats ortho.owlg.vrt
```

In QGIS itself, install the plugin instead — it configures GDAL for you, adds
drag-and-drop for `.owlg`/`.owlgt`, and asks before ever making a GeoTIFF copy:

```bash
python scripts/build_all.py        # writes dist/owlg_qgis-0.1.0.zip
```

Plugins ▸ Manage and Install Plugins ▸ Install from ZIP ▸ restart QGIS ▸ drop the file
on the canvas. Details and troubleshooting in [qgis.md](qgis.md).

## 6. Large rasters

Above 16 MPixel the encoder switches to the **tiled** layout on its own: each tile is an
independent blob, encoding streams tile by tile, an overview pyramid is built inside the
file, and the reader keeps a bounded cache. RAM does not grow with the raster — 0.24 GB
for a 16 384² image, and the same for 100 GB.

Forced on a smaller raster here so the output fits (a 2937 x 2252 orthophoto), the
shape of the run is the same at any size — on a terminal the tile rows are one progress
bar:

```
$ owlg encode big.tif big.owlg --delta 2 --layout tiled
  2937x2252x4 uint8  raw 26.456 MB  layout tiled, 512px tiles
  base webp q=95  bound +/-2 DN
  constant bands dropped: {'3': 255}
    level 0 tile rows 5/5
  level 0: 30 tiles, base 2.72 MB + correction 2.11 MB
  overview 1: 1469x1126, 9 tiles, q=50, 0.25 MB
  overview 2: 735x563, 4 tiles, q=50, 0.07 MB
  overview 3: 368x282, 1 tile, q=50, 0.02 MB
  overview 4: 184x141, 1 tile, q=50, 0.01 MB
  -> big.owlg: 5.179 MB  5.11x  bound +/-2 DN  5 levels (6.0s)
```

Measured on the same imagery scaled up: 4 096² takes 0.22 GB, 16 384² takes 0.24 GB —
data x16, memory +8%. `verify` on such a file streams too, and never holds the raster. The overview levels
carry no bound (they are for display) and are encoded at `--overview-q` 50. `--target`
works here as well; it estimates from a handful of sampled tiles, then encodes.

## 7. Web tiles and a server

```bash
gdalwarp -t_srs EPSG:3857 ortho.tif ortho3857.tif
owlg tiles ortho3857.tif ortho.owlgt --minzoom 14 --maxzoom 19          # display profile
owlg tiles ortho3857.tif exact.owlgt --minzoom 14 --maxzoom 19 --profile exact --delta 3
owlg serve ortho.owlgt --port 8080
```

```
  pyramid z14-19: 13 tiles z14:1 z15:1 z16:1 z17:2 z18:2 z19:6
  -> ortho.owlgt: 0.078 MB (tiles 0.078 + index 0.001)  dedup 0 tiles  (0s)
OWLG serving 1 collection at http://127.0.0.1:8080
  ortho            z14-19, 13 tiles, profile view
OGC API landing  : http://127.0.0.1:8080/
Collections      : http://127.0.0.1:8080/collections
OGC tiles        : http://127.0.0.1:8080/collections/<id>/map/tiles/WebMercatorQuad/{z}/{y}/{x}.png
XYZ (Leaflet)    : http://127.0.0.1:8080/xyz/<id>/{z}/{x}/{y}.png
WMS 1.3.0        : http://127.0.0.1:8080/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0
  Ctrl-C to stop
```

Point QGIS at the WMS URL, or Leaflet/MapLibre at the XYZ route. `--profile view` tiles
are plain AVIF and go straight to the browser; `--profile exact` tiles carry the same
hard bound as OWLG and need the Python decoder (the server has it). `owlg revert` turns a
pyramid back into a GeoTIFF.

## 8. Python

```python
import owlg

# encode: same options as the CLI
owlg.write_owlg("ortho.tif", "ortho.owlg", delta=2)

# decode everything: (bands, height, width) uint8 plus the header
arr, hdr = owlg.read_owlg("ortho.owlg")
print(arr.shape, hdr["delta"], hdr["crs"][:30])

# header only, no pixels touched
hdr, _ = owlg.open_owlg("ortho.owlg")

# write a GeoTIFF back, with CRS, transform, colour interpretation, nodata, tags
owlg.to_tif("ortho.owlg", "back.tif")
```

Windowed reads for large files, and the ML bridge:

```python
from owlg.tiled_read import TiledReader
from owlg.ml import patches, OwlgDataset

r = TiledReader("huge.owlg")
patch = r.read_window(band=0, xoff=4096, yoff=4096, xsize=512, ysize=512)   # exact
thumb = r.read_window(band=0, xoff=0, yoff=0, xsize=512, ysize=512, level=4)  # overview
r.close()

for p in patches("ortho.owlg", size=256, stride=256):
    ...                                    # (bands, 256, 256) uint8 windows

ds = OwlgDataset("ortho.owlg", size=512)   # indexable, PyTorch-friendly
```

`owlg npy ortho.owlg data.npy --layout CHW` does the export from the command line, with a
`.meta.json` sidecar carrying the georeferencing. Full reference:
[python-api.md](python-api.md).

## 9. JavaScript

```bash
npm install github:fakmalpradana/owlait-owlg#main:packages/owlg-js   # until it is on npm
npx owlg info ortho.owlg
npx owlg serve ortho.owlgt --port 8080                               # OGC API + WMS, view profile
```

Zero dependencies, ESM, browser and Node ≥ 18. Displaying a tile pyramid needs no decoding
at all — the tiles are ordinary AVIF blobs:

```js
import maplibregl from 'maplibre-gl';
import { owlgtSource } from 'owlg/maplibre';

const bytes = new Uint8Array(await (await fetch('/ortho.owlgt')).arrayBuffer());
const { source } = await owlgtSource(map, 'ortho', bytes);   // adds source + layer 'ortho-layer'
```

```js
import L from 'leaflet';
import { createOwlgtLayer } from 'owlg/leaflet';

(await createOwlgtLayer(L, bytes)).addTo(map);
```

Reading an `.owlg` into pixels: the host supplies the base-layer image decoder. A browser
has one built in; Node does not, so pass one backed by `sharp` or `@jsquash/avif`:

```js
import { openOWLG, decodeOWLG, browserAvifDecoder } from 'owlg';

const file = await openOWLG(bytes);                      // header, blob directory
const { bands, width, height } = await decodeOWLG(file, {
  avifDecode: browserAvifDecoder(),                      // createImageBitmap under the hood
});
// bands[0] is a Uint8Array of width * height; the correction layer has been applied,
// bit-identical to what Python decodes.
```

Limits, stated plainly: the JS decoder reads flat (v3) files only for now, so rasters above
16 MPixel are Python-side; and the Node server handles `--profile view` pyramids.
Reference: [node-api.md](node-api.md).

## 10. Encryption, recovery, portability

```bash
owlg encode ortho.tif secret.owlg --delta 2 --encrypt -A      # prompts; AES-256-GCM over header AND blobs
OWLG_KEY='…' owlg decode secret.owlg back.tif

owlg encode ortho.tif both.owlg --delta 3 --recovery          # near-lossless + exact residual
owlg split both.owlg light.owlg rec.owlr                      # distribute light, archive rec
owlg join light.owlg rec.owlr full.owlg && owlg decode full.owlg exact.tif --tier full --check-sha

owlg rebase small_avif.owlg portable.owlg --base webp --delta 1   # AVIF -> WebP, bound becomes 2+1
```

## Cheat sheet

| I want to… | command |
|---|---|
| see what this machine can decode | `owlg check [file.owlg]` |
| bit-identical archive | `owlg encode in.tif out.owlg` |
| bounded working copy | `owlg encode in.tif out.owlg --delta 2` |
| the smallest file that is still bounded, 20x | `owlg encode in.tif out.owlg --target 20x --base avif` |
| prove the bound (CI gate: exit 0/2) | `owlg verify out.owlg in.tif` |
| what does this file promise? | `owlg info out.owlg` (`--json` for scripts) |
| open in QGIS/GDAL, no copy | `owlg vrt out.owlg` + `GDAL_VRT_ENABLE_PYTHON=YES` |
| back to GeoTIFF | `owlg decode out.owlg back.tif` |
| web tiles + OGC API/WMS | `owlg tiles in3857.tif map.owlgt --minzoom 14 --maxzoom 19 && owlg serve map.owlgt` |
| numpy for ML | `owlg npy out.owlg data.npy` |
