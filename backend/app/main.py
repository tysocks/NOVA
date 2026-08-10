from pathlib import Path
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Iterator

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .engine.arrow_codec import arrow_ipc_to_points, _time_to_epoch_ms
from .engine.catalog_store import (
    catalog_path_override,
    create_range,
    create_range_rule,
    delete_catalog_test,
    delete_range,
    list_catalog_channels,
    list_catalog_tests,
    list_range_rules,
    list_ranges,
    list_results,
    list_test_parameters,
    update_catalog_test,
    update_range,
    write_result,
)
from .engine.range_detect import apply_range_rule_to_test
from .engine.range_store import (
    create_temp_range,
    delete_temp_range,
    ensure_source_range_from_manifest,
    list_temp_ranges,
    update_temp_range,
)
from .engine.file_index import get_ingest_status, manifest_to_channels, manifest_to_tests, run_ingest
from .engine.file_probe import probe_file_with_validation
from .engine.series_query import execute_series_query
from .models import (
    ApplyRangeRuleRequest,
    CatalogTestUpdateRequest,
    ChannelItem,
    DatabaseItem,
    FileIngestRequest,
    FileIngestResponse,
    FileProbeRequest,
    FileProbeResponse,
    HealthResponse,
    IngestWithRuleRequest,
    RangeCreateRequest,
    RangeDeleteRequest,
    RangeItem,
    RangeListRequest,
    RangeRuleCreateRequest,
    RangeRuleItem,
    RangeSourceRef,
    RangeUpdateRequest,
    ResultItem,
    ResultWriteRequest,
    SeriesQueryRequest,
    TestParameterItem,
    TestRunItem,
    TimeSeriesEnvelope,
    TimeSeriesPoint,
)
from .services.timeseries import (
    get_timeseries_envelope,
    get_timeseries,
    list_channels,
    list_channels_for_tests,
    list_databases,
    list_test_metadata,
    list_test_tables,
    list_tests,
)
from .services.file_sources import detect_source_type, file_channels, file_tests, file_timeseries
from .services.unit_library import clean_unit_library_rows, default_unit_library_rows
from .services.config_library import clean_config_library_rows
from .services.range_definition_library import clean_range_definition_rows
from .services.ingestion_rule_library import clean_ingestion_rule, clean_ingestion_rule_rows
from .services.query_router import resolve_overlay_targets
from .services import database_library as db_library
from .config import settings


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    """Clear temporary file-open cache between app instances; pin process catalog to local."""
    from .engine.session_store import clear_local_catalog_db, clear_temporary_sessions

    try:
        clear_temporary_sessions()
        clear_local_catalog_db()
    except Exception:
        pass
    try:
        db_library.apply_active_catalog_from_library()
    except Exception:
        pass
    yield


app = FastAPI(
    title="NOVA API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=_app_lifespan,
)

_LEGACY_TS_HEADERS = {
    "Deprecation": "true",
    "X-NOVA-Deprecated": "Use POST /api/v3/series/query",
    "Link": '</api/v3/series/query>; rel="successor-version"',
}


def _mark_legacy_timeseries(response: Response) -> None:
    for key, value in _LEGACY_TS_HEADERS.items():
        response.headers[key] = value


STATIC_DIR = Path(__file__).resolve().parent / "static"
APPEARANCE_FILE = Path(__file__).resolve().parents[1] / ".nova_appearance.json"
# Kept for test monkeypatch compatibility; library IO uses db_library.DATABASE_LIBRARY_FILE.
DATABASE_LIBRARY_FILE = db_library.DATABASE_LIBRARY_FILE
UNIT_LIBRARY_FILE = Path(__file__).resolve().parents[1] / ".nova_unit_library.json"
CONFIG_LIBRARY_FILE = Path(__file__).resolve().parents[1] / ".nova_config_library.json"
RANGE_DEFINITION_LIBRARY_FILE = Path(__file__).resolve().parents[1] / ".nova_range_definition_library.json"
INGESTION_LIBRARY_FILE = Path(__file__).resolve().parents[1] / ".nova_ingestion_library.json"
SOURCES_WORKSPACE_FILE = Path(__file__).resolve().parents[1] / ".nova_sources_workspace.json"


@contextmanager
def _with_catalog(catalog_id: str | None) -> Iterator[dict]:
    try:
        profile = db_library.resolve_duckdb_profile(catalog_id)
        with catalog_path_override(profile["catalog_path"]):
            yield profile
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


try:
    db_library.apply_active_catalog_from_library()
except Exception:
    pass


@app.get("/", include_in_schema=False)
def nova_app() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/js/{file_path:path}", include_in_schema=False)
def static_js(file_path: str) -> FileResponse:
    target = (STATIC_DIR / "js" / file_path).resolve()
    root = (STATIC_DIR / "js").resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True, app="NOVA")


@app.get("/api/database-library")
def get_database_library() -> dict:
    # Keep test monkeypatch of app.main.DATABASE_LIBRARY_FILE working.
    db_library.DATABASE_LIBRARY_FILE = DATABASE_LIBRARY_FILE
    payload = db_library.load_library_payload()
    try:
        db_library.apply_active_catalog_from_library()
    except Exception:
        pass
    return payload


