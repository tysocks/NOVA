"""Database Manager library: project DuckDB catalogs + optional Postgres profiles.

The system local catalog (temporary file-open cache) is NOT a user-visible profile.
It lives at DEFAULT_CATALOG_DB_PATH and is only used for temporary ingest.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..config import settings
from ..engine.catalog_store import (
    DEFAULT_CATALOG_DB_PATH,
    connect_catalog,
    get_catalog_db_path,
    list_catalog_tests,
    set_catalog_db_path,
)

DATABASE_LIBRARY_FILE = Path(__file__).resolve().parents[1] / ".nova_database_library.json"
LOCAL_CATALOG_ID = "local"


def default_duckdb_catalog_path() -> Path:
    return Path(DEFAULT_CATALOG_DB_PATH)


def default_parquet_root_for(catalog_path: str | Path) -> str:
    root = Path(catalog_path).expanduser().resolve().parent / "parquet"
    return str(root)


def local_catalog_profile() -> dict:
    """Hidden system catalog used only for temporary file-open caching.

    Uses the process ``CATALOG_DB_PATH`` so tests can redirect the local DB;
    production keeps that path at ``DEFAULT_CATALOG_DB_PATH``.
    """
    from ..engine.catalog_store import CATALOG_DB_PATH

    return {
        "id": LOCAL_CATALOG_ID,
        "type": "duckdb",
        "name": "Local Catalog",
        "catalog_path": str(Path(CATALOG_DB_PATH)),
        "parquet_root": str(Path(settings.parquet_root)),
        "default_ingest_mode": "temporary",
        "is_default": False,
        "role": "local",
        "hidden": True,
    }


def is_local_catalog_profile(row: dict | None) -> bool:
    if not isinstance(row, dict):
        return False
    if str(row.get("role") or "").lower() == "local":
        return True
    if str(row.get("id") or "") == LOCAL_CATALOG_ID:
        return True
    if bool(row.get("hidden")) and str(row.get("name") or "").strip().lower() == "local catalog":
        return True
    try:
        path = Path(str(row.get("catalog_path") or "")).expanduser().resolve()
        if path == default_duckdb_catalog_path().resolve():
            # Treat the default path as local only when marked temporary/hidden/legacy seed.
            mode = str(row.get("default_ingest_mode") or "").lower()
            name = str(row.get("name") or "").strip().lower()
            if mode == "temporary" or name == "local catalog" or bool(row.get("hidden")):
                return True
    except Exception:
        pass
    return False


def clean_tags(value: Any) -> list[str]:
    """Normalize profile tags to a de-duplicated list of non-empty strings."""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item or "").strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def clean_updated_at(value: Any) -> str:
    """Preserve ISO-ish updated_at strings; empty when missing/invalid."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Accept common ISO-8601 forms (with/without Z, fractional seconds).
    if len(raw) < 10 or raw[4] != "-" or raw[7] != "-":
        return ""
    return raw


def clean_duckdb_row(row: dict) -> dict:
    """User/project DuckDB catalogs are always permanent and visible."""
    catalog_path = str(row.get("catalog_path") or "").strip()
    if not catalog_path:
        raise ValueError("catalog_path is required for DuckDB catalogs")
    catalog_path_obj = Path(catalog_path).expanduser()
    if catalog_path_obj.exists() and catalog_path_obj.is_dir():
        raise ValueError("catalog_path must be a DuckDB file, not a directory")
    parquet_root = str(row.get("parquet_root") or "").strip() or default_parquet_root_for(catalog_path)
    return {
        "id": str(row.get("id") or uuid.uuid4()),
        "type": "duckdb",
        "name": str(row.get("name") or "DuckDB Catalog").strip() or "DuckDB Catalog",
        "catalog_path": str(catalog_path_obj),
        "parquet_root": str(Path(parquet_root).expanduser()),
        "default_ingest_mode": "permanent",
        "is_default": bool(row.get("is_default")),
        "role": "project",
        "hidden": False,
        "tags": clean_tags(row.get("tags")),
        "updated_at": clean_updated_at(row.get("updated_at")),
    }


def clean_postgres_row(row: dict) -> dict:
    return {
        "id": str(row.get("id") or uuid.uuid4()),
        "type": "postgres",
        "name": str(row.get("name") or "PostgreSQL Database"),
        "host": str(row.get("host") or "localhost"),
        "port": int(row.get("port") or 5432),
        "user": str(row.get("user") or "pipeline"),
        "password": str(row.get("password") or ""),
        "sslmode": str(row.get("sslmode") or "disable"),
        "is_default": bool(row.get("is_default")),
        "role": "postgres",
        "hidden": False,
        "tags": clean_tags(row.get("tags")),
        "updated_at": clean_updated_at(row.get("updated_at")),
    }


def clean_library_row(row: dict) -> dict | None:
    if not isinstance(row, dict):
        return None
    if is_local_catalog_profile(row):
        # Never persist the system local catalog in the user library.
        return None
    kind = str(row.get("type") or "duckdb").strip().lower()
    if kind in {"duckdb", "catalog"}:
        try:
            return clean_duckdb_row(row)
        except ValueError:
            return None
    if kind == "postgres":
        return clean_postgres_row(row)
    return None


def clean_library_rows(rows: list) -> list[dict]:
    cleaned: list[dict] = []
    for row in rows:
        item = clean_library_row(row)
        if item:
            cleaned.append(item)
    duck = [r for r in cleaned if r["type"] == "duckdb"]
    if duck and not any(r.get("is_default") for r in duck):
        duck[0]["is_default"] = True
    defaults = [r for r in duck if r.get("is_default")]
    if len(defaults) > 1:
        keep = defaults[0]["id"]
        for r in cleaned:
            if r["type"] == "duckdb":
                r["is_default"] = r["id"] == keep
    return cleaned


