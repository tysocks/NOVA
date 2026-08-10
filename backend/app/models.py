from datetime import datetime
from typing import Annotated, Any, Literal

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
    icon: str | None = None


class CatalogTestUpdateRequest(BaseModel):
    run_code: str | None = None
    icon: str | None = None
    catalog_id: str | None = None


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


class TestParameterItem(BaseModel):
    test_id: int
    key: str
    value_text: str | None = None
    value_num: float | None = None


class RangeItem(BaseModel):
    range_id: int
    test_id: int | None = None
    artifact_id: str | None = None
    catalog_id: str | None = None
    durability: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    name: str
    label: str | None = None
    status: str | None = None
    start_time: datetime
    end_time: datetime
    start_ms: float | None = None
    end_ms: float | None = None
    color: str | None = None
    tags: list[str] = Field(default_factory=list)
    parent_range_id: int | None = None
    source: str = "user"
    rule_id: int | None = None
    notes: str | None = None
    parameters: list["RangeParameterItem"] = Field(default_factory=list)


class RangeParameterItem(BaseModel):
    range_id: int
    key: str
    value_text: str | None = None
    value_num: float | None = None


class RangeParameterWrite(BaseModel):
    key: str
    value_text: str | None = None
    value_num: float | None = None


class RangeRuleItem(BaseModel):
    rule_id: int
    name: str
    description: str | None = None
    kind: str
    channel_name: str
    config: str
    default_label: str | None = None
    default_color: str | None = None


class RangeSourceRef(BaseModel):
    artifact_id: str | None = None
    test_id: int | None = None
    catalog_id: str | None = None
    file_path: str | None = None
    durability: str | None = None
    source_id: str | None = None
    source_name: str | None = None


class RangeListRequest(BaseModel):
    sources: list[RangeSourceRef] = Field(default_factory=list)


class RangeUpdateRequest(BaseModel):
    name: str | None = None
    label: str | None = None
    status: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    color: str | None = None
    tags: list[str] | None = None
    parent_range_id: int | None = None
    notes: str | None = None
    parameters: list[RangeParameterWrite] | None = None
    artifact_id: str | None = None
    test_id: int | None = None
    catalog_id: str | None = None
    file_path: str | None = None
    durability: str | None = None


class RangeDeleteRequest(BaseModel):
    artifact_id: str | None = None
    test_id: int | None = None
    catalog_id: str | None = None
    file_path: str | None = None
    durability: str | None = None



class ResultItem(BaseModel):
    result_id: int
    test_id: int
    range_id: int | None = None
    analysis_name: str
    key: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None


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


class CatalogSeriesSource(BaseModel):
    """DuckDB catalog-backed series query (preferred for permanent/session Parquet)."""

    type: Literal["catalog"] = "catalog"
    test_id: int = Field(ge=1)
    channel_names: list[str] = Field(min_length=1)
    catalog_id: str | None = None


class CalculatedChannelSpec(BaseModel):
    """Server-side calculated channel definition."""

    kind: Literal["rolling", "formula"]
    name: str = Field(min_length=1)
    unit: str | None = None
    channels: list[str] = Field(default_factory=list)
    op: str | None = None
    window: int | None = Field(default=None, ge=1)
    formula: str | None = None


SeriesSource = Annotated[
    PostgresSeriesSource | FileSeriesSource | CatalogSeriesSource,
    Field(discriminator="type"),
]


class FileIngestRequest(BaseModel):
    source_type: Literal["csv", "h5", "tdms", "parquet", "arrow"]
    file_path: str
    units_in_headers: bool = False
    time_index_channel: str | None = None
    ingest_mode: Literal["temporary", "permanent"] | None = None
    parameters: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    apply_range_rule_ids: list[int] = Field(default_factory=list)
    catalog_id: str | None = None
    # Permanent ingestion rule controls (ignored for temporary folder/file open).
    channel_mode: Literal["all", "include"] | None = None
    channel_include: list[str] = Field(default_factory=list)
    channel_exclude: list[str] = Field(default_factory=list)
    channel_rename: dict[str, str] = Field(default_factory=dict)
    channel_require: list[str] = Field(default_factory=list)
    calculated_channels: list[CalculatedChannelSpec] = Field(default_factory=list)
    range_definition_ids: list[str] = Field(default_factory=list)
    ingestion_rule_id: str | None = None


class IngestWithRuleRequest(BaseModel):
    file_path: str
    source_type: Literal["csv", "h5", "tdms", "parquet", "arrow"] | None = None
    rule_id: str | None = None
    rule: dict[str, Any] | None = None


class FileProbeChannel(BaseModel):
    channel_name: str
    unit: str | None = None
    unit_from_metadata: bool = False


class FileUnitsMetadataReport(BaseModel):
    supports_unit_metadata: bool
    parse_units_from_header: bool = False
    channels_with_units: list[str] = Field(default_factory=list)
    channels_without_units: list[str] = Field(default_factory=list)
    all_channels_have_units: bool = False
    summary: str = ""
    flag: Literal["ok", "partial", "missing", "na"] = "na"


class FileSchemaValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""
    format_requirements: str = ""


class FileProbeRequest(BaseModel):
    file_path: str
    units_in_headers: bool = False
    time_index_channel: str | None = None


class FileProbeResponse(BaseModel):
    source_type: str
    file_path: str
    time_index_candidates: list[str] = Field(default_factory=list)
    time_index_default: str | None = None
    channels: list[FileProbeChannel] = Field(default_factory=list)
    units_metadata: FileUnitsMetadataReport
    schema_validation: FileSchemaValidation | None = None


class FileIngestResponse(BaseModel):
    artifact_id: str
    status: str
    run_code: str | None = None
    channels: list[dict] = Field(default_factory=list)
    time_bounds: dict | None = None
    error: str | None = None
    test_id: int | None = None
    durability: str | None = None
    applied_ranges: list[RangeItem] = Field(default_factory=list)


class ApplyRangeRuleRequest(BaseModel):
    test_id: int = Field(ge=1)
    rule_id: int = Field(ge=1)
    catalog_id: str | None = None


class RangeCreateRequest(BaseModel):
    test_id: int | None = None
    artifact_id: str | None = None
    file_path: str | None = None
    durability: str | None = None
    name: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    label: str | None = None
    status: str | None = "completed"
    color: str | None = None
    tags: list[str] = Field(default_factory=list)
    parent_range_id: int | None = None
    notes: str | None = None
    source: Literal["user", "rule", "source"] = "user"
    rule_id: int | None = None
    parameters: list[RangeParameterWrite] = Field(default_factory=list)
    catalog_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None


class RangeRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    kind: Literal["threshold", "edge", "formula"]
    channel_name: str = Field(min_length=1)
    config: str = Field(min_length=2)
    description: str | None = None
    default_label: str | None = None
    default_color: str | None = None


class ResultWriteRequest(BaseModel):
    test_id: int
    range_id: int | None = None
    analysis_name: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None


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
