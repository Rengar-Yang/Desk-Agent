# -*- mode: python ; coding: utf-8 -*-
"""Build a windowed Orbit folder. Run this spec on 64-bit Windows."""

from pathlib import Path

root = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(root / "native_app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "config.toml"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Orbit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=str(Path(SPECPATH) / "orbit.exe.manifest"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Orbit",
)
