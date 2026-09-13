// Node server for .owlgt: OGC API - Tiles + XYZ, zero dependencies.
// Tiles from the "view" profile are passed through as-is as AVIF - the server never
// decodes them, the browser decodes them natively. WMS GetMap needs compositing, so
// it is only available when the optional decode library (sharp) is installed.
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { basename, extname } from 'node:path';
import { openOWLGT, m2lon, m2lat, WORLD, TS } from './container.mjs';

const TMS = 'WebMercatorQuad';
const CONFORMANCE = [
  'http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core',
  'http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/json',
  'http://www.opengis.net/spec/ogcapi-common-2/1.0/conf/collections',
  'http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core',
  'http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tileset',
  'http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tilesets-list',
  'http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/geodata-tilesets'
];

function loadSource(path) {
  const u8 = new Uint8Array(readFileSync(path));
  const t = openOWLGT(u8);
  const id = basename(path, extname(path));
  const b = t.hdr.bounds;
  return { id, t, path,
    bbox3857: b, bbox4326: [m2lon(b[0]), m2lat(b[1]), m2lon(b[2]), m2lat(b[3])] };
}

function tmsDoc() {
  const m = [];
  for (let z = 0; z < 25; z++) m.push({
    id: String(z), scaleDenominator: 559082264.028717 / 2 ** z,
    cellSize: 2 * WORLD / (TS * 2 ** z), pointOfOrigin: [-WORLD, WORLD],
    tileWidth: TS, tileHeight: TS, matrixWidth: 2 ** z, matrixHeight: 2 ** z
  });
  return { id: TMS, title: 'Google Maps Compatible for the World',
    uri: 'http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad',
    crs: 'http://www.opengis.net/def/crs/EPSG/0/3857', orderedAxes: ['E', 'N'], tileMatrices: m };
}

