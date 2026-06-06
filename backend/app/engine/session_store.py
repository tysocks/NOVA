"""On-disk session artifacts for indexed file sources (Phase 3)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSIONS_ROOT = Path(__file__).resolve().parents[2] / ".nova_sessions"


def ensure_sessions_root() -> Path:
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    return SESSIONS_ROOT


def artifact_id_for_path(source_type: str, file_path: str) -> str:
    """Stable id for the same file path + type (re-ingest overwrites)."""
    key = f"{source_type}:{Path(file_path).resolve()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def sanitize_channel_filename(channel_name: str) -> str:
    """Map channel names to safe parquet filenames."""
    safe = re.sub(r"[^\w.\-]+", "_", channel_name.replace("/", "__"))
    return safe or "channel"


def artifact_dir(artifact_id: str) -> Path:
    return ensure_sessions_root() / artifact_id


def data_dir(artifact_id: str) -> Path:
    d = artifact_dir(artifact_id) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(artifact_id: str) -> Path:
    return artifact_dir(artifact_id) / "manifest.json"


def load_manifest(artifact_id: str) -> dict[str, Any] | None:
    path = manifest_path(artifact_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_manifest(artifact_id: str, manifest: dict[str, Any]) -> None:
    root = artifact_dir(artifact_id)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path(artifact_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def find_artifact_for_path(file_path: str) -> str | None:
    """Return artifact_id if a ready manifest exists for this file path."""
    resolved = str(Path(file_path).resolve())
    root = ensure_sessions_root()
    if not root.exists():
        return None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest = load_manifest(child.name)
        if not manifest:
            continue
        if manifest.get("status") != "ready":
            continue
        if str(Path(str(manifest.get("file_path", ""))).resolve()) == resolved:
            return child.name
    return None


def initial_manifest(
    *,
    artifact_id: str,
    source_type: str,
    file_path: str,
    units_in_headers: bool,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "status": "running",
        "source_type": source_type,
        "file_path": str(Path(file_path).resolve()),
        "run_code": Path(file_path).stem,
        "test_run_id": 1,
        "units_in_headers": units_in_headers,
        "time_bounds": None,
        "channels": [],
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
