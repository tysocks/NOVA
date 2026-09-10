"""Installer-safe per-user paths (not under the project / Program Files tree)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def nova_user_data_dir() -> Path:
    """Per-user writable data root."""
    override = str(os.environ.get("NOVA_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "NOVA"
    xdg = str(os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "NOVA"
    return Path.home() / ".local" / "share" / "NOVA"


def nova_libraries_dir() -> Path:
    return nova_user_data_dir() / "libraries"


def ensure_user_data_dir() -> Path:
    root = nova_user_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_settings_file() -> Path:
    return nova_user_data_dir() / ".nova_user_settings.json"


def database_library_file() -> Path:
    return nova_user_data_dir() / ".nova_database_library.json"


def appearance_file() -> Path:
    return nova_user_data_dir() / ".nova_appearance.json"


def sources_workspace_file() -> Path:
    return nova_user_data_dir() / ".nova_sources_workspace.json"


def sessions_root() -> Path:
    return nova_user_data_dir() / ".nova_sessions"


def catalog_db_file() -> Path:
    return nova_user_data_dir() / ".nova_catalog.duckdb"


def parquet_root() -> Path:
    return nova_user_data_dir() / ".nova_parquet"


def profile_root() -> Path:
    return nova_user_data_dir() / ".nova_profile"


def backend_port_file() -> Path:
    return nova_user_data_dir() / ".nova_backend_port"
