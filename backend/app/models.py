from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool
    app: str


class TestRunItem(BaseModel):
    test_run_id: int
    run_code: str
    start_time: datetime
    end_time: datetime | None = None
    duration_s: float | None = None
    t0_utc: datetime | None = None


class ChannelItem(BaseModel):
    channel_id: int
    channel_name: str
    display_name: str | None = None
    unit: str | None = None
    sample_rate_hz: float | None = None
    valid_min: float | None = None
    valid_max: float | None = None


class DatabaseItem(BaseModel):
    name: str
    is_default: bool


class TimeSeriesPoint(BaseModel):
    test_run_id: int
    test_run_code: str
    channel_name: str
    unit: str | None = None
    time: datetime
    value: float


class TimeSeriesSeriesMeta(BaseModel):
    test_run_id: int
    channel_name: str
    source: str | None = None
    database: str | None = None
    unit: str | None = None
    points: int
    min_value: float | None = None
    max_value: float | None = None
    first_time: datetime | None = None
    last_time: datetime | None = None


class TimeSeriesDetailHint(BaseModel):
    reason: str
    recommended_start: datetime | None = None
    recommended_end: datetime | None = None


class TimeSeriesEnvelope(BaseModel):
    overview: list[TimeSeriesPoint]
    series_meta: list[TimeSeriesSeriesMeta]
    detail_hint: TimeSeriesDetailHint | None = None


class PostgresSeriesSource(BaseModel):
    """PostgreSQL-backed source for v3 series queries."""

    type: Literal["postgres"] = "postgres"
    test_run_ids: list[int] = Field(min_length=1)
    channel_names: list[str] = Field(min_length=1)
    test_table: str | None = None
    db_name: str | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_sslmode: str | None = None


class FileSeriesSource(BaseModel):
    """Indexed file artifact for v3 series queries."""

    type: Literal["file"] = "file"
    artifact_id: str = Field(min_length=1)
    channel_names: list[str] = Field(min_length=1)


class CalculatedChannelSpec(BaseModel):
    """Server-side calculated channel definition."""

    kind: Literal["rolling", "formula"]
    name: str = Field(min_length=1)
    unit: str | None = None
    channels: list[str] = Field(default_factory=list)
    op: str | None = None
    window: int | None = Field(default=None, ge=1)
    formula: str | None = None


SeriesSource = Annotated[PostgresSeriesSource | FileSeriesSource, Field(discriminator="type")]


class FileIngestRequest(BaseModel):
    source_type: Literal["csv", "h5", "tdms"]
    file_path: str
    units_in_headers: bool = False


class FileIngestResponse(BaseModel):
    artifact_id: str
    status: str
    run_code: str | None = None
    channels: list[dict] = Field(default_factory=list)
    time_bounds: dict | None = None
    error: str | None = None


class SeriesQueryRequest(BaseModel):
    """POST /api/v3/series/query body."""

    sources: list[SeriesSource] = Field(min_length=1)
    time_range: list[str | None] | None = None
    mode: Literal["overview", "detail", "raw"] = "overview"
    resolution_px: int | None = Field(default=None, ge=1, le=100_000)
    aggregation_mode: str = "auto"
    max_points: int | None = Field(default=None, ge=2, le=5_000_000)
    limit: int | None = Field(default=None, ge=1, le=5_000_000)
    source: str = "auto"
    overlay_mode: str = "single"
    calculated_channels: list[CalculatedChannelSpec] = Field(default_factory=list)


class SeriesQueryResponseMeta(BaseModel):
    """JSON metadata returned in X-NOVA-Series-Meta header."""

    row_count: int
    series_meta: list[TimeSeriesSeriesMeta]
    detail_hint: TimeSeriesDetailHint | None = None
    points_cap_per_series: int | None = None
    mode: str
    fetch_strategy: str | None = None