export function createServer(paths, opts = {}) {
  const srcs = new Map();
  for (const p of (Array.isArray(paths) ? paths : [paths])) { const s = loadSource(p); srcs.set(s.id, s); }
  const send = (res, code, body, ct, extra = {}) => {
    const b = typeof body === 'string' ? Buffer.from(body) : Buffer.from(body);
    res.writeHead(code, { 'Content-Type': ct, 'Content-Length': b.length,
      'Access-Control-Allow-Origin': '*', ...extra });
    res.end(b);
  };
  const json = (res, o, code = 200) => send(res, code, JSON.stringify(o, null, 1), 'application/json');
  const err = (res, code, msg) => json(res, { code, description: msg }, code);

  const coll = (root, s) => ({
    id: s.id, title: s.id, dataType: 'map',
    extent: { spatial: { bbox: [s.bbox4326], crs: 'http://www.opengis.net/def/crs/OGC/1.3/CRS84' } },
    crs: ['http://www.opengis.net/def/crs/EPSG/0/3857', 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'],
    links: [
      { rel: 'self', type: 'application/json', href: `${root}/collections/${s.id}` },
      { rel: 'http://www.opengis.net/def/rel/ogc/1.0/tilesets-map', type: 'application/json',
        href: `${root}/collections/${s.id}/map/tiles` },
      { rel: 'item', type: 'image/avif', templated: true, title: 'XYZ',
        href: `${root}/xyz/${s.id}/{z}/{x}/{y}.avif` }
    ],
    owlgt: { profile: s.t.hdr.profile, delta: s.t.hdr.delta,
      minzoom: s.t.hdr.minzoom, maxzoom: s.t.hdr.maxzoom, tiles: Object.keys(s.t.hdr.index).length,
      guarantee: s.t.hdr.profile === 'exact' ? `|original-decoded| <= ${s.t.hdr.delta} DN per pixel` : null }
  });

  const server = http.createServer((req, res) => {
    const u = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const root = `http://${req.headers.host || 'localhost'}`;
    const p = u.pathname.replace(/\/$/, '') || '/';
    try {
      if (p === '/') return json(res, { title: 'OWLG tile service (Node)',
        description: 'Serves .owlgt: OGC API - Tiles + XYZ. AVIF tiles are passed through as-is.',
        links: [
          { rel: 'self', type: 'application/json', href: `${root}/` },
          { rel: 'conformance', type: 'application/json', href: `${root}/conformance` },
          { rel: 'data', type: 'application/json', href: `${root}/collections` },
          { rel: 'http://www.opengis.net/def/rel/ogc/1.0/tiling-schemes', type: 'application/json',
            href: `${root}/tileMatrixSets` }
        ] });
      if (p === '/conformance') return json(res, { conformsTo: CONFORMANCE });
      if (p === '/tileMatrixSets') return json(res, { tileMatrixSets: [{ id: TMS, title: TMS,
        links: [{ rel: 'self', type: 'application/json', href: `${root}/tileMatrixSets/${TMS}` }] }] });
      if (p === `/tileMatrixSets/${TMS}`) return json(res, tmsDoc());
      if (p === '/collections') return json(res, {
        collections: [...srcs.values()].map(s => coll(root, s)),
        links: [{ rel: 'self', type: 'application/json', href: `${root}/collections` }] });
      let m = p.match(/^\/collections\/([^/]+)$/);
      if (m) { const s = srcs.get(m[1]); return s ? json(res, coll(root, s)) : err(res, 404, 'unknown collection'); }
      m = p.match(/^\/collections\/([^/]+)\/map\/tiles$/);
      if (m) { const s = srcs.get(m[1]); if (!s) return err(res, 404, 'unknown collection');
        return json(res, { tilesets: [{ title: s.id, dataType: 'map',
          crs: 'http://www.opengis.net/def/crs/EPSG/0/3857',
          tileMatrixSetURI: 'http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad',
          links: [{ rel: 'item', type: 'image/avif', templated: true,
            href: `${root}/collections/${s.id}/map/tiles/${TMS}/{tileMatrix}/{tileRow}/{tileCol}.avif` }] }] }); }
      m = p.match(new RegExp(`^/collections/([^/]+)/map/tiles/${TMS}/(\\d+)/(\\d+)/(\\d+)(?:\\.\\w+)?$`));
      if (m) return tile(res, srcs.get(m[1]), +m[2], +m[4], +m[3]);
      m = p.match(/^\/xyz\/([^/]+)\/(\d+)\/(\d+)\/(\d+)(?:\.\w+)?$/);
      if (m) return tile(res, srcs.get(m[1]), +m[2], +m[3], +m[4]);
      if (p === '/wms') return wms(res, root, srcs, u.searchParams);
      return err(res, 404, `not found: ${p}`);
    } catch (e) { return err(res, 500, `${e.name}: ${e.message}`); }
  });

  function tile(res, s, z, x, y) {
    if (!s) return err(res, 404, 'unknown collection');
    const a = s.t.avif(z, x, y);
    if (a) return send(res, 200, a, 'image/avif', { 'Cache-Control': 'public, max-age=86400' });
    const raw = s.t.raw(z, x, y);
    if (!raw) return err(res, 404, 'tile not present');
    return err(res, 501, 'this tile uses residual/correction mode - use the Python server (owlg serve) '
                       + 'or decode it on the client with this package');
  }
  function wms(res, root, srcs, q) {
    const req = (q.get('REQUEST') || q.get('request') || '').toLowerCase();
    if (req === 'getcapabilities') {
      const layers = [...srcs.values()].map(s => {
        const b = s.bbox4326;
        return `    <Layer queryable="0"><Name>${s.id}</Name><Title>${s.id}</Title>
      <CRS>EPSG:3857</CRS><CRS>EPSG:4326</CRS><CRS>CRS:84</CRS>
      <EX_GeographicBoundingBox><westBoundLongitude>${b[0]}</westBoundLongitude><eastBoundLongitude>${b[2]}</eastBoundLongitude><southBoundLatitude>${b[1]}</southBoundLatitude><northBoundLatitude>${b[3]}</northBoundLatitude></EX_GeographicBoundingBox>
    </Layer>`; }).join('\n');
      return send(res, 200, `<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms" xmlns:xlink="http://www.w3.org/1999/xlink">
  <Service><Name>WMS</Name><Title>OWLG WMS (Node)</Title>
  <OnlineResource xlink:href="${root}/wms"/></Service>
  <Capability><Request>
    <GetCapabilities><Format>text/xml</Format><DCPType><HTTP><Get><OnlineResource xlink:href="${root}/wms?"/></Get></HTTP></DCPType></GetCapabilities>
  </Request><Exception><Format>XML</Format></Exception>
  <Layer><Title>OWLG</Title><CRS>EPSG:3857</CRS>
${layers}
  </Layer></Capability></WMS_Capabilities>`, 'text/xml');
    }
    return send(res, 501,
      `<?xml version="1.0"?><ServiceExceptionReport version="1.3.0" xmlns="http://www.opengis.net/ogc">` +
      `<ServiceException code="OperationNotSupported">GetMap requires bbox compositing. ` +
      `This Node server provides OGC API - Tiles and XYZ. For full WMS GetMap, use the Python server: owlg serve` +
      `</ServiceException></ServiceExceptionReport>`, 'text/xml');
  }
  server.sources = srcs;
  return server;
}

export function serve(paths, { host = '127.0.0.1', port = 8080 } = {}) {
  const s = createServer(paths);
  s.listen(port, host, () => {
    const url = `http://${host}:${port}`;
    console.log(`OWLG (Node) serving ${s.sources.size} collections at ${url}`);
    for (const id of s.sources.keys()) {
      console.log(`  XYZ        : ${url}/xyz/${id}/{z}/{x}/{y}.avif`);
      console.log(`  OGC Tiles  : ${url}/collections/${id}/map/tiles/${TMS}/{z}/{y}/{x}.avif`);
    }
    console.log(`  Collections: ${url}/collections`);
    console.log(`  Note       : Full WMS GetMap is available in the Python server (owlg serve).`);
  });
  return s;
}
