"""Read/write compressed imagery through whichever backend is available.

Order tried: imagecodecs (fastest, most complete) -> Pillow (ships with QGIS,
and already supports AVIF) -> GDAL. This is what lets the QGIS plugin run
without any pip install at all.
"""
import io, os, base64, subprocess, tempfile, numpy as np

# Tiny probe blobs, to make sure a backend REALLY can decode rather than merely
# being present. A check based on module presence once reported "all ready" in an
# environment that turned out to have no AVIF driver at all.
AVIF_PROBE = 'AAAAIGZ0eXBhdmlmAAAAAGF2aWZtaWYxbWlhZk1BMUEAAADrbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAcGljdAAAAAAAAAAAAAAAAAAAAAAOcGl0bQAAAAAAAQAAAB5pbG9jAAAAAEQAAAEAAQAAAAEAAAETAAAAJgAAAChpaW5mAAAAAAABAAAAGmluZmUCAAAAAAEAAGF2MDFDb2xvcgAAAABqaXBycAAAAEtpcGNvAAAAFGlzcGUAAAAAAAAACAAAAAgAAAAQcGl4aQAAAAADCAgIAAAADGF2MUOBIAAAAAAAE2NvbHJuY2x4AAIAAgAGgAAAABdpcG1hAAAAAAAAAAEAAQQBAoMEAAAALm1kYXQSAAoIOAi/aQICBpAyGBICQ0qAQQQfIAAAkD/jwO1kgQfiePA1ZA=='
WEBP_PROBE = 'UklGRj4AAABXRUJQVlA4IDIAAADQAQCdASoIAAgAAMASJaACdLoB+AADsAD+2ib/7vN+09e09f1M//jKnyA/4yp/xcwAAA=='
JXL_PROBE  = '/wpBQCQIBAEAhABLEsWCBVIg/WIMzotBF4MuBufdAUCfFwCIfAGDQ2yAFgE='


_HAVE_IC = False
try:
    import imagecodecs as _ic; _HAVE_IC = True
except Exception: _ic = None
try:
    from PIL import Image as _PIL
except Exception: _PIL = None

def can_decode(codec):
    """A REAL test: decode a tiny blob. Returns (can, detail)."""
    probe = {'avif': AVIF_PROBE, 'webp': WEBP_PROBE, 'jxl': JXL_PROBE}.get(codec)
    if probe is None: return False, 'unknown codec'
    blob = base64.b64decode(probe)
    try:
        a = np.asarray(decode_by_codec(codec, blob))
        if a.shape[0] != 8 or a.shape[1] != 8:
            return False, f'unexpected shape {a.shape}'
        return True, 'proven able to decode'
    except Exception as e:
        return False, str(e)[:200]

def capabilities():
    """Summary of this environment's capabilities - used by the plugin diagnostic dialog."""
    caps = {'backends': backends()}
    try:
        from ._compat import HAVE_NUMBA; caps['numba'] = HAVE_NUMBA
    except Exception: caps['numba'] = False
    try:
        from .crypto import backend_name; caps['crypto'] = backend_name()
    except Exception as e: caps['crypto'] = f'failed: {e}'
    try:
        import rasterio; caps['rasterio'] = rasterio.__version__
    except Exception: caps['rasterio'] = None
    try:
        from .geotiff import backend as _gb; caps['geotiff_writer'] = _gb()
    except Exception: caps['geotiff_writer'] = None
    caps['decode'] = {}
    for c in ('avif', 'webp', 'jxl'):
        okc, why = can_decode(c)
        caps['decode'][c] = okc
        if not okc: caps.setdefault('decode_why', {})[c] = why
    caps['can_read_owlg'] = any(caps['decode'].values())
    caps['can_write_geotiff'] = caps['geotiff_writer'] is not None
    return caps

def backends():
    b = []
    if _HAVE_IC: b.append('imagecodecs')
    if _PIL is not None:
        try:
            from PIL import features
            b.append('pillow' + ('+avif' if features.check('avif') else ''))
        except Exception: b.append('pillow')
    try:
        from osgeo import gdal  # noqa
        b.append('gdal')
    except Exception: pass
    return b

def _pil_decode(buf):
    if _PIL is None: raise RuntimeError('Pillow is not available')
    im = _PIL.open(io.BytesIO(bytes(buf)))
    if im.mode not in ('RGB', 'RGBA', 'L'): im = im.convert('RGB')
    return np.asarray(im)

def _gdal_decode(buf, ext):
    from osgeo import gdal
    mem = f'/vsimem/_owlg_dec.{ext}'
    gdal.FileFromMemBuffer(mem, bytes(buf))
    try:
        ds = gdal.Open(mem)
        if ds is None: raise RuntimeError('GDAL failed to open the blob')
        a = ds.ReadAsArray()
        return a.transpose(1, 2, 0) if a.ndim == 3 else a
    finally:
        gdal.Unlink(mem)

def _rio_decode(buf, ext):
    import rasterio
    from rasterio.io import MemoryFile
    with MemoryFile(bytes(buf), ext='.' + ext) as mf:
        with mf.open() as ds:
            a = ds.read()
    return a.transpose(1, 2, 0) if a.ndim == 3 else a

def _qt_decode(buf):
    """Qt is always present in QGIS; on some systems Qt ships an AVIF/HEIF plugin."""
    from qgis.PyQt.QtGui import QImage
    from qgis.PyQt.QtCore import QByteArray, QBuffer, QIODevice
    ba = QByteArray(bytes(buf)); b = QBuffer(ba); b.open(QIODevice.ReadOnly)
    img = QImage()
    if not img.load(b, ''):
        raise RuntimeError('QImage failed to load the blob')
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.constBits(); ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(bytes(ptr), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 3]
    return arr.reshape(h, w, 3).copy()

