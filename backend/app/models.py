from datetime import datetime

from pydantic import BaseModel


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
