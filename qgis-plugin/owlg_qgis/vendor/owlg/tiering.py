"""Split off / rejoin the .owlg recovery tier: .owlg <-> .owlr."""
import json, struct, os, hashlib
from .container import MAGIC, REC_MAGIC, open_owlg, _write_file, OwlgError

def split(src, light_out, rec_out, password=None):
    """Split a tiered .owlg into a light file plus a recovery file."""
    hdr, get = open_owlg(src, password)
    nrec = hdr.get('n_rec', 0)
    if not nrec: raise OwlgError('this file has no recovery tier')
    nb, nc = hdr['n_base'], hdr['n_corr']
    bb = [get(i) for i in range(nb)]
    tb = [get(nb + i) for i in range(nc)]
    rb = [get(nb + nc + i) for i in range(nrec)]
    lh = {k: v for k, v in hdr.items()
          if k not in ('dir', 'n_base', 'n_corr', 'n_rec', 'encrypted', 'sha256_match')}
    lh['tiers'] = ['light']
    _write_file(light_out, lh, bb, tb, [], None, 0)
    meta = dict(parent_sha256=hdr.get('sha256'), nty=hdr['nty'], ntx=hdr['ntx'],
                tile=hdr['tile'], delta=hdr['delta'], w=hdr['w'], h=hdr['h'],
                n_rec=nrec, lens=[len(b) for b in rb])
    mj = json.dumps(meta).encode()
    with open(rec_out, 'wb') as f:
        f.write(REC_MAGIC); f.write(struct.pack('<I', len(mj))); f.write(mj)
        for b in rb: f.write(b)
    return light_out, rec_out

def join(light, recovery, out, password=None):
    """Rejoin the pair so the result can be decoded bit-identically."""
    hdr, get = open_owlg(light, password)
    raw = open(recovery, 'rb').read()
    if raw[:4] != REC_MAGIC: raise OwlgError('not an .owlr file')
    ml = struct.unpack('<I', raw[4:8])[0]
    meta = json.loads(raw[8:8+ml]); p = 8 + ml
    if meta.get('parent_sha256') and hdr.get('sha256') and meta['parent_sha256'] != hdr['sha256']:
        raise OwlgError('this recovery file does not belong to this light file '
                        '(parent SHA-256 differs)')
    rb = []
    for L in meta['lens']: rb.append(raw[p:p+L]); p += L
    nb, nc = hdr['n_base'], hdr['n_corr']
    bb = [get(i) for i in range(nb)]; tb = [get(nb+i) for i in range(nc)]
    nh = {k: v for k, v in hdr.items()
          if k not in ('dir', 'n_base', 'n_corr', 'n_rec', 'encrypted', 'sha256_match')}
    nh['tiers'] = ['light', 'full']
    _write_file(out, nh, bb, tb, rb, None, 0)
    return out
