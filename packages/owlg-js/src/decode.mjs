// OWLG correction-layer decoder. The contexts must match the Python encoder exactly.
import { RangeDecoder, newProbs } from './rangecoder.mjs';

const NA = 7;
export const C0_N = 3 * NA * 3 * 3 * 3;
export const CS_OFF = C0_N;
export const CG_OFF = CS_OFF + 3 * NA;
export const CE_OFF = CG_OFF + 3 * NA * 3;
export const NPROB = CE_OFF + 3 * 16;

function actBin(bp, y, x, H, W) {
  const xm = x > 0 ? x - 1 : 0, xp = x < W - 1 ? x + 1 : W - 1;
  const ym = y > 0 ? y - 1 : 0, yp = y < H - 1 ? y + 1 : H - 1;
  const g = Math.abs(bp[y * W + xp] - bp[y * W + xm]) + Math.abs(bp[yp * W + x] - bp[ym * W + x]);
  return g <= 2 ? 0 : g <= 6 ? 1 : g <= 12 ? 2 : g <= 24 ? 3 : g <= 48 ? 4 : g <= 96 ? 5 : 6;
}
const n3 = q => q === 0 ? 0 : (q === 1 || q === -1) ? 1 : 2;

/** base & rec: arrays of H*W Uint8Array planes. rec is modified in place. */
export function decodeCorrectionTile(buf, base, B, H, W, y0, y1, x0, x1, delta, rec) {
  const s = 2 * delta + 1, th = y1 - y0, tw = x1 - x0;
  const pr = newProbs(NPROB), rc = new RangeDecoder(buf);
  const Q = new Int32Array(B * th * tw);
  for (let b = 0; b < B; b++) {
    const bb = b < 3 ? b : 2, bp = base[b], rp = rec[b], qo = b * th * tw, qpo = (b - 1) * th * tw;
    for (let y = 0; y < th; y++) {
      const gy = y0 + y;
      for (let x = 0; x < tw; x++) {
        const gx = x0 + x, a = actBin(bp, gy, gx, H, W);
        const nl = x > 0 ? n3(Q[qo + y * tw + x - 1]) : 0;
        const nu = y > 0 ? n3(Q[qo + (y - 1) * tw + x]) : 0;
        const pv = b > 0 ? n3(Q[qpo + y * tw + x]) : 0;
        const c0 = ((((bb * NA + a) * 3 + nl) * 3 + nu) * 3 + pv);
        let q = 0;
        if (rc.bit(pr, c0) === 1) {
          const sg = rc.bit(pr, CS_OFF + bb * NA + a);
          let k = 0, m = 0;
          while (k < 3) { if (rc.bit(pr, CG_OFF + (bb * NA + a) * 3 + k) === 0) { m = k; break; } k++; }
          if (k === 3) {
            let nb = 0;
            while (rc.bit(pr, CE_OFF + bb * 16 + (nb < 16 ? nb : 15)) === 1) nb++;
            let v = 1;
            for (let i = 0; i < nb; i++) v = (v << 1) | rc.bypass();
            m = v - 1 + 3;
          }
          q = sg === 0 ? (m + 1) : -(m + 1);
        }
        Q[qo + y * tw + x] = q;
        let r = bp[gy * W + gx] + q * s;
        rp[gy * W + gx] = r < 0 ? 0 : r > 255 ? 255 : r;
      }
    }
  }
  return rec;
}