@app.post("/api/database-library")
def save_database_library(payload: dict = Body(...)) -> dict:
    db_library.DATABASE_LIBRARY_FILE = DATABASE_LIBRARY_FILE
    rows = payload.get("databases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "databases must be a list"}
    active = payload.get("active_catalog_id") if isinstance(payload, dict) else None
    cleaned = db_library.clean_library_rows(rows)
    duplicate = db_library.find_duplicate_database_name(cleaned)
    if duplicate:
        return {"ok": False, "error": f"A database named '{duplicate}' already exists."}
    try:
        db_library.write_library_payload({"databases": cleaned, "active_catalog_id": active})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        db_library.apply_active_catalog_from_library()
    except Exception:
        pass
    saved = db_library.load_library_payload()
    return {"ok": True, "count": len(saved.get("databases") or []), "active_catalog_id": saved.get("active_catalog_id")}


@app.post("/api/database-library/active")
def set_active_catalog(payload: dict = Body(...)) -> dict:
    db_library.DATABASE_LIBRARY_FILE = DATABASE_LIBRARY_FILE
    catalog_id = str((payload or {}).get("catalog_id") or "").strip()
    if not catalog_id:
        return {"ok": False, "error": "catalog_id is required"}
    lib = db_library.load_library_payload()
    rows = lib.get("databases") or []
    match = next((r for r in rows if str(r.get("id")) == catalog_id and r.get("type") == "duckdb"), None)
    if not match:
        return {"ok": False, "error": f"DuckDB catalog '{catalog_id}' not found"}
    for row in rows:
        if row.get("type") == "duckdb":
            row["is_default"] = str(row.get("id")) == catalog_id
    db_library.write_library_payload({"databases": rows, "active_catalog_id": catalog_id})
    path = db_library.apply_active_catalog_from_library()
    return {"ok": True, "active_catalog_id": catalog_id, "catalog_path": str(path)}


@app.post("/api/database-library/test")
def test_database_library_connection(payload: dict = Body(...)) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "request body must be a JSON object"}
    kind = str(payload.get("type") or "duckdb").strip().lower()
    if kind in {"duckdb", "catalog"}:
        profile = db_library.clean_duckdb_row(payload)
        return db_library.test_duckdb_profile(profile)

    if not settings.enable_postgres:
        return {"ok": False, "error": "PostgreSQL is disabled. Set NOVA_ENABLE_POSTGRES=1 or use a DuckDB catalog."}
    host_raw = payload.get("host")
    user_raw = payload.get("user")
    if host_raw is None or not str(host_raw).strip():
        return {"ok": False, "error": "host is required"}
    if user_raw is None or not str(user_raw).strip():
        return {"ok": False, "error": "user is required"}
    host = str(host_raw).strip()
    user = str(user_raw).strip()
    try:
        port = int(payload.get("port") or 5432)
    except (TypeError, ValueError):
        return {"ok": False, "error": "port must be an integer"}
    if port <= 0:
        return {"ok": False, "error": "port must be positive"}
    password = str(payload.get("password") or "")
    sslmode = str(payload.get("sslmode") or "disable").strip() or "disable"
    try:
        databases = list_databases(
            db_host=host,
            db_port=port,
            db_user=user,
            db_password=password,
            db_sslmode=sslmode,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "database_count": len(databases)}


@app.get("/api/sources-workspace")
def get_sources_workspace() -> dict:
    if not SOURCES_WORKSPACE_FILE.exists():
        return {}
    try:
        data = json.loads(SOURCES_WORKSPACE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.post("/api/sources-workspace")
def save_sources_workspace(payload: dict = Body(...)) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "request body must be a JSON object"}
    sources = payload.get("sources")
    if isinstance(sources, list):
        cleaned_sources = []
        for row in sources:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item.pop("probe_snapshot", None)
            cleaned_sources.append(item)
        payload = {**payload, "sources": cleaned_sources}
    SOURCES_WORKSPACE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True}


def _load_config_library_rows() -> list[dict]:
    if not CONFIG_LIBRARY_FILE.exists():
        return []
    try:
        payload = json.loads(CONFIG_LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    try:
        return clean_config_library_rows(rows)
    except Exception:
        return []


def _write_config_library_rows(rows: list[dict]) -> None:
    CONFIG_LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_LIBRARY_FILE.write_text(
        json.dumps({"configs": rows}, indent=2),
        encoding="utf-8",
    )


@app.get("/api/config-library")
def get_config_library() -> dict:
    return {"configs": _load_config_library_rows()}


@app.post("/api/config-library")
def save_config_library(payload: dict = Body(...)) -> dict:
    rows = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "configs must be a list"}
    try:
        cleaned = clean_config_library_rows(rows)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    _write_config_library_rows(cleaned)
    return {"ok": True, "count": len(cleaned)}


def _load_json_library_rows(path: Path, key: str) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get(key) if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def _write_json_library_rows(path: Path, key: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: rows}, indent=2), encoding="utf-8")


