from pathlib import Path
import json

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .models import ChannelItem, DatabaseItem, HealthResponse, TestRunItem, TimeSeriesEnvelope, TimeSeriesPoint
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
from .services.file_sources import file_channels, file_tests, file_timeseries
from .services.query_router import resolve_overlay_targets
from .config import settings

app = FastAPI(title="NOVA API", version="0.1.0", docs_url=None, redoc_url=None)
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
APPEARANCE_FILE = Path(__file__).resolve().parents[1] / ".nova_appearance.json"
SOURCE_DEFAULTS_FILE = Path(__file__).resolve().parents[1] / ".nova_source_defaults.json"
CONFIG_LIBRARY_FILE = Path(__file__).resolve().parents[1] / ".nova_config_library.json"


@app.get("/", include_in_schema=False)
def nova_app() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True, app="NOVA")


@app.get("/api/source-defaults")
def source_defaults() -> dict:
    defaults = [
        {
            "key": "redscale",
            "name": "RedScale",
            "type": "postgres",
            "host": settings.redscale_host,
            "port": settings.redscale_port,
            "user": settings.redscale_user,
            "password": settings.redscale_password,
            "sslmode": settings.redscale_sslmode,
        },
        {
            "key": "bluescale",
            "name": "BlueScale",
            "type": "postgres",
            "host": settings.bluescale_host,
            "port": settings.bluescale_port,
            "user": settings.bluescale_user,
            "password": settings.bluescale_password,
            "sslmode": settings.bluescale_sslmode,
        },
    ]
    if SOURCE_DEFAULTS_FILE.exists():
        try:
            payload = json.loads(SOURCE_DEFAULTS_FILE.read_text(encoding="utf-8"))
            from_file = payload.get("defaults") if isinstance(payload, dict) else None
            if isinstance(from_file, list):
                cleaned = []
                for row in from_file:
                    if not isinstance(row, dict):
                        continue
                    if row.get("type", "postgres") != "postgres":
                        continue
                    cleaned.append(
                        {
                            "key": str(row.get("key") or f"src_{len(cleaned)+1}"),
                            "name": str(row.get("name") or "PostgreSQL Source"),
                            "type": "postgres",
                            "host": str(row.get("host") or "localhost"),
                            "port": int(row.get("port") or 5432),
                            "user": str(row.get("user") or "pipeline"),
                            "password": str(row.get("password") or ""),
                            "sslmode": str(row.get("sslmode") or "disable"),
                        }
                    )
                defaults = cleaned or defaults
        except Exception:
            pass
    return {"defaults": defaults}


@app.post("/api/source-defaults")
def save_source_defaults(payload: dict = Body(...)) -> dict:
    rows = payload.get("defaults") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "defaults must be a list"}
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("type", "postgres") != "postgres":
            continue
        cleaned.append(
            {
                "key": str(row.get("key") or f"src_{len(cleaned)+1}"),
                "name": str(row.get("name") or "PostgreSQL Source"),
                "type": "postgres",
                "host": str(row.get("host") or "localhost"),
                "port": int(row.get("port") or 5432),
                "user": str(row.get("user") or "pipeline"),
                "password": str(row.get("password") or ""),
                "sslmode": str(row.get("sslmode") or "disable"),
            }
        )
    if not cleaned:
        return {"ok": False, "error": "at least one postgres default is required"}
    SOURCE_DEFAULTS_FILE.write_text(json.dumps({"defaults": cleaned}, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(cleaned)}


@app.get("/api/config-library")
def get_config_library() -> dict:
    if not CONFIG_LIBRARY_FILE.exists():
        return {"configs": []}
    try:
        payload = json.loads(CONFIG_LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"configs": []}
    rows = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"configs": []}
    cleaned: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or "Unnamed Config"),
                "savedAt": str(row.get("savedAt") or ""),
                "payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
            }
        )
    return {"configs": cleaned}


@app.post("/api/config-library")
def save_config_library(payload: dict = Body(...)) -> dict:
    rows = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"ok": False, "error": "configs must be a list"}
    cleaned: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cfg_payload = row.get("payload")
        if not isinstance(cfg_payload, dict):
            continue
        cleaned.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or "Unnamed Config"),
                "savedAt": str(row.get("savedAt") or ""),
                "payload": cfg_payload,
            }
        )
    CONFIG_LIBRARY_FILE.write_text(json.dumps({"configs": cleaned}, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(cleaned)}


@app.get("/api/databases", response_model=list[DatabaseItem])
def databases(
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[DatabaseItem]:
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
) -> list[TestRunItem]:
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


@app.get("/api/test-tables", response_model=list[str])
def test_tables(
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[str]:
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
) -> list[ChannelItem]:
    return list_channels(
        limit=limit,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )


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


@app.get("/api/timeseries", response_model=list[TimeSeriesPoint])
def timeseries(
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
) -> list[ChannelItem]:
    try:
        return file_channels(source_type=source_type, file_path=file_path, units_in_headers=units_in_headers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/file/timeseries", response_model=list[TimeSeriesPoint])
def file_timeseries_api(
    source_type: str = Query(..., description="csv or tdms or h5"),
    file_path: str = Query(..., description="Absolute file path."),
    channel_names: list[str] = Query(...),
    limit: int | None = Query(default=5000000, ge=1, le=5000000),
    units_in_headers: bool = Query(default=False, description="If true (CSV only), parse units from column headers."),
) -> list[TimeSeriesPoint]:
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


@app.post("/api/file/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    safe_name = Path(file.filename or "uploaded.bin").name
    out_path = UPLOADS_DIR / safe_name
    content = await file.read()
    out_path.write_bytes(content)
    return {"path": str(out_path)}


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
