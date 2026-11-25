# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller --onedir build specification for SocialBidAI."""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "frontend" / "ui_dist"), "frontend/ui_dist"),
    (str(ROOT / "defaults"), "defaults"),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# 핵심 로컬 라이브러리 전체 수집 (sqlite-vec vec0.dll/dylib 포함)
for pkg in ("sqlite_vec", "hwpx", "pdfplumber", "pypdfium2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Windows Edge WebView2 / pythonnet / clr_loader 동적 .NET DLL 누락 방지
if sys.platform == "win32":
    for pkg in ("webview", "pythonnet", "clr_loader"):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
else:
    d, b, h = collect_all("webview")
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("app")

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide6",
        "wx",
        "matplotlib",
        "scipy",
        "torch",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SocialBidAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=["Python.Runtime.dll", "*.pyd", "*.dll"],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=["Python.Runtime.dll", "*.pyd", "*.dll"],
    name="SocialBidAI",
)
