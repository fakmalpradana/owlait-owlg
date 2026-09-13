"""Compatibility layer: run without numba (e.g. inside QGIS) by supplying a
no-op decorator. The results are identical, only slower."""
HAVE_NUMBA = True
try:
    from numba import njit as _njit
    def njit(*a, **k):
        k.pop('inline', None) if False else None
        return _njit(*a, **k)
except Exception:                                   # pragma: no cover
    HAVE_NUMBA = False
    def njit(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        def deco(f): return f
        return deco
