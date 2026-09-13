"""OWLG — Optimized Owl GeoTIFF.
Near-lossless raster with a hard per-pixel error bound, optionally encrypted."""
__version__ = "0.1.0"
from .errors import OwlgError, NeedKey
from .container import write_owlg, read_owlg, open_owlg, info, to_tif

__all__ = ['write_owlg', 'read_owlg', 'open_owlg', 'info', 'to_tif',
           'OwlgError', 'NeedKey', '__version__']
