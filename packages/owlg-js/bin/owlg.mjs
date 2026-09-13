#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs';
import { openOWLG, openOWLGT } from '../src/container.mjs';

const args = process.argv.slice(2);
const cmd = args[0];
const flag = (n, d) => { const i = args.indexOf('--' + n); return i >= 0 ? args[i + 1] : d; };
const has = n => args.includes('--' + n);

function usage() {
  console.log(`owlg - OWLG / OWLGT reader (Node, zero dependencies)

  npx owlg info <file.owlg|file.owlgt>
  npx owlg tile <file.owlgt> <z> <x> <y> [-o output.avif]
  npx owlg serve <file.owlgt...> [--port 8080] [--host 127.0.0.1]

Encoding requires the Python package: pip install owlg`);
}

const read = p => new Uint8Array(readFileSync(p));

if (!cmd || has('help') || cmd === 'help') { usage(); process.exit(0); }

if (cmd === 'info') {
  const p = args[1]; if (!p) { usage(); process.exit(1); }
  const u8 = read(p);
  const tag = String.fromCharCode(...u8.slice(0, 6));
  if (tag.startsWith('OWLGT')) {
    const t = openOWLGT(u8);
    const per = {};
    for (const k of Object.keys(t.hdr.index)) { const z = k.split('/')[0]; per[z] = (per[z] || 0) + 1; }
    console.log(JSON.stringify({ format: 'OWLGT', profile: t.hdr.profile, delta: t.hdr.delta,
      minzoom: t.hdr.minzoom, maxzoom: t.hdr.maxzoom, tilesize: t.hdr.tilesize,
      tiles: Object.keys(t.hdr.index).length, perZoom: per, bounds3857: t.hdr.bounds,
      fileBytes: u8.length }, null, 1));
  } else {
    const pw = flag('password', null);
    openOWLG(u8, pw).then(s => {
      const h = s.hdr;
      console.log(JSON.stringify({ format: 'OWLG', version: h.v, mode: h.mode, delta: h.delta,
        width: h.w, height: h.h, bands: h.bands, codec: h.codec, tiers: h.tiers,
        encrypted: s.encrypted, tile: h.tile, constBands: h.const, sha256: h.sha256,
        bytes: { base: s.baseBytes, correction: s.corrBytes, recovery: s.recBytes, file: u8.length }
      }, null, 1));
    }).catch(e => { console.error('Error:', e.message); process.exit(1); });
  }
} else if (cmd === 'tile') {
  const [p, z, x, y] = args.slice(1);
  const t = openOWLGT(read(p));
  const a = t.avif(+z, +x, +y);
  if (!a) { console.error('tile not present, or it uses residual mode (a full decode is required)'); process.exit(1); }
  const out = flag('o', null) || (args.includes('-o') ? args[args.indexOf('-o') + 1] : null) || `${z}_${x}_${y}.avif`;
  writeFileSync(out, Buffer.from(a));
  console.log(`-> ${out} (${a.length} B, image/avif)`);
} else if (cmd === 'serve') {
  const paths = args.slice(1).filter(a => !a.startsWith('--') && !/^\d+$/.test(a));
  const { serve } = await import('../src/server.mjs');
  serve(paths, { host: flag('host', '127.0.0.1'), port: +flag('port', 8080) });
} else { usage(); process.exit(1); }
