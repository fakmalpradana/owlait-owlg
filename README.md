# OWLG — Optimized Owl GeoTIFF

**A raster format with a hard per-pixel error bound.** For `--delta d`, every pixel of
every band satisfies `|original - decoded| <= d`. Not on average. Not at the 99th
percentile. Every pixel, provably, and `owlg verify` proves it over the whole raster.

At `--delta 0` a round trip is bit-identical to the original GeoTIFF.

This repository also contains **OWLGT**, a web tile pyramid built from the same codec,
served over OGC API - Tiles/Maps and WMS 1.3.0; a **QGIS plugin** that reads `.owlg`
files in place without decoding them to a GeoTIFF copy; a **pip** package; and a
zero-dependency **npm** package.

```bash
pip install owlg[full]

owlg encode ortho.tif ortho.owlg --delta 2    # 2.9x smaller than a lossless GeoTIFF
owlg verify ortho.owlg ortho.tif              # proves the bound over 6.6 M pixels
owlg vrt    ortho.owlg                        # now QGIS and gdalinfo read it directly
```

---

## Why a bound, and not just a smaller file

Lossy codecs are compared by average error, and averages hide the pixel that matters.
JPEG at quality 85 has a respectable RMSE of 3.7 on the sample in this repository — and
a worst pixel off by **30 DN**. WebP at quality 85 looks better still by RMSE, 4.3, and
its worst pixel is off by **134 DN**. If that pixel sits on a building edge you are
classifying, or in a shadow you are thresholding, the average never told you.

OWLG stores a lossy base layer plus a **correction layer** that is entropy-coded against
a hard bound. The base layer can be as aggressive as you like; the correction layer
drags every pixel back inside `±delta` before the file is written. So you choose the
error you can tolerate, and the format guarantees it rather than hoping for it.

| | RMSE | worst pixel | size |
|---|---:|---:|---:|
| GeoTIFF JPEG q85 | 3.71 | **30 DN** | 0.384 MB |
| GeoTIFF WebP q85 | 4.31 | **134 DN** | 0.114 MB |
| OWLG `--delta 3` | 1.49 | **3 DN, guaranteed** | 0.289 MB |
| OWLG `--delta 8` | 2.74 | **8 DN, guaranteed** | 0.164 MB |

