# -*- coding: utf-8 -*-
"""QGIS plugin for .owlg / .owlgt - self-contained, no extra packages to install.

The decoder ships as the SUBPACKAGE `owlg_qgis.vendor.owlg`, not as a top-level
package named `owlg`. That matters: when the plugin is reinstalled, QGIS clears
sys.modules of every module whose name starts with `owlg_qgis.`, so the old
decoder is thrown out along with it. If the decoder stood on its own as `owlg`,
its old modules would survive in the cache and a fresh install would appear to
have no effect until QGIS is restarted.

From QGIS, only numpy plus one of Pillow / GDAL is required. numba,
imagecodecs, rasterio and cryptography are used when present, but are optional.
"""
import os, sys, hashlib, tempfile, traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = "OWLG"
EXPECTED_VERSION = "0.1.0"  # kept in sync with src/owlg/__version__ by scripts/build_all.py
EXTS = ('.owlg', '.owlgt')

from qgis.PyQt.QtWidgets import (QAction, QFileDialog, QInputDialog, QLineEdit,
                                 QMessageBox, QProgressDialog, QDialog, QVBoxLayout,
                                 QLabel, QRadioButton, QDialogButtonBox, QApplication)
from qgis.PyQt.QtCore import QSettings, Qt
from qgis.core import (QgsRasterLayer, QgsProject, QgsMessageLog, Qgis,
                       QgsApplication, QgsDataItemProvider, QgsDataProvider,
                       QgsDataItem, QgsMimeDataUtils)
from qgis.gui import QgsCustomDropHandler


def _log(m, lvl=Qgis.Info):
    QgsMessageLog.logMessage(str(m), PLUGIN, lvl)


# --------------------------------------------------------------- vendor loader
def vendor():
    """Return the decoder package module, or raise with a message you can act on."""
    from .vendor import owlg as _v
    got = getattr(_v, '__version__', '?')
    if got != EXPECTED_VERSION:
        raise ImportError(
            f"decoder version mismatch: found {got}, expected {EXPECTED_VERSION}. "
            "This is usually because QGIS is still holding an old copy in memory - "
            "close QGIS and open it again after installing the plugin.")
    return _v


def _mod(name):
    import importlib
    return importlib.import_module(f'.vendor.owlg.{name}', package=__package__)


