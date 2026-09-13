# OWLG JavaScript / Node API reference

`owlg` on npm is a **reader**, not an encoder: it parses `.owlg` containers and `.owlgt`
tile pyramids, decodes the correction layer in pure JavaScript, and hands tiles to
MapLibre or Leaflet. It has zero dependencies and runs the same code in a browser and in
Node 18+. The one thing it does not bring is an image codec — the base layer of an OWLG
file is WebP or AVIF, and decoding that is the host's job. A browser already has one; Node
does not, and the library says so rather than guessing. Everything below was executed
against a real `.owlg` and a real `.owlgt` built from `samples/rgb_small.tif`. See also the
[CLI reference](cli.md), the [Python API](python-api.md), and the
[project README](../README.md).

## Contents

- [Install](#install)
- [What this package can and cannot do](#what-this-package-can-and-cannot-do)
- [Entry points](#entry-points)
- [Exports from `src/index.mjs`](#exports-from-srcindexmjs)
- [Reading a `.owlg`](#reading-a-owlg)
  - [`openOWLG`](#openowlg) · [`decodeOWLG`](#decodeowlg) · [`browserAvifDecoder`](#browseravifdecoder)
- [Reading a `.owlgt` tile pyramid](#reading-a-owlgt-tile-pyramid)
  - [`openOWLGT`](#openowlgt) · [Coordinate helpers](#coordinate-helpers)
- [Low-level exports](#low-level-exports)
- [The command line: `bin/owlg.mjs`](#the-command-line-binowlgmjs)
- [The Node server](#the-node-server)
- [MapLibre helper: `owlg/maplibre`](#maplibre-helper-owlgmaplibre)
- [Leaflet helper: `owlg/leaflet`](#leaflet-helper-owlgleaflet)
- [Browser snippet](#browser-snippet)
- [Node snippet](#node-snippet)
- [Known limitations](#known-limitations)

## Install

```
npm install owlg
```

`"type": "module"`, ESM only, `engines.node >= 18`, no dependencies. Encoding requires the
Python package (`pip install owlg`); this package never writes an OWLG file.

## What this package can and cannot do

**Without any image codec at all** it can read headers, enumerate a tile pyramid, and pull
individual AVIF tiles out of a `.owlgt` and hand them to a browser (or write them to disk).
That covers the whole map-display path, which is why the MapLibre and Leaflet helpers work
in a plain browser with nothing installed.

**To decode a `.owlg` into pixels** it needs a decoder for the file's base layer, supplied
by you as a function. In a browser, `browserAvifDecoder()` wraps the built-in
`createImageBitmap`. In Node there is no equivalent — as of Node 22 neither
`createImageBitmap` nor `OffscreenCanvas` exists — so you must pass a decoder backed by
something like `sharp` or `@jsquash/avif`. If you do not, the call fails with a clear
message instead of returning wrong pixels:

```
$ node readowlg.mjs
header: 791x718 webp delta=3 encrypted=false
bytes : { base: 135946, correction: 151343, recovery: 0 }
error : an avifDecode function is required (see README)
```

When a conforming decoder *is* supplied, the JavaScript correction-layer decoder is exact.
Decoding the same file through this package and through the Python reference
implementation produced byte-identical band planes:

```
$ node decode.mjs
decoded: 791x718 | bands 3 | Uint8Array of 567938
$ python3 -c "..."     # compare against owlg.read_owlg
identical to the Python decoder: True
```

## Entry points

| Specifier | File | Contents |
| --- | --- | --- |
| `owlg` | `src/index.mjs` | Container + correction decoder + range coder + crypto. |
| `owlg/maplibre` | `src/maplibre.mjs` | `owlgtSource`, `owlgtServerSource`. |
| `owlg/leaflet` | `src/leaflet.mjs` | `createOwlgtLayer`, `createOwlgtServerLayer`. |
| `owlg/server` | `src/server.mjs` | `createServer`, `serve`. |
| `npx owlg` | `bin/owlg.mjs` | CLI: `info`, `tile`, `serve`. |

## Exports from `src/index.mjs`

Printed from a live import:

```js
const m = await import('owlg');
console.log(Object.keys(m));
```

```
[
 "C0_N", "CE_OFF", "CG_OFF", "CS_OFF", "NPROB",
 "RangeDecoder", "TS", "WORLD",
 "browserAvifDecoder", "decodeCorrectionTile", "decodeOWLG",
 "deriveKey", "m2lat", "m2lon", "newProbs",
 "openOWLG", "openOWLGT", "tileBounds", "unseal"
]
```

`src/index.mjs` re-exports all of `container.mjs` and `decode.mjs`, plus `RangeDecoder` and
`newProbs` from `rangecoder.mjs` and `deriveKey`/`unseal` from `crypto.mjs`. The range
coder's tuning constants (`PBITS`, `MOVE`, `KTOP`) are exported by `rangecoder.mjs` but not
re-exported here; import them from `owlg/src/rangecoder.mjs` if you need them.

## Reading a `.owlg`

### `openOWLG`

```js
await openOWLG(u8, password = null)
```

Parse the container from a `Uint8Array`. `password` may be a string, `null`, or an async
function returning a string — useful for prompting only when the file turns out to be
encrypted. Decryption uses WebCrypto (`globalThis.crypto.subtle`), which is available in
browsers and in Node 18+.

Returns an object:

| Property | Type | Meaning |
| --- | --- | --- |
| `hdr` | object | The header JSON: `w`, `h`, `bands`, `delta`, `mode`, `codec`, `tile`, `const`, `coded`, `groups`, `nty`, `ntx`, `sha256`, `tiers`, `crs`, `transform`, and the blob directory `dir`. |
| `blob` | `async (i) => Uint8Array` | Blob `i`, decrypted if the file is encrypted. |
| `encrypted` | boolean | Whether the envelope's encrypted flag is set. |
| `baseBytes` | number | Total bytes of the base blobs. |
| `corrBytes` | number | Total bytes of the correction blobs. |
| `recBytes` | number | Total bytes of the recovery blobs. |

Throws `Error('not an OWLG file')`, `Error('encrypted file: a password is required')` or
`Error('wrong password')`. The legacy `GTZ1` container is also recognised.

### `decodeOWLG`

```js
await decodeOWLG(src, { avifDecode, fast = false, tier = 'auto' })
```

Decode the container returned by `openOWLG` into pixel planes.

| Parameter | Meaning |
| --- | --- |
| `src` | The object from `openOWLG`. |
| `avifDecode` | **Required.** `async (bytes) => { width, height, data, channels? }`. `data` is a `Uint8Array` of interleaved samples; `channels` defaults to `data.length / (width * height)`. Despite the name this function receives the file's base layer whatever its codec — for a WebP-based file it is handed WebP bytes, so your decoder must handle what `src.hdr.codec` says. |
| `fast` | Skip the correction layer; base only, outside the error bound. |
| `tier` | `'auto'` applies the recovery tier when present, `'light'` stops at the near-lossless result, `'full'` throws if there is no recovery tier. |

Returns `{ width, height, bands, header }`, where `bands` is an array of `Uint8Array`
planes, one per band, each `width * height` long in row-major order. Constant bands are
materialised from `hdr.const`. Note this is **planar** output, not the band-first single
array the Python API returns; index band `b` pixel `(x, y)` as `bands[b][y * width + x]`.

Throws `Error('an avifDecode function is required (see README)')` when `avifDecode` is
missing, and `Error('file has no recovery tier: bit-identical output is not possible')` for
`tier: 'full'` on a file without one.

### `browserAvifDecoder`

```js
browserAvifDecoder()
```

Returns an `avifDecode` function built on `createImageBitmap` + `OffscreenCanvas`, decoding
through a `Blob` typed `image/avif`. Browser only — both globals are `undefined` in Node.
It always returns `channels: 4` (RGBA), which `decodeOWLG` handles.

## Reading a `.owlgt` tile pyramid

### `openOWLGT`

```js
openOWLGT(u8)
```

Synchronous; parses the `OWLGT1` header. Returns:

| Property | Type | Meaning |
| --- | --- | --- |
| `hdr` | object | `profile`, `delta`, `minzoom`, `maxzoom`, `tilesize`, `bounds` (EPSG:3857), and `index`, a map from `"z/x/y"` to `[offset, length]`. |
| `raw(z, x, y)` | `Uint8Array \| null` | The stored tile record, mode byte first. |
| `avif(z, x, y)` | `Uint8Array \| null` | The bare AVIF blob, ready to hand to a browser — **only for standalone tiles (mode 0)**. Returns `null` for a missing tile and for residual-coded tiles. |
| `zooms` | number[] | Sorted list of zoom levels actually present. |
| `base`, `u8` | number, Uint8Array | Offset of the blob region and the backing buffer. |

Throws `Error('not an OWLGT file')` on a bad magic.

```js
const t = openOWLGT(new Uint8Array(readFileSync('ortho.owlgt')));
console.log('profile :', t.hdr.profile, '| zooms', t.zooms.join(','),
            '| tiles', Object.keys(t.hdr.index).length);
const blob = t.avif(9, 144, 219);
console.log('tile    :', blob.length, 'bytes,', String.fromCharCode(...blob.subarray(4, 12)));
```

```
profile : view | zooms 8,9,10,11 | tiles 277
tile    : 10759 bytes, ftypavif
```

**This pass-through path requires `--profile view`.** A `view` pyramid stores every tile
standalone (mode byte 0). An `exact` pyramid stores residual-against-parent (mode 1) and
standalone-plus-correction (mode 2) tiles, neither of which is a plain AVIF file. Counted
over both pyramids built from the same source:

```
ortho.owlgt profile=view  mode-byte histogram: {"0":277}
exact.owlgt profile=exact mode-byte histogram: {"1":248,"2":23}
```

so `avif()` returns a blob for all 277 tiles of the first and `null` for all 271 of the
second. Reconstructing an `exact` pyramid needs the Python decoder; build with
`--profile view` for anything a browser will display.

### Coordinate helpers

| Export | Signature | Meaning |
| --- | --- | --- |
| `WORLD` | `20037508.342789244` | Half the width of the Web Mercator world, in metres. |
| `TS` | `256` | Tile edge in pixels. |
| `tileBounds(z, x, y)` | `[minX, minY, maxX, maxY]` | Tile extent in EPSG:3857 metres. |
| `m2lon(x)` | number | EPSG:3857 x to longitude. |
| `m2lat(y)` | number | EPSG:3857 y to latitude. |

```js
console.log('bounds :', tileBounds(9, 144, 219).map(v => Math.round(v)).join(', '), '(EPSG:3857)');
const b = t.hdr.bounds;
console.log('lon/lat:', [m2lon(b[0]), m2lat(b[1]), m2lon(b[2]), m2lat(b[3])].map(v => v.toFixed(4)).join(', '));
```

```
bounds : -8766410, 2817775, -8688138, 2896046 (EPSG:3857)
lon/lat: -78.9586, 23.5647, -76.5760, 25.5509
```

## Low-level exports

You do not normally call these; they are exported so the decoder can be embedded or
audited.

| Export | Purpose |
| --- | --- |
| `decodeCorrectionTile(buf, base, B, H, W, y0, y1, x0, x1, delta, rec)` | Decode one correction tile into `rec` (an array of `H*W` planes), using `base` as the prediction context. Modifies `rec` in place and returns it. The context model must match the Python encoder exactly. |
| `RangeDecoder` | Binary adaptive range decoder (LZMA-style), a mirror of the Python implementation. `bit(probs, ctx)` and `bypass()`. |
| `newProbs(n)` | A `Uint16Array(n)` initialised to the half-probability state. |
| `C0_N`, `CS_OFF`, `CG_OFF`, `CE_OFF`, `NPROB` | Context-model offsets and the total probability-context count. |
| `deriveKey(password, salt, iterations)` | PBKDF2-SHA256 to an AES-256-GCM key, via WebCrypto. |
| `unseal(key, prefix, idx, data)` | AES-256-GCM open; the nonce is the 8-byte prefix followed by the big-endian blob index. |

## The command line: `bin/owlg.mjs`

```
$ npx owlg
owlg - OWLG / OWLGT reader (Node, zero dependencies)

  npx owlg info <file.owlg|file.owlgt>
  npx owlg tile <file.owlgt> <z> <x> <y> [-o output.avif]
  npx owlg serve <file.owlgt...> [--port 8080] [--host 127.0.0.1]

Encoding requires the Python package: pip install owlg
```

### `info`

Dispatches on the magic. For an `.owlgt` it prints the profile, zoom range, per-zoom tile
counts and the EPSG:3857 bounds:

```
$ npx owlg info ortho.owlgt
{
 "format": "OWLGT",
 "profile": "view",
 "delta": 3,
 "minzoom": 8,
 "maxzoom": 11,
 "tilesize": 256,
 "tiles": 277,
 "perZoom": {
  "8": 6,
  "9": 20,
  "10": 56,
  "11": 195
 },
 "bounds3857": [
  -8789636.707871985,
  2700459.9569205055,
  -8524406.377716506,
  2943560.234622164
 ],
 "fileBytes": 753863
}
```

For a `.owlg` it prints the header and the byte split. `--password <pw>` supplies a
passphrase.

```
$ npx owlg info rgb.owlg
{
 "format": "OWLG",
 "version": 3,
 "mode": "nearlossless",
 "delta": 3,
 "width": 791,
 "height": 718,
 "bands": 3,
 "codec": "webp",
 "tiers": [
  "light"
 ],
 "encrypted": false,
 "tile": 1024,
 "constBands": {},
 "sha256": "6d212801201b0e2657cb8d2e94e2aceadb4decbbaa3bc86b0e721a01b40bcb03",
 "bytes": {
  "base": 135946,
  "correction": 151343,
  "recovery": 0,
  "file": 288521
 }
}
```

Reading an encrypted file without a passphrase exits 1 with a clear message:

```
$ npx owlg info secret.owlg
Error: encrypted file: a password is required
```

### `tile`

Extract one tile as a standalone `.avif`. `-o` or `--o` sets the output name; the default
is `<z>_<x>_<y>.avif`. Coordinates are `z x y` (XYZ order, not the OGC row/col order).

```
$ npx owlg tile ortho.owlgt 9 144 219 -o tile.avif
-> tile.avif (10759 B, image/avif)
```

On an `exact`-profile pyramid there is nothing to pass through, and it says so, exiting 1:

```
$ npx owlg tile exact.owlgt 11 574 873
tile not present, or it uses residual mode (a full decode is required)
```

### `serve`

Starts the Node server described below.

## The Node server

```js
import { serve, createServer } from 'owlg/server';
serve(paths, { host = '127.0.0.1', port = 8080 });
```

Zero-dependency HTTP server for `.owlgt`, serving OGC API - Tiles and XYZ. AVIF tiles are
passed through untouched — the server never decodes anything, the browser does. Each file
becomes a collection named after its filename stem. `createServer` returns the
`http.Server` without listening, for mounting inside your own app.

```
$ npx owlg serve ortho.owlgt --port 8988
OWLG (Node) serving 1 collections at http://127.0.0.1:8988
  XYZ        : http://127.0.0.1:8988/xyz/ortho/{z}/{x}/{y}.avif
  OGC Tiles  : http://127.0.0.1:8988/collections/ortho/map/tiles/WebMercatorQuad/{z}/{y}/{x}.avif
  Collections: http://127.0.0.1:8988/collections
  Note       : Full WMS GetMap is available in the Python server (owlg serve).
```

Routes, with the status and media type each actually returned:

| Path | Returns |
| --- | --- |
| `/` | `200 application/json` — landing page |
| `/conformance` | `200 application/json` — seven OGC API Common/Tiles classes |
| `/tileMatrixSets`, `/tileMatrixSets/WebMercatorQuad` | `200 application/json` |
| `/collections`, `/collections/{id}`, `/collections/{id}/map/tiles` | `200 application/json` |
| `/collections/{id}/map/tiles/WebMercatorQuad/{z}/{row}/{col}[.ext]` | `200 image/avif` |
| `/xyz/{id}/{z}/{x}/{y}[.ext]` | `200 image/avif` |
| `/wms?…&REQUEST=GetCapabilities` | `200 text/xml` |
| `/wms?…&REQUEST=GetMap` | `501 text/xml` — service exception |

GetMap needs bbox compositing, so it is deliberately not implemented here. The response
body, on one line:

```
<?xml version="1.0"?><ServiceExceptionReport version="1.3.0" xmlns="http://www.opengis.net/ogc"><ServiceException code="OperationNotSupported">GetMap requires bbox compositing. This Node server provides OGC API - Tiles and XYZ. For full WMS GetMap, use the Python server: owlg serve</ServiceException></ServiceExceptionReport>
```

`createServer(paths)` returns the same server without calling `listen`, with a `sources`
`Map` attached, so it can be mounted or started by your own code:

```js
const s = createServer(['ortho.owlgt']);
console.log(s.constructor.name, s.listening, [...s.sources.keys()]);
```

```
Server false [ 'ortho' ]
```

Use this server when you want a static tile endpoint with no Python on the box. Use
[`owlg serve`](cli.md#serve) from the Python package when you need OGC API - Maps, WMS
GetMap, PNG/JPEG/WebP transcoding, or `exact`-profile pyramids.

## MapLibre helper: `owlg/maplibre`

### `owlgtSource(map, id, bytes, opts = {})`

Register an in-memory `.owlgt` as a MapLibre raster source. Tiles are served through a
registered `maplibregl` protocol handler, straight out of the buffer — no server. `bytes`
is a `Uint8Array` or `ArrayBuffer`. `opts.protocol` renames the URL scheme (default
`owlgt`), `opts.attribution` sets the attribution string. If `map` has `addSource`, the
source and a raster layer named `<id>-layer` are added for you.

Returns `{ source, tiles, urls }`, where `source` is the MapLibre source descriptor and
`tiles` is the `openOWLGT` handle.

```js
const r = await owlgtSource(null, 'ortho', bytes);
console.log(JSON.stringify(r.source, null, 1));
```

```
{
 "type": "raster",
 "tileSize": 256,
 "tiles": [
  "owlgt://{z}/{x}/{y}"
 ],
 "minzoom": 8,
 "maxzoom": 11,
 "bounds": [
  -78.95864996539396,
  23.564749780903355,
  -76.57604537148663,
  25.550873767434343
 ],
 "attribution": "OWLGT"
}
```

### `owlgtServerSource(baseUrl, collection, opts = {})`

A plain raster source pointed at the XYZ route of either server. `opts.minzoom` (default 0),
`opts.maxzoom` (default 22), `opts.attribution`.

```js
owlgtServerSource('http://localhost:8080', 'ortho', { minzoom: 8, maxzoom: 11 })
```

```
{"type":"raster","tileSize":256,"tiles":["http://localhost:8080/xyz/ortho/{z}/{x}/{y}.png"],"minzoom":8,"maxzoom":11,"attribution":"OWLGT"}
```

## Leaflet helper: `owlg/leaflet`

### `createOwlgtLayer(L, bytes, opts = {})`

Build an `L.GridLayer` subclass that creates each tile as an `<img>` fed from a blob URL
over the stored AVIF, revoking the URL once it loads. Pass your Leaflet namespace as `L` —
the package does not import Leaflet. Bounds, `minZoom` and `maxZoom` are taken from the
pyramid header; `opts` is merged over them. Browser only: it touches `document` and
`URL.createObjectURL`.

### `createOwlgtServerLayer(L, baseUrl, collection, opts = {})`

`L.tileLayer` against the XYZ route of either server.

## Browser snippet

Displaying a `view`-profile pyramid with MapLibre, no server and no image codec beyond the
browser's own:

```html
<script type="module">
import { owlgtSource } from 'owlg/maplibre';   // via your bundler, or an import map

const map = new maplibregl.Map({
  container: 'map',
  style: { version: 8, sources: {}, layers: [] },
  center: [-77.77, 24.56],
  zoom: 9
});

const bytes = new Uint8Array(await (await fetch('/data/ortho.owlgt')).arrayBuffer());

map.on('load', async () => {
  const { tiles } = await owlgtSource(map, 'ortho', bytes);
  console.log('zooms', tiles.zooms.join(','), 'profile', tiles.hdr.profile);
});
</script>
```

And decoding a `.owlg` to pixels in the browser, where a decoder comes for free:

```js
import { openOWLG, decodeOWLG, browserAvifDecoder } from 'owlg';

const bytes = new Uint8Array(await (await fetch('/data/scene.owlg')).arrayBuffer());
const src = await openOWLG(bytes);
const { width, height, bands } = await decodeOWLG(src, { avifDecode: browserAvifDecoder() });

// planar -> RGBA for a canvas
const rgba = new Uint8ClampedArray(width * height * 4);
for (let i = 0; i < width * height; i++) {
  rgba[i * 4] = bands[0][i];
  rgba[i * 4 + 1] = bands[1][i];
  rgba[i * 4 + 2] = bands[2][i];
  rgba[i * 4 + 3] = 255;
}
document.querySelector('canvas').getContext('2d')
  .putImageData(new ImageData(rgba, width, height), 0, 0);
```

## Node snippet

Reading a header and a tile needs nothing extra:

```js
import { readFileSync } from 'node:fs';
import { openOWLG, openOWLGT, tileBounds, m2lon, m2lat } from 'owlg';

const src = await openOWLG(new Uint8Array(readFileSync('rgb.owlg')));
console.log('header:', src.hdr.w + 'x' + src.hdr.h, src.hdr.codec,
            'delta=' + src.hdr.delta, 'encrypted=' + src.encrypted);
console.log('bytes :', { base: src.baseBytes, correction: src.corrBytes, recovery: src.recBytes });

const t = openOWLGT(new Uint8Array(readFileSync('ortho.owlgt')));
console.log('profile :', t.hdr.profile, '| zooms', t.zooms.join(','));
console.log('bounds  :', tileBounds(9, 144, 219).map(v => Math.round(v)).join(', '));
```

```
header: 791x718 webp delta=3 encrypted=false
bytes : { base: 135946, correction: 151343, recovery: 0 }
profile : view | zooms 8,9,10,11
bounds  : -8766410, 2817775, -8688138, 2896046
```

Decoding to pixels needs a decoder you supply. Any function matching the contract works;
this one shells out to Python so the example is self-contained, and the result was checked
against the Python reference implementation:

```js
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { openOWLG, decodeOWLG } from 'owlg';

// Node ships no AVIF/WebP decoder. In production use sharp or @jsquash/avif;
// this variant shells out to Python/imagecodecs to keep the example dependency-free.
const hostDecode = async (bytes) => {
  writeFileSync('/tmp/blob.bin', Buffer.from(bytes));
  const out = execFileSync('python3', ['-c',
    "import imagecodecs,numpy as np,sys\n" +
    "a=np.asarray(imagecodecs.webp_decode(open('/tmp/blob.bin','rb').read()))\n" +
    "sys.stdout.write(f'{a.shape[1]} {a.shape[0]} {a.shape[2]}\\n'); sys.stdout.flush()\n" +
    "sys.stdout.buffer.write(np.ascontiguousarray(a).tobytes())"], { maxBuffer: 1 << 28 });
  const nl = out.indexOf(10);
  const [width, height, channels] = out.subarray(0, nl).toString().trim().split(' ').map(Number);
  return { width, height, channels, data: new Uint8Array(out.subarray(nl + 1)) };
};

const src = await openOWLG(new Uint8Array(readFileSync('rgb.owlg')));
const img = await decodeOWLG(src, { avifDecode: hostDecode });
console.log('decoded:', img.width + 'x' + img.height, '| bands', img.bands.length,
            '|', img.bands[0].constructor.name, 'of', img.bands[0].length);
```

```
decoded: 791x718 | bands 3 | Uint8Array of 567938
```

With `sharp` installed, the same contract is satisfied by:

```js
import sharp from 'sharp';
const hostDecode = async (bytes) => {
  const { data, info } = await sharp(Buffer.from(bytes))
    .raw().toBuffer({ resolveWithObject: true });
  return { width: info.width, height: info.height, channels: info.channels,
           data: new Uint8Array(data) };
};
```

## Known limitations

These are properties of the current implementation, verified by running it — not
speculation.

**No encoder.** This package reads. Producing `.owlg` or `.owlgt` requires
`pip install owlg` and the [CLI](cli.md).

**No image codec.** `decodeOWLG` requires a host-supplied decoder, and Node has none built
in. The parameter is named `avifDecode` for historical reasons but receives whatever the
base codec is — check `src.hdr.codec` and supply a decoder that handles it. A missing
decoder is an explicit error, never silently wrong pixels.

**`decodeOWLG` handles v3 and legacy containers only.** It reads `hdr.groups`, `hdr.nty`
and `hdr.ntx`, which the tiled v4 layout does not have. Passing a v4 file throws inside the
decoder:

```
TypeError: Cannot read properties of undefined (reading 'length')
    at decodeOWLG (.../src/container.mjs:66:30)
```

Since `owlg encode` selects the tiled layout automatically above 16 MPixel, a large file
will be v4 and this package cannot decode its pixels. `openOWLG` still parses a v4 header,
and `.owlgt` pyramids — the browser-facing format — are unaffected.

**`npx owlg info` misreports a v4 file's byte split.** The byte accounting slices the blob
directory using `hdr.n_base` / `hdr.n_corr` / `hdr.n_rec`, which a v4 header does not
carry, so the whole directory is counted twice — once as `base` and once as `recovery` —
and `tiers` and `sha256` are missing. For a v4 file, `owlg info` from the Python package
gives the correct pyramid breakdown.

**AVIF pass-through requires `--profile view`.** `avif()`, `npx owlg tile`, the Node server
and the in-memory MapLibre/Leaflet helpers all depend on tiles being stored standalone.
`exact`-profile pyramids store residual-coded tiles, which need a full decode.

**Leaflet and MapLibre helpers are browser-only.** They touch `document`,
`URL.createObjectURL` and `globalThis.maplibregl`; importing them in Node is fine, calling
them is not. `owlgtSource(null, id, bytes)` is the exception — with no map it just returns
the source descriptor, which is how the output above was produced.
