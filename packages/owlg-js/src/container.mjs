// Container reader for .owlg and .owlgt. Zero dependencies.
import { deriveKey, unseal } from './crypto.mjs';
import { decodeCorrectionTile } from './decode.mjs';

export const WORLD = 20037508.342789244;
export const TS = 256;
const td = new TextDecoder();

function magic(u8, n) { return String.fromCharCode(...u8.slice(0, n)); }

/** Read a .owlg file. `password` may be a string or an async function that returns one. */
export async function openOWLG(u8, password = null) {
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const m4 = magic(u8, 4);
  if (m4 === 'GTZ1') return openLegacy(u8, dv);
  if (m4 !== 'OWLG') throw new Error('not an OWLG file');
  const flags = u8[5];
  let p = 6, key = null, prefix = null;
  if (flags & 1) {
    p++;
    const iters = dv.getUint32(p, true); p += 4;
    const salt = u8.slice(p, p + 16); p += 16;
    prefix = u8.slice(p, p + 8); p += 8;
    const pw = typeof password === 'function' ? await password() : password;
    if (pw == null) throw new Error('encrypted file: a password is required');
    key = await deriveKey(pw, salt, iters);
  }
  const hl = dv.getUint32(p, true); p += 4;
  let hb = u8.subarray(p, p + hl); p += hl;
  if (key) { try { hb = await unseal(key, prefix, 0, hb); } catch { throw new Error('wrong password'); } }
  const hdr = JSON.parse(td.decode(hb));
  const off = p;
  const blob = async i => {
    const [o, L] = hdr.dir[i];
    const b = u8.subarray(off + o, off + o + L);
    return key ? await unseal(key, prefix, i + 1, b) : b;
  };
  return { hdr, blob, encrypted: !!(flags & 1),
           baseBytes: hdr.dir.slice(0, hdr.n_base).reduce((a, d) => a + d[1], 0),
           corrBytes: hdr.dir.slice(hdr.n_base, hdr.n_base + (hdr.n_corr || 0)).reduce((a, d) => a + d[1], 0),
           recBytes: hdr.dir.slice(hdr.n_base + (hdr.n_corr || 0)).reduce((a, d) => a + d[1], 0) };
}
function openLegacy(u8, dv) {
  let p = 4; const hl = dv.getUint32(p, true); p += 4;
  const hdr = JSON.parse(td.decode(u8.subarray(p, p + hl))); p += hl;
  const ng = dv.getUint32(p, true); p += 4;
  const bl = []; for (let i = 0; i < ng; i++) { bl.push(dv.getUint32(p, true)); p += 4; }
  const blobs = []; for (const L of bl) { blobs.push(u8.subarray(p, p + L)); p += L; }
  const nt = dv.getUint32(p, true); p += 4;
  const tl = []; for (let i = 0; i < nt; i++) { tl.push(dv.getUint32(p, true)); p += 4; }
  for (const L of tl) { blobs.push(u8.subarray(p, p + L)); p += L; }
  hdr.n_base = ng; hdr.n_corr = nt; hdr.n_rec = 0;
  return { hdr, blob: async i => blobs[i], encrypted: false, baseBytes: 0, corrBytes: 0, recBytes: 0 };
}

/**
 * Decode a .owlg file into pixel planes.
 * `avifDecode(bytes) -> {width,height,data:Uint8Array RGBA|RGB, channels}` must be supplied
 * (in the browser use browserAvifDecoder(); in Node install sharp or @jsquash/avif).
 */