# --------------------------------------------------------------- diagnostics
def capability_report():
    """Always return a readable report, even when something is broken."""
    try:
        vendor()
    except Exception as e:
        return None, (f"The decoder package failed to load:\n{type(e).__name__}: {e}\n\n"
                      "The step that almost always fixes this: close QGIS, open it again, "
                      "then try once more. If it still fails, delete the plugin folder "
                      f"'{_HERE}' and reinstall from the ZIP.")
    caps = {}
    try:
        imgio = _mod('imgio')
        fn = getattr(imgio, 'capabilities', None)
        caps = fn() if fn else {'backends': imgio.backends()}
    except Exception as e:
        return None, f"imgio module failed: {type(e).__name__}: {e}"
    # fill in fields that may be missing on older versions
    caps.setdefault('backends', [])
    if 'geotiff_writer' not in caps:
        try: caps['geotiff_writer'] = _mod('geotiff').backend()
        except Exception: caps['geotiff_writer'] = None
    if 'numba' not in caps:
        try: caps['numba'] = _mod('_compat').HAVE_NUMBA
        except Exception: caps['numba'] = False
    if 'crypto' not in caps:
        try: caps['crypto'] = _mod('crypto').backend_name()
        except Exception: caps['crypto'] = 'unknown'
    caps.setdefault('rasterio', None)
    # A REAL test per codec - not merely "the module is importable".
    if 'decode' not in caps:
        caps['decode'] = {}; caps['decode_why'] = {}
        try:
            cd = getattr(_mod('imgio'), 'can_decode', None)
            if cd:
                for cc in ('avif', 'webp', 'jxl'):
                    okc, why = cd(cc)
                    caps['decode'][cc] = okc
                    if not okc: caps['decode_why'][cc] = why
        except Exception: pass
    caps['can_read_owlg'] = any(caps['decode'].values()) if caps['decode'] else bool(caps['backends'])
    caps['can_write_geotiff'] = caps.get('geotiff_writer') is not None

    lines = [
        f"Decoder version : {getattr(vendor(), '__version__', '?')}",
        f"Image backends  : {', '.join(caps['backends']) or 'NONE'}",
        f"GeoTIFF writer  : {caps['geotiff_writer'] or 'NONE'}",
        f"Encryption      : {caps['crypto']}",
        "numba (fast)    : " + ('present' if caps['numba'] else
            'absent - exact decoding falls back to the pure Python path: correct and '
            'bit-identical, but roughly 2-4 s per 512 px tile'),
        f"rasterio        : {caps['rasterio'] or 'absent (not required)'}",
    ]
    if caps.get('decode'):
        d = caps['decode']
        lines.append("Real decode test: " + ", ".join(
            f"{k}={'YES' if v else 'NO'}" for k, v in d.items()))
    problems = []
    if not caps['can_read_owlg']:
        problems.append("No image codec can be decoded in this environment.")
    elif caps.get('decode') and not caps['decode'].get('avif'):
        problems.append("AVIF CANNOT be decoded here (WebP can). .owlg files with an "
                        "AVIF base will not open. Create files with a portable base: "
                        "`owlg encode input.tif output.owlg --base webp`, or convert an "
                        "existing file on another machine: `owlg rebase old.owlg new.owlg --base webp`.")
    if not caps['can_write_geotiff']:
        problems.append("No GeoTIFF writer (osgeo.gdal or rasterio is required). "
                        "osgeo.gdal should always be present in QGIS.")
    return caps, "\n".join(lines) + ("\n\nPROBLEMS:\n- " + "\n- ".join(problems) if problems
                                     else "\n\nEverything is ready - nothing needs to be installed.")


def _cache_dir():
    d = os.path.join(tempfile.gettempdir(), 'owlg_qgis_cache'); os.makedirs(d, exist_ok=True); return d

def _cache_path(path, suffix):
    key = hashlib.sha256((path + str(os.path.getmtime(path)) + suffix +
                          EXPECTED_VERSION).encode()).hexdigest()[:16]
    return os.path.join(_cache_dir(), key + '.tif')


MAX_CACHE_BYTES = 2 << 30        # above this, decoding to GeoTIFF is refused


class ModeDialog(QDialog):
    def __init__(self, parent, info, slow, size_owlg=0, size_tif=0,
                 tiled=False, levels=0, convert_block=None):
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN} - how to load")
        v = QVBoxLayout(self)
        lab = QLabel(info); lab.setWordWrap(True); v.addWidget(lab)
        v.addWidget(QLabel("<b>Layer source</b>"))
        self.direct = QRadioButton(
            f"Read directly from the .owlg - no copy "
            f"({size_owlg/1e6:.2f} MB on disk)")
        if convert_block:
            self.convert = QRadioButton("Decode to GeoTIFF (cache) - not available")
            self.convert.setEnabled(False)
            self.convert.setToolTip(convert_block)
        else:
            self.convert = QRadioButton(
                f"Decode to GeoTIFF (cache) - needs about +/-{size_tif/1e6:.1f} MB extra")
        self.direct.setChecked(True)
        v.addWidget(self.direct); v.addWidget(self.convert)
        if convert_block:
            w = QLabel("Warning: " + convert_block); w.setWordWrap(True)
            w.setStyleSheet("color:#b35c00;"); v.addWidget(w)

        v.addWidget(QLabel("<b>Accuracy</b>"))
        self.exact = QRadioButton("Exact - apply the corrections, error bound guaranteed")
        self.fast = QRadioButton("Fast preview - base layer only")
        # Without numba, correction decoding runs on the pure Python path: correct,
        # but several seconds per tile. That makes panning the map unpleasant, so
        # the default is preview - and the reason is stated as it is.
        if slow: self.fast.setChecked(True)
        else: self.exact.setChecked(True)
        v.addWidget(self.exact); v.addWidget(self.fast)

        note = ["Reading directly keeps the format as .owlg: QGIS decodes only the "
                "tiles currently visible, and no GeoTIFF copy is created."]
        if tiled:
            note.append(f"This file is tiled with {levels} pyramid levels, "
                        "so when you zoom out QGIS reads a coarse level - not the whole raster.")
        else:
            note.append("This file uses the flat (v3) layout: the whole raster is decoded "
                        "once when it is opened. For large data, re-encode with "
                        "'owlg encode ... --layout tiled'.")
        if slow:
            note.append("numba is not installed in this QGIS Python. Exact mode is still "
                        "correct, but takes +/-2-4 seconds per 512 px tile. Fast preview "
                        "is instantaneous (+/-50 ms per tile). Exact mode becomes fast "
                        "once numba is installed in the QGIS Python environment.")
        note = QLabel("\n".join("- " + n for n in note)); note.setWordWrap(True)
        note.setStyleSheet("color: gray;"); v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); v.addWidget(bb)


