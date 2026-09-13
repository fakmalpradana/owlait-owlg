// OWLGT tile layer for Leaflet.
import { openOWLGT, m2lon, m2lat } from './container.mjs';

/** L.owlgtLayer(bytes) - reads tiles from memory, no server needed. */
export function createOwlgtLayer(L, bytes, opts = {}) {
  const t = openOWLGT(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
  const b = t.hdr.bounds;
  const Layer = L.GridLayer.extend({
    createTile(coords, done) {
      const img = document.createElement('img');
      const a = t.avif(coords.z, coords.x, coords.y);
      if (!a) { setTimeout(() => done(null, img), 0); return img; }
      const url = URL.createObjectURL(new Blob([a], { type: 'image/avif' }));
      img.onload = () => { URL.revokeObjectURL(url); done(null, img); };
      img.onerror = e => { URL.revokeObjectURL(url); done(e, img); };
      img.src = url;
      return img;
    }
  });
  return new Layer(Object.assign({
    minZoom: t.hdr.minzoom, maxZoom: t.hdr.maxzoom, tileSize: 256,
    bounds: L.latLngBounds([m2lat(b[1]), m2lon(b[0])], [m2lat(b[3]), m2lon(b[2])]),
    attribution: 'OWLGT'
  }, opts));
}

/** Layer backed by a server (owlg serve). */
export function createOwlgtServerLayer(L, baseUrl, collection, opts = {}) {
  return L.tileLayer(`${baseUrl.replace(/\/$/, '')}/xyz/${collection}/{z}/{x}/{y}.png`,
    Object.assign({ tileSize: 256, attribution: 'OWLGT' }, opts));
}