export async function decodeOWLG(src, { avifDecode, fast = false, tier = 'auto' } = {}) {
  const { hdr, blob } = src;
  if (!avifDecode) throw new Error('an avifDecode function is required (see README)');
  const W = hdr.w, H = hdr.h, coded = hdr.coded, groups = hdr.groups;
  const planes = [];
  for (let g = 0; g < groups.length; g++) {
    const img = await avifDecode(await blob(g));
    const ch = img.channels || (img.data.length / (img.width * img.height));
    for (let b = 0; b < groups[g].length; b++) {
      const pl = new Uint8Array(W * H);
      for (let i = 0; i < W * H; i++) pl[i] = img.data[i * ch + b];
      planes.push(pl);
    }
  }
  let out = planes;
  if (!fast && hdr.mode === 'nearlossless') {
    const rec = planes.map(p => p.slice());
    const T = hdr.tile; let k = hdr.n_base;
    for (let ty = 0; ty < hdr.nty; ty++) for (let tx = 0; tx < hdr.ntx; tx++) {
      const y0 = ty * T, y1 = Math.min((ty + 1) * T, H), x0 = tx * T, x1 = Math.min((tx + 1) * T, W);
      decodeCorrectionTile(await blob(k++), planes, planes.length, H, W, y0, y1, x0, x1, hdr.delta, rec);
    }
    out = rec;
    const wantFull = (tier === 'auto' || tier === 'full') && (hdr.n_rec || 0) > 0;
    if (tier === 'full' && !(hdr.n_rec > 0)) throw new Error('file has no recovery tier: bit-identical output is not possible');
    if (wantFull) {
      const src2 = rec.map(p => p.slice()), out2 = rec.map(p => p.slice());
      const T2 = hdr.tile; let k2 = hdr.n_base + hdr.n_corr;
      for (let ty = 0; ty < hdr.nty; ty++) for (let tx = 0; tx < hdr.ntx; tx++) {
        const y0 = ty * T2, y1 = Math.min((ty + 1) * T2, H), x0 = tx * T2, x1 = Math.min((tx + 1) * T2, W);
        decodeCorrectionTile(await blob(k2++), src2, src2.length, H, W, y0, y1, x0, x1, 0, out2);
      }
      out = out2;
    }
  }
  const bands = [];
  for (let b = 0; b < hdr.bands; b++) {
    const idx = coded.indexOf(b);
    if (idx >= 0) bands.push(out[idx]);
    else bands.push(new Uint8Array(W * H).fill(hdr.const[String(b)] ?? 0));
  }
  return { width: W, height: H, bands, header: hdr };
}

/** Read a .owlgt file. Mode 0 tiles are returned as-is as AVIF (no decoding). */
export function openOWLGT(u8) {
  if (magic(u8, 6) !== 'OWLGT1') throw new Error('not an OWLGT file');
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const hl = dv.getUint32(6, true);
  const hdr = JSON.parse(td.decode(u8.subarray(10, 10 + hl)));
  const base = 10 + hl;
  const raw = (z, x, y) => {
    const e = hdr.index[`${z}/${x}/${y}`];
    if (!e) return null;
    return u8.subarray(base + e[0], base + e[0] + e[1]);
  };
  /** AVIF blob ready to send to a browser - standalone tiles (mode 0) only. */
  const avif = (z, x, y) => {
    const b = raw(z, x, y);
    if (!b) return null;
    if (b[0] !== 0) return null;                       // modes 1/2 need a full decode
    const bl = new DataView(b.buffer, b.byteOffset + 1, 4).getUint32(0, true);
    return b.subarray(5, 5 + bl);
  };
  const zooms = [...new Set(Object.keys(hdr.index).map(k => +k.split('/')[0]))].sort((a, b) => a - b);
  return { hdr, raw, avif, zooms, base, u8 };
}

export const tileBounds = (z, x, y) => {
  const n = 2 ** z, s = 2 * WORLD / n;
  return [-WORLD + x * s, WORLD - (y + 1) * s, -WORLD + (x + 1) * s, WORLD - y * s];
};
export const m2lon = x => x * 180 / WORLD;
export const m2lat = y => (2 * Math.atan(Math.exp(y * Math.PI / WORLD)) - Math.PI / 2) * 180 / Math.PI;

/** Built-in browser AVIF decoder (createImageBitmap). */
export function browserAvifDecoder() {
  return async (bytes) => {
    const bm = await createImageBitmap(new Blob([bytes], { type: 'image/avif' }),
      { colorSpaceConversion: 'none', premultiplyAlpha: 'none' });
    const c = new OffscreenCanvas(bm.width, bm.height);
    const g = c.getContext('2d'); g.drawImage(bm, 0, 0);
    const d = g.getImageData(0, 0, bm.width, bm.height);
    return { width: bm.width, height: bm.height, data: new Uint8Array(d.data.buffer), channels: 4 };
  };
}
