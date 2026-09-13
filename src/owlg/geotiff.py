"""Write GeoTIFF through whichever backend is present: rasterio, then osgeo.gdal.

QGIS always ships osgeo.gdal but rarely ships rasterio, so it is the GDAL path
that lets the plugin write decoded output without any installation at all.
"""
import numpy as np

def backend():
    try:
        import rasterio  # noqa
        return 'rasterio'
    except Exception: pass
    try:
        from osgeo import gdal  # noqa
        return 'gdal'
    except Exception: pass
    return None

def _one_nodata(nodata):
    """GeoTIFF carries a single nodata value for the whole dataset, while the
    header stores one per band. Take the first value that is set; return None if
    the list is empty, all None, or disagrees between bands, since inventing a
    dataset-wide value that is wrong for some band would be worse than omitting
    it."""
    if nodata is None:
        return None
    if not isinstance(nodata, (list, tuple)):
        return nodata
    vals = [v for v in nodata if v is not None]
    if not vals:
        return None
    return vals[0] if all(v == vals[0] for v in vals) else None


def write(path, bands, crs_wkt=None, transform6=None, tags=None, colorinterp=None,
          compress='DEFLATE', predictor=2, blocksize=512, nodata=None):
    """bands: (B,H,W) uint8. transform6: (a,b,c,d,e,f) in rasterio/affine style.
    nodata: a scalar, or the per-band list kept in the OWLG header."""
    bands = np.ascontiguousarray(bands)
    nd = _one_nodata(nodata)
    b = backend()
    if b == 'rasterio':  return _w_rasterio(path, bands, crs_wkt, transform6, tags, colorinterp,
                                            compress, predictor, blocksize, nd)
    if b == 'gdal':      return _w_gdal(path, bands, crs_wkt, transform6, tags, colorinterp,
                                        compress, predictor, blocksize, nd)
    raise RuntimeError('writing GeoTIFF requires rasterio or osgeo.gdal')

def _w_rasterio(path, bands, crs_wkt, tr, tags, ci, compress, predictor, bs, nodata=None):
    import rasterio
    from rasterio.transform import Affine
    prof = dict(driver='GTiff', width=bands.shape[2], height=bands.shape[1],
                count=bands.shape[0], dtype=str(bands.dtype), tiled=True,
                blockxsize=bs, blockysize=bs, compress=compress, predictor=predictor)
    if crs_wkt: prof['crs'] = crs_wkt
    if tr: prof['transform'] = Affine(*tr)
    if nodata is not None: prof['nodata'] = nodata
    with rasterio.open(path, 'w', **prof) as ds:
        ds.write(bands)
        if tags: ds.update_tags(**{k: str(v) for k, v in tags.items()})
        if ci:
            try:
                from rasterio.enums import ColorInterp
                ds.colorinterp = [ColorInterp[c] for c in ci]
            except Exception: pass
    return path

_GCI = {'red': 3, 'green': 4, 'blue': 5, 'alpha': 6, 'gray': 1, 'grey': 1, 'undefined': 0}

def _w_gdal(path, bands, crs_wkt, tr, tags, ci, compress, predictor, bs, nodata=None):
    from osgeo import gdal, osr
    gdal.UseExceptions()
    B, H, W = bands.shape
    dt = gdal.GDT_Byte if bands.dtype == np.uint8 else gdal.GDT_UInt16
    opts = [f'COMPRESS={compress}', f'PREDICTOR={predictor}', 'TILED=YES',
            f'BLOCKXSIZE={bs}', f'BLOCKYSIZE={bs}', 'BIGTIFF=IF_SAFER']
    ds = gdal.GetDriverByName('GTiff').Create(path, W, H, B, dt, options=opts)
    if tr:
        # rasterio affine (a,b,c,d,e,f) -> GDAL geotransform (c,a,b,f,d,e)
        ds.SetGeoTransform([tr[2], tr[0], tr[1], tr[5], tr[3], tr[4]])
    if crs_wkt:
        srs = osr.SpatialReference()
        try:
            srs.SetFromUserInput(crs_wkt); ds.SetProjection(srs.ExportToWkt())
        except Exception: pass
    for i in range(B):
        bnd = ds.GetRasterBand(i + 1)
        bnd.WriteArray(bands[i])
        if nodata is not None:
            try: bnd.SetNoDataValue(float(nodata))
            except Exception: pass
        if ci and i < len(ci):
            try: bnd.SetColorInterpretation(_GCI.get(str(ci[i]).lower(), 0))
            except Exception: pass
    if tags:
        try: ds.SetMetadata({k: str(v) for k, v in tags.items()})
        except Exception: pass
    ds.FlushCache(); ds = None
    return path
