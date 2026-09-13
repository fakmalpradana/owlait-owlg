#!/usr/bin/env python3
"""Build every OWLG release artefact from one source of truth: src/owlg.

  python3 scripts/build_all.py

Produces  dist/owlg_qgis-<v>.zip            QGIS plugin (decoder vendored inside)
          dist/owlg-<v>.tar.gz, dist/owlg-<v>-py3-none-any.whl
          dist/owlg-<v>.tgz                 npm tarball
"""
import os, sys, shutil, subprocess, zipfile, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'dist')

VERSION = re.search(r'__version__ = "([^"]+)"',
                    open(os.path.join(ROOT, 'src/owlg/__init__.py')).read()).group(1)

# Modules that must ship inside the plugin. Deliberately explicit, so that
# a new module that is forgotten here fails the build rather than failing in a
# user's plugin.
VENDOR = ['__init__.py', '__main__.py', '_aes.py', '_compat.py', 'cli.py',
          'codec.py', 'codec_py.py', 'container.py', 'crypto.py', 'errors.py',
          'ext.py', 'geotiff.py', 'imgio.py', 'ml.py', 'mosaic.py', 'rc.py',
          'rebase.py', 'server.py', 'tiering.py', 'tiled.py', 'tiled_read.py',
          'tiles.py', 'vrt.py']


def sync_vendor():
    src = os.path.join(ROOT, 'src/owlg')
    dst = os.path.join(ROOT, 'qgis-plugin/owlg_qgis/vendor/owlg')
    have = {f for f in os.listdir(src) if f.endswith('.py')}
    missing = have - set(VENDOR)
    if missing:
        sys.exit(f"these owlg modules are missing from VENDOR: {sorted(missing)}")
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(dst):
        if f.endswith('.py'): os.remove(os.path.join(dst, f))
    for f in VENDOR:
        shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
    shutil.rmtree(os.path.join(dst, '__pycache__'), ignore_errors=True)
    print(f"  vendor synced: {len(VENDOR)} modules")


def stage():
    """This repository is already the package layout, so there is nothing to stage."""
    print(f"  source: {ROOT}")


def zip_plugin():
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, f'owlg_qgis-{VERSION}.zip')
    base = os.path.join(ROOT, 'qgis-plugin')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, files in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for f in files:
                if f.endswith('.pyc'): continue
                fp = os.path.join(dirpath, f)
                z.write(fp, os.path.relpath(fp, base))
    print(f"  {out}  ({os.path.getsize(out)/1e3:.0f} kB)")
    return out


def build_pip():
    r = subprocess.run([sys.executable, '-m', 'build', '--outdir', DIST, ROOT],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  pip build failed (is the 'build' module installed?); skipping")
        print('  ' + r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '')
        return []
    made = [f for f in os.listdir(DIST) if VERSION in f and f.endswith(('.whl', '.tar.gz'))]
    for f in made: print(f"  dist/{f}  ({os.path.getsize(os.path.join(DIST,f))/1e3:.0f} kB)")
    return made


def pack_npm():
    d = os.path.join(ROOT, 'packages/owlg-js')
    r = subprocess.run(['npm', 'pack', '--pack-destination', DIST], cwd=d,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  npm pack failed, or npm is absent; skipping"); return None
    name = r.stdout.strip().splitlines()[-1]
    print(f"  dist/{name}  ({os.path.getsize(os.path.join(DIST,name))/1e3:.0f} kB)")
    return name


if __name__ == '__main__':
    print(f"OWLG {VERSION}")
    sync_vendor(); stage(); zip_plugin(); build_pip(); pack_npm()
    print("done.")
