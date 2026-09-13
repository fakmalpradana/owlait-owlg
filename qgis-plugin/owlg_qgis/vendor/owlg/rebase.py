"""Swap the base-layer codec of an .owlg without changing its error guarantee.

Worth knowing: the correction layer is computed relative to the already-decoded
base, so the base cannot simply be substituted -- the file has to be re-encoded.
The default is delta=0 against the DECODED CONTENT of the source file, so the
result is bit-identical to whatever the old file decoded to. That means the
guarantee against the original GeoTIFF does not change at all, only the size goes up.

If size matters more than keeping the guarantee exact, pass --delta N; the combined
guarantee against the original becomes (source_delta + N).
"""
import os, tempfile
from .container import read_owlg, write_owlg, open_owlg, DEFAULT_BASE
from .geotiff import write as _gwrite

def rebase(src, dst, base=DEFAULT_BASE, delta=0, password=None, verbose=True):
    hdr, _ = open_owlg(src, password)
    src_delta = int(hdr.get('delta', 0))
    arr, h = read_owlg(src, password)
    tmp = tempfile.mktemp(suffix='.tif')
    try:
        _gwrite(tmp, arr, crs_wkt=h.get('crs'), transform6=h.get('transform'),
                tags=h.get('tags'), colorinterp=h.get('colorinterp'),
                nodata=h.get('nodata'))
        if verbose:
            print(f"  source: base={h['codec']} delta=+/-{src_delta}")
            print(f"  target: base={base} delta=+/-{delta} against the decoded source content")
        # Record what this file actually guarantees against the ORIGINAL raster.
        # The header's `delta` is the correction-layer step used at decode time and
        # must not be touched; `bound_vs_original` is separate provenance, and it is
        # what `owlg verify <rebased> <original>` compares against.
        r = write_owlg(tmp, dst, delta=delta, base=base, tile=h.get('tile', 1024),
                       verbose=verbose,
                       extra_header=dict(bound_vs_original=src_delta + delta,
                                         rebased_from=dict(codec=h.get('codec'),
                                                           delta=src_delta)))
    finally:
        try: os.remove(tmp)
        except Exception: pass
    total = src_delta + delta
    if verbose:
        if delta == 0:
            print(f"  guarantee vs the ORIGINAL GeoTIFF stays +/-{src_delta} DN (unchanged)")
        else:
            from . import _term as T
            print(T.warn(f"  WARNING: the guarantee vs the ORIGINAL GeoTIFF is now +/-{total} DN ")
                  + T.dim(f"({src_delta} from the source + {delta} from re-encoding)"))
    r['guarantee_vs_original'] = total
    return r
