# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for KlippiK Image Processor
# Run:  pyinstaller klippik_processor.spec
# Or:   double-click build_exe.bat on Windows
#
# Produces: dist/KlippiK_Image_Processor.exe  (~300–400 MB)
# The EXE bundles Python + Streamlit + all dependencies.

import os
from PyInstaller.utils.hooks import collect_data_files, collect_all

# Collect everything Streamlit needs (templates, static, vendor JS, etc.)
st_datas, st_binaries, st_hiddenimports = collect_all("streamlit")
alt_datas, alt_binaries, alt_hiddenimports = collect_all("altair")
pyd_datas, pyd_binaries, pyd_hiddenimports = collect_all("pydeck")

# Bundle app.py and .streamlit config alongside the launcher
extra_datas = [
    ("app.py",      "."),
    (".streamlit",  ".streamlit"),
]

a = Analysis(
    ["launcher_windows.py"],
    pathex=[],
    binaries=st_binaries + alt_binaries + pyd_binaries,
    datas=st_datas + alt_datas + pyd_datas + extra_datas,
    hiddenimports=(
        st_hiddenimports
        + alt_hiddenimports
        + pyd_hiddenimports
        + [
            "PIL",
            "PIL.Image",
            "PIL.ImageOps",
            "bs4",
            "requests",
            "pandas",
            "sqlite3",
            "supabase",
            "streamlit.web.cli",
            "streamlit.runtime.scriptrunner",
            "streamlit.components.v1",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",  # not needed — keeps file smaller
        "scipy",
        "sklearn",
        "torch",
        "tensorflow",
        "notebook",
        "IPython",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KlippiK_Image_Processor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,          # compress with UPX if available — reduces file size ~30%
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,      # keep console visible so users can see errors
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,         # add an .ico file path here if you have a KlippiK icon
)
