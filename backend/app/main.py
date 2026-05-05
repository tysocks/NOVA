from pathlib import Path
import json

from fastapi import Body, FastAPI, File, Query, UploadFile
from fastapi.responses import FileResponse

from .models import ChannelItem, DatabaseItem, HealthResponse, TestRunItem, TimeSeriesEnvelope, TimeSeriesPoint
from .services.timeseries import (
    get_timeseries_envelope,
    get_timeseries,
    list_channels,
    list_channels_for_tests,
    list_databases,
    list_test_metadata,
    list_tests,
)
from .services.file_sources import file_channels, file_tests, file_timeseries
from .services.query_router import resolve_source_db_name

app = FastAPI(title="NOVA API", version="0.1.0", docs_url=None, redoc_url=None)
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
APPEARANCE_FILE = Path(__file__).resolve().parents[1] / ".nova_appearance.json"


@app.get("/", include_in_schema=False)
def nova_app() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True, app="NOVA")


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
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[TestRunItem]:
    return list_tests(
        limit=limit,
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
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[ChannelItem]:
    return list_channels_for_tests(
        test_run_ids=test_run_ids,
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
    t0_mode: str | None = Query(default="absolute", description="absolute, first_index, or t0_relative."),
    resolution_px: int | None = Query(default=None, ge=1, le=100000, description="Viewport width in pixels for adaptive resolution."),
    aggregation_mode: str | None = Query(default="auto", description="auto, lttb, raw/none."),
    limit: int | None = Query(default=None, ge=1, le=5000000),
    max_points: int | None = Query(default=None, ge=2, le=5000000, description="Optional hard cap per series."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> TimeSeriesEnvelope:
    target_db = resolve_source_db_name(source=source, db_name=db_name)
    return get_timeseries_envelope(
        test_run_ids=test_run_ids,
        channel_names=channel_names,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        max_points=max_points,
        resolution_px=resolution_px,
        aggregation_mode=aggregation_mode,
        t0_mode=t0_mode,
        db_name=target_db,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )


@app.get("/api/metadata")
def metadata(
    test_run_ids: list[int] = Query(..., description="One or more test_run_id values."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[dict]:
    return list_test_metadata(
        test_run_ids=test_run_ids,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )


@app.get("/api/file/tests", response_model=list[TestRunItem])
def file_tests_api(
    source_type: str = Query(..., description="csv or tdms"),
    file_path: str = Query(..., description="Absolute file path."),
) -> list[TestRunItem]:
    return file_tests(source_type=source_type, file_path=file_path)


@app.get("/api/file/channels", response_model=list[ChannelItem])
def file_channels_api(
    source_type: str = Query(..., description="csv or tdms"),
    file_path: str = Query(..., description="Absolute file path."),
) -> list[ChannelItem]:
    return file_channels(source_type=source_type, file_path=file_path)


@app.get("/api/file/timeseries", response_model=list[TimeSeriesPoint])
def file_timeseries_api(
    source_type: str = Query(..., description="csv or tdms"),
    file_path: str = Query(..., description="Absolute file path."),
    channel_names: list[str] = Query(...),
    limit: int | None = Query(default=5000000, ge=1, le=5000000),
) -> list[TimeSeriesPoint]:
    return file_timeseries(source_type=source_type, file_path=file_path, channel_names=channel_names, limit=limit or 5000000)


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
