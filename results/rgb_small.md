### `rgb_small.tif` — 791 x 718 x 3 uint8, 1.7 MB raw


**Reference formats**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| GeoTIFF DEFLATE+pred (lossless)<br><sub>baseline for the "vs GeoTIFF" column</sub> | 0.763 MB | 2.23x | 1.00x | **0** | 0.000 | 4.2 s |
| GeoTIFF LZW (lossless) | 0.880 MB | 1.94x | 0.87x | **0** | 0.000 | 0.1 s |
| GeoTIFF ZSTD (lossless) | 0.758 MB | 2.25x | 1.01x | **0** | 0.000 | 0.1 s |
| GeoTIFF WEBP lossless | 0.598 MB | 2.85x | 1.28x | **0** | 0.000 | 2.4 s |
| COG DEFLATE (lossless) | 0.780 MB | 2.19x | 0.98x | **0** | 0.000 | 0.1 s |
| GeoTIFF JPEG q85 (lossy) | 0.384 MB | 4.44x | 1.99x | 30 | 3.710 | 0.1 s |
| GeoTIFF JPEG q75 (lossy) | 0.296 MB | 5.76x | 2.58x | 54 | 5.604 | 0.1 s |
| GeoTIFF WEBP q85 (lossy) | 0.114 MB | 14.95x | 6.70x | 134 | 4.311 | 0.1 s |

**OWLG lossless — revert is bit-identical**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG lossless, base WebP<br><sub>the default: portable, opens anywhere WebP does</sub> | 0.680 MB | 2.51x | 1.12x | **0** | 0.000 | 1.6 s |
| OWLG lossless, base AVIF | 0.659 MB | 2.58x | 1.16x | **0** | 0.000 | 3.9 s |
| OWLG lossless, base JPEG XL<br><sub>smallest lossless, but needs a JXL decoder</sub> | 0.587 MB | 2.90x | 1.30x | **0** | 0.000 | 0.8 s |
| OWLG lossless, tiled+pyramid<br><sub>constant RAM, internal overviews</sub> | 0.746 MB | 2.28x | 1.02x | **0** | 0.000 | 0.7 s |

**OWLG near-lossless — hard per-pixel bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG near-lossless delta=1<br><sub>guaranteed max error <= 1 DN</sub> | 0.455 MB | 3.74x | 1.68x | 1 | 0.659 | 0.9 s |
| OWLG near-lossless delta=2<br><sub>guaranteed max error <= 2 DN</sub> | 0.353 MB | 4.83x | 2.16x | 2 | 1.116 | 0.8 s |
| OWLG near-lossless delta=3<br><sub>guaranteed max error <= 3 DN</sub> | 0.289 MB | 5.91x | 2.65x | 3 | 1.487 | 0.8 s |
| OWLG near-lossless delta=5<br><sub>guaranteed max error <= 5 DN</sub> | 0.216 MB | 7.89x | 3.54x | 5 | 2.136 | 0.7 s |
| OWLG near-lossless delta=8<br><sub>guaranteed max error <= 8 DN</sub> | 0.164 MB | 10.41x | 4.66x | 8 | 2.742 | 0.7 s |

**Base codec, same bound**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, base AVIF<br><sub>smallest, needs an AVIF decoder</sub> | 0.338 MB | 5.04x | 2.26x | 2 | 1.122 | 3.4 s |

**Layout**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2, tiled+pyramid<br><sub>constant RAM; required for 10-100 GB</sub> | 0.392 MB | 4.35x | 1.95x | 2 | 1.126 | 1.0 s |
| OWLG delta=2, tiled, no overviews<br><sub>pyramid costs ~+23% of level 0</sub> | 0.354 MB | 4.82x | 2.16x | 2 | 1.126 | 1.0 s |

**Recovery tier and encryption**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLG delta=2 + recovery tier<br><sub>ship light, archive recovery; revert is bit-identical</sub> | 0.692 MB | 2.46x | 1.10x | **0** | 0.000 | 0.9 s |
| OWLG delta=2, AES-256-GCM<br><sub>encryption overhead is the tag+nonce per blob</sub> | 0.353 MB | 4.83x | 2.16x | 2 | 1.116 | 0.8 s |
|   -> light half (distribute)<br><sub>near-lossless +/-2, safe to share widely</sub> | 0.353 MB | 4.83x | 2.16x | — | — | — |
|   -> recovery half (archive)<br><sub>rejoin to get bit-identical pixels back</sub> | 0.339 MB | 5.02x | 2.25x | — | — | — |

**OWLGT web tiles**

| Option | Size | vs raw | vs GeoTIFF | max err | RMSE | enc |
|---|---:|---:|---:|---:|---:|---:|
| OWLGT profile=view q50<br><sub>display only; smallest</sub> | 0.081 MB | 21.01x | 9.42x | — | — | 0.8 s |
| OWLGT profile=view q72<br><sub>default display quality</sub> | 0.151 MB | 11.31x | 5.07x | — | — | 0.8 s |
| OWLGT profile=exact delta=3<br><sub>bounded error per tile; analysis-safe</sub> | 0.372 MB | 4.58x | 2.05x | — | — | 1.4 s |
| gdal2tiles PNG tree (32 tiles)<br><sub>the usual answer; one PNG per tile on disk</sub> | 1.290 MB | 1.32x | 0.59x | — | — | 3.0 s |
| MBTiles PNG<br><sub>single file, but still PNG tiles inside</sub> | 1.225 MB | 1.39x | 0.62x | — | — | 0.5 s |
