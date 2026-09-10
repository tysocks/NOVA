"""User-configurable library locations.

Settings and library JSON files default to a per-user data directory so they
remain writable after an installer places the app in Program Files.
Each library is a JSON file inside a user-chosen directory. Legacy settings
that stored a `.json` file path still resolve to that file.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import BASE_DIR
from ..paths import (
    appearance_file,
    database_library_file,
    nova_libraries_dir,
    nova_user_data_dir,
    sources_workspace_file,
    user_settings_file,
)

# Re-export for callers / tests that historically imported these here.
__all__ = (
    "LIBRARY_KEYS",
    "DEFAULT_LIBRARY_FILENAMES",
    "USER_SETTINGS_FILE",
    "nova_libraries_dir",
    "nova_user_data_dir",
    "library_path",
    "resolved_library_paths",
    "set_library_paths",
    "migrate_legacy_runtime_files",
)

LIBRARY_KEYS = (
    "configurations",
    "units",
    "range_definitions",
    "ingestion_rules",
    "limits",
)

DEFAULT_LIBRARY_FILENAMES = {
    "configurations": ".nova_config_library.json",
    "units": ".nova_unit_library.json",
    "range_definitions": ".nova_range_definition_library.json",
    "ingestion_rules": ".nova_ingestion_library.json",
    "limits": ".nova_limit_library.json",
}

# Monkeypatched by tests; production resolves under %LOCALAPPDATA%/NOVA (or XDG).
USER_SETTINGS_FILE = user_settings_file()

_fallback_getters: dict[str, Callable[[], Path]] = {}


def register_library_fallback(kind: str, getter: Callable[[], Path]) -> None:
    if kind not in DEFAULT_LIBRARY_FILENAMES:
        raise KeyError(kind)
    _fallback_getters[kind] = getter


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except (ValueError, OSError):
        return False


def _copy_if_missing(source: Path, dest: Path) -> None:
    if dest.exists() or not source.exists():
        return
    try:
        if source.resolve() == dest.expanduser().resolve():
            return
    except OSError:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())


def _legacy_backend_candidates(filename: str) -> list[Path]:
    """Historical locations under the project backend/ tree."""
    return [
        BASE_DIR / filename,
        BASE_DIR / "app" / filename,
    ]


def _maybe_migrate_legacy_library(kind: str, dest: Path) -> None:
    if dest.exists() or not _is_under(dest, nova_user_data_dir()):
        return
    for legacy in _legacy_backend_candidates(DEFAULT_LIBRARY_FILENAMES[kind]):
        if legacy.exists():
            _copy_if_missing(legacy, dest)
            return


def _maybe_migrate_legacy_settings() -> None:
    if USER_SETTINGS_FILE.exists() or not _is_under(USER_SETTINGS_FILE, nova_user_data_dir()):
        return
    for legacy in _legacy_backend_candidates(".nova_user_settings.json"):
        if legacy.exists():
            _copy_if_missing(legacy, USER_SETTINGS_FILE)
            return


def migrate_legacy_runtime_files() -> None:
    """Copy leftover backend/ user files into the per-user data dir once."""
    _maybe_migrate_legacy_settings()
    for kind in LIBRARY_KEYS:
        default_library_path(kind)
    pairs = [
        (_legacy_backend_candidates(".nova_database_library.json"), database_library_file()),
        (_legacy_backend_candidates(".nova_appearance.json"), appearance_file()),
        (_legacy_backend_candidates(".nova_sources_workspace.json"), sources_workspace_file()),
    ]
    for sources, dest in pairs:
        if dest.exists():
            continue
        for source in sources:
            if source.exists():
                _copy_if_missing(source, dest)
                break


def default_library_path(kind: str) -> Path:
    getter = _fallback_getters.get(kind)
    if getter is not None:
        dest = Path(getter())
    else:
        dest = nova_libraries_dir() / DEFAULT_LIBRARY_FILENAMES[kind]
    _maybe_migrate_legacy_library(kind, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def load_user_settings() -> dict[str, Any]:
    _maybe_migrate_legacy_settings()
    if not USER_SETTINGS_FILE.exists():
        return {"libraries": {}}
    try:
        payload = json.loads(USER_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"libraries": {}}
    if not isinstance(payload, dict):
        return {"libraries": {}}
    libs = payload.get("libraries")
    if not isinstance(libs, dict):
        payload["libraries"] = {}
    return payload


def save_user_settings(payload: dict[str, Any]) -> None:
    USER_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _library_file_from_configured(kind: str, raw: Path) -> Path:
    path = Path(raw).expanduser()
    filename = DEFAULT_LIBRARY_FILENAMES[kind]
    if path.suffix.lower() == ".json":
        return path
    return path / filename


def configured_library_path(kind: str) -> Path | None:
    if kind not in DEFAULT_LIBRARY_FILENAMES:
        raise KeyError(kind)
    libs = load_user_settings().get("libraries") or {}
    raw = str(libs.get(kind) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def library_path(kind: str) -> Path:
    custom = configured_library_path(kind)
    if custom is not None:
        return _library_file_from_configured(kind, custom)
    return default_library_path(kind)


def resolved_library_paths() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind in LIBRARY_KEYS:
        path = library_path(kind)
        out[kind] = {
            "path": str(path),
            "directory": str(path.parent),
            "exists": path.exists(),
            "default": str(default_library_path(kind).parent),
        }
    return out


def set_library_paths(updates: dict[str, str], *, copy_existing: bool = True) -> dict[str, dict[str, Any]]:
    payload = load_user_settings()
    libs = dict(payload.get("libraries") or {})
    pending_copies: list[tuple[Path, Path]] = []
    for kind, raw in (updates or {}).items():
        if kind not in DEFAULT_LIBRARY_FILENAMES:
            raise ValueError(f"Unknown library: {kind}")
        text = str(raw or "").strip()
        if not text:
            libs.pop(kind, None)
            continue
        dest = Path(text).expanduser()
        dest_file = _library_file_from_configured(kind, dest).resolve()
        dest_dir = dest_file.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        previous = library_path(kind)
        libs[kind] = str(dest_file) if dest.suffix.lower() == ".json" else str(dest_dir)
        if (
            copy_existing
            and not dest_file.exists()
            and previous.exists()
            and previous.resolve() != dest_file
        ):
            pending_copies.append((previous, dest_file))
    payload["libraries"] = libs
    save_user_settings(payload)
    for source, dest_file in pending_copies:
        try:
            if source.exists() and not dest_file.exists():
                dest_file.write_bytes(source.read_bytes())
        except OSError:
            pass
    return resolved_library_paths()


def clean_limit_library_rows(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        item = dict(row)
        ident = str(item.get("id") or "").strip() or None
        if not ident:
            ident = str(uuid.uuid4())
        if ident in seen:
            continue
        seen.add(ident)
        item["id"] = ident
        item["name"] = name
        out.append(item)
    return out
