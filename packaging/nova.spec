# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for NOVA desktop (Windows onedir)."""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPECPATH = Path(SPECPATH)
REPO_ROOT = SPECPATH.parent
BACKEND = REPO_ROOT / "backend"
ASSETS = REPO_ROOT / "assets"

sys.path.insert(0, str(BACKEND))

datas: list = []
binaries: list = []
hiddenimports: list = []

for pkg in (
    "PySide6",
    "uvicorn",
    "fastapi",
    "starlette",
    "duckdb",
    "polars",
    "pyarrow",
    "h5py",
    "nptdms",
    "pandas",
    "numpy",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        try:
            hiddenimports += collect_submodules(pkg)
        except Exception:
            pass

# Static UI + icon (ship beside the frozen app / inside _internal)
datas += [(str(BACKEND / "app" / "static"), "app/static")]
datas += [(str(ASSETS / "NOVA_ICON.png"), "assets")]
ico = ASSETS / "NOVA_ICON.ico"
if ico.exists():
    datas += [(str(ico), "assets")]

hiddenimports += [
    "app",
    "app.main",
    "app.cli",
    "app.config",
    "app.paths",
    "app.models",
    "app.db",
    "app.services.native_dialogs",
    "app.services.user_settings",
    "app.services.database_library",
    "app.services.config_library",
    "app.services.unit_library",
    "app.services.file_sources",
    "app.services.ingestion_rule_library",
    "app.services.range_definition_library",
    "app.engine.catalog_store",
    "app.engine.session_store",
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
    "pydantic_settings",
    "multipart",
    "anyio._backends._asyncio",
]

a = Analysis(
    [str(BACKEND / "desktop_app.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NOVA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ico) if ico.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="NOVA",
)
