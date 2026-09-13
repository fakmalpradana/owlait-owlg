// OWLGT tile source for MapLibre GL / Mapbox GL.
import { openOWLGT, m2lon, m2lat } from './container.mjs';

/**
 * Register a .owlgt file as a MapLibre raster source.
 * Tiles are served from memory through blob URLs - no server needed.
 *   const src = await owlgtSource(map, 'ortho', bytes);
 */
export async function owlgtSource(map, id, bytes, opts = {}) {
  const t = openOWLGT(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
  const urls = new Map();
  const proto = opts.protocol || 'owlgt';
  if (map && typeof map.addProtocol !== 'function' && globalThis.maplibregl?.addProtocol) {
    globalThis.maplibregl.addProtocol(proto, async (params) => {
      const m = /^\w+:\/\/(\d+)\/(\d+)\/(\d+)/.exec(params.url);
      if (!m) return { data: null };
      const a = t.avif(+m[1], +m[2], +m[3]);
      return { data: a ? a.slice().buffer : null };
    });
  }
  const b = t.hdr.bounds;
  const source = {
    type: 'raster', tileSize: 256,
    tiles: [`${proto}://{z}/{x}/{y}`],
    minzoom: t.hdr.minzoom, maxzoom: t.hdr.maxzoom,
    bounds: [m2lon(b[0]), m2lat(b[1]), m2lon(b[2]), m2lat(b[3])],
    attribution: opts.attribution || 'OWLGT'
  };
  if (map?.addSource) {
    map.addSource(id, source);
    map.addLayer({ id: id + '-layer', type: 'raster', source: id, paint: { 'raster-opacity': 1 } });
  }
  return { source, tiles: t, urls };
}

/** Source backed by an OGC API / XYZ server (owlg serve or npx owlg serve). */
export function owlgtServerSource(baseUrl, collection, opts = {}) {
  return {
    type: 'raster', tileSize: 256,
    tiles: [`${baseUrl.replace(/\/$/, '')}/xyz/${collection}/{z}/{x}/{y}.png`],
    minzoom: opts.minzoom ?? 0, maxzoom: opts.maxzoom ?? 22,
    attribution: opts.attribution || 'OWLGT'
  };
}
