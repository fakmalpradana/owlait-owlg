"""Shared fixtures for the OWLG test suite.

Everything here is deliberately lazy: optional dependencies (rasterio,
imagecodecs, cryptography, GDAL) are probed at import time but never required,
so the suite still collects and runs in a minimal environment.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SAMPLE = ROOT / "samples" / "rgb_small.tif"

LAYOUTS = ("flat", "tiled")


# --------------------------------------------------------------- capabilities
def _have(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


HAVE_RASTERIO = _have("rasterio")
HAVE_IMAGECODECS = _have("imagecodecs")
HAVE_CRYPTOGRAPHY = _have("cryptography")
HAVE_GDAL = _have("osgeo.gdal")


def _codec_ok(codec: str) -> bool:
    try:
        from owlg import imgio

        return bool(imgio.can_decode(codec)[0])
    except Exception:
        return False


HAVE_WEBP = _codec_ok("webp")
HAVE_JXL = HAVE_IMAGECODECS and _codec_ok("jxl")

# An encoder needs rasterio (to read the source) and a real image codec.
needs_encoder = pytest.mark.skipif(
    not (HAVE_RASTERIO and HAVE_WEBP),
    reason="encoding needs rasterio and a working WebP codec",
)
needs_rasterio = pytest.mark.skipif(not HAVE_RASTERIO, reason="rasterio is not installed")
needs_cryptography = pytest.mark.skipif(
    not HAVE_CRYPTOGRAPHY, reason="the cryptography package is not installed"
)


def layout_supports(layout: str, delta: int) -> bool:
    """The flat layout stores delta=0 as JPEG XL lossless; everything else is WebP."""
    if layout == "flat" and delta == 0:
        return HAVE_JXL
    return HAVE_WEBP


def skip_unless_supported(layout: str, delta: int) -> None:
    if not layout_supports(layout, delta):
        pytest.skip(f"{layout}/delta={delta} needs a codec this environment lacks")


# ------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _clean_owlg_state(monkeypatch):
    """OWLG picks passphrases up from the environment, and both readers memoise
    open files in module-level caches. Neither may leak between tests."""
    monkeypatch.delenv("OWLG_KEY", raising=False)
    _drop_caches()
    yield
    _drop_caches()


def _drop_caches() -> None:
    try:
        from owlg import container

        container._WCACHE.clear()
    except Exception:
        pass
    try:
        from owlg import tiled_read

        for k in list(tiled_read._RCACHE):
            r = tiled_read._RCACHE.pop(k, None)
            try:
                r.close()
            except Exception:
                pass
    except Exception:
        pass


@pytest.fixture(scope="session")
def sample_tif() -> pathlib.Path:
    """The RGB sample raster shipped with the repository (791x718x3, EPSG:32618)."""
    if not SAMPLE.is_file():
        pytest.skip(f"sample raster missing: {SAMPLE}")
    return SAMPLE


@pytest.fixture
def workspace(tmp_path) -> pathlib.Path:
    """A scratch directory for one test."""
    return tmp_path


@pytest.fixture(scope="session")
def shared_dir(tmp_path_factory) -> pathlib.Path:
    """Session-wide scratch directory: encoded fixtures are cached here."""
    return tmp_path_factory.mktemp("owlg")


@pytest.fixture(scope="session")
def read_geotiff():
    """read_geotiff(path) -> (B, H, W) uint8 ndarray, straight off disk."""

    def _read(path, indexes=None):
        rasterio = pytest.importorskip("rasterio")
        import numpy as np
        import warnings

        warnings.filterwarnings("ignore")
        with rasterio.open(str(path)) as ds:
            a = ds.read() if indexes is None else ds.read(indexes)
        return np.ascontiguousarray(a)

    return _read


@pytest.fixture(scope="session")
def geotiff_meta():
    """geotiff_meta(path) -> dict of the georeferencing that must survive a round trip."""

    def _meta(path):
        rasterio = pytest.importorskip("rasterio")
        import warnings

        warnings.filterwarnings("ignore")
        with rasterio.open(str(path)) as ds:
            return dict(
                width=ds.width,
                height=ds.height,
                count=ds.count,
                dtype=ds.dtypes[0],
                crs_wkt=ds.crs.to_wkt() if ds.crs else None,
                transform=list(ds.transform)[:6],
                colorinterp=[c.name for c in ds.colorinterp],
                nodata=list(ds.nodatavals),
                tags=dict(ds.tags()),
            )

    return _meta


@pytest.fixture(scope="session")
def make_raster(shared_dir):
    """make_raster(name, array, nodata=None, colorinterp=None, crs=..., transform=...)

    Writes a small GeoTIFF from a (B, H, W) uint8 array and returns its path.
    Georeferencing defaults to the sample raster's, so the synthetic files are
    directly comparable with it.
    """

    def _make(name, array, nodata=None, colorinterp=None, crs=None, transform=None):
        rasterio = pytest.importorskip("rasterio")
        import numpy as np
        import warnings

        warnings.filterwarnings("ignore")
        array = np.ascontiguousarray(array.astype(np.uint8))
        b, h, w = array.shape
        if crs is None or transform is None:
            if not SAMPLE.is_file():
                pytest.skip("sample raster missing; cannot borrow its georeferencing")
            with rasterio.open(str(SAMPLE)) as ds:
                crs = crs if crs is not None else ds.crs
                transform = transform if transform is not None else ds.transform
        out = shared_dir / name
        prof = dict(
            driver="GTiff",
            width=w,
            height=h,
            count=b,
            dtype="uint8",
            crs=crs,
            transform=transform,
        )
        if nodata is not None:
            prof["nodata"] = nodata
        with rasterio.open(str(out), "w", **prof) as ds:
            ds.write(array)
            if colorinterp:
                from rasterio.enums import ColorInterp

                ds.colorinterp = [ColorInterp[c] for c in colorinterp]
        return out

    return _make


@pytest.fixture(scope="session")
def encode(shared_dir):
    """encode(layout, delta=0, src=None, tag='', **kw) -> path to an .owlg file.

    Results are memoised for the whole session: the sample is small, but a dozen
    encodes still add up.
    """
    cache: dict = {}

    def _encode(layout, delta=0, src=None, tag="", **kw):
        assert layout in LAYOUTS
        src = pathlib.Path(src) if src is not None else SAMPLE
        if not src.is_file():
            pytest.skip(f"source raster missing: {src}")
        key = (str(src), layout, int(delta), tag, tuple(sorted(kw.items())))
        hit = cache.get(key)
        if hit is not None:
            return hit
        skip_unless_supported(layout, delta)
        pytest.importorskip("rasterio")
        dst = shared_dir / f"{src.stem}_{layout}_d{delta}{('_' + tag) if tag else ''}.owlg"
        if layout == "flat":
            from owlg.container import write_owlg

            write_owlg(str(src), str(dst), delta=delta, verbose=False, **kw)
        else:
            from owlg.tiled import write_tiled

            write_tiled(str(src), str(dst), delta=delta, verbose=False, **kw)
        assert dst.is_file() and dst.stat().st_size > 0
        cache[key] = dst
        return dst

    return _encode


@pytest.fixture(scope="session")
def decode_array():
    """decode_array(path, **kw) -> (B, H, W) ndarray decoded from an .owlg file."""

    def _decode(path, password=None, **kw):
        from owlg.container import read_owlg

        arr, _hdr = read_owlg(str(path), password, **kw)
        return arr

    return _decode


@pytest.fixture(scope="session")
def run_cli():
    """run_cli(args) -> (exit_code_or_None, stdout). SystemExit is captured."""
    import contextlib
    import io

    def _run(args):
        buf = io.StringIO()
        code = None
        from owlg import cli

        try:
            with contextlib.redirect_stdout(buf):
                cli.main([str(a) for a in args])
        except SystemExit as exc:
            code = exc.code
        return code, buf.getvalue()

    return _run