*(`samples/rgb_small.tif`, reproduce with `python benchmarks/bench_options.py`.)*

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Efficiency, option by option](#efficiency-option-by-option)
- [Large rasters: 10–100 GB](#large-rasters-10100-gb)
- [QGIS plugin](#qgis-plugin)
- [Python API](#python-api)
- [JavaScript / Node](#javascript--node)
- [OWLGT web tiles and serving](#owlgt-web-tiles-and-serving)
- [Machine learning pipeline](#machine-learning-pipeline)
- [Encryption](#encryption)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Reproducing the numbers](#reproducing-the-numbers)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)
- [Licence](#licence)

---

## Install

### Python (pip)

```bash
pip install owlg[full]      # everything: rasterio, imagecodecs, numba, cryptography, pillow
pip install owlg            # reader only; numpy is the single hard dependency
```

From this repository:

```bash
git clone https://github.com/fakmalpradana/owlait-owlg.git
cd owlait-owlg
pip install -e .[dev]
pytest -q                   # 200 tests
```

The extras are deliberately granular, because the point of a small reader is that it
installs anywhere:

| Extra | Pulls in | What you gain |
|---|---|---|
| *(none)* | numpy | Read `.owlg` whose base layer your environment can already decode |
| `codecs` | imagecodecs | AVIF / WebP / JPEG XL base layers |
| `encode` | rasterio + imagecodecs | Writing `.owlg` at all |
| `fast` | numba | ~26x faster exact decoding (140 ms vs 3.7 s per 512 px window) |
| `crypto` | cryptography | Faster AES-256-GCM; a pure-Python fallback ships in the package |
| `pillow` | pillow | An alternative image backend when imagecodecs is unavailable |
| `full` | all of the above | |

Check what your environment can actually do — this runs real probe decodes, it does not
just test whether a module imports:

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

### Node (npm)

```bash
npm install owlg           # zero dependencies, ~10 kB packed
npx owlg info file.owlg
```

### QGIS plugin

Build the plugin zip and install it through **Plugins ▸ Manage and Install Plugins ▸
Install from ZIP**, then **restart QGIS**:

```bash
python scripts/build_all.py            # writes dist/owlg_qgis-4.0.0.zip
```

---

## Quick start

```bash
# Lossless: revert is bit-identical, SHA-256 verified
owlg encode ortho.tif ortho.owlg
owlg decode ortho.owlg back.tif --check-sha

# Near-lossless with a guaranteed bound
owlg encode ortho.tif small.owlg --delta 2
owlg verify small.owlg ortho.tif
#   error  : max=2 (bound +/-2) -> BOUND PROVEN

# Read it in QGIS / gdalinfo / rasterio without decoding a copy
owlg vrt small.owlg
export GDAL_VRT_ENABLE_PYTHON=YES
gdalinfo small.owlg.vrt

# Web tiles, then serve them over OGC API and WMS
gdalwarp -t_srs EPSG:3857 ortho.tif ortho3857.tif
owlg tiles ortho3857.tif map.owlgt --minzoom 16 --maxzoom 20
owlg serve map.owlgt --port 8080
```

Full flag-by-flag reference: **[docs/cli.md](docs/cli.md)**.

---

## Efficiency, option by option

Every number below was produced by `benchmarks/bench_options.py` on the machine that
wrote this README, and can be regenerated on yours. Two rasters are shown because
compression ratios depend enormously on the imagery: satellite scenes and drone
orthophotos do not behave alike, and quoting only the flattering one would be dishonest.

The **vs GeoTIFF** column compares against a `DEFLATE + PREDICTOR=2` GeoTIFF, which is
what most people mean by "the lossless file I already have".

### A. `samples/rgb_small.tif` — 791 × 718 × 3, Landsat, ships with this repo

**Reference formats**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE |
|---|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred (lossless) | 0.763 MB | 2.23x | 1.00x | **0** | 0.000 |
| GeoTIFF LZW (lossless) | 0.880 MB | 1.94x | 0.87x | **0** | 0.000 |
| GeoTIFF ZSTD (lossless) | 0.758 MB | 2.25x | 1.01x | **0** | 0.000 |
| GeoTIFF WebP lossless | 0.598 MB | 2.85x | 1.28x | **0** | 0.000 |
| COG DEFLATE (lossless) | 0.780 MB | 2.19x | 0.98x | **0** | 0.000 |
| GeoTIFF JPEG q85 (lossy) | 0.384 MB | 4.44x | 1.99x | 30 | 3.710 |
| GeoTIFF JPEG q75 (lossy) | 0.296 MB | 5.76x | 2.58x | 54 | 5.604 |
| GeoTIFF WebP q85 (lossy) | 0.114 MB | 14.95x | 6.70x | 134 | 4.311 |

**OWLG lossless — revert is bit-identical**

| Option | Size | vs raw | vs GeoTIFF | max err |
|---|---:|---:|---:|---:|
| `--delta 0 --base webp` *(default: portable)* | 0.680 MB | 2.51x | 1.12x | **0** |
| `--delta 0 --base avif` | 0.659 MB | 2.58x | 1.16x | **0** |
| `--delta 0 --base jxl` *(smallest; needs a JXL decoder)* | 0.587 MB | 2.90x | 1.30x | **0** |
| `--delta 0 --layout tiled` *(constant RAM + pyramid)* | 0.746 MB | 2.28x | 1.02x | **0** |

**OWLG near-lossless — the hard bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE |
|---|---:|---:|---:|---:|---:|
| `--delta 1` | 0.455 MB | 3.74x | 1.68x | 1 | 0.659 |
| `--delta 2` | 0.353 MB | 4.83x | 2.16x | 2 | 1.116 |
| `--delta 3` | 0.289 MB | 5.91x | 2.65x | 3 | 1.487 |
| `--delta 5` | 0.216 MB | 7.89x | 3.54x | 5 | 2.136 |
| `--delta 8` | 0.164 MB | 10.41x | 4.66x | 8 | 2.742 |

**Layout, recovery tier, encryption** (all at `--delta 2`)

| Option | Size | vs GeoTIFF | max err | Note |
|---|---:|---:|---:|---|
| `--base avif` | 0.338 MB | 2.26x | 2 | smallest at this bound |
| `--layout tiled` | 0.392 MB | 1.95x | 2 | constant RAM, 3 pyramid levels |
| `--layout tiled --no-overviews` | 0.354 MB | 2.16x | 2 | the pyramid costs ~11% here |
| `--recovery` | 0.692 MB | 1.10x | **0** | ships light, reverts bit-identical |
| `--encrypt` | 0.353 MB | 2.16x | 2 | AES-256-GCM costs ~0.1% |
| ↳ `owlg split` light half | 0.353 MB | 2.16x | 2 | distribute this |
| ↳ `owlg split` recovery half | 0.339 MB | 2.25x | — | archive this |

**OWLGT web tiles** (z6–z9, 32 tiles)

| Option | Size | vs raw | vs gdal2tiles |
|---|---:|---:|---:|
| `--profile view --q 50` | 0.081 MB | 21.0x | **15.9x smaller** |
| `--profile view --q 72` *(default)* | 0.151 MB | 11.3x | **8.5x smaller** |
| `--profile exact --delta 3` | 0.372 MB | 4.6x | **3.5x smaller** |
| gdal2tiles PNG tree | 1.290 MB | 1.3x | 1.00x |
| MBTiles PNG | 1.225 MB | 1.4x | 1.05x |

### B. A real drone orthophoto — 1471 × 1128 × 4, RGBA, 6.6 MB raw

Not redistributable, so this table is evidence rather than something you can rerun. It
is the more representative case for photogrammetry work: aerial imagery compresses far
better than satellite scenes.

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE |
|---|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred | 3.211 MB | 2.07x | 1.00x | **0** | 0.000 |
| GeoTIFF WebP lossless | 2.063 MB | 3.22x | 1.56x | **0** | 0.000 |
| GeoTIFF JPEG q85 | 1.252 MB | 5.30x | 2.57x | 29 | 2.976 |
| **OWLG `--delta 0`** | 1.864 MB | 3.56x | **1.72x** | **0** | 0.000 |
| **OWLG `--delta 1`** | 1.487 MB | 4.46x | **2.16x** | 1 | 0.696 |
| **OWLG `--delta 2`** | 1.095 MB | 6.06x | **2.93x** | 2 | 1.096 |
| **OWLG `--delta 3`** | 0.880 MB | 7.54x | **3.65x** | 3 | 1.345 |
| **OWLG `--delta 5`** | 0.644 MB | 10.31x | **4.99x** | 5 | 1.911 |
| **OWLG `--delta 8`** | 0.479 MB | 13.86x | **6.70x** | 8 | 2.599 |
| OWLGT `view q72` | 0.693 MB | 9.57x | 4.63x | — | — |
| gdal2tiles PNG tree (91 tiles) | 7.113 MB | 0.93x | 0.45x | — | — |

At `--delta 2` — an error invisible on screen and below the radiometric noise of most
drone sensors — the file is **2.9x smaller than the lossless GeoTIFF** and still
smaller than JPEG q85, while JPEG's worst pixel is off by 29 DN and OWLG's by 2.

### Which option should I use?

| If you want | Use |
|---|---|
| An archival master, byte-for-byte | `--delta 0` (add `--base jxl` for the smallest, if your readers have JXL) |
| A working copy for analysis | `--delta 1` or `--delta 2` |
| A distribution copy for viewing | `--delta 3` to `--delta 8` |
| Both at once | `--delta 2 --recovery`, then `owlg split` |
| A raster over ~16 MPixel | nothing — `--layout auto` already picks `tiled` |
| Maximum portability | `--base webp` (the default) |
| Minimum size at a given bound | `--base avif` |
| Web delivery | `owlg tiles … --profile view` |
| Web delivery that stays analysable | `owlg tiles … --profile exact --delta 3` |

---

## Large rasters: 10–100 GB

The flat layout holds the whole raster in memory. The **tiled layout** does not: tiles
are independent blobs, encoding streams tile by tile, and the reader keeps a bounded LRU
cache. `--layout auto` selects it above 16 MPixel.

```bash
owlg encode huge.tif huge.owlg --delta 2            # auto -> tiled
owlg encode huge.tif huge.owlg --delta 2 --layout tiled --tile 512
owlg verify huge.owlg huge.tif                      # streams; never loads the raster
```

Measured on one machine, same imagery scaled up, GDAL block cache capped at 256 MB:

| Raster | Raw | Peak RAM above baseline | Encode time |
|---|---:|---:|---:|
| 4 096² × 3 | 0.050 GB | **0.220 GB** | 13 s |
| 8 192² × 3 | 0.201 GB | **0.236 GB** | 43 s |
| 16 384² × 3 | 0.805 GB | **0.239 GB** | 164 s |

Data grows 16x; memory grows 8%. Time is linear, memory is flat — which is what makes
100 GB a question of patience rather than of hardware. Extrapolating: roughly 5.7 hours
single-threaded at the same ~0.24 GB.

Reading is windowed. On the 16 384² file (242.8 MB, 7 pyramid levels), through GDAL:

| | without numba | with numba |
|---|---:|---:|
| Open the file | 8 ms | 8 ms |
| 512² window, exact, cold | 3 726 ms | **140 ms** |
| Same window, cached | 1 ms | 1 ms |
| Coarsest overview, whole level | 5 ms | 4 ms |
| 512² window, base-layer preview | 47–82 ms | 47–82 ms |

Zooming out never touches level 0, because the pyramid levels are base-only. That is why
opening a 100 GB file feels like opening a small one.

**Integrity without loading the raster.** Tiled files carry `sha256_scan`: a SHA-256 over
a defined tile-scan serialization, computed as the encoder streams and recomputed the same
way by `owlg verify`. Never more than one tile is in memory. The digest is of the
*original* pixels, so verifying proves two things at once:

```
$ owlg verify huge.owlg huge.tif
shape  : same  (268.4 MPixel x 3 bands = 805.3 M samples checked)
error  : max=2 (bound +/-2) -> BOUND PROVEN
digest : original pixels MATCH (ebcc7dca28cc7d64...) -> this file really came from huge.tif
geo    : transform same, crs same
```

---

## QGIS plugin

The plugin loads a `.owlg` **as itself**. It writes a small `.vrt` sidecar containing a
Python pixel function, so GDAL — and therefore QGIS — treats it as an ordinary raster
while every block request is routed to the OWLG decoder. Only the tiles on screen are
decoded. No GeoTIFF copy is created, so the disk footprint stays the size of the `.owlg`
plus a few kB.

```
Plugins ▸ Manage and Install Plugins ▸ Install from ZIP ▸ dist/owlg_qgis-4.0.0.zip
Restart QGIS.  Then drag a .owlg onto the canvas.
```

The decoder is vendored inside the plugin — nothing to `pip install` into the QGIS
Python. From QGIS it needs only numpy plus one of Pillow / GDAL, both of which QGIS
always ships.

**If a direct read fails, the plugin says so.** It used to log the failure and quietly
decode the whole raster to a GeoTIFF cache instead, which is why the Layer Information
panel would report `GTiff` and a size that had nothing to do with the `.owlg`. It now
shows the cause and asks; a copy is only made if you accept it, and that layer is
labelled `[GeoTIFF copy]`. **OWLG ▸ Decoder diagnostics** runs a genuine self-test — a
4×4 pixel VRT with a trivial pixel function — so you can tell a GDAL policy problem from
a file problem.

Details, including the GDAL configuration involved: **[docs/qgis.md](docs/qgis.md)**.

---

## Python API

```python
import owlg
from owlg.tiled_read import TiledReader

# Encode
owlg.write_owlg("ortho.tif", "ortho.owlg", delta=2)

# Decode the whole thing
array, header = owlg.read_owlg("ortho.owlg")      # (bands, height, width) uint8
print(header["delta"], header["crs"])

# Or read one window of a very large file, with bounded memory
r = TiledReader("huge.owlg")
patch = r.read_window(band=0, xoff=4096, yoff=4096, xsize=512, ysize=512)
thumb = r.read_window(band=0, xoff=0, yoff=0, xsize=512, ysize=512, level=4)
r.close()
```

Full reference, every function exercised: **[docs/python-api.md](docs/python-api.md)**.

---

## JavaScript / Node

Zero dependencies, ES modules, works in the browser and in Node.

```js
import { openOWLG, decodeOWLG } from 'owlg';

const file = await openOWLG(bytes);
const { bands, width, height } = await decodeOWLG(file, {
  // The host supplies the base-layer image decoder. In a browser that is
  // createImageBitmap; Node has none built in, which is a real limitation.
  avifDecode: async (buf) => { /* ... */ },
});
```

```js
import { owlgtSource } from 'owlg/maplibre';   // or 'owlg/leaflet'
map.addSource('ortho', await owlgtSource('/map.owlgt'));
```

Full reference and the honest list of what the JS decoder does not yet do:
**[docs/node-api.md](docs/node-api.md)**.

---

## OWLGT web tiles and serving

`.owlgt` is a single-file tile pyramid: one container, an offset directory, and tiles
that share identical content are stored once. Two profiles:

- `--profile view` — display only, smallest;
- `--profile exact --delta N` — each tile carries the same hard bound as OWLG, so tiles
  remain usable for analysis rather than only for looking at.

```bash
gdalwarp -t_srs EPSG:3857 ortho.tif ortho3857.tif
owlg tiles ortho3857.tif map.owlgt --minzoom 16 --maxzoom 20 --profile view --q 72
owlg serve map.owlgt --port 8080
owlg revert map.owlgt back.tif --zoom 20 --t-srs EPSG:32749   # tiles -> GeoTIFF
```

`owlg serve` speaks both standards, from stdlib only:

```
OGC API landing   http://127.0.0.1:8080/
Collections       http://127.0.0.1:8080/collections
OGC API - Tiles   .../collections/<id>/map/tiles/WebMercatorQuad/{z}/{y}/{x}.png
XYZ (Leaflet)     http://127.0.0.1:8080/xyz/<id>/{z}/{x}/{y}.png
WMS 1.3.0         http://127.0.0.1:8080/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0
```

The WMS implementation observes the 1.3.0 axis-order rule: `EPSG:4326` is lat,lon while
`CRS:84` is lon,lat. Getting that backwards is the classic way to produce a blank map.

---

## Machine learning pipeline

```bash
owlg npy ortho.owlg data.npy --layout CHW
owlg npy ortho.owlg data.npz --format npz
owlg npy ortho.owlg data.dat --format memmap     # for rasters larger than RAM
```

```python
from owlg.ml import OwlgDataset, patches

for patch in patches("ortho.owlg", size=256, stride=256):
    ...                                   # (bands, 256, 256) uint8

ds = OwlgDataset("ortho.owlg", size=512)  # indexable, PyTorch-friendly
```

---

## Encryption

`--encrypt` applies AES-256-GCM to every blob **and to the header**, so dimensions, CRS
and the blob directory are encrypted too — not just the pixels. The key is derived with
PBKDF2-HMAC-SHA256 (600 000 iterations by default).

```bash
owlg encode ortho.tif secret.owlg --delta 2 --encrypt -A
OWLG_KEY='…' owlg decode secret.owlg back.tif
```

A pure-Python AES-256-GCM implementation ships in the package and is verified
bit-identical to `cryptography` in the test suite, so encrypted files open inside a QGIS
Python that has no crypto library at all.

---

## How it works

```
GeoTIFF  ->  base layer (lossy WebP/AVIF, or lossless JPEG XL)
              +
             correction layer: per-pixel residual, quantized so |error| <= delta,
             entropy-coded with a binary adaptive range coder over 567 contexts
```

The trick is that the correction coder predicts from the **fully decoded base layer**,
which is available on both sides. A pure DPCM coder only sees pixels it has already
decoded — causal context. Here the context is non-causal: the coder knows what the
base layer says about the pixel to the right and below, not only to the left and above.
That is why a lossy base plus corrections beats a straight lossless coder.

Context modelling crosses 3 band slots with 7 activity bins from the base-layer
gradient, the left neighbour, the upper neighbour and the previous band:
3 × 7 × 3 × 3 × 3 = 567 contexts for the zero flag, each an adaptive binary
probability (699 in total with the sign, unary and escape contexts).

The tiled layout confines that context to a tile, so every tile is genuinely independent
and can be decoded on its own. That independence is what makes windowed reads, the
pyramid, and constant-memory encoding possible.

Format specification: **[docs/format.md](docs/format.md)**.

---

## Repository layout

```
src/owlg/              Python package (the format, the CLI, the server)
packages/owlg-js/      npm package: zero-dependency reader, CLI, MapLibre/Leaflet helpers
qgis-plugin/owlg_qgis/ QGIS plugin, decoder vendored inside
docs/                  CLI, Python API, Node API, format spec, QGIS notes
benchmarks/            bench_options.py — every number in this README
samples/               rgb_small.tif (Landsat, public domain, via rasterio's test data)
results/               benchmark output, .json and .md
tests/                 200 pytest tests
scripts/build_all.py   builds the plugin zip, the wheel and the npm tarball
```

---

## Reproducing the numbers

```bash
pip install -e .[dev]
python benchmarks/bench_options.py samples/rgb_small.tif -o results/rgb_small
python benchmarks/bench_options.py your_own_raster.tif -o results/yours --deep
```

It writes `<out>.json` and `<out>.md`. Reference formats are produced with GDAL, so the
comparison is against what a GIS user would actually create, not a strawman. Ratios vary
a lot with imagery — run it on your own data before believing any table, including this
one.

---

## Known limitations

Stated plainly, because finding these out yourself after adopting a format is worse than
reading them here.

- **uint8 only.** Int16, uint16 and float rasters are rejected. DEMs and multispectral
  imagery at higher bit depths are not supported yet.
- **Exact decoding without numba is slow**: about 3.7 s per 512 px window versus 140 ms
  with it. Correct and bit-identical either way, but install `owlg[fast]` for
  interactive work.
- **The JavaScript decoder does not read tiled (v4) files yet**, and it needs the host to
  supply a base-layer image decoder. A browser has one; Node does not by default. Since
  `--layout auto` picks tiled above 16 MPixel, large files are currently Python-side only.
- **The Node OWLGT helpers only handle `--profile view`.** Tiles written with
  `--profile exact` need a full correction decode and return HTTP 501 with an explanation.
- **Encoding is single-threaded.** Tiles are independent, so parallelising by tile row
  should scale nearly linearly. Not done yet.
- **`--recovery` is flat-layout only**, so it is not available for very large rasters. In
  the tiled layout `--delta 0` already gives bit-identical revert.
- The benchmark tables come from two rasters on one machine. Ratios are not universal.

---

## Contributing

Issues and pull requests are welcome. Please run `pytest -q` before opening a PR; if you
change anything that touches the codec, add a test that asserts the bound over every
pixel, since that is the one promise this format makes.

---

## Licence

GNU Affero General Public License v3.0 or later — see [LICENSE](LICENSE).

The AGPL's network clause matters here: if you run a modified version of this code as a
network service (for example `owlg serve` behind a public endpoint), you must offer the
source of your modified version to its users.

`samples/rgb_small.tif` is Landsat 7 imagery (USGS, public domain), taken from rasterio's
test fixtures.
