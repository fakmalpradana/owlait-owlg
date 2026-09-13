# OWLG command-line reference

`owlg` is a single command with thirteen subcommands. It turns a uint8 GeoTIFF into a
`.owlg` file with a **hard per-pixel error bound**, reads that file back, proves the
bound holds, exposes the file to GDAL/QGIS without making a copy, builds `.owlgt` web
tile pyramids, and serves them over OGC API - Tiles/Maps and WMS 1.3.0. This page
documents every subcommand and every flag, with output captured from real runs against
`samples/rgb_small.tif` (791 x 718 x 3 uint8, EPSG:32618). See also the
[Python API](python-api.md), the [JavaScript/Node API](node-api.md), and the
[project README](../README.md).

## Contents

- [Invoking the CLI](#invoking-the-cli)
- [Global options and the passphrase](#global-options-and-the-passphrase)
- [Two things to get right first](#two-things-to-get-right-first)
- [`encode`](#encode)
- [`decode`](#decode)
- [`info`](#info)
- [`verify`](#verify)
- [`vrt`](#vrt)
- [`split`](#split)
- [`join`](#join)
- [`rebase`](#rebase)
- [`check`](#check)
- [`revert`](#revert)
- [`serve`](#serve)
- [`npy`](#npy)
- [`tiles`](#tiles)
- [Exit codes](#exit-codes)

## Invoking the CLI

Installing the package puts an `owlg` executable on the path:

```
pip install owlg[full]
owlg --help
```

From a source checkout without installing, the same entry point is
`python3 -m owlg.cli` with `src/` on `PYTHONPATH`:

```
PYTHONPATH=src python3 -m owlg.cli --help
```

Every example below was run through the second form from a working directory holding a
copy of `samples/rgb_small.tif`; the commands are written in the installed `owlg …` form
because the two produce identical output.

```
usage: owlg [-h] [--password PASSWORD] [--ask-password]
            {encode,decode,info,verify,vrt,split,join,rebase,check,revert,serve,npy,tiles}
            ...
```

## Global options and the passphrase

These three options precede the subcommand, not follow it: `owlg -P secret info f.owlg`,
never `owlg info f.owlg -P secret`.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `--password`, `-P` | string | none | Passphrase for an encrypted file, given inline. |
| `--ask-password`, `-A` | flag | off | Prompt for the passphrase on the terminal (`getpass`), so it never reaches the shell history or the process table. |
| `OWLG_KEY` | env var | unset | Passphrase taken from the environment. |
| `--no-color` | flag | off | Plain output. Colour is on only when stdout is a terminal; `NO_COLOR=1` also disables it and `OWLG_COLOR=always` forces it (for CI logs that render ANSI). The words never change, only the styling, so every example on this page is what a pipe or a test sees. |

Resolution order is `-P`, then `OWLG_KEY`, then `-A`; `owlg encode --encrypt` prompts
even without `-A`, because a passphrase is mandatory there. `owlg vrt` also exports the
resolved passphrase into `OWLG_KEY` for the child GDAL process.

Opening an encrypted file with no passphrase is a distinct, recognisable failure:

```
$ owlg info secret.owlg
This file is encrypted. Supply a passphrase with -P, -A, or env OWLG_KEY.
$ echo $?
1
```

```
$ OWLG_KEY='a long passphrase' owlg info secret.owlg
... header JSON elided ...
file 0.353 MB | base 0.136 | correction 0.216 | blobs 2 | encrypted True
bit-identical revert: NO - this file is near-lossless +/-2 DN
```

A wrong passphrase fails loudly rather than producing garbage pixels:

```
$ owlg -P wrong info secret.owlg
Error: wrong passphrase, or the file is corrupt
```

## Two things to get right first

**`--delta` is a hard bound, in DN, not a quality knob.** Every band value in the decoded
raster is within `±delta` of the original — not on average, not usually, but for every
single sample, and `owlg verify` proves it. `--delta 0` is lossless: the revert is
bit-identical to the source pixels. `--delta 3` means no pixel moved by more than 3 DN.
Going from 0 to 3 on the sample raster takes the file from 0.587 MB to 0.289 MB.

**`--layout` decides whether memory scales with the raster.** In the `tiled` layout (v4)
each tile is an independent blob with its own base and correction stream, plus an internal
overview pyramid, so encoding streams tile by tile and a read touches only the tiles it
needs — encode and read memory stay flat as the file grows. The `flat` layout (v3) holds
one base image for the whole raster and must decode all of it. `auto`, the default, reads
the source dimensions and picks `tiled` above 16 MPixel (`AUTO_TILED_PIXELS = 16 << 20`),
`flat` below. **For very large rasters, tiled is not an optimisation, it is the only
layout that works** — a flat 30 GB raster needs 30 GB of RAM to encode and to read.

`--recovery` exists only in the `flat` layout, and `--layout auto` therefore resolves to
`flat` whenever `--recovery` is given, whatever the raster size. Asking for both
explicitly is refused:

```
$ owlg encode rgb_small.tif out.owlg --delta 3 --recovery --layout tiled
--recovery is only available with --layout flat (in the tiled layout, --delta 0 is already bit-identical)
```

## `encode`

GeoTIFF (uint8) to `.owlg`.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Source GeoTIFF. Must be uint8; anything else is refused. |
| `dst` | path | required | Output `.owlg`. |
| `--delta` | int | `0` | Hard per-pixel error bound in DN. `0` = lossless, revert is bit-identical. |
| `--target` | ratio | none | A size goal against the raw pixels, `20` or `20x`. The encoder searches the smallest delta on its ladder (1…32) that reaches it, prints the probes, then encodes with that delta. The ratio is the goal; the delta it lands on is the promise, written to the header like any other. |
| `--layout` | `auto`\|`tiled`\|`flat` | `auto` | Storage layout. `auto` = tiled above 16 MPixel, flat below; forced to flat by `--recovery`. |
| `--base` | `webp`\|`avif`\|`auto` | `webp` | Base-layer codec. `webp` is portable (every GDAL, Pillow, Qt and browser build decodes it); `avif` is smaller; `auto` picks per layout — see the note below. |
| `--q` | int | auto-picked | Base-layer quality. Omit it and the encoder searches a quality ladder and keeps the smallest total. |
| `--tile` | int | `512` tiled, `1024` flat | Tile edge in pixels. |
| `--no-overviews` | flag | overviews on | Skip the internal pyramid. Tiled layout only. |
| `--min-overview` | int | `256` | Stop halving once a level's longest edge is at or below this. |
| `--overview-q` | int | `50` | Base quality of the overview levels. They carry no bound (display only), so a lower quality is free; never raised above the level-0 quality. |
| `--encrypt` | flag | off | AES-256-GCM over every blob and over the header itself. |
| `--iters` | int | `600000` | PBKDF2-SHA256 iterations for the key derivation. |
| `--recovery` | flag | off | Add a recovery tier so a near-lossless file can still revert bit-identically. Flat layout only. |
| `--codec` | string | `auto` | Legacy. Flat layout only, requires `--q`, and bypasses the quality search. Prefer `--base`. |

Lossless, flat (0.57 MPixel, so `auto` chose flat):

```
$ owlg encode rgb_small.tif rgb_ll.owlg
  791x718x3 uint8  raw 1.704 MB  layout flat
    searching webp quality 1/7 q=95: 0.681 MB
    searching webp quality 2/7 q=90: 0.675 MB
    ...
    searching webp quality 7/7 q=40: 0.739 MB
  base webp q=90  base 0.132 MB + correction 0.543 MB
  -> rgb_ll.owlg: 0.676 MB  2.52x  LOSSLESS  (0.9s)
```

Lossless here is a lossy WebP base plus a correction layer bounded at zero — that is
what keeps the file portable. `--base jxl` gives a JPEG XL lossless base instead (0.587
MB on this raster) for readers that have a JXL decoder. On a terminal the quality
search is a single progress bar; piped, it is the lines above.

Near-lossless at ±3 DN. The quality ladder is searched and the smallest total wins:

```
$ owlg encode rgb_small.tif rgb.owlg --delta 3
  791x718x3 uint8  raw 1.704 MB  layout flat
    searching webp quality 1/7 q=95: 0.295 MB
    searching webp quality 2/7 q=90: 0.284 MB
    searching webp quality 3/7 q=85: 0.284 MB
    searching webp quality 4/7 q=75: 0.293 MB
    searching webp quality 5/7 q=60: 0.299 MB
    searching webp quality 6/7 q=50: 0.305 MB
    searching webp quality 7/7 q=40: 0.313 MB
  base webp q=90  base 0.132 MB + correction 0.152 MB
  -> rgb.owlg: 0.285 MB  5.98x  bound +/-3 DN  (0.7s)
```

A size goal instead of a bound. `--target 8x` asks for a file at least eight times
smaller than the raw pixels; the encoder binary-searches its delta ladder, shows each
probe, and settles on the smallest bound that gets there — the header then says
`delta: 6`, exactly as if you had typed it:

```
$ owlg encode rgb_small.tif rgb_t.owlg --target 8x
  searching the smallest delta that gives 8x vs raw (base webp)
    delta 8  q=85  -> 0.164 MB   10.4x
    delta 3  q=90  -> 0.288 MB    5.9x
    delta 5  q=85  -> 0.216 MB    7.9x
    delta 6  q=85  -> 0.195 MB    8.7x
  -> delta 6 (+/-6 DN) is the smallest bound that reaches 8x
  791x718x3 uint8  raw 1.704 MB  layout flat
  base webp q=85  base 0.106 MB + correction 0.085 MB
  -> rgb_t.owlg: 0.191 MB  8.92x  bound +/-6 DN  (0.1s)
  target 8x reached: 8.9x with a guaranteed bound of +/-6 DN
```

The search never trades the bound for the ratio silently: if even delta 32 falls
short, it says so and encodes at 32. On a drone orthophoto `--target 20x --base avif`
lands on +/-14 DN (22.5x); see the README tables.

Tiled and lossless. Note that lossless here is a lossy WebP base plus a correction
stream that pulls every sample back to exactly the original value:

```
$ owlg encode rgb_small.tif rgb_tiled.owlg --layout tiled --delta 0
  791x718x3 uint8  raw 1.704 MB  layout tiled, 512px tiles
  base webp q=90  LOSSLESS
  level 0: 4 tiles, base 0.13 MB + correction 0.54 MB
  overview 1: 396x359, 1 tile, q=50, 0.02 MB
  overview 2: 198x180, 1 tile, q=50, 0.00 MB
  -> rgb_tiled.owlg: 0.696 MB  2.45x  LOSSLESS  3 levels (1.1s)
```

The overview levels are encoded at `--overview-q` (default 50) whatever level 0 uses:
they carry no bound, so quality there is only a matter of how the zoomed-out view
looks, and 50 is indistinguishable at overview scale.

On an 18 MPixel raster `auto` selects tiled without being asked, and encoding streams
tile row by tile row:

```
$ owlg encode big.tif big.owlg --delta 2 --q 75
  4500x4000x3 uint8  tile 512px  base=webp q=75  delta=+/-2
    level 0: tile row 1/8
    ...
    level 0: tile row 8/8
  level 0: 72 tiles, base 0.40 MB + correction 3.12 MB
  overview 1: 2250x2000, 20 tiles, 0.15 MB
  overview 2: 1125x1000, 6 tiles, 0.07 MB
  overview 3: 563x500, 2 tiles, 0.03 MB
  overview 4: 282x250, 1 tile, 0.01 MB
  overview 5: 141x125, 1 tile, 0.01 MB
  -> big.owlg: 3.797 MB  14.22x  6 levels  (6.0s)
```

`--min-overview` controls where the pyramid stops. The loop halves while the *current*
level's longest edge exceeds the threshold, so the smallest level is the first one at or
below it — with `--min-overview 1024` the last level is 563 x 500, not 1125 x 1000:

```
$ owlg encode big.tif big_mo.owlg --delta 2 --q 75 --min-overview 1024
  ...
  overview 1: 2250x2000, 20 tiles, 0.15 MB
  overview 2: 1125x1000, 6 tiles, 0.07 MB
  overview 3: 563x500, 2 tiles, 0.03 MB
  -> big_mo.owlg: 3.780 MB  14.29x  4 levels  (6.0s)
```

`--no-overviews` gives a single level and a slightly smaller file, at the cost of having
nothing to read when zoomed out:

```
$ owlg encode big.tif big_noov.owlg --delta 2 --q 75 --no-overviews
  ...
  level 0: 72 tiles, base 0.40 MB + correction 3.12 MB
  -> big_noov.owlg: 3.529 MB  15.30x  1 levels  (4.0s)
```

Recovery tier — near-lossless for everyday use, with the exact residual kept alongside:

```
$ owlg encode rgb_small.tif rgb_rec.owlg --delta 3 --recovery
  791x718x3 uint8  raw 1.704 MB  layout flat
    searching webp quality 1/7 q=95: 0.295 MB
    ...
  base webp q=90  base 0.132 MB + correction 0.152 MB
  recovery tier: +0.402 MB -> revert becomes bit-identical
  -> rgb_rec.owlg: 0.687 MB  2.48x  bound +/-3 DN  (0.8s)
```

Encryption. Header, blob directory, CRS and transform are all inside the ciphertext;
only the small envelope stays readable so the file remains identifiable:

```
$ owlg -P 'a long passphrase' encode rgb_small.tif secret.owlg --delta 2 --encrypt
  ...
  -> secret.owlg: 0.351 MB  4.85x  bound +/-2 DN ENCRYPTED  (1.5s)
```

**When to use which.** Archival master, or any file that must survive a bit-exact audit:
`--delta 0`. Working copy for viewing, tiling and analysis: `--delta 2` or `3`, which is
roughly a 2x saving over lossless on this data. Both properties at once, at the cost of
size: `--delta 3 --recovery`, then `split` the recovery tier off to cold storage. Leave
`--layout` alone unless you know the raster is near the 16 MPixel boundary and you want a
predictable result. Use `--base webp` (the default) for anything you will hand to other
people, and `--base avif` only when you control every machine that will open the file.

`--base auto` resolves differently in the two layouts. In the flat layout it means
JPEG XL lossless when this machine can decode JXL (only relevant at `--delta 0`), else
WebP. In the tiled layout it means AVIF when an AVIF encode *and* decode probe both
succeed, else WebP, and it prints what it resolved to. When the codec matters to the
people who will open the file, name it explicitly.
## `decode`

`.owlg` back to GeoTIFF, with the CRS, transform, colour interpretation and tags restored.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Input `.owlg`. |
| `dst` | path | required | Output GeoTIFF. |
| `--fast` | flag | off | Decode the base layer only and skip the correction stream. Fast, and outside the error bound. |
| `--tier` | `auto`\|`light`\|`full` | `auto` | `auto` uses the recovery tier when present; `light` stops at the near-lossless reconstruction; `full` demands a bit-identical result and fails if there is no recovery tier. |
| `--check-sha` | flag | off | Fail unless the decoded pixels hash to the SHA-256 stored at encode time. |

```
$ owlg decode rgb.owlg rgb_out.tif
-> rgb_out.tif  0.749 MB
$ owlg decode rgb.owlg rgb_fast.tif --fast
-> rgb_fast.tif  0.686 MB (base layer only, outside the bound)
```

`--check-sha` is a real gate, not a warning. On a near-lossless file the decoded pixels
are deliberately not the original pixels, so it fails, as it should:

```
$ owlg decode rgb.owlg x.tif --check-sha
Error: SHA-256 mismatch: the decoded result is not the original pixels
$ echo $?
1
```

`--tier full` on a file with no recovery tier refuses rather than silently downgrading:

```
$ owlg decode rgb.owlg x.tif --tier full
Error: this file has no recovery tier; the result cannot be bit-identical
```

On a file that does have one, `--tier full --check-sha` is the belt-and-braces
combination — it reconstructs through the recovery tier and then proves the result:

```
$ owlg decode full.owlg full.tif --tier full --check-sha
-> full.tif
$ echo $?
0
```

**When to use which.** `--fast` for a thumbnail or a quick look where the bound does not
matter. Plain `decode` for normal work. `--tier full --check-sha` when you are handing
the GeoTIFF to someone who will treat it as the original.

## `info`

A key/value sheet about the file, without decoding any pixels. Takes the global
passphrase options for encrypted files.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `--json` | flag | off | Print the raw header JSON (minus the blob directory) and nothing else, for scripts. |

Flat (v3) files list their layers:

```
$ owlg info rgb.owlg
── rgb.owlg ────────────────────────────────────────────────
format         : OWLG v3 flat
raster         : 791 x 718 x 3 uint8  raw 1.70 MB
size           : 0.285 MB  5.98x vs raw
guarantee      : bound +/-3 DN  every pixel of every band
base codec     : webp q=90
crs            : WGS 84 / UTM zone 18N EPSG:32618
pixel size     : 300.038 x 300.042  origin (101985.000, 2826915.000)
encrypted      : no
layer          bytes
──────────  ────────  ──────
base layer  0.132 MB  1 blob
correction  0.152 MB  1 tile
sha256         : 6d212801201b0e26...
```

Tiled (v4) files show the pyramid level by level and whether the tile-scan digest is
stored:

```
$ owlg info rgb_tiled.owlg
── rgb_tiled.owlg ──────────────────────────────────────────
format         : OWLG v4 tiled + pyramid
raster         : 791 x 718 x 3 uint8  raw 1.70 MB
size           : 0.696 MB  2.45x vs raw
guarantee      : LOSSLESS  revert is bit-identical
base codec     : webp q=90
crs            : WGS 84 / UTM zone 18N EPSG:32618
pixel size     : 300.038 x 300.042  origin (101985.000, 2826915.000)
encrypted      : no
tiles          : 512 px, 6 tiles, 10 blobs
pyramid  size       tiles     bytes  content
───────  ─────────  ─────  ────────  ─────────────────
level 0  791 x 718  2 x 2  0.675 MB  base + correction
level 1  396 x 359  1 x 1  0.015 MB  base only
level 2  198 x 180  1 x 1  0.004 MB  base only
tile digest    : stored eb33364349408284...
```

Only level 0 carries the error guarantee. The overview levels are averages of the
already-corrected data, stored base-only for display. A file produced by `rebase`
additionally shows a `vs original` line with the bound against the original GeoTIFF.

```
$ owlg info rgb.owlg --json | head -6
{
 "v": 3,
 "mode": "nearlossless",
 "w": 791,
 "h": 718,
 "bands": 3,
```

## `verify`

Compare a `.owlg` against the original GeoTIFF and prove the error bound over every
sample. Two positional arguments, `owlg` then `orig`, in that order.

For v4 files the comparison streams tile by tile and never holds the whole raster, so it
is safe on a 100 GB file; for v3 files both rasters are loaded.

```
$ owlg verify rgb.owlg rgb_small.tif
shape  : same
error  : max=3 (bound +/-3) -> BOUND PROVEN
         mean|e|=0.9965  rmse=1.4832  pixels changed=53.71%
geo    : transform same, crs same
$ echo $?
0
```

On a terminal the verdict is green, `*** VIOLATED ***` red, and a large tiled file shows
a progress bar while its tile rows stream through.

On a tiled lossless file `verify` also checks the stored tile-scan digest, which proves
two separate things: that the file really was made from *this* GeoTIFF, and — in lossless
mode — that the decode reproduces it bit for bit:

```
$ owlg verify rgb_tiled.owlg rgb_small.tif
shape  : same  (0.6 MPixel x 3 bands = 1.7 M samples checked)
error  : max=0 (bound +/-0) -> BOUND PROVEN
digest : original pixels MATCH (eb33364349408284...) -> this file really came from rgb_small.tif
         decoded pixels MATCH -> revert is bit-identical
lossless : bit-identical to the original = True
geo    : transform same, crs same
```

**`verify` exits 0 when the bound is proven and 2 when it is violated.** Pointing it at
the wrong original demonstrates both the message and the exit code:

```
$ owlg verify rgb.owlg other.tif
shape  : same
error  : max=129 (bound +/-3) -> *** VIOLATED ***
         mean|e|=3.1590  rmse=6.1369  pixels changed=58.60%
geo    : transform same, crs same
$ echo $?
2
```

That makes it usable as a CI gate with no output parsing:

```yaml
# .github/workflows/raster.yml
- name: Encode and prove the error bound
  run: |
    owlg encode data/ortho.tif build/ortho.owlg --delta 2
    owlg verify build/ortho.owlg data/ortho.tif   # exit 2 fails the job
```

Or in a shell script, distinguishing a violated bound from a broken run:

```bash
owlg verify build/ortho.owlg data/ortho.tif
case $? in
  0) echo "bound holds" ;;
  2) echo "BOUND VIOLATED - do not publish"; exit 1 ;;
  *) echo "verify could not run"; exit 1 ;;
esac
```

For a file produced by `rebase`, `verify` compares against `bound_vs_original` — the
bound relative to the original GeoTIFF — rather than the header's `delta`, which is
relative to the source `.owlg`. See [`rebase`](#rebase).

## `vrt`

Write a small `.vrt` sidecar so that GDAL-based software — QGIS, `gdalinfo`,
`gdal_translate`, rasterio, ArcGIS — opens the `.owlg` **directly**. No GeoTIFF copy is
made; what the application reads is still the `.owlg`, one window at a time.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Input `.owlg`. |
| `dst` | path | `<src>.vrt` | Output sidecar path. |
| `--fast` | flag | off | Sidecar reads the base layer only. |

```
$ owlg vrt rgb_tiled.owlg
…/rgb_tiled.owlg.vrt
  disk footprint: 0.696 MB (.owlg) + 4.4 kB (.vrt) — no GeoTIFF copy
  usage: export GDAL_VRT_ENABLE_PYTHON=YES  then open the .vrt in QGIS/gdalinfo
```

(The first line is the absolute path of the sidecar; the directory prefix is elided here.)

For a v4 file the internal pyramid is exposed as GDAL overviews, written as additional
sidecars next to the main one (`rgb_tiled.owlg.vrt.ov1.vrt`, `.ov2.vrt`, …), so a zoomed-out
view reads a coarse level instead of the whole raster.

The sidecar uses a VRT *derived* band with a Python pixel function, which GDAL will only
run when it is told to:

```
export GDAL_VRT_ENABLE_PYTHON=YES
export GDAL_VRT_PYTHON_TRUSTED_MODULES=owlg.vrt
gdalinfo rgb_tiled.owlg.vrt
```

Reading the metadata needs neither of those and works everywhere:

```
$ gdalinfo rgb.vrt
Driver: VRT/Virtual Raster
Size is 791, 718
...
Metadata:
  OWLG_BASE_CODEC=webp
  OWLG_BYTES=352830
  OWLG_DELTA=2
  OWLG_MODE=nearlossless
  OWLG_SOURCE=api.owlg
```

Reading *pixels* through the sidecar additionally requires that the GDAL build embeds a
working Python interpreter which can `import owlg` — set `PYTHONPATH` for that interpreter
if the package is not installed system-wide. Inside QGIS the bundled plugin configures
this itself.

## `split`

Split a file that has a recovery tier into a light `.owlg` plus a `.owlr` recovery file.
Three positional arguments; no flags.

```
$ owlg split rgb_rec.owlg light.owlg rec.owlr
-> light.owlg (0.285 MB, distribute)  +  rec.owlr (0.402 MB, archive)
```

The light half is byte-for-byte the near-lossless file you would have got from
`owlg encode --delta 3` without `--recovery`, and reports itself as such:

```
$ owlg info light.owlg
...
file 0.289 MB | base 0.136 | correction 0.151 | blobs 2 | encrypted False
bit-identical revert: NO - this file is near-lossless +/-3 DN
```

A file with no recovery tier is refused:

```
$ owlg split rgb.owlg a.owlg a.owlr
Error: this file has no recovery tier
```

## `join`

Rejoin a light `.owlg` and its `.owlr`. Three positional arguments; no flags.

```
$ owlg join light.owlg rec.owlr full.owlg
-> full.owlg (0.687 MB, revert is now bit-identical)
```

The `.owlr` records the parent's SHA-256, so pairing the wrong two files is caught rather
than producing corrupt pixels (`this recovery file does not belong to this light file`).

**When to use `split`/`join`.** This pair is the reason `--recovery` exists. Encode once
with `--delta N --recovery`, `split`, then ship the light file to the people who need to
look at the data and keep the `.owlr` in cold storage or a separate archive. The
distributed file stays small, and the exact original remains recoverable by whoever holds
both halves. `join` followed by `decode --tier full --check-sha` is the full round trip.

## `rebase`

Re-encode a file with a different base-layer codec, for example to make an AVIF-based
file readable on a machine with no AVIF decoder.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Input `.owlg`. |
| `dst` | path | required | Output `.owlg`. |
| `--base` | `webp`\|`avif`\|`auto` | `webp` | Target base-layer codec. |
| `--delta` | int | `0` | Bound of the re-encode, measured against the decoded content of `src`, not against the original GeoTIFF. |

The correction layer is computed relative to the decoded base, so the base cannot simply
be swapped — the file is genuinely re-encoded. With `--delta 1`, the tool states the
combined guarantee explicitly:

```
$ owlg rebase rgb_avif.owlg rgb_portable.owlg --base webp --delta 1
  source: base=avif delta=+/-2
  target: base=webp delta=+/-1 against the decoded source content
  791x718x3 uint8  raw 1.704 MB  layout flat
    searching webp quality 1/7 q=95: 0.477 MB
    ...
    searching webp quality 7/7 q=40: 0.500 MB
  base webp q=90  base 0.134 MB + correction 0.326 MB
  -> rgb_portable.owlg: 0.462 MB  3.69x  bound +/-1 DN  (0.7s)
  WARNING: the guarantee vs the ORIGINAL GeoTIFF is now +/-3 DN (2 from the source + 1 from re-encoding)
```

Two details of the header are worth knowing. `delta` in a rebased file is the bound
against the *decoded source `.owlg`*; the bound against the original GeoTIFF is stored
separately as `bound_vs_original` (2 + 1 = 3 above), together with `rebased_from`
recording the source codec and delta. `owlg verify` and `owlg info` use
`bound_vs_original` when it is present, so verifying a rebased file against the original
GeoTIFF reports the combined bound and exits 0 (`owlg info` shows it as `vs original :
+/-3 DN  (rebased from avif +/-2)`). And `--delta 0` honours `--base`: the
default is a WebP base with a zero-bounded correction layer, which is bit-exact and
portable; `--base jxl` gives the smallest lossless result when the reader has JXL.

## `check`

Report what this environment can actually decode, by decoding tiny probe blobs rather
than by checking which modules import. Optionally, say whether one specific file can be
opened here.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | optional | An `.owlg` to test against this environment's decoders. |

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
$ echo $?
0
```

With a file, it names the base codec and says whether this machine can open it, and if
not, how to fix it:

```
$ owlg check rgb_avif.owlg
image backends : imagecodecs, pillow+avif
GeoTIFF writer : rasterio
encryption     : cryptography
numba          : present
real decode probes:
  avif : YES
  webp : YES
  jxl  : YES

file rgb_avif.owlg uses base avif -> CAN be opened here
```

`check` exits `0` if at least one codec decodes and `2` if none do — run it first in a
container or CI image, before blaming a file.

## `revert`

Mosaic an `.owlgt` tile pyramid back into a GeoTIFF. The output is RGB plus an alpha band.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Input `.owlgt`. |
| `dst` | path | required | Output GeoTIFF. |
| `--zoom` | int | maximum zoom | Which pyramid level to mosaic. |
| `--t-srs` | string | none (stays EPSG:3857) | Warp the mosaic to this CRS. |

```
$ owlg revert ortho.owlgt ortho_z11.tif
-> ortho_z11.tif  crs=EPSG:3857 zoom=11 warped=False
$ owlg revert ortho.owlgt ortho_z9.tif --zoom 9
-> ortho_z9.tif  crs=EPSG:3857 zoom=9 warped=False
```

Without `--t-srs` no resampling happens at all beyond the mosaic itself, which is what you
want when the pyramid is the authoritative copy.

With `--t-srs` the mosaic is warped through rasterio (`calculate_default_transform` +
`reproject`, bilinear by default) and tagged `OWLGT_WARPED_FROM=EPSG:3857`:

```
$ owlg revert ortho.owlgt ortho_utm.tif --t-srs EPSG:32749
-> ortho_utm.tif  crs=EPSG:32749 zoom=11 warped=True
```

## `serve`

Publish one or more `.owlgt` files over OGC API - Tiles, OGC API - Maps and WMS 1.3.0. The
HTTP layer is standard library only, so it runs anywhere the package installs.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path(s) | required | One or more `.owlgt` files. Each becomes a collection named after its filename stem. |
| `--host` | string | `127.0.0.1` | Bind address. |
| `--port` | int | `8080` | Bind port. |

```
$ owlg serve ortho.owlgt --port 8977
OWLG serving 1 collections at http://127.0.0.1:8977
  OGC API landing : http://127.0.0.1:8977/
  Collections     : http://127.0.0.1:8977/collections
  OGC tiles       : http://127.0.0.1:8977/collections/<id>/map/tiles/WebMercatorQuad/{z}/{y}/{x}.png
  XYZ (Leaflet)   : http://127.0.0.1:8977/xyz/<id>/{z}/{x}/{y}.png
  WMS 1.3.0       : http://127.0.0.1:8977/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0
```

Every route below was requested against a running instance; the status and media type
shown are what came back.

| Path | Returns | Notes |
| --- | --- | --- |
| `/` | `200 application/json` | Landing page with `self`, `conformance`, `data`, `service-desc`, tiling-schemes and WMS links. |
| `/conformance` | `200 application/json` | Nine conformance classes: OGC API Common core/json/collections, Tiles core/tileset/tilesets-list/geodata-tilesets, Maps core/scaling. |
| `/api` | `200 application/json` | Minimal OpenAPI 3.0.3 description. |
| `/tileMatrixSets` | `200 application/json` | Lists the single tile matrix set, `WebMercatorQuad`. |
| `/tileMatrixSets/WebMercatorQuad` | `200 application/json` | Full definition, zoom 0-24. |
| `/collections` | `200 application/json` | All collections, each with a CRS84 bbox and an `owlgt` block giving profile, delta, zoom range and tile count. |
| `/collections/{id}` | `200 application/json` | One collection. |
| `/collections/{id}/map/tiles` | `200 application/json` | Tileset list. |
| `/collections/{id}/map/tiles/WebMercatorQuad` | `200 application/json` | Tileset with `tileMatrixSetLimits` per zoom. |
| `/collections/{id}/map/tiles/WebMercatorQuad/{tileMatrix}/{tileRow}/{tileCol}[.ext]` | `200 image/*` | **OGC order: z / row / col, that is z/y/x.** |
| `/xyz/{id}/{z}/{x}/{y}[.ext]` | `200 image/*` | XYZ convenience route for Leaflet and MapLibre. **z/x/y.** |
| `/collections/{id}/map` | `200 image/*` | OGC API - Maps. `bbox`, `crs` (default `CRS:84`), `width`, `height`, `f`. |
| `/wms?…&REQUEST=GetCapabilities` | `200 text/xml` | WMS 1.3.0 capabilities. |
| `/wms?…&REQUEST=GetMap` | `200 image/*` | `LAYERS`, `CRS`/`SRS`, `BBOX`, `WIDTH`, `HEIGHT`, `FORMAT`, `TRANSPARENT`. Max 8192 x 8192. |

The two tile routes are the same tile under two different coordinate conventions, and the
byte counts prove it:

```
$ curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}B\n' \
    'http://127.0.0.1:8977/collections/ortho/map/tiles/WebMercatorQuad/9/219/144.png'
200 image/png 90807B
$ curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}B\n' \
    'http://127.0.0.1:8977/xyz/ortho/9/144/219.png'
200 image/png 90807B
```

Format is chosen by extension or by the `f` query parameter; PNG is the default,
`jpeg`/`jpg` and `webp` are also encoded on the fly:

```
$ curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}B\n' \
    'http://127.0.0.1:8977/xyz/ortho/9/144/219.jpeg'
200 image/jpeg 14618B
```

OGC API - Maps and WMS GetMap both render an arbitrary bbox:

```
$ curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}B\n' \
    'http://127.0.0.1:8977/collections/ortho/map?bbox=-78.9,23.6,-76.6,25.5&width=400&height=300'
200 image/png 162942B
$ curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}B\n' \
    'http://127.0.0.1:8977/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=ortho&CRS=EPSG:3857&BBOX=-8789636,2700459,-8524406,2943560&WIDTH=512&HEIGHT=512&FORMAT=image/png'
200 image/png 214443B
```

WMS 1.3.0 axis order is honoured: `CRS=EPSG:4326` is interpreted as lat,lon and `CRS=CRS:84`
as lon,lat. Unsupported operations return a proper service exception, not a stack trace:

```
$ curl -s 'http://127.0.0.1:8977/wms?SERVICE=WMS&REQUEST=GetFeatureInfo'
<?xml version="1.0" encoding="UTF-8"?>
<ServiceExceptionReport version="1.3.0" xmlns="http://www.opengis.net/ogc"><ServiceException code="OperationNotSupported">REQUEST=getfeatureinfo</ServiceException></ServiceExceptionReport>
```

A tile outside the pyramid's zoom range is a 404 that says why:

```
$ curl -s 'http://127.0.0.1:8977/collections/ortho/map/tiles/WebMercatorQuad/4/1/1.png'
{
 "code": 404,
 "description": "zoom 4 outside 8-11"
}
```

Every response carries `Access-Control-Allow-Origin: *`, and tiles carry
`Cache-Control: public, max-age=86400`. The server binds to loopback by default; pass
`--host 0.0.0.0` deliberately, and put a reverse proxy in front of it for anything public.
`serve` accepts `.owlgt` only — handing it a `.owlg` fails with `Error: not an OWLGT file`.
There is also a zero-dependency Node server with a smaller feature set; see the
[Node API](node-api.md#the-node-server).

## `npy`

Export the decoded raster as a NumPy array for ML pipelines.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Input `.owlg`. |
| `dst` | path | required | Output file. |
| `--format` | `npy`\|`npz`\|`memmap` | `npy` | `npy` = plain array; `npz` = compressed archive with the metadata embedded; `memmap` = a `.npy` that `np.load(..., mmap_mode='r')` opens without reading it into RAM. |
| `--layout` | `CHW`\|`HWC` | `CHW` | Band-first (rasterio/PyTorch) or band-last (PIL/TensorFlow). |
| `--fast` | flag | off | Base layer only. |

```
$ owlg npy rgb.owlg rgb.npy
-> rgb.npy  shape=(3, 718, 791) layout=CHW
$ owlg npy rgb.owlg rgb.npz --format npz --layout HWC
-> rgb.npz  shape=(718, 791, 3) layout=HWC
$ owlg npy rgb.owlg rgb_mm.npy --format memmap
-> rgb_mm.npy  shape=(3, 718, 791) layout=CHW
```

`npy` and `memmap` also write a sidecar `<name>.meta.json` carrying width, height, bands,
dtype, CRS WKT, the six-element transform, nodata, delta, colour interpretation and
layout. `npz` embeds the same metadata as a `meta` entry inside the archive alongside
`image`.

The printed `shape=` follows the requested layout, so it matches what `np.load` will
report.

## `tiles`

Build an `.owlgt` web tile pyramid from an EPSG:3857 source. Child tiles may be coded as a
residual against the upsampled parent — in a slippy map the client always already holds
the parent — so the inter-level redundancy that inflates an ordinary pyramid disappears
without adding a request. The mode is chosen per tile, whichever is smaller.

| Flag | Type | Default | Meaning |
| --- | --- | --- | --- |
| `src` | path | required | Source raster or `.owlg`. **Must already be EPSG:3857.** |
| `dst` | path | required | Output `.owlgt`. |
| `--minzoom` | int | required | Lowest zoom to build. |
| `--maxzoom` | int | required | Highest zoom to build. |
| `--profile` | `view`\|`exact` | `view` | `view` stores every tile standalone so it can be served as AVIF with no server-side decode. `exact` carries a per-pixel bound through the pyramid. |
| `--delta` | int | `3` | Per-pixel bound in DN. |
| `--q` | int | `72` | AVIF quality for the tile base. |

```
$ owlg tiles web.tif ortho.owlgt --minzoom 8 --maxzoom 11
  pyramid z8-11: 277 tiles z8:6 z9:20 z10:56 z11:195
  -> ortho.owlgt: 0.754 MB (tiles 0.747 + index 0.007)  dedup 84 tiles  (8s)
     z8: residual    0 tiles/     0.0 kB | standalone    6/    29.7 kB
     z9: residual    0 tiles/     0.0 kB | standalone   20/    84.0 kB
     z10: residual    0 tiles/     0.0 kB | standalone   56/   199.9 kB
     z11: residual    0 tiles/     0.0 kB | standalone  195/   461.2 kB
```

```
$ owlg tiles web.tif exact.owlgt --minzoom 9 --maxzoom 11 --profile exact --delta 0
  pyramid z9-11: 271 tiles z9:20 z10:56 z11:195
  -> exact.owlgt: 6.584 MB (tiles 6.577 + index 0.008)  dedup 82 tiles  (16s)
     z9: residual    0 tiles/     0.0 kB | standalone   20/   648.9 kB
     z10: residual   53 tiles/  1449.8 kB | standalone    3/   297.7 kB
     z11: residual  195 tiles/  4186.3 kB | standalone    0/     0.0 kB
```

A non-3857 source is refused up front: `Error: source must be EPSG:3857`. Reproject first.

**When to use which.** `--profile view` for anything a browser will look at: every tile is
a plain AVIF blob, so `owlg serve`, the Node server and the in-browser MapLibre/Leaflet
helpers can hand it straight to the browser with no decoding at all. `--profile exact` when
the pyramid must carry a provable bound — it is roughly 9x larger on this data, and its
tiles cannot be passed through, so serving it requires the Python decoder.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. For `verify`, the error bound was proven; for `check`, at least one codec decodes here. |
| `1` | Any error: bad input, missing file, wrong or missing passphrase, unsupported combination of flags, SHA mismatch under `--check-sha`, `--tier full` with no recovery tier. The message is printed to stderr as `Error: …`, or as the encryption hint for a missing key. |
| `2` | `verify`: the error bound was **violated**. `check`: no image codec in this environment can decode OWLG. Also emitted by the argument parser for a usage error (unknown subcommand, missing required flag), so treat `2` as meaningful only after the command line has parsed. |