def _fail(iface, title, body):
    box = QMessageBox(iface.mainWindow() if iface else None)
    box.setIcon(QMessageBox.Critical); box.setWindowTitle(PLUGIN)
    box.setText(title); box.setInformativeText(body)
    box.setDetailedText(traceback.format_exc() + f"\nvendor: {_HERE}\npython: {sys.version}")
    box.exec_()


def _enable_vrt_python():
    """Allow Python pixel functions in GDAL, restricted to our own module.

    The option is named GDAL_VRT_PYTHON_TRUSTED_MODULES (not GDAL_VRT_TRUSTED_MODULES).
    Wrong name = GDAL refuses to run the pixel function, the direct read fails,
    and the plugin used to silently fall back to a GeoTIFF copy.
    """
    modname = __package__ + '.vendor.owlg.vrt'
    names = ('GDAL_VRT_PYTHON_TRUSTED_MODULES', 'GDAL_VRT_TRUSTED_MODULES')
    try:
        from osgeo import gdal
        cur = (gdal.GetConfigOption('GDAL_VRT_ENABLE_PYTHON', '') or '').upper()
        for opt in names:
            trusted = gdal.GetConfigOption(opt, '') or os.environ.get(opt, '') or ''
            if modname not in trusted.split(','):
                trusted = (trusted + ',' if trusted else '') + modname
            gdal.SetConfigOption(opt, trusted)
            os.environ[opt] = trusted
        if cur != 'YES':
            gdal.SetConfigOption('GDAL_VRT_ENABLE_PYTHON', 'TRUSTED_MODULES')
            os.environ.setdefault('GDAL_VRT_ENABLE_PYTHON', 'TRUSTED_MODULES')
    except Exception as e:
        _log(f"could not set GDAL_VRT_ENABLE_PYTHON: {e}", Qgis.Warning)


def _vrt_selftest():
    """A real one-shot test: will GDAL actually run our pixel function?
    Returns (ok, message) - used by the 'Decoder diagnostics' menu entry."""
    import tempfile as _tf
    try:
        from osgeo import gdal
        import numpy as _np
    except Exception as e:
        return False, f"osgeo.gdal not available: {e}"
    _enable_vrt_python()
    fn = __package__ + '.vendor.owlg.vrt.selftest_fn'
    xml = ('<VRTDataset rasterXSize="4" rasterYSize="4">'
           '<VRTRasterBand dataType="Byte" band="1" subClass="VRTDerivedRasterBand">'
           f'<PixelFunctionType>{fn}</PixelFunctionType>'
           '<PixelFunctionLanguage>Python</PixelFunctionLanguage>'
           '</VRTRasterBand></VRTDataset>')
    p = os.path.join(_tf.gettempdir(), 'owlg_vrt_selftest.vrt')
    try:
        with open(p, 'w') as f: f.write(xml)
        ds = gdal.Open(p)
        a = ds.GetRasterBand(1).ReadAsArray()
        return (int(a[0, 0]) == 42), ('Python pixel function runs'
                                      if int(a[0, 0]) == 42 else 'unexpected result')
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try: os.remove(p)
        except Exception: pass


