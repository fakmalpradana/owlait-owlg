"""GDAL bridge: a small .vrt sidecar that makes an .owlg readable DIRECTLY by
every GDAL-based application — QGIS, gdal_translate, rasterio, ArcGIS.

What QGIS loads is still the .owlg file itself; no GeoTIFF copy is created, so
the disk footprint stays the size of the .owlg (plus a few kilobytes for the
.vrt). Reads are per-window: only the correction tiles that are actually
visible get decoded.

Requires GDAL_VRT_ENABLE_PYTHON=YES (or a GDAL_VRT_TRUSTED_MODULES that includes
this module). Inside QGIS, the plugin sets this up itself.
"""
import os, sys, numpy as np

def _container():
    try:
        from .container import read_window, open_owlg
        return read_window, open_owlg
    except ImportError:                       # called as a top-level module
        from owlg.container import read_window, open_owlg
        return read_window, open_owlg


def read_band(in_ar, out_ar, xoff, yoff, xsize, ysize,
              raster_xsize, raster_ysize, buf_radius, gt, **kwargs):
    """GDAL pixel function. kwargs come from <PixelFunctionArguments>."""
    def _s(v):
        return v.decode() if isinstance(v, bytes) else v
    path = _s(kwargs['path'])
    band = int(_s(kwargs['band']))
    level = int(_s(kwargs.get('level', '0')))
    fast = str(_s(kwargs.get('fast', '0'))).lower() in ('1', 'true', 'yes')
    pw = _s(kwargs.get('password') or '') or os.environ.get('OWLG_KEY') or None
    read_window, _ = _container()
    try:
        a = read_window(path, band, xoff, yoff, xsize, ysize,
                        password=pw, fast=fast, level=level)
    except TypeError:                      # v3 reader, which has no level parameter
        a = read_window(path, band, xoff, yoff, xsize, ysize, password=pw, fast=fast)
    h, w = a.shape
    out_ar[:] = 0
    out_ar[:h, :w] = a


MODULE_NAME = None          # set by the caller when the package is vendored under another name

def _module_path():
    if MODULE_NAME: return MODULE_NAME
    m = __name__                     # e.g. 'owlg.vrt' or 'owlg_qgis.vendor.owlg.vrt'
    return m if m != '__main__' else 'owlg.vrt'


def _one_vrt(hdr, owlg_path, level, fast, bs, overview_files=None):
    """Build the VRT XML for a single level."""
    lv = (hdr.get('levels') or [None])[level] if hdr.get('levels') else None
    W = lv['w'] if lv else hdr['w']
    H = lv['h'] if lv else hdr['h']
    gt = list(hdr['transform'])
    if lv:                                   # the transform scale follows the level
        sx = hdr['w'] / float(W); sy = hdr['h'] / float(H)
        gt[0] *= sx; gt[1] *= sx; gt[3] *= sy; gt[4] *= sy
    ci = hdr.get('colorinterp') or []
    fn = _module_path() + '.read_band'
    esc = lambda t: (t.replace('&', '&amp;').replace('<', '&lt;')
                      .replace('>', '&gt;').replace('"', '&quot;'))
    xml = [f'<VRTDataset rasterXSize="{W}" rasterYSize="{H}">']
    if hdr.get('crs'):
        xml.append('  <SRS>' + esc(hdr['crs']) + '</SRS>')
    xml.append('  <GeoTransform>' + ', '.join(f'{v:.12g}' for v in
               [gt[2], gt[0], gt[1], gt[5], gt[3], gt[4]]) + '</GeoTransform>')
    if level == 0:
        xml.append('  <Metadata>')
        for k, v in [('OWLG_SOURCE', os.path.basename(owlg_path)),
                     ('OWLG_VERSION', hdr.get('v')), ('OWLG_MODE', hdr.get('mode')),
                     ('OWLG_DELTA', hdr.get('delta')), ('OWLG_BASE_CODEC', hdr.get('codec')),
                     ('OWLG_TILE', hdr.get('tile')),
                     ('OWLG_LEVELS', len(hdr.get('levels') or [1])),
                     ('OWLG_BYTES', os.path.getsize(owlg_path))]:
            xml.append(f'    <MDI key="{k}">{esc(str(v))}</MDI>')
        xml.append('  </Metadata>')
    names = {'red': 'Red', 'green': 'Green', 'blue': 'Blue', 'alpha': 'Alpha', 'gray': 'Gray'}
    for b in range(hdr['bands']):
        nm = names.get((ci[b].lower() if b < len(ci) else ''), None)
        xml += [f'  <VRTRasterBand dataType="Byte" band="{b+1}" subClass="VRTDerivedRasterBand"'
                f' blockXSize="{bs}" blockYSize="{bs}">',
                f'    <PixelFunctionType>{fn}</PixelFunctionType>',
                 '    <PixelFunctionLanguage>Python</PixelFunctionLanguage>',
                f'    <PixelFunctionArguments path="{esc(owlg_path)}" band="{b}"'
                f' level="{level}" fast="{1 if fast else 0}"/>']
        if nm: xml.append(f'    <ColorInterp>{nm}</ColorInterp>')
        for ovf in (overview_files or []):
            xml += ['    <Overview>',
                    f'      <SourceFilename relativeToVRT="0">{esc(ovf)}</SourceFilename>',
                    f'      <SourceBand>{b+1}</SourceBand>',
                    '    </Overview>']
        xml.append('  </VRTRasterBand>')
    xml.append('</VRTDataset>')
    return '\n'.join(xml)