@app.get("/api/range-definition-library")
def get_range_definition_library() -> dict:
    rows = _load_json_library_rows(RANGE_DEFINITION_LIBRARY_FILE, "definitions")
    try:
        return {"definitions": clean_range_definition_rows(rows)}
    except Exception:
        return {"definitions": []}


@app.post("/api/range-definition-library")
def save_range_definition_library(payload: dict = Body(...)) -> dict:
    rows = payload.get("definitions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "definitions must be a list"}
    try:
        cleaned = clean_range_definition_rows(rows)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    _write_json_library_rows(RANGE_DEFINITION_LIBRARY_FILE, "definitions", cleaned)
    return {"ok": True, "count": len(cleaned)}


@app.get("/api/ingestion-library")
def get_ingestion_library() -> dict:
    rows = _load_json_library_rows(INGESTION_LIBRARY_FILE, "rules")
    try:
        return {"rules": clean_ingestion_rule_rows(rows)}
    except Exception:
        return {"rules": []}


@app.post("/api/ingestion-library")
def save_ingestion_library(payload: dict = Body(...)) -> dict:
    rows = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "rules must be a list"}
    try:
        cleaned = clean_ingestion_rule_rows(rows)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    _write_json_library_rows(INGESTION_LIBRARY_FILE, "rules", cleaned)
    return {"ok": True, "count": len(cleaned)}


def _require_postgres() -> None:
    if not settings.enable_postgres:
        raise HTTPException(
            status_code=410,
            detail="PostgreSQL sources are disabled. Use /api/catalog/* endpoints (set NOVA_ENABLE_POSTGRES=1 to re-enable).",
        )