def _vrt_path_for(path):
    """Sidecar .vrt next to the file; if that folder is not writable, in the cache."""
    side = path + '.vrt'
    try:
        with open(side, 'a'): pass
        return side
    except Exception:
        return _cache_path(path, 'vrt').replace('.tif', '.vrt')


def _apply_metadata(lyr, path, hdr, how):
    """Write the OWLG identity into the layer metadata so the Information panel
    does not just show the GDAL driver that is used as a bridge."""
    sz = os.path.getsize(path)
    lv = len(hdr.get('levels') or [])
    mode = ('lossless (bit-identical)' if hdr.get('delta') == 0
            else f"near-lossless +/-{hdr.get('delta')} DN")
    abstract = (f"Format: OWLG v{hdr.get('v')} ({'tiled + pyramid' if lv > 1 else 'flat'})\n"
                f"Source file: {os.path.basename(path)} - {sz/1e6:.2f} MB on disk\n"
                f"Mode: {mode}\nBase codec: {hdr.get('codec')}\n"
                f"Tile: {hdr.get('tile')} px" + (f" - {lv} pyramid levels" if lv else "") + "\n"
                f"Loaded: {how}\n"
                "Note: the driver shown in the provider panel is the GDAL bridge; "
                "the bytes that are stored and read still belong to the .owlg file above.")
    try:
        md = lyr.metadata()
        md.setTitle(os.path.basename(path))
        md.setAbstract(abstract)
        try: md.setKeywords({'format': ['OWLG', f"v{hdr.get('v')}", mode]})
        except Exception: pass
        lyr.setMetadata(md)
    except Exception as e:
        _log(f"layer metadata could not be filled in: {e}", Qgis.Warning)
    for k, v in (('owlg/path', path), ('owlg/bytes', str(sz)),
                 ('owlg/version', str(hdr.get('v'))), ('owlg/mode', mode),
                 ('owlg/levels', str(lv))):
        try: lyr.setCustomProperty(k, v)
        except Exception: pass


def _load_direct(path, password, fast, iface):
    """Load the .owlg AS IS through the VRT bridge - no GeoTIFF copy."""
    _enable_vrt_python()
    vrt_mod = _mod('vrt')
    if password: os.environ['OWLG_KEY'] = password
    vp = _vrt_path_for(path)
    vrt_mod.make_vrt(path, vp, password=password, fast=fast)
    lyr = QgsRasterLayer(vp, os.path.splitext(os.path.basename(path))[0]
                         + ('' if not fast else ' (preview)'), 'gdal')
    if not lyr.isValid():
        raise RuntimeError(f"VRT layer is not valid: {lyr.error().summary() if lyr.error() else ''}")
    prov = lyr.dataProvider()
    blk = prov.block(1, lyr.extent(), 2, 2)      # a real read test, not just isValid
    if blk is None or not blk.isValid():
        raise RuntimeError("GDAL cannot read pixels through the VRT "
                           "(the Python pixel function was probably refused)")
    return lyr, vp


