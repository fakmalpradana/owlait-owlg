# The QGIS plugin

The plugin makes QGIS open a `.owlg` **as itself**, decoding only the tiles currently on
screen. No GeoTIFF copy is made, so the disk footprint stays the size of the `.owlg` plus
a few kilobytes of sidecar.

See also the [CLI reference](cli.md), the [format specification](format.md) and the
[project README](../README.md).

## Contents

- [Install](#install)
- [How direct reading works](#how-direct-reading-works)
- [The load dialog](#the-load-dialog)
- [Exact versus preview, and why the default changes](#exact-versus-preview-and-why-the-default-changes)
- [Diagnostics](#diagnostics)
- [Why a layer used to say GTiff](#why-a-layer-used-to-say-gtiff)
- [Requirements](#requirements)
- [Using .owlg from GDAL without the plugin](#using-owlg-from-gdal-without-the-plugin)
- [Troubleshooting](#troubleshooting)

---

## Install

```bash
python scripts/build_all.py        # writes dist/owlg_qgis-<version>.zip
```

In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, choose the zip,
then **restart QGIS**. The restart matters: QGIS caches plugin modules in `sys.modules`,
and a reinstall without a restart can leave the old decoder in memory.

Nothing needs to be `pip install`ed into the QGIS Python. The decoder is vendored inside
the plugin as the subpackage `owlg_qgis.vendor.owlg` — deliberately not a top-level
`owlg`, because QGIS purges modules under the plugin's own prefix on reinstall but would
leave a top-level package behind.

Then drag a `.owlg` onto the canvas, use **Layer ▸ Add Layer ▸ OWLG**, or find it in the
Browser panel.

---

## How direct reading works

The plugin writes a small `.vrt` sidecar next to the file (or into a cache directory if
the folder is not writable). The VRT declares a `VRTDerivedRasterBand` whose pixel
function is the OWLG decoder:

```xml
<VRTRasterBand dataType="Byte" band="1" subClass="VRTDerivedRasterBand"
               blockXSize="512" blockYSize="512">
  <PixelFunctionType>owlg_qgis.vendor.owlg.vrt.read_band</PixelFunctionType>
  <PixelFunctionLanguage>Python</PixelFunctionLanguage>
  <PixelFunctionArguments path="/path/to/file.owlg" band="0" level="0" fast="0"/>
</VRTRasterBand>
```

GDAL — and therefore QGIS, gdalinfo, rasterio, and anything else built on GDAL — reads
it as an ordinary raster, while every block request is routed to the decoder. The VRT
block size is set to the OWLG tile size so a GDAL block request maps onto one tile.

For tiled (v4) files the internal pyramid is exposed as GDAL overviews through sidecar
`.ov{n}.vrt` files, so zooming out reads a coarse level instead of the full raster.

Running Python from a VRT is gated by GDAL policy. The plugin enables it narrowly, for
its own module only:

```
GDAL_VRT_ENABLE_PYTHON          = TRUSTED_MODULES
GDAL_VRT_PYTHON_TRUSTED_MODULES = owlg_qgis.vendor.owlg.vrt
```

Note the option name: it is `GDAL_VRT_PYTHON_TRUSTED_MODULES`, with `PYTHON` in the
middle. Setting `GDAL_VRT_TRUSTED_MODULES` instead does nothing at all, silently — see
below.

---

## The load dialog

Dropping a `.owlg` opens a dialog with two choices.

**Layer source**

- *Read directly from the .owlg* — the default. No copy; the layer is the `.owlg`.
- *Decode to a GeoTIFF (cache)* — a full decode into a temporary file. This option is
  **disabled with a stated reason** when the raster is too large for it to be sensible;
  decoding a 40 GB raster into a cache is not slow, it is a machine that stops
  responding.

**Precision**

- *Exact* — apply the correction layer. The pixels satisfy the file's bound.
- *Fast preview* — base layer only. Quick, and visibly labelled `(preview)` in the layer
  name so it cannot be mistaken for exact data.

The dialog also states the file size, the pyramid depth, and the mode and bound recorded
in the header.

---

## Exact versus preview, and why the default changes

The correction decoder has two implementations with bit-identical output: a numba-shaped
one, and a flat pure-Python one for environments without numba. QGIS Python usually has
no numba.

Measured on a 16 384 × 16 384 × 3 file, through GDAL:

| | without numba | with numba |
|---|---:|---:|
| 512² window, exact, cold | 3 726 ms | 140 ms |
| Same window, cached | 1 ms | 1 ms |
| 512² window, preview (base only) | 47–82 ms | 47–82 ms |
| Coarsest overview, whole level | 5 ms | 4 ms |

Roughly 3.7 seconds per tile makes panning unpleasant. So when numba is absent the dialog
**defaults to fast preview and says why**, rather than defaulting to exact and letting
you discover the cost by dragging the map. Exact remains one click away, and installing
numba into the QGIS Python makes it about 26x faster.

Zooming out is fast either way, because pyramid levels are base-only.

---

## Diagnostics

**OWLG ▸ Decoder diagnostics** reports the image backends found, the GeoTIFF writer,
whether numba is present, and — importantly — the result of **real probe decodes** of
small embedded AVIF/WebP/JXL blobs. A capability check that only tests whether a module
imports can cheerfully report "all ready" in an environment that cannot decode anything;
this one actually decodes.

It also runs a VRT self-test: a 4 × 4 pixel VRT whose pixel function returns the constant
42. If that fails, the problem is GDAL's Python policy or build, not your file — which is
a distinction worth being able to make before you start suspecting the data.

---

## Why a layer used to say GTiff

Worth recording, because it explains a confusing symptom that older builds produced.

The plugin registered its decoder under `GDAL_VRT_TRUSTED_MODULES`. GDAL reads
`GDAL_VRT_PYTHON_TRUSTED_MODULES`. The names differ by one word, so GDAL refused to run
the pixel function — **every time**:

```
Python code needs to be executed, but it uses code from module
'owlg_qgis.vendor.owlg.vrt', whereas the current policy is to trust only code from
modules defined in the GDAL_VRT_PYTHON_TRUSTED_MODULES configuration option, which is
currently unset.
```

What made that much worse than a typo was the error handling: the failure was caught,
written to the log, and then the plugin quietly decoded the whole raster into a GeoTIFF
cache and loaded that instead. From the user's side nothing looked wrong — just a layer
whose Information panel said `GTiff` and whose size had nothing to do with the `.owlg`.

Both are fixed. The option name is correct, and a failed direct read is never silent: the
plugin shows the cause and asks. A GeoTIFF copy is only made if you accept it, and the
resulting layer is labelled `[GeoTIFF copy]` so the two can never be confused. Layer
metadata now also records the format, version, mode, base codec and on-disk `.owlg` size,
so the Information panel says what the layer really is rather than only naming the GDAL
driver used as a bridge.

---

## Requirements

| Package | Status | Consequence if missing |
|---|---|---|
| numpy | required | — (QGIS always ships it) |
| Pillow **or** GDAL | one required | — (QGIS ships both) |
| numba | optional | Exact decoding uses the pure-Python path: correct, ~2-4 s per 512 px tile |
| imagecodecs | optional | Pillow decodes AVIF instead |
| rasterio | optional | GDAL writes GeoTIFF instead |
| cryptography | optional | The bundled pure-Python AES-256-GCM decrypts instead |

---

## Using .owlg from GDAL without the plugin

The sidecar is not QGIS-specific:

```bash
owlg vrt data.owlg
export GDAL_VRT_ENABLE_PYTHON=YES
gdalinfo data.owlg.vrt
gdal_translate -of GTiff data.owlg.vrt out.tif
```

```python
import rasterio
with rasterio.open("data.owlg.vrt") as ds:
    window = ds.read(1, window=((0, 512), (0, 512)))
```

`GDAL_VRT_ENABLE_PYTHON=YES` trusts any module. To keep it narrow, use the same pair the
plugin uses:

```bash
export GDAL_VRT_ENABLE_PYTHON=TRUSTED_MODULES
export GDAL_VRT_PYTHON_TRUSTED_MODULES=owlg.vrt
```

---

## Troubleshooting

**"Decoder version mismatch"** — QGIS is still holding the previous plugin in memory.
Close QGIS and reopen it.

**"This environment cannot decode OWLG"** — the file's base codec has no working decoder
here. The diagnostics panel names which one. Two ways out:

```bash
owlg encode original.tif new.owlg --delta 2 --base webp    # re-encode from the original
owlg rebase old.owlg new.owlg --base webp --delta 2        # or convert, on a machine that can
```

`rebase` preserves the guarantee against the original raster and records it in the header
as `bound_vs_original`, so `owlg verify` on the result stays meaningful.

**A direct read fails and the dialog explains why** — usually the GDAL build has no
Python pixel function support, a policy blocks it, or the folder cannot take a sidecar.
Run the diagnostics self-test to tell these apart.

**Panning is slow in exact mode** — numba is not installed in the QGIS Python. Use fast
preview for navigation, or install numba.

**AVIF files are slower than WebP files on macOS** — the QGIS macOS build (3.44) ships
neither Pillow nor a GDAL AVIF driver, so the plugin decodes AVIF tiles through the
system `sips` tool, one subprocess per tile (~0.5 s). WebP decodes in-process through
GDAL. For files meant to be opened in QGIS on a Mac, prefer `--base webp`.

Full logs: **View ▸ Panels ▸ Log Messages ▸ OWLG**.
