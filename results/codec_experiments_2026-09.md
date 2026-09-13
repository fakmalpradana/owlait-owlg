# Where the bytes go, and what did not help (September 2026)

The question behind 0.1.0 was: how small can an OWLG get while keeping a
written, proven per-pixel bound — concretely, what is the smallest `delta`
that makes a drone orthophoto **20x smaller than raw**? These are the
measurements that decided what went into the release and what was dropped.
Everything here is reproducible with `benchmarks/bench_codec_lab.py` (rows
tagged *lab*) or the one-off scripts described in each section. Raster:
`sample_raster2.tif`, 1471 x 1128 x 4 (RGBA drone orthophoto, alpha constant),
6.64 MB raw, unless stated otherwise.

## 1. Where the bytes go

At high delta the base layer is most of the file and the correction layer is
almost entirely the cost of *saying where* the few pixels outside the bound
are, not of correcting them:

| base | delta | pixels outside bound | correction bytes | bits per outlier | total | vs raw |
|---|---:|---:|---:|---:|---:|---:|
| AVIF q75 (0.348 MB) | 8 | 2.33 % | 0.097 MB | 6.7 | 0.445 MB | 14.9x |
| AVIF q75 | 12 | 0.41 % | 0.023 MB | 11 | 0.371 MB | 17.9x |
| AVIF q60 (0.223 MB) | 12 | 2.97 % | 0.109 MB | 5.9 | 0.332 MB | 20.0x |
| AVIF q60 | 16 | ~1 % | 0.045 MB | — | 0.269 MB | 24.7x |
| WebP q75 (0.259 MB) | 12 | 2.55 % | 0.100 MB | 6.3 | 0.359 MB | 18.5x |
| WebP q75 | 16 | 0.78 % | 0.038 MB | 7.8 | 0.297 MB | 22.3x |

A pixel that is scattered at random among 2 % of positions costs at least
log2(1/0.02) ≈ 5.6 bits to locate, plus a sign and a magnitude. The coder
pays 6–7 bits in total, i.e. it is already 15–30 % below the order-0 entropy
of the residuals thanks to its contexts. **There is very little left to win in
the correction layer.** The outliers are sensor noise, not structure: 40 % of
8 x 8 blocks contain at least one, at ~5 % density inside those blocks, so
hierarchical significance coding cannot skip much either.

## 2. Encoder-side levers (kept)

| lever | effect | shipped as |
|---|---|---|
| AVIF ladder extended to q45 / q30 | 20x reached at delta 12–16 instead of never | `QUALITY_LADDERS` |
| WebP ladder extended to q50 / q40 | same for the portable base | `QUALITY_LADDERS` |
| WebP `method=6` | base −4 %, encode 2x slower, invisible to readers | default |
| overview levels at q50 (no bound there) | pyramid −35 %, tiled file −7 % | `OVERVIEW_Q`, `--overview-q` |
| quality pick at delta 0 includes the correction cost | tiled lossless no longer picks the worst base | `_Sampler` |
| search delta for a size goal | `--target 20x` → +/-14 DN, 22.5x on the drone ortho | `--target` |

Base codec choice at a fixed bound (lab, best of ladder): the optimum is
always the *highest* quality the ladder offers at small delta and moves down
only past delta 8. Swapping codecs changes the total by at most 7 %:

| delta | WebP | AVIF | JPEG XL (lossy) |
|---:|---:|---:|---:|
| 2 | 1.094 MB | 1.140 MB | 1.135 MB |
| 3 | 0.879 | 0.902 | 0.923 |
| 5 | 0.643 | 0.629 | 0.693 |
| 8 | 0.478 | 0.445 | 0.520 |

Chroma subsampling in the base (AVIF 4:2:0 vs 4:4:4) changes the total by
≈1 %. AVIF `speed=3` buys 4 % for 15x the encode time and is not the default.

## 3. Correction coder v2 — tried and rejected

Prototype encoder (`scratchpad/proto_v2.py`, exact range-coder byte counts)
with each idea isolated, measured as change in correction bytes:

| idea | delta 8 (AVIF q75) | delta 12 (WebP q75) | delta 2 (WebP q95) |
|---|---:|---:|---:|
| two-rate probability adaptation (shift 4 + 7) | −1.5 % | −1.3 % | −0.6 % |
| base-intensity context (3 bins) | worse | worse | worse |
| causal non-zero neighbour count (4 classes) | worse | worse | worse |
| 8 x 8 block skip flag | worse | worse | worse |
| all of the above combined | +2 % | +0.4 % | −0.2 % |

Nothing reaches the 3 % that would justify a second bitstream, a second
pure-Python decoder and a second JavaScript port. **The correction coder stays
at v1.** Context dilution on 512–1024 px tiles outweighs the modelling gain.

## 4. Two structural ideas that made things worse

*Closed-loop base*: feed the base encoder the original plus the residuals it
missed, so the lossy codec "tries harder" where it failed. Each iteration
increased both the base and the number of outliers (AVIF q60, delta 12:
20.0x → 18.7x → 17.8x → 17.0x). The codec was spending bits on noise.

*Pre-denoised base*: encode a median- or Gaussian-filtered image as the base so
it carries structure only. The base shrinks 25–45 %, but the correction layer
grows far more: 20.0x → 13.8x (median 3 x 3), 14.6x (Gaussian σ 0.7).
The lossy codec's own rate–distortion choice is already the best "denoiser"
for this bound.

## 5. Where 20x actually lands

| raster | 20x vs raw at | achieved |
|---|---|---|
| drone orthophoto 1471 x 1128 (`sample_raster2`) | delta 14, AVIF (`--target 20x`) | 22.5x |
| drone orthophoto 2937 x 2252 (`sample_raster`), tiled + pyramid | delta 20, AVIF | 21.7x |
| Landsat 7 `samples/rgb_small.tif` | not below delta 20 (19.7x at 20) | — |

Satellite scenes are noisier per pixel than aerial imagery and compress worse
at every bound; aerial orthophotos reach 20x with a bound of 12–16 DN,
which is 5–6 % of the 8-bit range. Whether that is acceptable depends on the
use — it is written in the header either way, and `owlg verify` proves it.
