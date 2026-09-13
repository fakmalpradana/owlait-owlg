// Binary adaptive range coder (LZMA-style), an exact mirror of the Python implementation.
export const PBITS = 15, MOVE = 5, KTOP = 1 << 24;

export class RangeDecoder {
  constructor(buf) {
    this.b = buf; this.p = 1; this.r = 0xFFFFFFFF >>> 0; this.c = 0;
    for (let i = 0; i < 4; i++) this.c = ((this.c << 8) | this.b[this.p++]) >>> 0;
  }
  bit(pr, ci) {
    const bd = ((this.r >>> PBITS) * pr[ci]) >>> 0;
    let bit;
    if (this.c < bd) { this.r = bd; bit = 0; pr[ci] = pr[ci] + (((1 << PBITS) - pr[ci]) >> MOVE); }
    else { this.c = (this.c - bd) >>> 0; this.r = (this.r - bd) >>> 0; bit = 1; pr[ci] = pr[ci] - (pr[ci] >> MOVE); }
    while (this.r < KTOP) { this.r = (this.r << 8) >>> 0; this.c = ((this.c << 8) | (this.b[this.p] || 0)) >>> 0; this.p++; }
    return bit;
  }
  bypass() {
    this.r = this.r >>> 1;
    let bit;
    if (this.c >= this.r) { this.c = (this.c - this.r) >>> 0; bit = 1; } else bit = 0;
    while (this.r < KTOP) { this.r = (this.r << 8) >>> 0; this.c = ((this.c << 8) | (this.b[this.p] || 0)) >>> 0; this.p++; }
    return bit;
  }
}
export const newProbs = (n) => new Uint16Array(n).fill(1 << (PBITS - 1));
