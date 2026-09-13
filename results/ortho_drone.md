### `sample_raster2.tif` — 1471 x 1128 x 4 uint8, 6.6 MB raw


**Reference formats**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred (lossless)<br><sub>baseline for the "vs GeoTIFF" column</sub> | 3.353 MB | 1.98x | 1.00x | **0** | 0.000 | 1.6 s |
| GeoTIFF LZW (lossless) | 3.833 MB | 1.73x | 0.87x | **0** | 0.000 | 0.2 s |
| GeoTIFF ZSTD (lossless) | 3.283 MB | 2.02x | 1.02x | **0** | 0.000 | 0.2 s |
| COG DEFLATE (lossless) | 3.379 MB | 1.96x | 0.99x | **0** | 0.000 | 0.4 s |
| GeoTIFF JPEG q85 (lossy) | 1.307 MB | 5.08x | 2.56x | 29 | 2.975 | 0.1 s |
| GeoTIFF JPEG q75 (lossy) | 0.999 MB | 6.64x | 3.36x | 40 | 4.074 | 0.1 s |
| JP2 OpenJPEG lossless | 1.986 MB | 3.34x | 1.69x | **0** | 0.000 | 0.1 s |
| JP2 OpenJPEG q25 (lossy, ~4x) | 1.251 MB | 5.31x | 2.68x | 4 | 0.555 | 0.2 s |
| JP2 OpenJPEG q10 (lossy, ~10x) | 0.666 MB | 9.96x | 5.03x | 15 | 1.263 | 0.1 s |
| JP2 OpenJPEG q5 (lossy, ~20x) | 0.334 MB | 19.86x | 10.03x | 44 | 2.957 | 0.1 s |
| JP2 OpenJPEG q3 (lossy, ~33x) | 0.202 MB | 32.91x | 16.63x | 59 | 4.943 | 0.1 s |
| JPEG XL lossless | 1.889 MB | 3.51x | 1.78x | **0** | 0.000 | 0.8 s |
| JPEG XL distance 1 (lossy) | 0.449 MB | 14.77x | 7.46x | 125 | 3.080 | 0.3 s |
| JPEG XL distance 2 (lossy) | 0.280 MB | 23.70x | 11.97x | 106 | 4.423 | 0.3 s |
| JPEG XL distance 4 (lossy) | 0.155 MB | 42.82x | 21.63x | 153 | 6.793 | 0.3 s |

**OWLG lossless — revert is bit-identical**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG lossless, base WebP<br><sub>the default: portable, opens anywhere WebP does</sub> | 2.405 MB | 2.76x | 1.39x | **0** | 0.000 | 2.8 s |
| OWLG lossless, base AVIF | 2.298 MB | 2.89x | 1.46x | **0** | 0.000 | 4.8 s |
| OWLG lossless, base JPEG XL<br><sub>smallest lossless, but needs a JXL decoder</sub> | 1.884 MB | 3.52x | 1.78x | **0** | 0.000 | 0.7 s |
| OWLG lossless, tiled+pyramid<br><sub>constant RAM, internal overviews</sub> | 2.490 MB | 2.67x | 1.35x | **0** | 0.000 | 3.1 s |

**OWLG near-lossless — hard per-pixel bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG near-lossless delta=1<br><sub>guaranteed max error <= 1 DN</sub> | 1.481 MB | 4.48x | 2.26x | 1 | 0.695 | 2.4 s |
| OWLG near-lossless delta=2<br><sub>guaranteed max error <= 2 DN</sub> | 1.090 MB | 6.09x | 3.08x | 2 | 1.098 | 2.2 s |
| OWLG near-lossless delta=3<br><sub>guaranteed max error <= 3 DN</sub> | 0.873 MB | 7.60x | 3.84x | 3 | 1.350 | 2.2 s |
| OWLG near-lossless delta=5<br><sub>guaranteed max error <= 5 DN</sub> | 0.636 MB | 10.44x | 5.27x | 5 | 1.928 | 2.1 s |
| OWLG near-lossless delta=8<br><sub>guaranteed max error <= 8 DN</sub> | 0.473 MB | 14.04x | 7.09x | 8 | 2.623 | 2.0 s |
| OWLG near-lossless delta=12<br><sub>guaranteed max error <= 12 DN</sub> | 0.355 MB | 18.67x | 9.43x | 12 | 3.790 | 2.0 s |
| OWLG near-lossless delta=16<br><sub>guaranteed max error <= 16 DN</sub> | 0.283 MB | 23.44x | 11.84x | 16 | 4.566 | 2.0 s |

**Base codec, same bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, base AVIF<br><sub>smallest, needs an AVIF decoder</sub> | 1.034 MB | 6.42x | 3.24x | 2 | 1.112 | 4.4 s |

**A size goal: `--target 20x`**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG --target 20x, base WEBP<br><sub>encoder chose delta=14; bound proven by the decode</sub> | 0.316 MB | 21.02x | 10.62x | 14 | 3.955 | 8.3 s |
| OWLG --target 20x, base AVIF<br><sub>encoder chose delta=14; bound proven by the decode</sub> | 0.296 MB | 22.46x | 11.34x | 14 | 3.922 | 17.4 s |

**Layout**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, tiled+pyramid<br><sub>constant RAM; required for 10-100 GB</sub> | 1.173 MB | 5.66x | 2.86x | 2 | 1.102 | 2.6 s |
| OWLG delta=2, tiled, no overviews<br><sub>pyramid costs ~+23% of level 0</sub> | 1.096 MB | 6.06x | 3.06x | 2 | 1.102 | 2.5 s |

**Recovery tier and encryption**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2 + recovery tier<br><sub>ship light, archive recovery; revert is bit-identical</sub> | 2.481 MB | 2.68x | 1.35x | **0** | 0.000 | 2.4 s |
| OWLG delta=2, AES-256-GCM<br><sub>encryption overhead is the tag+nonce per blob</sub> | 1.090 MB | 6.09x | 3.07x | 2 | 1.098 | 2.3 s |
|   -> light half (distribute)<br><sub>near-lossless +/-2, safe to share widely</sub> | 1.090 MB | 6.09x | 3.08x | — | — | — |
|   -> recovery half (archive)<br><sub>rejoin to get bit-identical pixels back</sub> | 1.390 MB | 4.77x | 2.41x | — | — | — |

**OWLGT web tiles**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLGT profile=view q50<br><sub>display only; smallest</sub> | 0.363 MB | 18.27x | 9.23x | — | — | 1.4 s |
| OWLGT profile=view q72<br><sub>default display quality</sub> | 0.695 MB | 9.55x | 4.82x | — | — | 1.5 s |
| OWLGT profile=exact delta=3<br><sub>bounded error per tile; analysis-safe</sub> | 1.811 MB | 3.67x | 1.85x | — | — | 2.1 s |
| gdal2tiles PNG tree (91 tiles)<br><sub>the usual answer; one PNG per tile on disk</sub> | 7.109 MB | 0.93x | 0.47x | — | — | 1.9 s |
| MBTiles PNG<br><sub>single file, but still PNG tiles inside</sub> | 6.648 MB | 1.00x | 0.50x | — | — | 1.5 s |