def load_owlg(path, iface=None):
    caps, rep = capability_report()
    if caps is None:
        _fail(iface, "The plugin decoder is not ready.", rep); return None
    if not caps['can_read_owlg']:
        _fail(iface, "This environment cannot decode OWLG yet.", rep); return None
    try:
        container = _mod('container')
        HAVE_NUMBA = _mod('_compat').HAVE_NUMBA
    except Exception as e:
        _fail(iface, "The decoder modules failed to load.", f"{type(e).__name__}: {e}\n\n{rep}"); return None

    password = None
    try:
        container.open_owlg(path)
    except Exception as e:
        if type(e).__name__ == 'NeedKey' or 'encrypted' in str(e):
            if iface is None:
                _log(f"encrypted file, a passphrase is required: {path}", Qgis.Warning); return None
            password, ok = QInputDialog.getText(iface.mainWindow(), PLUGIN,
                f"Encrypted file:\n{os.path.basename(path)}\n\nPassphrase:", QLineEdit.Password)
            if not ok: return None
        else:
            _fail(iface, "Cannot read the file.", str(e)); return None
    try:
        hdr, _unused = container.open_owlg(path, password)
    except Exception as e:
        _fail(iface, "Failed to open the file.", str(e)); return None

    need = str(hdr.get('codec', '')).replace('_lossless', '')
    if caps.get('decode') and need in caps['decode'] and not caps['decode'][need]:
        why = (caps.get('decode_why') or {}).get(need, '')
        _fail(iface, f"This file uses the base layer '{hdr.get('codec')}', "
                     "which cannot be decoded in this QGIS environment.",
              f"Reason: {why[:300]}\n\n"
              "Two ways out:\n"
              f"1. Re-encode from the original GeoTIFF with a portable base:\n"
              f"     owlg encode original.tif new.owlg --delta {hdr.get('delta', 2)} --base webp\n"
              "2. If the original file no longer exists, change its base on a machine "
              "that can decode AVIF (the error bound is unchanged):\n"
              "     owlg rebase old.owlg new.owlg --base webp\n\n"
              + (rep or ''))
        return None

    lossless = hdr.get('mode') == 'lossless'
    has_rec = bool(hdr.get('n_rec'))
    levels = hdr.get('levels') or []
    tiled = int(hdr.get('v', 0)) >= 4 and len(levels) >= 1
    sz = os.path.getsize(path)
    raw = hdr['w'] * hdr['h'] * hdr['bands']
    approx_tif = raw * 0.45                      # rough GeoTIFF DEFLATE estimate

    # Decode-to-GeoTIFF loads the whole raster into RAM and then writes it to disk.
    # For 10-100 GB of data that is not a slow option, it is an option that cannot
    # finish. Block it and say why; do not let it be attempted silently.
    convert_block = None
    if raw > MAX_CACHE_BYTES:
        convert_block = (f"This raster is {raw/1e9:.1f} GB uncompressed. Decoding to "
                         f"GeoTIFF would load all of it into RAM and write +/-"
                         f"{approx_tif/1e9:.1f} GB to disk. Use the direct read; "
                         "to export, use the CLI: owlg decode file.owlg out.tif")
    elif not tiled and raw > (512 << 20):
        convert_block = (f"This file uses the flat (v3) layout and is {raw/1e9:.1f} GB "
                         "uncompressed - decoding it in one go is impractical.")

    info = (f"<b>{os.path.basename(path)}</b><br>"
            f"{hdr['w']} x {hdr['h']} x {hdr['bands']} pixels &nbsp;&middot;&nbsp; "
            f"{sz/1e6:.2f} MB on disk &nbsp;&middot;&nbsp; OWLG v{hdr.get('v')}<br>"
            + ("Mode: <b>lossless</b> - pixels identical to the original GeoTIFF." if lossless else
               (f"Mode: near-lossless +/-{hdr['delta']} DN, <b>with a recovery tier</b> - "
                "the exact result will be bit-identical to the original." if has_rec else
                f"Mode: near-lossless, error bound <b>+/-{hdr['delta']} DN</b> per pixel.")))
    fast = not HAVE_NUMBA; direct = True
    if iface is not None:
        dlg = ModeDialog(iface.mainWindow(), info, slow=not HAVE_NUMBA,
                         size_owlg=sz, size_tif=approx_tif,
                         tiled=tiled, levels=len(levels), convert_block=convert_block)
        if lossless and HAVE_NUMBA:
            dlg.exact.setChecked(True); dlg.fast.setEnabled(False)
        if dlg.exec_() != QDialog.Accepted: return None
        fast = dlg.fast.isChecked(); direct = dlg.direct.isChecked()

    name = os.path.splitext(os.path.basename(path))[0] + ('' if not fast else ' (preview)')
    lyr = None; how = ''
    if direct:
        try:
            lyr, vp = _load_direct(path, password, fast, iface)
            how = f"directly from the .owlg ({sz/1e6:.2f} MB, sidecar {os.path.getsize(vp)/1e3:.0f} kB)"
        except Exception as e:
            # A failed direct read must NOT silently turn into a GeoTIFF copy:
            # the user would believe the format is still .owlg when it is not.
            detail = f"{type(e).__name__}: {e}"
            _log("direct load failed: " + detail, Qgis.Critical)
            body = ("The GDAL bridge refused to read the .owlg as is. The most "
                    "common causes:\n"
                    "- the GDAL in this QGIS was built without Python pixel function support;\n"
                    "- the GDAL_VRT_ENABLE_PYTHON policy blocks the plugin module;\n"
                    "- the file's folder cannot be written to for the .vrt sidecar.\n\n"
                    f"Original message: {detail}\n\n"
                    "The OWLG > 'Decoder diagnostics' menu shows the full diagnosis.")
            if convert_block:
                _fail(iface, "Failed to load the .owlg directly.",
                      body + "\n\nDecoding to GeoTIFF is not offered: " + convert_block)
                return None
            if iface is None:
                # Without a GUI there is nobody to ask. Carry on to the GeoTIFF
                # copy so scripts do not die, but log loudly that the format HAS
                # CHANGED - this is exactly what used to happen silently.
                _log("WARNING: direct read failed; the layer will use a GeoTIFF "
                     "COPY, not the .owlg. " + body.replace("\n", " "), Qgis.Critical)
                lyr = None
            else:
                box = QMessageBox(iface.mainWindow())
                box.setIcon(QMessageBox.Critical); box.setWindowTitle(PLUGIN)
                box.setText("Failed to load the .owlg directly.")
                box.setInformativeText(body + "\n\nDecode to a temporary GeoTIFF copy "
                                       f"(+/-{approx_tif/1e6:.0f} MB) instead? "
                                       "The resulting layer is NO LONGER an .owlg.")
                box.setDetailedText(traceback.format_exc() + f"\nvendor: {_HERE}\npython: {sys.version}")
                box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
                box.setDefaultButton(QMessageBox.Cancel)
                if box.exec_() != QMessageBox.Yes: return None
                lyr = None
    if lyr is None:
        if convert_block:
            _fail(iface, "Decoding to GeoTIFF cannot be done.", convert_block); return None
        tif = _cache_path(path, 'fast' if fast else 'exact')
        if not os.path.exists(tif):
            prog = QProgressDialog("Decoding OWLG...", None, 0, 0,
                                   iface.mainWindow() if iface else None)
            prog.setWindowModality(Qt.WindowModal); prog.setMinimumDuration(0); prog.show()
            QApplication.processEvents()
            try:
                container.to_tif(path, tif, password=password, fast=fast)
            except Exception as e:
                prog.close(); _fail(iface, "Failed to decode the file.",
                                    f"{type(e).__name__}: {e}\n\n{rep}")
                return None
            finally: prog.close()
        lyr = QgsRasterLayer(tif, name + ' [GeoTIFF copy]', 'gdal')
        if not lyr.isValid():
            _fail(iface, "Layer is not valid after decoding.", tif); return None
        how = f"decoded to a cached GeoTIFF ({os.path.getsize(tif)/1e6:.1f} MB)"
    _apply_metadata(lyr, path, hdr, how)
    QgsProject.instance().addMapLayer(lyr)
    if iface:
        msg = f"OWLG loaded - {how}"
        if not lossless and not fast: msg += f" - error guaranteed <= +/-{hdr['delta']} DN"
        if fast: msg += " - base layer preview, corrections not applied"
        iface.messageBar().pushMessage(PLUGIN, msg,
                                       level=Qgis.Success if direct else Qgis.Warning,
                                       duration=10)
    _log(f"{os.path.basename(path)}: {how}")
    return lyr


