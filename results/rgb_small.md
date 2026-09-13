### `rgb_small.tif` — 791 x 718 x 3 uint8, 1.7 MB raw


**Reference formats**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred (lossless)<br><sub>baseline for the "vs GeoTIFF" column</sub> | 0.779 MB | 2.19x | 1.00x | **0** | 0.000 | 0.2 s |
| GeoTIFF LZW (lossless) | 0.880 MB | 1.94x | 0.88x | **0** | 0.000 | 0.1 s |
| GeoTIFF ZSTD (lossless) | 0.758 MB | 2.25x | 1.03x | **0** | 0.000 | 0.1 s |
| COG DEFLATE (lossless) | 0.782 MB | 2.18x | 1.00x | **0** | 0.000 | 0.2 s |
| GeoTIFF JPEG q85 (lossy) | 0.384 MB | 4.44x | 2.03x | 30 | 3.710 | 0.1 s |
| GeoTIFF JPEG q75 (lossy) | 0.296 MB | 5.76x | 2.63x | 54 | 5.604 | 0.1 s |
| JP2 OpenJPEG lossless | 0.674 MB | 2.53x | 1.16x | **0** | 0.000 | 0.1 s |
| JP2 OpenJPEG q25 (lossy, ~4x) | 0.428 MB | 3.98x | 1.82x | 6 | 0.830 | 0.1 s |
| JP2 OpenJPEG q10 (lossy, ~10x) | 0.173 MB | 9.86x | 4.51x | 31 | 2.680 | 0.1 s |
| JP2 OpenJPEG q5 (lossy, ~20x) | 0.088 MB | 19.43x | 8.88x | 81 | 5.346 | 0.1 s |
| JP2 OpenJPEG q3 (lossy, ~33x) | 0.053 MB | 31.88x | 14.57x | 104 | 8.304 | 0.1 s |
| JPEG XL lossless | 0.597 MB | 2.85x | 1.30x | **0** | 0.000 | 0.3 s |
| JPEG XL distance 1 (lossy) | 0.137 MB | 12.41x | 5.67x | 86 | 4.228 | 0.1 s |
| JPEG XL distance 2 (lossy) | 0.090 MB | 18.86x | 8.62x | 94 | 5.607 | 0.1 s |
| JPEG XL distance 4 (lossy) | 0.058 MB | 29.61x | 13.53x | 139 | 7.926 | 0.1 s |

**OWLG lossless — revert is bit-identical**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG lossless, base WebP<br><sub>the default: portable, opens anywhere WebP does</sub> | 0.676 MB | 2.52x | 1.15x | **0** | 0.000 | 0.9 s |
| OWLG lossless, base AVIF | 0.664 MB | 2.56x | 1.17x | **0** | 0.000 | 1.5 s |
| OWLG lossless, base JPEG XL<br><sub>smallest lossless, but needs a JXL decoder</sub> | 0.597 MB | 2.85x | 1.30x | **0** | 0.000 | 0.2 s |
| OWLG lossless, tiled+pyramid<br><sub>constant RAM, internal overviews</sub> | 0.696 MB | 2.45x | 1.12x | **0** | 0.000 | 0.9 s |

**OWLG near-lossless — hard per-pixel bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG near-lossless delta=1<br><sub>guaranteed max error <= 1 DN</sub> | 0.452 MB | 3.77x | 1.72x | 1 | 0.659 | 0.7 s |
| OWLG near-lossless delta=2<br><sub>guaranteed max error <= 2 DN</sub> | 0.349 MB | 4.88x | 2.23x | 2 | 1.115 | 0.6 s |
| OWLG near-lossless delta=3<br><sub>guaranteed max error <= 3 DN</sub> | 0.285 MB | 5.98x | 2.73x | 3 | 1.483 | 0.6 s |
| OWLG near-lossless delta=5<br><sub>guaranteed max error <= 5 DN</sub> | 0.213 MB | 7.99x | 3.65x | 5 | 2.130 | 0.6 s |
| OWLG near-lossless delta=8<br><sub>guaranteed max error <= 8 DN</sub> | 0.162 MB | 10.55x | 4.82x | 8 | 2.743 | 0.6 s |
| OWLG near-lossless delta=12<br><sub>guaranteed max error <= 12 DN</sub> | 0.124 MB | 13.77x | 6.30x | 12 | 3.823 | 0.6 s |
| OWLG near-lossless delta=16<br><sub>guaranteed max error <= 16 DN</sub> | 0.101 MB | 16.81x | 7.68x | 16 | 4.649 | 0.6 s |

**Base codec, same bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, base AVIF<br><sub>smallest, needs an AVIF decoder</sub> | 0.342 MB | 4.98x | 2.28x | 2 | 1.123 | 1.4 s |

**A size goal: `--target 20x`**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG --target 20x, base WEBP<br><sub>encoder chose delta=24; bound proven by the decode</sub> | 0.076 MB | 22.52x | 10.30x | 24 | 6.065 | 2.3 s |
| OWLG --target 20x, base AVIF<br><sub>encoder chose delta=24; bound proven by the decode</sub> | 0.077 MB | 22.11x | 10.11x | 24 | 6.211 | 5.4 s |

**Layout**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, tiled+pyramid<br><sub>constant RAM; required for 10-100 GB</sub> | 0.370 MB | 4.60x | 2.10x | 2 | 1.116 | 0.8 s |
| OWLG delta=2, tiled, no overviews<br><sub>pyramid costs ~+23% of level 0</sub> | 0.350 MB | 4.86x | 2.22x | 2 | 1.116 | 0.7 s |

**Recovery tier and encryption**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2 + recovery tier<br><sub>ship light, archive recovery; revert is bit-identical</sub> | 0.688 MB | 2.48x | 1.13x | **0** | 0.000 | 0.7 s |
| OWLG delta=2, AES-256-GCM<br><sub>encryption overhead is the tag+nonce per blob</sub> | 0.349 MB | 4.88x | 2.23x | 2 | 1.115 | 0.6 s |
|   -> light half (distribute)<br><sub>near-lossless +/-2, safe to share widely</sub> | 0.349 MB | 4.88x | 2.23x | — | — | — |
|   -> recovery half (archive)<br><sub>rejoin to get bit-identical pixels back</sub> | 0.339 MB | 5.02x | 2.30x | — | — | — |

**OWLGT web tiles**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLGT profile=view q50<br><sub>display only; smallest</sub> | 0.081 MB | 20.96x | 9.58x | — | — | 0.3 s |
| OWLGT profile=view q72<br><sub>default display quality</sub> | 0.151 MB | 11.28x | 5.16x | — | — | 0.3 s |
| OWLGT profile=exact delta=3<br><sub>bounded error per tile; analysis-safe</sub> | 0.377 MB | 4.52x | 2.07x | — | — | 0.5 s |
| gdal2tiles PNG tree (32 tiles)<br><sub>the usual answer; one PNG per tile on disk</sub> | 1.280 MB | 1.33x | 0.61x | — | — | 0.6 s |
| MBTiles PNG<br><sub>single file, but still PNG tiles inside</sub> | 1.225 MB | 1.39x | 0.64x | — | — | 0.4 s |