def _sips_decode(buf, ext):
    """macOS: decode via the system's built-in `sips` (AVIF/HEIF supported since Ventura)."""
    import platform
    if platform.system() != 'Darwin': raise RuntimeError('not macOS')
    d = tempfile.mkdtemp()
    src = os.path.join(d, 'in.' + ext); dst = os.path.join(d, 'out.png')
    try:
        with open(src, 'wb') as f: f.write(bytes(buf))
        r = subprocess.run(['sips', '-s', 'format', 'png', src, '--out', dst],
                           capture_output=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError((r.stderr or b'').decode()[:120] or 'sips failed')
        return png_decode(open(dst, 'rb').read())
    finally:
        for f in (src, dst):
            try: os.remove(f)
            except Exception: pass
        try: os.rmdir(d)
        except Exception: pass

def avif_decode(buf):
    errs = []
    if _HAVE_IC:
        try: return np.asarray(_ic.avif_decode(buf))
        except Exception as e: errs.append(f'imagecodecs: {e}')
    try: return _pil_decode(buf)
    except Exception as e: errs.append(f'pillow: {e}')
    for ext in ('avif', 'heic'):
        try: return _gdal_decode(buf, ext)
        except Exception as e: errs.append(f'gdal/{ext}: {str(e)[:60]}')
    for ext in ('avif', 'heic'):
        try: return _rio_decode(buf, ext)
        except Exception as e: errs.append(f'rasterio/{ext}: {str(e)[:60]}')
    try: return _qt_decode(buf)
    except Exception as e: errs.append(f'qt: {str(e)[:60]}')
    for ext in ('avif', 'heic'):
        try: return _sips_decode(buf, ext)
        except Exception as e: errs.append(f'sips/{ext}: {str(e)[:60]}')
    raise RuntimeError('no backend in this environment can decode AVIF. '
                       'Quickest fix: re-create the file with a portable base '
                       '(owlg encode ... --base webp), or run owlg rebase file.owlg new.owlg '
                       '--base webp on a machine that can decode AVIF. Attempt details: '
                       + ' | '.join(str(x)[:80] for x in errs))

def avif_encode(a, level=90, speed=6):
    if _HAVE_IC: return _ic.avif_encode(np.ascontiguousarray(a), level=int(level), speed=speed)
    if _PIL is not None:
        bio = io.BytesIO()
        _PIL.fromarray(np.ascontiguousarray(a)).save(bio, format='AVIF', quality=int(level))
        return bio.getvalue()
    raise RuntimeError('encoding AVIF requires imagecodecs or Pillow+AVIF')

def jxl_decode(buf):
    if _HAVE_IC:
        try: return np.asarray(_ic.jpegxl_decode(buf))
        except Exception: pass
    return _pil_decode(buf)

def jxl_encode(a, lossless=True, distance=None, effort=6):
    if not _HAVE_IC: raise RuntimeError('encoding JPEG XL requires imagecodecs')
    if lossless: return _ic.jpegxl_encode(np.ascontiguousarray(a), lossless=True, effort=effort)
    return _ic.jpegxl_encode(np.ascontiguousarray(a), distance=float(distance), effort=effort)

def png_encode(a, level=6):
    if _HAVE_IC: return _ic.png_encode(np.ascontiguousarray(a), level=level)
    bio = io.BytesIO(); _PIL.fromarray(np.ascontiguousarray(a)).save(bio, format='PNG'); return bio.getvalue()

def png_decode(buf):
    if _HAVE_IC: return np.asarray(_ic.png_decode(buf))
    try: return _pil_decode(buf)
    except Exception: pass
    try: return _gdal_decode(buf, 'png')
    except Exception: pass
    return _rio_decode(buf, 'png')

def webp_decode(buf):
    errs = []
    if _HAVE_IC:
        try: return np.asarray(_ic.webp_decode(buf))
        except Exception as e: errs.append(f'imagecodecs: {e}')
    try: return _pil_decode(buf)
    except Exception as e: errs.append(f'pillow: {e}')
    try: return _gdal_decode(buf, 'webp')
    except Exception as e: errs.append(f'gdal: {str(e)[:60]}')
    try: return _rio_decode(buf, 'webp')
    except Exception as e: errs.append(f'rasterio: {str(e)[:60]}')
    try: return _qt_decode(buf)
    except Exception as e: errs.append(f'qt: {str(e)[:60]}')
    raise RuntimeError('no backend can decode WebP: '
                       + ' | '.join(str(x)[:80] for x in errs))

def decode_by_codec(codec, buf):
    c = (codec or '').lower()
    if c.startswith('jxl'): return jxl_decode(buf)
    if c == 'webp': return webp_decode(buf)
    return avif_decode(buf)

def jpeg_encode(a, level=85):
    if _HAVE_IC: return _ic.jpeg8_encode(np.ascontiguousarray(a), level=level)
    bio = io.BytesIO(); _PIL.fromarray(np.ascontiguousarray(a)).save(bio, format='JPEG', quality=level); return bio.getvalue()

def webp_encode(a, level=80):
    if _HAVE_IC: return _ic.webp_encode(np.ascontiguousarray(a), level=level, lossless=False)
    bio = io.BytesIO(); _PIL.fromarray(np.ascontiguousarray(a)).save(bio, format='WEBP', quality=level); return bio.getvalue()