def find_duplicate_database_name(rows: list[dict]) -> str | None:
    """Return the first duplicate profile name (case-insensitive), if any."""
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            return name
        seen.add(key)
    return None


def _seed_library_rows() -> list[dict]:
    """User library starts empty (local catalog is system-managed). Optionally seed Postgres."""
    rows: list[dict] = []
    if settings.enable_postgres:
        rows.append(
            clean_postgres_row(
                {
                    "name": "RedScale",
                    "host": settings.redscale_host,
                    "port": settings.redscale_port,
                    "user": settings.redscale_user,
                    "password": settings.redscale_password,
                    "sslmode": settings.redscale_sslmode,
                }
            )
        )
        bluescale = clean_postgres_row(
            {
                "name": "BlueScale",
                "host": settings.bluescale_host,
                "port": settings.bluescale_port,
                "user": settings.bluescale_user,
                "password": settings.bluescale_password,
                "sslmode": settings.bluescale_sslmode,
            }
        )
        if not any(
            p["type"] == "postgres"
            and p["host"] == bluescale["host"]
            and p["port"] == bluescale["port"]
            and p["user"] == bluescale["user"]
            for p in rows
        ):
            rows.append(bluescale)
    return rows


def load_library_payload() -> dict[str, Any]:
    if not DATABASE_LIBRARY_FILE.exists():
        rows = _seed_library_rows()
        payload = {"databases": rows, "active_catalog_id": None}
        write_library_payload(payload)
        return payload
    try:
        payload = json.loads(DATABASE_LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    rows = payload.get("databases")
    if not isinstance(rows, list):
        rows = []
    cleaned = clean_library_rows(rows)
    # Migrate away from legacy seeded Local Catalog entries.
    if cleaned != rows:
        write_library_payload({"databases": cleaned, "active_catalog_id": payload.get("active_catalog_id")})
        payload = load_library_payload()
        return payload
    active = payload.get("active_catalog_id")
    if active == LOCAL_CATALOG_ID or not active or not any(
        r["id"] == active and r["type"] == "duckdb" for r in cleaned
    ):
        default = next((r for r in cleaned if r["type"] == "duckdb" and r.get("is_default")), None)
        active = default["id"] if default else next((r["id"] for r in cleaned if r["type"] == "duckdb"), None)
    return {"databases": cleaned, "active_catalog_id": active}


def write_library_payload(payload: dict[str, Any]) -> None:
    rows = clean_library_rows(payload.get("databases") or [])
    active = payload.get("active_catalog_id")
    if active == LOCAL_CATALOG_ID:
        active = None
    if not active or not any(r["id"] == active and r["type"] == "duckdb" for r in rows):
        default = next((r for r in rows if r["type"] == "duckdb" and r.get("is_default")), None)
        active = default["id"] if default else next((r["id"] for r in rows if r["type"] == "duckdb"), None)
    DATABASE_LIBRARY_FILE.write_text(
        json.dumps({"databases": rows, "active_catalog_id": active}, indent=2),
        encoding="utf-8",
    )


def get_profile(catalog_id: str | None) -> dict | None:
    if catalog_id and str(catalog_id) == LOCAL_CATALOG_ID:
        return local_catalog_profile()
    payload = load_library_payload()
    rows = payload.get("databases") or []
    if catalog_id:
        for row in rows:
            if str(row.get("id")) == str(catalog_id):
                return row
        return None
    active = payload.get("active_catalog_id")
    if active:
        for row in rows:
            if str(row.get("id")) == str(active):
                return row
    return None


def resolve_duckdb_profile(catalog_id: str | None = None) -> dict:
    """Resolve a DuckDB profile.

    - catalog_id None / 'local' → system local (temporary) catalog
    - otherwise → user project catalog (permanent)
    """
    if catalog_id is None or str(catalog_id) == LOCAL_CATALOG_ID:
        return local_catalog_profile()

    profile = get_profile(catalog_id)
    if profile and profile.get("type") == "duckdb":
        if is_local_catalog_profile(profile):
            return local_catalog_profile()
        return profile
    raise ValueError(f"DuckDB catalog profile '{catalog_id}' not found.")


def apply_active_catalog_from_library() -> Path:
    """Process default catalog path is always the system local catalog."""
    return set_catalog_db_path(local_catalog_profile()["catalog_path"])


def test_duckdb_profile(profile: dict) -> dict:
    path = Path(str(profile.get("catalog_path") or "")).expanduser()
    if not str(path):
        return {"ok": False, "error": "catalog_path is required"}
    if path.exists() and path.is_dir():
        return {
            "ok": False,
            "error": f"catalog_path must be a DuckDB file, not a directory: {path}",
        }
    try:
        con = connect_catalog(path)
        try:
            tests = con.execute("SELECT COUNT(*) FROM tests").fetchone()
            channels = con.execute("SELECT COUNT(*) FROM channels").fetchone()
        finally:
            con.close()
        return {
            "ok": True,
            "catalog_path": str(path.resolve()),
            "test_count": int(tests[0] if tests else 0),
            "channel_count": int(channels[0] if channels else 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_tests_for_profile(catalog_id: str | None = None, limit: int = 500) -> list:
    from ..engine.catalog_store import catalog_path_override

    profile = resolve_duckdb_profile(catalog_id)
    with catalog_path_override(profile["catalog_path"]):
        return list_catalog_tests(limit=limit)


def visible_library_rows(rows: list[dict] | None = None) -> list[dict]:
    payload_rows = rows if rows is not None else (load_library_payload().get("databases") or [])
    return [r for r in payload_rows if not is_local_catalog_profile(r)]
