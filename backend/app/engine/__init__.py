"""NOVA v3 query engine (columnar series, LOD planning)."""

from .arrow_codec import arrow_ipc_to_points, encode_series_arrow_ipc, points_to_arrow_table
from .duckdb_source import fetch_artifact_timeseries
from .file_index import get_ingest_status, run_ingest
from .postgres_source import (
    bucket_interval_seconds,
    engine_enabled,
    fetch_postgres_timeseries,
    fetch_timeseries_aggregate,
)
from .query_planner import plan_points_cap, resolve_fetch_strategy
from .series_query import execute_series_query

__all__ = [
    "arrow_ipc_to_points",
    "bucket_interval_seconds",
    "encode_series_arrow_ipc",
    "engine_enabled",
    "execute_series_query",
    "fetch_artifact_timeseries",
    "fetch_postgres_timeseries",
    "fetch_timeseries_aggregate",
    "get_ingest_status",
    "plan_points_cap",
    "points_to_arrow_table",
    "resolve_fetch_strategy",
    "run_ingest",
]
