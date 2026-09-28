# PyInstaller spec: MT5 Trading Workstation (one-folder, Windows x64)
# SPEC I2: the MetaTrader5 package must be bundled; the CI build job proves it
# with `MT5TradingWorkstation.exe --self-check`.
# mode: python ; coding: utf-8

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # repository root
sys.path.insert(0, str(ROOT))

from app.__version__ import __version__  # noqa: E402  (kept in sync by tests/test_version)


def _version_info(version: str) -> str:
    """VSVersionInfo text so Explorer/AV see a real product version."""
    parts = (version.split(".") + ["0", "0", "0"])[:4]
    file_ver = ".".join(parts)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({', '.join(parts)}, 0),
    prodvers=({', '.join(parts)}, 0),
    mask=0x3F, flags=0x0,
    OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'mohmdstag7-cmd'),
        StringStruct('FileDescription', 'MT5 Trading Workstation'),
        StringStruct('FileVersion', '{file_ver}'),
        StringStruct('ProductName', 'MT5 Trading Workstation'),
        StringStruct('ProductVersion', '{file_ver}'),
        StringStruct('OriginalFilename', 'MT5TradingWorkstation.exe'),
        StringStruct('LegalCopyright', 'Proprietary - all rights reserved'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ]
)"""


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
    version=_version_info(__version__),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
