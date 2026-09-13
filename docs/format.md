# The OWLG and OWLGT formats

This document describes what is actually on disk, so that a reader can be written
against it without reading the Python. It covers the container, both layouts, the
codec, and the OWLGT tile pyramid.

See also the [CLI reference](cli.md), the [Python API](python-api.md), the
[Node API](node-api.md) and the [project README](../README.md).

## Contents

- [The guarantee](#the-guarantee)
- [Container](#container)
- [Header fields](#header-fields)
- [Layout v3 — flat](#layout-v3--flat)
- [Layout v4 — tiled](#layout-v4--tiled)
- [Band groups](#band-groups)
- [The correction codec](#the-correction-codec)
- [Encryption](#encryption)
- [Integrity digests](#integrity-digests)
- [OWLGT](#owlgt)
- [Design decisions and their costs](#design-decisions-and-their-costs)

---

## The guarantee

An OWLG file records a `delta`. For every band `b`, row `y` and column `x`:

```
|original[b, y, x] - decoded[b, y, x]| <= delta
```

with no exceptions, no percentile, and no dependence on image content. `delta = 0` means
the decoded array is bit-identical to the original.

This is the format's single promise. Everything below exists to keep it while making the
file small.

---

## Container

All integers are little-endian.

```
magic            4 bytes   'OWLG'
version          1 byte    3 = flat, 4 = tiled
flags            1 byte    bit 0 = encrypted
[ if encrypted:
  kdf_id         1 byte    1 = PBKDF2-HMAC-SHA256
  kdf_iters      uint32
  salt          16 bytes
  nonce_prefix   8 bytes
]
hdr_len          uint32
hdr_bytes        hdr_len bytes   JSON; AES-256-GCM sealed when encrypted (blob index 0)
payload          concatenated blobs, located by the header's `dir`
```

`dir` is a list of `[offset, length]` pairs. Offsets are relative to the first payload
byte, which is the byte immediately after `hdr_bytes`. Every other structure in the
header refers to blobs by their index into `dir`, never by offset, so blobs can be
reordered or appended without rewriting anything but the header.

`.owlr` recovery files (produced by `owlg split`) use the magic `OWLR` and the same
container shape.

---

## Header fields

Common to both layouts:

| Field | Type | Meaning |
|---|---|---|
| `v` | int | 3 or 4 |
| `mode` | str | `lossless` or `nearlossless`. **Descriptive only** — never use it to decide whether a correction layer exists (a `delta = 0` file built on a lossy base has one). |
| `w`, `h`, `bands` | int | Raster dimensions; `bands` counts constant bands too |
| `delta` | int | The bound, and the quantizer step used at decode |
| `codec` | str | Base layer codec: `webp`, `avif`, `jxl_lossless` |
| `q` | int | Base layer quality that was chosen |
| `tile` | int | Tile edge in pixels |
| `const` | dict | `{band_index_as_string: value}` for bands that are a single value everywhere. These are never encoded; the decoder fills them from this dict. |
| `coded` | list | Indices of the bands that ARE encoded, in order |
| `groups` | list | `coded` split into groups of at most three — see [Band groups](#band-groups) |
| `crs` | str | WKT, or null |
| `transform` | list | Six affine coefficients `(a, b, c, d, e, f)` in rasterio order |
| `nodata` | list | One entry per band, may contain nulls |
| `colorinterp` | list | Per-band colour interpretation names |
| `tags` | dict | GeoTIFF metadata tags, carried through verbatim |
| `dir` | list | `[offset, length]` per blob |
| `bound_vs_original` | int | *(optional)* Present on rebased files: the guarantee against the ORIGINAL raster, which differs from `delta`. `owlg verify` prefers this when present. |
| `rebased_from` | dict | *(optional)* `{codec, delta}` of the file this one was rebased from |

v3 only: `nty`, `ntx` (tile grid), `sha256` (of the original pixel array), `n_base`,
`n_corr`, `n_rec` (blob counts per section), `tiers`.

v4 only: `levels` (see below), `sha256_scan`.

---

## Layout v3 — flat

The whole raster is one unit. Blobs are ordered: all base blobs, then all correction
blobs, then all recovery blobs.

```
dir[0 .. n_base)                       one base blob per band group
dir[n_base .. n_base+n_corr)           one correction blob per tile, row-major
dir[n_base+n_corr .. +n_rec)           recovery tier, same tile order
```

Decoding: decode every base blob, concatenate the groups into a `(bands, h, w)` array,
then apply each tile's correction stream in place.

The correction layer is applied **whenever `n_corr > 0`**, regardless of `mode`.

`n_rec > 0` means the file carries a recovery tier: a second correction pass, bounded at
zero, computed against the delta-level reconstruction. `owlg split` separates it into a
`.owlr` file so the light half can be distributed while the recovery half is archived;
`owlg join` puts them back together and validates `parent_sha256`.

The flat layout holds the entire raster in memory during both encode and decode. That is
the reason v4 exists.

---

## Layout v4 — tiled

The raster is a grid of independent tiles, plus an overview pyramid, all inside one file.

```json
"levels": [
  {"w": 791, "h": 718, "nty": 2, "ntx": 2,
   "base": [[0], [2], [4], [6]],
   "corr": [1, 3, 5, 7]},
  {"w": 396, "h": 359, "nty": 1, "ntx": 1, "base": [[8]], "corr": null},
  {"w": 198, "h": 180, "nty": 1, "ntx": 1, "base": [[9]], "corr": null}
]
```

- `levels[0]` is full resolution and is the only level carrying the error guarantee.
- Every further level is the previous one box-filtered 2x, stored **base only**: it
  exists for display when zoomed out, and carries no correction layer. Because there is
  no bound to keep there, overview blobs are encoded at a lower quality than level 0
  (`--overview-q`, default 50); a reader treats them like any other base blob.
- Within a level, tile `(ty, tx)` is at index `ty * ntx + tx`.
- `base[i]` is a **list** of blob indices, one per band group. Readers should accept a
  bare integer as well, meaning a single group, for files written before grouping
  existed.
- `corr[i]` is a single blob index, or the whole field is `null` for overview levels.

Tile edges are clipped at the right and bottom, so a tile is at most `tile x tile` and
may be smaller.

**Why tiles are genuinely independent.** The entropy coder's context window is confined
to the tile: the encoder and the decoder both see only that tile's own pixels. Nothing
carries across a tile boundary. That is what makes windowed reads, the pyramid, and
streaming encode possible at all — and it is a real cost, because a coder allowed to look
across boundaries would compress slightly better.

Encoding streams: a tile is read from the source, encoded, written, and dropped. Reading
keeps a bounded LRU cache of decoded tiles (48 by default). Neither scales with raster
size.

---

## Band groups

A base blob is an ordinary RGB image, so it holds at most three bands. `groups` splits
`coded` into runs of three:

```
6 bands -> [[0,1,2], [3,4,5]]      two base blobs per tile
4 bands -> [[0,1,2], [3]]          two base blobs; the second has one real band
2 bands -> [[0,1]]                 one base blob with two real bands
```

A trailing group of one or two bands is **padded up to three channels by appending
copies of its first channel**. The real channels must be kept in place; replacing all
three with band 0 silently destroys the second band of a two-band group — invisible at
`delta > 0` because the correction layer repairs it, and a real data loss at `delta = 0`.
This was a genuine bug in earlier versions; `tests/test_bands.py` exists to keep it dead.

The decoder slices each decoded group back to `len(group)` channels and concatenates.

---

## The correction codec

```
GeoTIFF  ->  base layer (lossy WebP/AVIF, or lossless JPEG XL)
              +
             correction layer over the decoded base
```

For each pixel, with `e` the residual between original and decoded base, and
`s = 2 * delta + 1`:

```
q = (e + delta) // s        quantized residual (a JPEG-LS style quantizer)
reconstruction = clamp(base + q * s, 0, 255)
```

This guarantees `|original - reconstruction| <= delta` by construction, for any base
layer at all — including an extremely lossy one. The base quality is chosen by encoding
a few sample tiles at several qualities and keeping whichever gives the smallest
`base + correction` total; a worse base makes a bigger correction layer, so there is a
real optimum rather than a monotone trade. At small deltas that optimum is the highest
quality on the ladder; past delta 8 it moves down (AVIF q60/q45, WebP q75/q50), which is
where 20x vs raw is reached on aerial imagery. `--target RATIO` searches the delta
ladder for the smallest bound whose optimum meets a size goal.

`q` is entropy-coded with a **binary adaptive range coder** (LZMA-style, 15-bit
probabilities, shift 5) using CABAC-style binarization: a zero flag, then a sign, then
truncated unary, then Exp-Golomb bypass bits for large magnitudes.

**Context modelling.** The zero flag is coded in one of 567 contexts:

```
3 band slots (band 0, band 1, band 2-and-above)
  x 7 activity bins (from the base-layer gradient around the pixel)
  x 3 (left neighbour's quantized residual: negative / zero / positive)
  x 3 (upper neighbour, same)
  x 3 (same pixel in the previous band, same)
```

The sign, the truncated-unary bits and the Exp-Golomb prefix bits have their own smaller
context sets (21, 63 and 48), for 699 adaptive binary probabilities in total, all reset
at the start of every tile.

The important property is that the context is **non-causal**. A conventional DPCM coder
can only condition on pixels it has already decoded. Here the fully decoded base layer is
available on both sides before correction decoding starts, so the coder can look at what
the base says about the pixel to the right and below as well. That is the main reason a
lossy base plus corrections beats a straight lossless coder.

Two decoder implementations exist and are verified bit-identical: a numba-shaped one and
a flat pure-Python one (`codec_py.py`). A single dispatch point in `codec.py` selects
between them; importing `dec_tile` from anywhere else would bypass it and silently run
the numba-shaped code as plain Python, about 60x slower.

---

## Encryption

`--encrypt` seals the header and every blob with AES-256-GCM. The key comes from
PBKDF2-HMAC-SHA256 over the passphrase, with the salt and iteration count stored in the
clear (they must be, to derive the key at all).

Nonces are `nonce_prefix (8 bytes) || counter_be32 (4 bytes)`, where the counter is 0 for
the header and `blob_index + 1` for each blob. Each (key, nonce) pair is therefore used
exactly once, which is what GCM requires.

Because the header is sealed, dimensions, CRS, transform and the blob directory are all
encrypted — not just the pixels. An encrypted OWLG reveals its magic, version, KDF
parameters, and total size, and nothing else.

A pure-Python AES-256-GCM implementation ships alongside, verified bit-identical to
`cryptography` in the test suite, so encrypted files open in environments (such as a QGIS
Python) with no crypto library.

---

## Integrity digests

**v3: `sha256`** — SHA-256 over the original `(bands, h, w)` pixel array in C order.
Checked on decode when `--check-sha` is given.

**v4: `sha256_scan`** — SHA-256 over a *tile-scan serialization*, so it can be computed
while streaming and never needs the whole raster in memory:

```
digest = SHA256(
    json({"w", "h", "bands", "tile", "const", "coded"}, sorted keys, no spaces)
    || for ty in 0..nty-1:
         for tx in 0..ntx-1:
           bytes of the source tile, shape (len(coded), th, tw), C order
)
```

The digest is of the **original** pixels, not the decoded ones. So:

- at any `delta`, recomputing it from a GeoTIFF proves the `.owlg` came from that file;
- at `delta = 0`, recomputing it from the decoded pixels proves the revert is
  bit-identical — without writing a GeoTIFF to compare against.

`owlg verify` does both, streaming, which is why it works on a 100 GB file.

---

## OWLGT

A single-file web tile pyramid in EPSG:3857 / WebMercatorQuad.

```
magic 'OWLGT1' (6 bytes) | hdr_len uint32 | header JSON (zoom range, profile, delta, tile index)
payload: tiles, addressed by an offset directory
```

Tiles whose encoded bytes are identical are stored once and referenced many times, which
matters at low zoom where large areas are uniform.

Two profiles:

- **`view`** — each tile is a plain image blob (mode byte 0). Any client that can decode
  the base codec can display it, with no OWLG decoder at all.
- **`exact`** — tiles carry a correction layer with the same hard bound as OWLG (mode
  bytes 1 and 2), so a tile can be used for analysis and not only for looking at.
  Reading these requires a full OWLG decoder.

`owlg serve` exposes a pyramid over OGC API - Tiles/Maps and WMS 1.3.0. The WMS side
observes the 1.3.0 axis-order rule: `EPSG:4326` is lat,lon while `CRS:84` is lon,lat.

`owlg revert` mosaics a pyramid back to a GeoTIFF, optionally warping out of EPSG:3857
with `--t-srs`.

---

## Design decisions and their costs

Stated because a format document that only lists advantages is not useful.

**Tile-confined context.** Buys independent tiles, windowed reads, a pyramid, and flat
memory. Costs compression: a coder allowed to predict across tile boundaries would do
better, and smaller tiles cost more.

**An overview pyramid inside the file.** Buys instant zoom-out. Costs about 10-25% of
level 0, depending on how deep the pyramid goes.

**Base-only overviews.** Buys fast display at every zoom. Costs the guarantee at those
levels — overview pixels are a box filter of corrected data, but they carry no bound of
their own, so analysis must read level 0.

**A lossy base plus corrections, rather than a lossless coder.** Buys a large size
reduction at small bounds, and the bound itself. Costs a dependency on an image codec
(WebP, AVIF or JXL) being available to the reader — which is why `owlg check` probes for
real and why `owlg rebase` exists.

**uint8 only.** Keeps the codec and the contexts simple. Costs all int16, uint16 and
float rasters, which is a real limitation for DEMs and higher bit depth multispectral
imagery.
