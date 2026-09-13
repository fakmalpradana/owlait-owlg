### `sample_raster.tif` — 2937 x 2252 x 4 uint8, 26.5 MB raw


**Reference formats**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred (lossless)<br><sub>baseline for the "vs GeoTIFF" column</sub> | 14.573 MB | 1.82x | 1.00x | **0** | 0.000 | 3.9 s |
| GeoTIFF LZW (lossless) | 16.607 MB | 1.59x | 0.88x | **0** | 0.000 | 0.3 s |
| GeoTIFF ZSTD (lossless) | 14.174 MB | 1.87x | 1.03x | **0** | 0.000 | 0.5 s |
| COG DEFLATE (lossless) | 14.637 MB | 1.81x | 1.00x | **0** | 0.000 | 1.4 s |
| GeoTIFF JPEG q85 (lossy) | 5.559 MB | 4.76x | 2.62x | 34 | 3.273 | 0.2 s |
| GeoTIFF JPEG q75 (lossy) | 4.225 MB | 6.26x | 3.45x | 53 | 4.560 | 0.2 s |
| JP2 OpenJPEG lossless | 8.709 MB | 3.04x | 1.67x | **0** | 0.000 | 0.3 s |
| JP2 OpenJPEG q25 (lossy, ~4x) | 5.679 MB | 4.66x | 2.57x | 4 | 0.585 | 0.3 s |
| JP2 OpenJPEG q10 (lossy, ~10x) | 2.647 MB | 9.99x | 5.50x | 21 | 1.635 | 0.3 s |
| JP2 OpenJPEG q5 (lossy, ~20x) | 1.324 MB | 19.98x | 11.00x | 63 | 3.737 | 0.3 s |
| JP2 OpenJPEG q3 (lossy, ~33x) | 0.796 MB | 33.23x | 18.30x | 75 | 5.951 | 0.3 s |
| JPEG XL lossless | 8.080 MB | 3.27x | 1.80x | **0** | 0.000 | 0.9 s |
| JPEG XL distance 1 (lossy) | 2.065 MB | 12.81x | 7.06x | 106 | 3.192 | 0.5 s |
| JPEG XL distance 2 (lossy) | 1.304 MB | 20.29x | 11.17x | 119 | 4.639 | 0.5 s |
| JPEG XL distance 4 (lossy) | 0.723 MB | 36.58x | 20.15x | 115 | 7.165 | 0.5 s |

**OWLG lossless — revert is bit-identical**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG lossless, base WebP<br><sub>the default: portable, opens anywhere WebP does</sub> | 10.169 MB | 2.60x | 1.43x | **0** | 0.000 | 11.6 s |
| OWLG lossless, base AVIF | 9.764 MB | 2.71x | 1.49x | **0** | 0.000 | 21.2 s |
| OWLG lossless, base JPEG XL<br><sub>smallest lossless, but needs a JXL decoder</sub> | 8.017 MB | 3.30x | 1.82x | **0** | 0.000 | 2.8 s |
| OWLG lossless, tiled+pyramid<br><sub>constant RAM, internal overviews</sub> | 10.536 MB | 2.51x | 1.38x | **0** | 0.000 | 5.2 s |

**OWLG near-lossless — hard per-pixel bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG near-lossless delta=1<br><sub>guaranteed max error <= 1 DN</sub> | 6.440 MB | 4.11x | 2.26x | 1 | 0.700 | 10.1 s |
| OWLG near-lossless delta=2<br><sub>guaranteed max error <= 2 DN</sub> | 4.813 MB | 5.50x | 3.03x | 2 | 1.123 | 9.5 s |
| OWLG near-lossless delta=3<br><sub>guaranteed max error <= 3 DN</sub> | 3.875 MB | 6.83x | 3.76x | 3 | 1.394 | 9.3 s |
| OWLG near-lossless delta=5<br><sub>guaranteed max error <= 5 DN</sub> | 2.838 MB | 9.32x | 5.13x | 5 | 2.008 | 9.0 s |
| OWLG near-lossless delta=8<br><sub>guaranteed max error <= 8 DN</sub> | 2.118 MB | 12.49x | 6.88x | 8 | 2.746 | 8.7 s |
| OWLG near-lossless delta=12<br><sub>guaranteed max error <= 12 DN</sub> | 1.615 MB | 16.38x | 9.02x | 12 | 3.978 | 8.7 s |
| OWLG near-lossless delta=16<br><sub>guaranteed max error <= 16 DN</sub> | 1.285 MB | 20.59x | 11.34x | 16 | 5.095 | 8.6 s |

**Base codec, same bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, base AVIF<br><sub>smallest, needs an AVIF decoder</sub> | 4.614 MB | 5.73x | 3.16x | 2 | 1.137 | 19.4 s |

**A size goal: `--target 20x`**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG --target 20x, base WEBP<br><sub>encoder chose delta=16; bound proven by the decode</sub> | 1.285 MB | 20.59x | 11.34x | 16 | 5.095 | 35.5 s |
| OWLG --target 20x, base AVIF<br><sub>encoder chose delta=16; bound proven by the decode</sub> | 1.236 MB | 21.40x | 11.79x | 16 | 5.503 | 76.5 s |

**Layout**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, tiled+pyramid<br><sub>constant RAM; required for 10-100 GB</sub> | 5.179 MB | 5.11x | 2.81x | 2 | 1.123 | 4.3 s |
| OWLG delta=2, tiled, no overviews<br><sub>pyramid costs ~+23% of level 0</sub> | 4.834 MB | 5.47x | 3.01x | 2 | 1.123 | 3.6 s |

**Recovery tier and encryption**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2 + recovery tier<br><sub>ship light, archive recovery; revert is bit-identical</sub> | 10.472 MB | 2.53x | 1.39x | **0** | 0.000 | 10.3 s |
| OWLG delta=2, AES-256-GCM<br><sub>encryption overhead is the tag+nonce per blob</sub> | 4.813 MB | 5.50x | 3.03x | 2 | 1.123 | 9.6 s |
|   -> light half (distribute)<br><sub>near-lossless +/-2, safe to share widely</sub> | 4.813 MB | 5.50x | 3.03x | — | — | — |
|   -> recovery half (archive)<br><sub>rejoin to get bit-identical pixels back</sub> | 5.659 MB | 4.68x | 2.58x | — | — | — |

**OWLGT web tiles**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLGT profile=view q50<br><sub>display only; smallest</sub> | 1.971 MB | 13.42x | 7.39x | — | — | 7.7 s |
| OWLGT profile=view q72<br><sub>default display quality</sub> | 3.825 MB | 6.92x | 3.81x | — | — | 7.8 s |
| OWLGT profile=exact delta=3<br><sub>bounded error per tile; analysis-safe</sub> | 10.064 MB | 2.63x | 1.45x | — | — | 11.3 s |
| gdal2tiles PNG tree (430 tiles)<br><sub>the usual answer; one PNG per tile on disk</sub> | 40.046 MB | 0.66x | 0.36x | — | — | 8.9 s |
| MBTiles PNG<br><sub>single file, but still PNG tiles inside</sub> | 11.465 MB | 2.31x | 1.27x | — | — | 2.7 s |
