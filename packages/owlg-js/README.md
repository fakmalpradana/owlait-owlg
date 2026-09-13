# owlg (JavaScript)

Zero-dependency reader for **OWLG** — a raster format with a hard per-pixel error bound —
and **OWLGT** web tile pyramids. Works in the browser and in Node 18+.

Part of [fakmalpradana/owlait-owlg](https://github.com/fakmalpradana/owlait-owlg). The
full reference is [docs/node-api.md](https://github.com/fakmalpradana/owlait-owlg/blob/main/docs/node-api.md).

```bash
npm install owlg
```

## What it does

OWLG guarantees that every pixel of every band satisfies `|original - decoded| <= delta`.
This package decodes such files: the range coder and the correction-layer decoder are a
port of the Python implementation and are verified **bit-identical** to it in CI.

## Displaying a tile pyramid

The common case needs no decoding at all. A `.owlgt` written with `--profile view` holds
ordinary image blobs, so the browser decodes them:

```js
import { owlgtSource } from 'owlg/maplibre';

map.addSource('ortho', await owlgtSource('/map.owlgt'));
map.addLayer({ id: 'ortho', type: 'raster', source: 'ortho' });
```

```js
import { createOwlgtLayer } from 'owlg/leaflet';

(await createOwlgtLayer('/map.owlgt')).addTo(map);
```

## Reading an .owlg

```js
import { openOWLG, decodeOWLG, browserAvifDecoder } from 'owlg';

const file = await openOWLG(new Uint8Array(await blob.arrayBuffer()));
const { bands, width, height } = await decodeOWLG(file, {
  avifDecode: browserAvifDecoder(),   // the host supplies the base-layer decoder
});
// bands[0] is a Uint8Array of length width * height
```

Encrypted files:

```js
const file = await openOWLG(bytes, { password: 'secret' });
```

## Command line

```bash
npx owlg info file.owlg
npx owlg serve map.owlgt --port 8080     # OGC API - Tiles/Maps + WMS 1.3.0
```

## Exports

`openOWLG`, `decodeOWLG`, `openOWLGT`, `decodeCorrectionTile`, `browserAvifDecoder`,
`RangeDecoder`, `newProbs`, `deriveKey`, `unseal`, `tileBounds`, `m2lat`, `m2lon`,
`TS`, `WORLD`, and the context constants `C0_N`, `CS_OFF`, `CG_OFF`, `CE_OFF`, `NPROB`.

Subpath exports:

- `owlg/maplibre` — `owlgtSource`, `owlgtServerSource`
- `owlg/leaflet` — `createOwlgtLayer`, `createOwlgtServerLayer`
- `owlg/server` — the OGC API / WMS server used by `npx owlg serve`

## Limitations

Read these before choosing this package for a job:

- **The host supplies the image decoder.** A browser has `createImageBitmap`; Node has
  nothing built in. Without one, files whose base layer must be decoded return a clear
  error rather than wrong pixels.
- **Tiled (v4) `.owlg` files are not supported yet.** The Python encoder selects that
  layout automatically above 16 MPixel, so large files are currently Python-side only.
  Small files, and any file written with `--layout flat`, work.
- **The OWLGT helpers handle `--profile view`.** Tiles written with `--profile exact`
  carry a correction layer and need a full decode; the bundled server answers those with
  HTTP 501 and an explanation instead of serving something wrong.

Encoding is Python-only, by design: writing needs rasterio and an image encoder.

## Licence

AGPL-3.0-or-later. If you run a modified version as a network service, you must offer its
source to users.