def load_owlgt(path, iface=None):
    caps, rep = capability_report()
    if caps is None:
        _fail(iface, "The plugin decoder is not ready.", rep); return None
    try:
        tiles = _mod('tiles'); mosaic = _mod('mosaic')
        hdr, _a, _b = tiles.open_owlgt(path)
    except Exception as e:
        _fail(iface, "Not a valid OWLGT file, or the decoder failed.", str(e)); return None
    zooms = [str(z) for z in range(hdr['minzoom'], hdr['maxzoom'] + 1)]
    z = zooms[-1]
    if iface is not None:
        z, ok = QInputDialog.getItem(iface.mainWindow(), PLUGIN,
            f"{os.path.basename(path)}\nProfile: {hdr['profile']} - {len(hdr['index'])} tiles\n\n"
            "Assemble at zoom:", zooms, len(zooms) - 1, False)
        if not ok: return None
    tif = _cache_path(path, 'z' + str(z))
    if not os.path.exists(tif):
        prog = QProgressDialog("Assembling OWLGT tiles...", None, 0, 0, iface.mainWindow() if iface else None)
        prog.setWindowModality(Qt.WindowModal); prog.setMinimumDuration(0); prog.show()
        QApplication.processEvents()
        try:
            mosaic.to_tif(path, tif, z=int(z))
        except Exception as e:
            prog.close(); _fail(iface, "Failed to assemble the tiles.", str(e)); return None
        finally: prog.close()
    lyr = QgsRasterLayer(tif, f"{os.path.splitext(os.path.basename(path))[0]} z{z}", 'gdal')
    if not lyr.isValid(): _fail(iface, "Layer is not valid.", tif); return None
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def load_any(path, iface=None):
    return load_owlgt(path, iface) if path.lower().endswith('.owlgt') else load_owlg(path, iface)


