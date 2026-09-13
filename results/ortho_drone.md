### `sample_raster2.tif` — 1471 x 1128 x 4 uint8, 6.6 MB raw


**Reference formats**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred (lossless)<br><sub>baseline for the "vs GeoTIFF" column</sub> | 3.211 MB | 2.07x | 1.00x | **0** | 0.000 | 1.2 s |
| GeoTIFF LZW (lossless) | 3.833 MB | 1.73x | 0.84x | **0** | 0.000 | 0.2 s |
| GeoTIFF ZSTD (lossless) | 3.286 MB | 2.02x | 0.98x | **0** | 0.000 | 0.3 s |
| GeoTIFF WEBP lossless | 2.063 MB | 3.22x | 1.56x | **0** | 0.000 | 7.0 s |
| COG DEFLATE (lossless) | 3.354 MB | 1.98x | 0.96x | **0** | 0.000 | 0.3 s |
| GeoTIFF JPEG q85 (lossy) | 1.252 MB | 5.30x | 2.57x | 29 | 2.976 | 0.1 s |
| GeoTIFF JPEG q75 (lossy) | 0.955 MB | 6.95x | 3.36x | 40 | 4.076 | 0.1 s |
| GeoTIFF WEBP q85 (lossy) | 0.378 MB | 17.55x | 8.49x | 54 | 2.959 | 0.3 s |

**OWLG lossless — revert is bit-identical**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG lossless, base WebP<br><sub>the default: portable, opens anywhere WebP does</sub> | 2.410 MB | 2.75x | 1.33x | **0** | 0.000 | 4.7 s |
| OWLG lossless, base AVIF | 2.298 MB | 2.89x | 1.40x | **0** | 0.000 | 12.7 s |
| OWLG lossless, base JPEG XL<br><sub>smallest lossless, but needs a JXL decoder</sub> | 1.864 MB | 3.56x | 1.72x | **0** | 0.000 | 2.6 s |
| OWLG lossless, tiled+pyramid<br><sub>constant RAM, internal overviews</sub> | 2.930 MB | 2.26x | 1.10x | **0** | 0.000 | 2.5 s |

**OWLG near-lossless — hard per-pixel bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG near-lossless delta=1<br><sub>guaranteed max error <= 1 DN</sub> | 1.487 MB | 4.46x | 2.16x | 1 | 0.696 | 3.0 s |
| OWLG near-lossless delta=2<br><sub>guaranteed max error <= 2 DN</sub> | 1.095 MB | 6.06x | 2.93x | 2 | 1.096 | 2.6 s |
| OWLG near-lossless delta=3<br><sub>guaranteed max error <= 3 DN</sub> | 0.880 MB | 7.54x | 3.65x | 3 | 1.345 | 2.4 s |
| OWLG near-lossless delta=5<br><sub>guaranteed max error <= 5 DN</sub> | 0.644 MB | 10.31x | 4.99x | 5 | 1.911 | 2.2 s |
| OWLG near-lossless delta=8<br><sub>guaranteed max error <= 8 DN</sub> | 0.479 MB | 13.86x | 6.70x | 8 | 2.599 | 2.1 s |

**Base codec, same bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, base AVIF<br><sub>smallest, needs an AVIF decoder</sub> | 1.033 MB | 6.42x | 3.11x | 2 | 1.112 | 10.7 s |

**Layout**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, tiled+pyramid<br><sub>constant RAM; required for 10-100 GB</sub> | 1.361 MB | 4.88x | 2.36x | 2 | 1.100 | 3.2 s |
| OWLG delta=2, tiled, no overviews<br><sub>pyramid costs ~+23% of level 0</sub> | 1.103 MB | 6.02x | 2.91x | 2 | 1.100 | 2.8 s |

**Recovery tier and encryption**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2 + recovery tier<br><sub>ship light, archive recovery; revert is bit-identical</sub> | 2.485 MB | 2.67x | 1.29x | **0** | 0.000 | 3.0 s |
| OWLG delta=2, AES-256-GCM<br><sub>encryption overhead is the tag+nonce per blob</sub> | 1.096 MB | 6.06x | 2.93x | 2 | 1.096 | 2.6 s |
|   -> light half (distribute)<br><sub>near-lossless +/-2, safe to share widely</sub> | 1.095 MB | 6.06x | 2.93x | — | — | — |
|   -> recovery half (archive)<br><sub>rejoin to get bit-identical pixels back</sub> | 1.390 MB | 4.78x | 2.31x | — | — | — |

**OWLGT web tiles**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLGT profile=view q50<br><sub>display only; smallest</sub> | 0.362 MB | 18.33x | 8.87x | — | — | 3.8 s |
| OWLGT profile=view q72<br><sub>default display quality</sub> | 0.693 MB | 9.57x | 4.63x | — | — | 3.8 s |
| OWLGT profile=exact delta=3<br><sub>bounded error per tile; analysis-safe</sub> | 1.781 MB | 3.73x | 1.80x | — | — | 5.7 s |
| gdal2tiles PNG tree (91 tiles)<br><sub>the usual answer; one PNG per tile on disk</sub> | 7.113 MB | 0.93x | 0.45x | — | — | 2.8 s |
| MBTiles PNG<br><sub>single file, but still PNG tiles inside</sub> | 6.648 MB | 1.00x | 0.48x | — | — | 2.3 s |