@app.get("/api/databases", response_model=list[DatabaseItem])
def databases(
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[DatabaseItem]:
    _require_postgres()
    return list_databases(
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )


@app.get("/api/tests", response_model=list[TestRunItem])
def tests(
    limit: int | None = Query(default=None, ge=1, le=5000000),
    test_table: str | None = Query(default=None, description="Optional test table override (default: test_runs)."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
    catalog_id: str | None = Query(default=None),
) -> list[TestRunItem]:
    # Prefer DuckDB catalog unless explicit Postgres credentials/db are requested and enabled.
    if settings.enable_postgres and any([db_name, db_host, db_user, db_password, test_table]):
        return list_tests(
            limit=limit,
            test_table=test_table,
            db_name=db_name,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_sslmode=db_sslmode,
        )
    with _with_catalog(catalog_id):
        return list_catalog_tests(limit=limit or 500)


@app.get("/api/test-tables", response_model=list[str])
def test_tables(
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[str]:
    _require_postgres()
    return list_test_tables(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )


@app.get("/api/channels", response_model=list[ChannelItem])
def channels(
    limit: int | None = Query(default=None, ge=1, le=5000000),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
    catalog_id: str | None = Query(default=None),
) -> list[ChannelItem]:
    if settings.enable_postgres and any([db_name, db_host, db_user, db_password]):
        return list_channels(
            limit=limit,
            db_name=db_name,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_sslmode=db_sslmode,
        )
    with _with_catalog(catalog_id):
        out: list[ChannelItem] = []
        seen: set[str] = set()
        for test in list_catalog_tests(limit=min(limit or 500, 500)):
            for ch in list_catalog_channels(int(test.test_run_id)):
                key = ch.channel_name
                if key in seen:
                    continue
                seen.add(key)
                out.append(ch)
                if limit and len(out) >= limit:
                    return out
        return out


@app.get("/api/catalog/tests", response_model=list[TestRunItem])
def catalog_tests(
    limit: int | None = Query(default=500, ge=1, le=10000),
    catalog_id: str | None = Query(default=None),
) -> list[TestRunItem]:
    with _with_catalog(catalog_id):
        return list_catalog_tests(limit=limit or 500)


@app.get("/api/catalog/tests/{test_id}/channels", response_model=list[ChannelItem])
def catalog_test_channels(
    test_id: int,
    catalog_id: str | None = Query(default=None),
) -> list[ChannelItem]:
    with _with_catalog(catalog_id):
        return list_catalog_channels(test_id)


@app.get("/api/catalog/tests/{test_id}/parameters", response_model=list[TestParameterItem])
def catalog_test_parameters(
    test_id: int,
    catalog_id: str | None = Query(default=None),
) -> list[TestParameterItem]:
    with _with_catalog(catalog_id):
        return list_test_parameters(test_id)


@app.patch("/api/catalog/tests/{test_id}", response_model=TestRunItem)
def patch_catalog_test(test_id: int, body: CatalogTestUpdateRequest) -> TestRunItem:
    try:
        with _with_catalog(body.catalog_id):
            updated = update_catalog_test(
                test_id,
                run_code=body.run_code,
                icon=body.icon,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail=f"Test {test_id} not found")
    start = updated.get("start_time")
    if start is None:
        from datetime import datetime, timezone

        start = datetime.now(timezone.utc)
    return TestRunItem(
        test_run_id=int(updated["test_id"]),
        run_code=str(updated["run_code"]),
        start_time=start,
        end_time=updated.get("end_time"),
        duration_s=updated.get("duration_s"),
        t0_utc=None,
        icon=updated.get("icon"),
    )


@app.delete("/api/catalog/tests/{test_id}")
def remove_catalog_test(
    test_id: int,
    catalog_id: str | None = Query(default=None),
) -> dict:
    with _with_catalog(catalog_id):
        ok = delete_catalog_test(test_id, remove_parquet=True)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Test {test_id} not found")
    return {"ok": True, "test_id": test_id}


@app.get("/api/catalog/ranges", response_model=list[RangeItem])
def catalog_ranges(
    test_id: int = Query(..., ge=1),
    catalog_id: str | None = Query(default=None),
) -> list[RangeItem]:
    with _with_catalog(catalog_id):
        return list_ranges(test_id, catalog_id=catalog_id)


@app.post("/api/catalog/ranges", response_model=RangeItem)
def create_catalog_range(body: RangeCreateRequest) -> RangeItem:
    if body.test_id is None:
        raise HTTPException(status_code=400, detail="test_id is required.")
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time.")
    with _with_catalog(body.catalog_id):
        return create_range(body)


def _is_temporary_range_target(ref: RangeSourceRef | RangeCreateRequest | RangeUpdateRequest | RangeDeleteRequest) -> bool:
    durability = str(getattr(ref, "durability", None) or "").strip().lower()
    if durability == "temporary":
        return True
    if durability == "permanent":
        return False
    artifact_id = str(getattr(ref, "artifact_id", None) or "").strip()
    catalog_id = str(getattr(ref, "catalog_id", None) or "").strip()
    if artifact_id and (not catalog_id or catalog_id == db_library.LOCAL_CATALOG_ID):
        return True
    return False


def _list_ranges_for_source(ref: RangeSourceRef) -> list[RangeItem]:
    if _is_temporary_range_target(ref):
        artifact_id = str(ref.artifact_id or "").strip()
        if not artifact_id:
            return []
        items = list_temp_ranges(artifact_id, source_path=ref.file_path)
        return [
            item.model_copy(
                update={
                    "artifact_id": artifact_id,
                    "durability": "temporary",
                    "source_id": ref.source_id,
                    "source_name": ref.source_name,
                    "catalog_id": ref.catalog_id,
                    "test_id": ref.test_id,
                }
            )
            for item in items
        ]

    if ref.test_id is None:
        return []
    with _with_catalog(ref.catalog_id):
        items = list_ranges(int(ref.test_id), catalog_id=ref.catalog_id)
    return [
        item.model_copy(
            update={
                "catalog_id": ref.catalog_id,
                "durability": "permanent",
                "source_id": ref.source_id,
                "source_name": ref.source_name,
                "artifact_id": ref.artifact_id,
            }
        )
        for item in items
    ]


@app.post("/api/ranges/query", response_model=list[RangeItem])
def query_ranges(body: RangeListRequest) -> list[RangeItem]:
    out: list[RangeItem] = []
    seen_keys: set[tuple[int, str | None, int | None]] = set()
    for ref in body.sources or []:
        try:
            for item in _list_ranges_for_source(ref):
                key = (item.range_id, item.artifact_id, item.test_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                out.append(item)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    out.sort(key=lambda r: (r.start_ms or 0.0, r.range_id))
    return out


@app.post("/api/ranges/ensure-source", response_model=RangeItem | None)
def ensure_source_range(body: RangeSourceRef) -> RangeItem | None:
    """Ensure the locked full-span source range exists for an indexed temporary file source."""
    try:
        if not _is_temporary_range_target(body):
            return None
        artifact_id = str(body.artifact_id or "").strip()
        if not artifact_id:
            return None
        item = ensure_source_range_from_manifest(artifact_id, source_path=body.file_path)
        if item is None:
            return None
        return item.model_copy(
            update={
                "artifact_id": artifact_id,
                "durability": "temporary",
                "source_id": body.source_id,
                "source_name": body.source_name,
                "catalog_id": body.catalog_id,
                "test_id": body.test_id,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ranges", response_model=RangeItem)
def create_unified_range(body: RangeCreateRequest) -> RangeItem:
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time.")
    try:
        if _is_temporary_range_target(body):
            if not body.artifact_id:
                raise ValueError("artifact_id is required for temporary ranges.")
            item = create_temp_range(body)
        else:
            if body.test_id is None:
                raise ValueError("test_id is required for permanent ranges.")
            with _with_catalog(body.catalog_id):
                item = create_range(body)
        return item.model_copy(
            update={
                "source_id": body.source_id,
                "source_name": body.source_name,
                "catalog_id": body.catalog_id or item.catalog_id,
                "artifact_id": body.artifact_id or item.artifact_id,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/ranges/{range_id}", response_model=RangeItem)
def patch_unified_range(range_id: int, body: RangeUpdateRequest) -> RangeItem:
    try:
        if _is_temporary_range_target(body):
            if not body.artifact_id:
                raise ValueError("artifact_id is required for temporary ranges.")
            return update_temp_range(range_id, body)
        with _with_catalog(body.catalog_id):
            return update_range(range_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/ranges/{range_id}")
def delete_unified_range(
    range_id: int,
    body: RangeDeleteRequest = Body(default_factory=RangeDeleteRequest),
) -> dict:
    try:
        if _is_temporary_range_target(body):
            if not body.artifact_id:
                raise ValueError("artifact_id is required for temporary ranges.")
            ok = delete_temp_range(range_id, artifact_id=body.artifact_id, source_path=body.file_path)
        else:
            with _with_catalog(body.catalog_id):
                ok = delete_range(range_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Range not found.")
    return {"ok": True}


@app.get("/api/catalog/range-rules", response_model=list[RangeRuleItem])
def catalog_range_rules(
    catalog_id: str | None = Query(default=None),
) -> list[RangeRuleItem]:
    with _with_catalog(catalog_id):
        return list_range_rules()


@app.post("/api/catalog/range-rules", response_model=RangeRuleItem)
def create_catalog_range_rule(
    body: RangeRuleCreateRequest,
    catalog_id: str | None = Query(default=None),
) -> RangeRuleItem:
    with _with_catalog(catalog_id):
        return create_range_rule(body)


@app.post("/api/catalog/range-rules/apply", response_model=list[RangeItem])
def apply_catalog_range_rule(body: ApplyRangeRuleRequest) -> list[RangeItem]:
    try:
        with _with_catalog(body.catalog_id):
            return apply_range_rule_to_test(body.test_id, body.rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/catalog/results", response_model=list[ResultItem])
def catalog_results(
    test_id: int | None = Query(default=None, ge=1),
    analysis_name: str | None = Query(default=None),
    catalog_id: str | None = Query(default=None),
) -> list[ResultItem]:
    with _with_catalog(catalog_id):
        return list_results(test_id=test_id, analysis_name=analysis_name)


@app.post("/api/catalog/results", response_model=ResultItem)
def create_catalog_result(
    body: ResultWriteRequest,
    catalog_id: str | None = Query(default=None),
) -> ResultItem:
    if body.value_text is None and body.value_num is None:
        raise HTTPException(status_code=400, detail="One of value_text or value_num is required.")
    with _with_catalog(catalog_id):
        return write_result(body)


@app.get("/api/available-channels", response_model=list[ChannelItem])
def available_channels(
    test_run_ids: list[int] = Query(..., description="One or more selected test ids."),
    test_table: str | None = Query(default=None, description="Optional test table override (default: test_runs)."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[ChannelItem]:
    if settings.enable_postgres and any([db_name, db_host, db_user, db_password, test_table]):
        return list_channels_for_tests(
            test_run_ids=test_run_ids,
            test_table=test_table,
            db_name=db_name,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_sslmode=db_sslmode,
        )
    from .catalog_store import list_catalog_channels

    out: list[ChannelItem] = []
    seen: set[str] = set()
    for tid in test_run_ids:
        for ch in list_catalog_channels(int(tid)):
            if ch.channel_name in seen:
                continue
            seen.add(ch.channel_name)
            out.append(ch)
    return out


@app.get("/api/timeseries", response_model=list[TimeSeriesPoint])
def timeseries(
    response: Response,
    test_run_ids: list[int] = Query(..., description="One or more test_run_id values."),
    channel_names: list[str] = Query(..., description="One or more channel names."),
    start_time: str | None = Query(default=None, description="ISO timestamp inclusive lower bound."),
    end_time: str | None = Query(default=None, description="ISO timestamp inclusive upper bound."),
    limit: int | None = Query(default=None, ge=1, le=5000000),
    max_points: int | None = Query(default=None, ge=2, le=5000000, description="Max points per series (LTTB). Omit for full resolution."),
    test_table: str | None = Query(default=None, description="Optional test table override (default: test_runs)."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[TimeSeriesPoint]:
    _mark_legacy_timeseries(response)
    _require_postgres()
    return get_timeseries(
        test_run_ids=test_run_ids,
        channel_names=channel_names,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        max_points=max_points,
        test_table=test_table,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )


@app.get("/api/v2/timeseries", response_model=TimeSeriesEnvelope)
def timeseries_v2(
    response: Response,
    test_run_ids: list[int] = Query(..., description="One or more test_run_id values."),
    channel_names: list[str] = Query(..., description="One or more channel names."),
    start_time: str | None = Query(default=None, description="ISO timestamp inclusive lower bound."),
    end_time: str | None = Query(default=None, description="ISO timestamp inclusive upper bound."),
    source: str | None = Query(default="auto", description="Logical source selector: auto, redscale, bluescale, measured, simulation."),
    overlay_mode: str | None = Query(default="single", description="single or both (overlay)."),
    t0_mode: str | None = Query(default="absolute", description="absolute, first_index, or t0_relative."),
    resolution_px: int | None = Query(default=None, ge=1, le=100000, description="Viewport width in pixels for adaptive resolution."),
    aggregation_mode: str | None = Query(default="auto", description="auto, lttb, raw/none."),
    limit: int | None = Query(default=None, ge=1, le=5000000),
    max_points: int | None = Query(default=None, ge=2, le=5000000, description="Optional hard cap per series."),
    test_table: str | None = Query(default=None, description="Optional test table override (default: test_runs)."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> TimeSeriesEnvelope:
    _mark_legacy_timeseries(response)
    _require_postgres()
    targets = resolve_overlay_targets(source=source, overlay_mode=overlay_mode, db_name=db_name)
    combined_overview: list[TimeSeriesPoint] = []
    combined_meta = []
    detail_hint = None
    for src_label, target_db in targets:
        env = get_timeseries_envelope(
            test_run_ids=test_run_ids,
            channel_names=channel_names,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            max_points=max_points,
            resolution_px=resolution_px,
            aggregation_mode=aggregation_mode,
            t0_mode=t0_mode,
            test_table=test_table,
            db_name=target_db,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_sslmode=db_sslmode,
        )
        combined_overview.extend(env.overview)
        for m in env.series_meta:
            m.source = src_label
            m.database = target_db
            combined_meta.append(m)
        if detail_hint is None and env.detail_hint is not None:
            detail_hint = env.detail_hint
    combined_overview.sort(key=lambda p: p.time)
    return TimeSeriesEnvelope(
        overview=combined_overview,
        series_meta=combined_meta,
        detail_hint=detail_hint,
    )


@app.post("/api/v3/series/query", response_model=None)
def series_query_v3(
    request: SeriesQueryRequest,
    format: str = Query(
        default="arrow",
        description="arrow (IPC), series (columnar JSON), or json (row payload)",
    ),
):
    """
    Columnar series query (catalog/file sources; Postgres when enabled).

    Default: Apache Arrow IPC stream with X-NOVA-Series-Meta header.
    format=series: per-series x_ms/y arrays (preferred browser columnar path).
    format=json: TimeSeriesPoint rows for legacy UI helpers.
    """
    try:
        ipc_bytes, meta = execute_series_query(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fmt = format.strip().lower()
    if fmt == "json":
        points = arrow_ipc_to_points(ipc_bytes)
        return {
            "meta": meta.model_dump(mode="json"),
            "rows": [
                {**p.model_dump(mode="json"), "x_ms": _time_to_epoch_ms(p.time)}
                for p in points
            ],
        }
    if fmt == "series":
        from .engine.polars_series import frame_from_points, series_payload_from_frame

        points = arrow_ipc_to_points(ipc_bytes)
        return {
            "meta": meta.model_dump(mode="json"),
            "series": series_payload_from_frame(frame_from_points(points)),
        }

    return Response(
        content=ipc_bytes,
        media_type="application/vnd.apache.arrow.stream",
        headers={"X-NOVA-Series-Meta": meta.model_dump_json()},
    )


@app.post("/api/v3/file/probe", response_model=FileProbeResponse)
def probe_file_v3(body: FileProbeRequest) -> FileProbeResponse:
    """Inspect a data file for channels, time index options, and unit metadata."""
    try:
        return probe_file_with_validation(
            body.file_path,
            units_in_headers=body.units_in_headers,
            time_index_channel=body.time_index_channel,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v3/ingest/file", response_model=FileIngestResponse)
def ingest_file_v3(body: FileIngestRequest) -> FileIngestResponse:
    """Index CSV/H5/TDMS into session or permanent Parquet artifacts."""
    try:
        rule = None
        if body.ingestion_rule_id:
            rows = _load_json_library_rows(INGESTION_LIBRARY_FILE, "rules")
            cleaned = clean_ingestion_rule_rows(rows)
            rule = next((r for r in cleaned if str(r.get("id")) == str(body.ingestion_rule_id)), None)
            if rule is None:
                raise ValueError(f"Ingestion rule not found: {body.ingestion_rule_id}")
        manifest = run_ingest(
            body.source_type,
            body.file_path,
            units_in_headers=body.units_in_headers,
            time_index_channel=body.time_index_channel,
            ingest_mode=body.ingest_mode,
            parameters=body.parameters,
            apply_range_rule_ids=body.apply_range_rule_ids,
            catalog_id=body.catalog_id,
            channel_mode=body.channel_mode,
            channel_include=body.channel_include,
            channel_exclude=body.channel_exclude,
            channel_rename=body.channel_rename,
            channel_require=body.channel_require,
            calculated_channels=[c.model_dump() for c in (body.calculated_channels or [])],
            range_definition_ids=body.range_definition_ids,
            ingestion_rule=rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        from .engine.session_store import artifact_id_for_path

        artifact_id = artifact_id_for_path(body.source_type, body.file_path)
        failed = get_ingest_status(artifact_id)
        if failed:
            return FileIngestResponse(
                artifact_id=artifact_id,
                status="failed",
                error=str(exc),
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    applied = []
    for row in manifest.get("applied_ranges") or []:
        if isinstance(row, dict):
            try:
                applied.append(RangeItem(**row))
            except Exception:
                continue

    return FileIngestResponse(
        artifact_id=str(manifest["artifact_id"]),
        status=str(manifest.get("status", "ready")),
        run_code=manifest.get("run_code"),
        channels=list(manifest.get("channels") or []),
        time_bounds=manifest.get("time_bounds"),
        error=manifest.get("error"),
        test_id=int(manifest["test_run_id"]) if manifest.get("test_run_id") is not None else None,
        durability=str(manifest.get("durability") or "temporary"),
        applied_ranges=applied,
    )


@app.post("/api/v3/ingest/rule", response_model=FileIngestResponse)
def ingest_with_rule_v3(body: IngestWithRuleRequest) -> FileIngestResponse:
    """Permanently ingest a file using an ingestion rule (library id or inline rule)."""
    from .services.file_sources import detect_source_type

    try:
        rule = None
        if body.rule is not None:
            rule = clean_ingestion_rule(body.rule)
        elif body.rule_id:
            rows = _load_json_library_rows(INGESTION_LIBRARY_FILE, "rules")
            cleaned = clean_ingestion_rule_rows(rows)
            rule = next((r for r in cleaned if str(r.get("id")) == str(body.rule_id)), None)
            if rule is None:
                raise ValueError(f"Ingestion rule not found: {body.rule_id}")
        else:
            raise ValueError("Provide rule_id or rule")

        source_type = body.source_type or detect_source_type(body.file_path)
        if not source_type:
            raise ValueError("Could not detect source_type for file")

        manifest = run_ingest(
            source_type,
            body.file_path,
            ingest_mode="permanent",
            catalog_id=str(rule.get("target_catalog_id")),
            ingestion_rule=rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    applied = []
    for row in manifest.get("applied_ranges") or []:
        if isinstance(row, dict):
            try:
                applied.append(RangeItem(**row))
            except Exception:
                continue

    return FileIngestResponse(
        artifact_id=str(manifest["artifact_id"]),
        status=str(manifest.get("status", "ready")),
        run_code=manifest.get("run_code"),
        channels=list(manifest.get("channels") or []),
        time_bounds=manifest.get("time_bounds"),
        error=manifest.get("error"),
        test_id=int(manifest["test_run_id"]) if manifest.get("test_run_id") is not None else None,
        durability=str(manifest.get("durability") or "temporary"),
        applied_ranges=applied,
    )


@app.get("/api/v3/ingest/by-path", response_model=FileIngestResponse)
def ingest_lookup_by_path(
    file_path: str = Query(..., description="Absolute path to indexed file."),
    source_type: str = Query(..., description="csv, h5, tdms, parquet, or arrow"),
) -> FileIngestResponse:
    from .engine.session_store import find_artifact_for_path

    _ = source_type
    artifact_id = find_artifact_for_path(file_path)
    if not artifact_id:
        raise HTTPException(status_code=404, detail="No artifact for this file path.")
    manifest = get_ingest_status(artifact_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileIngestResponse(
        artifact_id=artifact_id,
        status=str(manifest.get("status", "unknown")),
        run_code=manifest.get("run_code"),
        channels=list(manifest.get("channels") or []),
        time_bounds=manifest.get("time_bounds"),
        error=manifest.get("error"),
    )


@app.get("/api/v3/ingest/{artifact_id}/status", response_model=FileIngestResponse)
def ingest_status_v3(artifact_id: str) -> FileIngestResponse:
    manifest = get_ingest_status(artifact_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileIngestResponse(
        artifact_id=artifact_id,
        status=str(manifest.get("status", "unknown")),
        run_code=manifest.get("run_code"),
        channels=list(manifest.get("channels") or []),
        time_bounds=manifest.get("time_bounds"),
        error=manifest.get("error"),
    )


@app.get("/api/v3/ingest/{artifact_id}/tests", response_model=list[TestRunItem])
def ingest_tests_v3(artifact_id: str) -> list[TestRunItem]:
    manifest = get_ingest_status(artifact_id)
    if not manifest or manifest.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Artifact not ready.")
    return manifest_to_tests(manifest)


@app.get("/api/v3/ingest/{artifact_id}/channels", response_model=list[ChannelItem])
def ingest_channels_v3(artifact_id: str) -> list[ChannelItem]:
    manifest = get_ingest_status(artifact_id)
    if not manifest or manifest.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Artifact not ready.")
    return manifest_to_channels(manifest)


@app.get("/api/metadata")
def metadata(
    test_run_ids: list[int] = Query(..., description="One or more test_run_id values."),
    test_table: str | None = Query(default=None, description="Optional test table override (default: test_runs)."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[dict]:
    if settings.enable_postgres and any([db_name, db_host, db_user, db_password, test_table]):
        return list_test_metadata(
            test_run_ids=test_run_ids,
            test_table=test_table,
            db_name=db_name,
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_sslmode=db_sslmode,
        )
    from .catalog_store import get_test_by_id, list_test_parameters

    out: list[dict] = []
    for tid in test_run_ids:
        test = get_test_by_id(int(tid))
        if not test:
            continue
        params = {p.key: (p.value_num if p.value_num is not None else p.value_text) for p in list_test_parameters(int(tid))}
        out.append(
            {
                "test_run_id": int(tid),
                "run_code": test.get("run_code"),
                "start_time": test.get("start_time"),
                "end_time": test.get("end_time"),
                "duration_s": test.get("duration_s"),
                "parameters": params,
            }
        )
    return out


@app.get("/api/file/tests", response_model=list[TestRunItem])
def file_tests_api(
    source_type: str = Query(..., description="csv or tdms or h5"),
    file_path: str = Query(..., description="Absolute file path."),
    units_in_headers: bool = Query(default=False, description="If true (CSV only), parse units from column headers."),
) -> list[TestRunItem]:
    try:
        _ = units_in_headers
        return file_tests(source_type=source_type, file_path=file_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/file/channels", response_model=list[ChannelItem])
def file_channels_api(
    source_type: str = Query(..., description="csv or tdms or h5"),
    file_path: str = Query(..., description="Absolute file path."),
    units_in_headers: bool = Query(default=False, description="If true (CSV only), parse units from column headers."),
    time_index_channel: str | None = Query(default=None, description="Shared time column/dataset for tabular files."),
) -> list[ChannelItem]:
    try:
        return file_channels(
            source_type=source_type,
            file_path=file_path,
            units_in_headers=units_in_headers,
            time_index_channel=time_index_channel,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/file/timeseries", response_model=list[TimeSeriesPoint])
def file_timeseries_api(
    response: Response,
    source_type: str = Query(..., description="csv or tdms or h5"),
    file_path: str = Query(..., description="Absolute file path."),
    channel_names: list[str] = Query(...),
    limit: int | None = Query(default=5000000, ge=1, le=5000000),
    units_in_headers: bool = Query(default=False, description="If true (CSV only), parse units from column headers."),
) -> list[TimeSeriesPoint]:
    _mark_legacy_timeseries(response)
    try:
        return file_timeseries(
            source_type=source_type,
            file_path=file_path,
            channel_names=channel_names,
            limit=limit or 5000000,
            units_in_headers=units_in_headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/desktop/pick-folder")
def pick_folder_dialog() -> dict:
    """Native folder dialog; returns the absolute folder path (no file copy)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Folder picker unavailable: {exc}") from exc
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        chosen = filedialog.askdirectory(title="Select data folder")
    finally:
        root.destroy()
    if not chosen:
        return {"path": None, "cancelled": True}
    return {"path": str(Path(chosen).resolve()), "cancelled": False}


@app.post("/api/desktop/pick-files")
def pick_files_dialog() -> dict:
    """Native multi-file dialog; returns absolute paths (no file copy)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"File picker unavailable: {exc}") from exc
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    filetypes = [
        ("Data files", "*.csv *.tdms *.h5 *.hdf5 *.parquet *.pq *.arrow *.arrows *.feather"),
        ("CSV", "*.csv"),
        ("TDMS", "*.tdms"),
        ("HDF5", "*.h5 *.hdf5"),
        ("Parquet", "*.parquet *.pq"),
        ("Arrow", "*.arrow *.arrows *.feather"),
        ("All files", "*.*"),
    ]
    try:
        chosen = filedialog.askopenfilenames(title="Select data files", filetypes=filetypes)
    finally:
        root.destroy()
    paths = [str(Path(p).resolve()) for p in (chosen or []) if p]
    return {"paths": paths, "cancelled": not paths}


@app.get("/api/file/scan-folder")
def scan_folder_files(path: str = Query(..., description="Absolute folder path to scan.")) -> dict:
    root = Path(path)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    root = root.resolve()
    files: list[dict] = []
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            source_type = detect_source_type(str(candidate))
        except ValueError:
            continue
        rel = candidate.relative_to(root).as_posix()
        files.append(
            {
                "path": str(candidate.resolve()),
                "name": candidate.name,
                "relative_path": rel,
                "source_type": source_type,
            }
        )
    files.sort(key=lambda row: str(row["relative_path"]).lower())
    parent_name = root.parent.name if root.parent and root.parent != root else ""
    return {
        "root": str(root),
        "root_name": root.name,
        "parent_name": parent_name,
        "files": files,
    }


def _load_unit_library_rows() -> list[dict]:
    if not UNIT_LIBRARY_FILE.exists():
        return []
    try:
        payload = json.loads(UNIT_LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("categories") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    try:
        return clean_unit_library_rows(rows)
    except ValueError:
        return []


def _write_unit_library_rows(rows: list[dict]) -> None:
    UNIT_LIBRARY_FILE.write_text(
        json.dumps({"categories": rows}, indent=2),
        encoding="utf-8",
    )


@app.get("/api/unit-library")
def get_unit_library() -> dict:
    cleaned = _load_unit_library_rows()
    if not cleaned:
        cleaned = default_unit_library_rows()
        _write_unit_library_rows(cleaned)
    return {"categories": cleaned}


@app.post("/api/unit-library")
def save_unit_library(payload: dict = Body(...)) -> dict:
    rows = payload.get("categories") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "categories must be a list"}
    try:
        cleaned = clean_unit_library_rows(rows)
    except (ValueError, SyntaxError) as exc:
        return {"ok": False, "error": str(exc)}
    _write_unit_library_rows(cleaned)
    return {"ok": True, "count": len(cleaned)}


@app.get("/api/appearance")
def get_appearance() -> dict:
    if not APPEARANCE_FILE.exists():
        return {}
    try:
        data = json.loads(APPEARANCE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.post("/api/appearance")
def save_appearance(payload: dict = Body(...)) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False}
    APPEARANCE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True}