# ------------------------------------------------- Browser panel integration
class OwlgLayerItem(QgsDataItem):
    def __init__(self, parent, name, path, iface):
        super().__init__(QgsDataItem.Custom, parent, name, path)
        self.filePath = path; self._iface = iface
        try:
            self.setState(QgsDataItem.Populated); self.setToolTip(path)
        except Exception: pass
    def hasDragEnabled(self): return True
    def mimeUri(self):
        u = QgsMimeDataUtils.Uri()
        u.layerType = "raster"; u.providerKey = "gdal"; u.name = self.name(); u.uri = self.filePath
        return u
    def handleDoubleClick(self):
        load_any(self.filePath, self._iface); return True


class OwlgDataItemProvider(QgsDataItemProvider):
    def __init__(self, iface): super().__init__(); self._iface = iface
    def name(self): return "OWLG"
    def capabilities(self): return QgsDataProvider.File
    def createDataItem(self, path, parentItem):
        if path and path.lower().endswith(EXTS):
            return OwlgLayerItem(parentItem, os.path.basename(path), path, self._iface)
        return None


class OwlgDropHandler(QgsCustomDropHandler):
    def __init__(self, iface): super().__init__(); self.iface = iface
    def handleFileDrop(self, f):
        if f.lower().endswith(EXTS):
            load_any(f, self.iface); return True
        return False


