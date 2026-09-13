"""OWLG -> ndarray bridge for AI/ML/DL.

Three tiers, matched to the size of the data:
  1. to_npy / to_npz   - load the whole thing into memory, the simplest option
  2. to_memmap         - a memory-mappable .npy, for rasters larger than RAM
  3. patches / Dataset - a window iterator for training, without loading the whole raster
"""
import numpy as np, json, os
from .container import read_owlg, open_owlg

def _meta(hdr):
    return dict(width=hdr['w'], height=hdr['h'], bands=hdr['bands'], dtype='uint8',
                crs=hdr.get('crs'), transform=hdr.get('transform'),
                nodata=hdr.get('nodata'), delta=hdr.get('delta'),
                colorinterp=hdr.get('colorinterp'), layout='CHW')

def to_array(path, password=None, fast=False, layout='CHW'):
    """Return (ndarray, meta). layout is 'CHW' (default, rasterio/torch style) or 'HWC'."""
    a, hdr = read_owlg(path, password, fast)
    if layout == 'HWC': a = np.ascontiguousarray(a.transpose(1, 2, 0))
    m = _meta(hdr); m['layout'] = layout
    return a, m

def to_npy(path, out, password=None, layout='CHW', fast=False):
    a, m = to_array(path, password, fast, layout)
    np.save(out, a)
    with open(os.path.splitext(out)[0] + '.meta.json', 'w') as f: json.dump(m, f, indent=1)
    return out, m

def to_npz(path, out, password=None, layout='CHW', compressed=True, fast=False):
    a, m = to_array(path, password, fast, layout)
    (np.savez_compressed if compressed else np.savez)(out, image=a, meta=np.array(json.dumps(m)))
    return out, m

def to_memmap(path, out, password=None, layout='CHW', fast=False):
    """Write a .npy that np.load(..., mmap_mode='r') can open without loading it into RAM."""
    a, m = to_array(path, password, fast, layout)
    mm = np.lib.format.open_memmap(out, mode='w+', dtype=a.dtype, shape=a.shape)
    mm[:] = a; mm.flush(); del mm
    with open(os.path.splitext(out)[0] + '.meta.json', 'w') as f: json.dump(m, f, indent=1)
    return out, m

def patches(path, size=256, stride=None, password=None, bands=None, drop_partial=True,
            fast=False, skip_empty_alpha=True):
    """Iterator of (patch_CHW, (row, col)) for training. The raster is decoded once, then sliced."""
    stride = stride or size
    a, hdr = read_owlg(path, password, fast)
    if bands is not None: a = a[list(bands)]
    B, H, W = a.shape
    alpha = None
    if skip_empty_alpha and hdr['bands'] == 4 and '3' not in (hdr.get('const') or {}):
        alpha = a[3] if bands is None else None
    for r in range(0, H - (size if drop_partial else 1) + 1, stride):
        for c in range(0, W - (size if drop_partial else 1) + 1, stride):
            p = a[:, r:r+size, c:c+size]
            if p.shape[1] != size or p.shape[2] != size:
                if drop_partial: continue
            if alpha is not None and alpha[r:r+size, c:c+size].max() == 0: continue
            yield np.ascontiguousarray(p), (r, c)

def patch_grid(path, size=256, stride=None, password=None):
    """Count the patches without decoding any pixels (header read only)."""
    hdr, _ = open_owlg(path, password); stride = stride or size
    nr = max(0, (hdr['h'] - size)//stride + 1); nc = max(0, (hdr['w'] - size)//stride + 1)
    return nr*nc, (nr, nc)

class OwlgDataset:
    """torch.utils.data.Dataset over one or many .owlg files.
    Each file's raster is decoded once and then cached; patch retrieval is O(1)."""
    def __init__(self, paths, size=256, stride=None, password=None, transform=None,
                 bands=None, fast=False):
        self.paths = [paths] if isinstance(paths, str) else list(paths)
        self.size = size; self.stride = stride or size; self.password = password
        self.transform = transform; self.bands = bands; self.fast = fast
        self._cache = {}; self.index = []
        for pi, p in enumerate(self.paths):
            hdr, _ = open_owlg(p, password)
            nr = max(0, (hdr['h']-size)//self.stride + 1); nc = max(0, (hdr['w']-size)//self.stride + 1)
            self.index += [(pi, r*self.stride, c*self.stride) for r in range(nr) for c in range(nc)]
    def __len__(self): return len(self.index)
    def _arr(self, pi):
        if pi not in self._cache:
            self._cache.clear()
            a, _ = read_owlg(self.paths[pi], self.password, self.fast)
            self._cache[pi] = a[list(self.bands)] if self.bands is not None else a
        return self._cache[pi]
    def __getitem__(self, i):
        pi, r, c = self.index[i]
        p = np.ascontiguousarray(self._arr(pi)[:, r:r+self.size, c:c+self.size])
        return self.transform(p) if self.transform else p