def make_vrt(owlg_path, vrt_path=None, password=None, fast=False, blocksize=None):
    """Write the .vrt sidecar. For OWLG v4 the internal pyramid is exposed as GDAL
    overviews, so QGIS reads the coarse levels when zoomed out."""
    _, open_owlg = _container()
    pw = password or os.environ.get('OWLG_KEY')
    hdr, _g = open_owlg(owlg_path, pw)
    owlg_path = os.path.abspath(owlg_path)
    vrt_path = vrt_path or (owlg_path + '.vrt')
    bs = int(blocksize or hdr.get('tile', 512) or 512)
    levels = hdr.get('levels') or []
    if len(levels) > 1:
        ovf = []
        for lv in range(1, len(levels)):
            op = f'{vrt_path}.ov{lv}.vrt'
            with open(op, 'w') as f:
                f.write(_one_vrt(hdr, owlg_path, lv, fast, bs))
            ovf.append(op)
        with open(vrt_path, 'w') as f:
            f.write(_one_vrt(hdr, owlg_path, 0, fast, bs, ovf))
        return vrt_path
    gt = hdr['transform']
    ci = hdr.get('colorinterp') or []
    bs = int(blocksize or hdr.get('tile', 512) or 512)
    fn = _module_path() + '.read_band'
    esc = lambda t: t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    xml = [f'<VRTDataset rasterXSize="{hdr["w"]}" rasterYSize="{hdr["h"]}">']
    if hdr.get('crs'):
        xml.append('  <SRS>' + esc(hdr['crs']) + '</SRS>')
    xml.append('  <GeoTransform>' + ', '.join(f'{v:.12g}' for v in
               [gt[2], gt[0], gt[1], gt[5], gt[3], gt[4]]) + '</GeoTransform>')
    xml.append('  <Metadata>')
    xml.append(f'    <MDI key="OWLG_SOURCE">{esc(os.path.basename(owlg_path))}</MDI>')
    xml.append(f'    <MDI key="OWLG_MODE">{hdr.get("mode")}</MDI>')
    xml.append(f'    <MDI key="OWLG_DELTA">{hdr.get("delta")}</MDI>')
    xml.append(f'    <MDI key="OWLG_BASE_CODEC">{hdr.get("codec")}</MDI>')
    xml.append(f'    <MDI key="OWLG_BYTES">{os.path.getsize(owlg_path)}</MDI>')
    xml.append('  </Metadata>')
    names = {'red': 'Red', 'green': 'Green', 'blue': 'Blue', 'alpha': 'Alpha', 'gray': 'Gray'}
    for b in range(hdr['bands']):
        nm = names.get((ci[b].lower() if b < len(ci) else ''), None)
        xml += [f'  <VRTRasterBand dataType="Byte" band="{b+1}" subClass="VRTDerivedRasterBand" blockXSize="{bs}" blockYSize="{bs}">',
                f'    <PixelFunctionType>{fn}</PixelFunctionType>',
                 '    <PixelFunctionLanguage>Python</PixelFunctionLanguage>',
                f'    <PixelFunctionArguments path="{esc(owlg_path)}" band="{b}" fast="{1 if fast else 0}"/>']
        if nm: xml.append(f'    <ColorInterp>{nm}</ColorInterp>')
        xml.append('  </VRTRasterBand>')
    xml.append('</VRTDataset>')
    with open(vrt_path, 'w') as f:
        f.write('\n'.join(xml))
    return vrt_path


def selftest_fn(in_ar, out_ar, xoff, yoff, xsize, ysize,
                raster_xsize, raster_ysize, buf_radius, gt, **kwargs):
    """Trivial pixel function: used by the plugin to prove that GDAL really is
    willing to run Python code, before blaming the .owlg file."""
    out_ar[:] = 42
