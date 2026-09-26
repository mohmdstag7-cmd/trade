# PyInstaller spec: MT5 Trading Workstation (one-folder, Windows x64)
# SPEC I2: the MetaTrader5 package must be bundled; the CI build job proves it
# with `MT5TradingWorkstation.exe --self-check`.
# mode: python ; coding: utf-8

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # repository root

a = Analysis(
    [str(ROOT / "app" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # MetaTrader5 ships as compiled .pyd modules: its runtime
        # "import numpy" is invisible to PyInstaller static analysis, so
        # numpy must be forced into the bundle explicitly.
        "MetaTrader5",
        "numpy",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # icon provider (Phase 6); hooks-contrib collects its font assets
        "qtawesome",
        "qtpy",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "IPython",
        "pytest",
        "pytest_qt",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="MT5TradingWorkstation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # CLI flags (--self-check, --version) must print; see PR notes
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MT5TradingWorkstation",
)
