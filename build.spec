# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_all

# The console variant is used by the maintainer to diagnose packaged builds.
CONSOLE = os.environ.get("JM_BUILD_CONSOLE") == "1"
APP_NAME = "JMComicDebug" if CONSOLE else "JMComicDownloader"

datas = [
    ("assets/icon.png", "assets"),
    ("assets/icon.ico", "assets"),
]
binaries = []
hiddenimports = ["PIL.ImageTk"]

# jmcomic loads a number of plugins and client implementations dynamically;
# curl_cffi ships its own libcurl DLLs next to the package.
for package in ("jmcomic", "commonx", "curl_cffi", "img2pdf", "pikepdf"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy",
        "pandas",
        "matplotlib",
        "scipy",
        "IPython",
        "pytest",
        "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