class OwlgPlugin:
    def __init__(self, iface):
        self.iface = iface; self.actions = []; self.drop = None
        self.provider = None; self.httpd = None; self.toolbar = None
    def initGui(self):
        self.toolbar = self.iface.addToolBar("OWLG"); self.toolbar.setObjectName("OwlgToolbar")
        for text, fn, on_bar in [("Open OWLG / OWLGT file...", self.on_open, True),
                                 ("Serve OWLGT as WMS / OGC API...", self.on_serve, False),
                                 ("Decoder diagnostics", self.on_env, False)]:
            a = QAction(text, self.iface.mainWindow()); a.triggered.connect(fn)
            self.iface.addPluginToRasterMenu("&OWLG", a)
            if on_bar: self.toolbar.addAction(a)
            self.actions.append(a)
        try:
            self.drop = OwlgDropHandler(self.iface)
            self.iface.registerCustomDropHandler(self.drop)
        except Exception as e: _log(f"drop handler failed: {e}", Qgis.Warning)
        try:
            self.provider = OwlgDataItemProvider(self.iface)
            QgsApplication.dataItemProviderRegistry().addProvider(self.provider)
        except Exception as e: _log(f"browser provider failed: {e}", Qgis.Warning)
        caps, rep = capability_report()
        if caps is None or not caps.get('can_read_owlg'):
            _log("OWLG is not ready - open Raster > OWLG > Decoder diagnostics\n" + (rep or ''),
                 Qgis.Warning)
            try:
                self.iface.messageBar().pushMessage(
                    PLUGIN, "The decoder is not ready - open Raster > OWLG > Decoder diagnostics",
                    level=Qgis.Warning, duration=10)
            except Exception: pass
        else:
            _log("OWLG ready. " + rep.splitlines()[0])
    def unload(self):
        for a in self.actions:
            try: self.iface.removePluginRasterMenu("&OWLG", a)
            except Exception: pass
        if self.toolbar:
            try: self.toolbar.deleteLater()
            except Exception: pass
        if self.drop:
            try: self.iface.unregisterCustomDropHandler(self.drop)
            except Exception: pass
        if self.provider:
            try: QgsApplication.dataItemProviderRegistry().removeProvider(self.provider)
            except Exception: pass
        if self.httpd:
            try: self.httpd.shutdown()
            except Exception: pass
        # drop the decoder from the module cache so a reinstall really takes effect
        pref = __package__ + '.vendor'
        for key in [k for k in list(sys.modules) if k == pref or k.startswith(pref + '.')]:
            sys.modules.pop(key, None)
    def _pick(self, filt):
        last = QSettings().value("owlg/lastdir", "")
        f, _ = QFileDialog.getOpenFileName(self.iface.mainWindow(), "Select file", last, filt)
        if f: QSettings().setValue("owlg/lastdir", os.path.dirname(f))
        return f
    def on_open(self):
        f = self._pick("OWLG & OWLGT (*.owlg *.owlgt);;OWLG (*.owlg);;OWLGT (*.owlgt);;All files (*)")
        if f: load_any(f, self.iface)
    def on_serve(self):
        f = self._pick("OWLGT (*.owlgt);;All files (*)")
        if not f: return
        port, ok = QInputDialog.getInt(self.iface.mainWindow(), PLUGIN, "Port:", 8080, 1024, 65535)
        if not ok: return
        try:
            self.httpd = _mod('server').serve(f, port=port, block=False)
            cid = os.path.splitext(os.path.basename(f))[0]
            url = f"http://127.0.0.1:{port}"
            QMessageBox.information(self.iface.mainWindow(), PLUGIN,
                "Server running.\n\n"
                f"WMS  : {url}/wms\n"
                "  (Layer > Add Layer > Add WMS/WMTS Layer, enter the URL above)\n\n"
                f"XYZ  : {url}/xyz/{cid}/{{z}}/{{x}}/{{y}}.png\n"
                f"OGC  : {url}/collections\n\n"
                "The server stops when the plugin is unloaded or QGIS is closed.")
        except Exception as e:
            _fail(self.iface, "Failed to start the server.", str(e))
    def on_env(self):
        caps, rep = capability_report()
        vok, vmsg = _vrt_selftest()
        rep = (rep or "") + (
            "\n\nDirect read (GDAL bridge)\n"
            f"  Python pixel function : {'YES' if vok else 'NO'} - {vmsg}\n"
            + ("" if vok else
               "  As a result .owlg cannot be read as is; the plugin will offer a\n"
               "  GeoTIFF copy (and refuse even that for large rasters). The GDAL in\n"
               "  this QGIS was probably built without Python pixel function support.\n"))
        box = QMessageBox(self.iface.mainWindow()); box.setWindowTitle(PLUGIN)
        box.setIcon(QMessageBox.Information if (caps and caps.get('can_read_owlg') and vok)
                    else QMessageBox.Warning)
        box.setText("The decoder is bundled inside the plugin - no pip install needed.")
        box.setInformativeText(rep or "")
        box.setDetailedText(f"plugin : {_HERE}\npython : {sys.version}\n"
                            f"package: {__package__}.vendor.owlg\n"
                            f"expected version {EXPECTED_VERSION}")
        box.exec_()
