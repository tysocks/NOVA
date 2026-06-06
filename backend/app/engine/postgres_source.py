"""
PostgreSQL timeseries fetch with aggregation pushdown (Phase 2).

Overview: epoch bucket + avg(value) in SQL.
Detail: raw points in time window + LTTB per series in Python.
Raw: row fetch with per-series ROW_NUMBER cap (fixes global LIMIT).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from ..config import settings
from ..db import get_conn
from ..models import TimeSeriesPoint
from ..services.timeseries import _downsample_timeseries, _test_table_ident
from .query_planner import QueryMode, resolve_fetch_strategy

FetchStrategy = Literal["aggregate", "raw_lttb", "raw"]

_MIN_BUCKET_SECONDS = 0.001
_MAX_BUCKET_SECONDS = 86_400.0


def engine_enabled() -> bool:
    """
    When true, legacy v1/v2 endpoints use the v3 postgres engine (default on).

    Opt out with NOVA_LEGACY_ROW_ENGINE=1 or NOVA_USE_ENGINE=0.
    """
    legacy = os.environ.get("NOVA_LEGACY_ROW_ENGINE", "").strip().lower()
    if legacy in {"1", "true", "yes"}:
        return False
    use_engine = os.environ.get("NOVA_USE_ENGINE", "").strip().lower()
    if use_engine in {"0", "false", "no"}:
        return False
    return True


def bucket_interval_seconds(duration_seconds: float, max_points_per_series: int) -> float:
    """Bucket width in seconds so ~max_points buckets cover the span."""
    if duration_seconds <= 0 or max_points_per_series <= 0:
        return _MIN_BUCKET_SECONDS
    interval = duration_seconds / float(max_points_per_series)
    return max(_MIN_BUCKET_SECONDS, min(interval, _MAX_BUCKET_SECONDS))


def _conn_kwargs(
    *,
    db_name: str | None,
    db_host: str | None,
    db_port: int | None,
    db_user: str | None,
    db_password: str | None,
    db_sslmode: str | None,
) -> dict:
    return {
        "db_name": db_name,
        "db_host": db_host,
        "db_port": db_port,
        "db_user": db_user,
        "db_password": db_password,
        "db_sslmode": db_sslmode,
    }


def _query_time_span_seconds(
    test_run_ids: list[int],
    channel_names: list[str],
    start_time: str | None,
    end_time: str | None,
    test_table: str | None,
    conn_kw: dict,
) -> float:
    """Return active time span in seconds for bucket sizing."""
    test_ident = _test_table_ident(test_table)
    bounds_sql = sql.SQL(
        """
    SELECT
      EXTRACT(EPOCH FROM MIN(sr.time)) AS tmin,
      EXTRACT(EPOCH FROM MAX(sr.time)) AS tmax
    FROM sensor_readings sr
    INNER JOIN channels c ON c.id = sr.channel_id
    WHERE sr.test_run_id = ANY(%s)
      AND c.channel_name = ANY(%s)
      AND (%s::timestamptz IS NULL OR sr.time >= %s::timestamptz)
      AND (%s::timestamptz IS NULL OR sr.time <= %s::timestamptz)
    """
    )
    params = (test_run_ids, channel_names, start_time, start_time, end_time, end_time)
    with get_conn(**conn_kw) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(bounds_sql, params)
        row = cur.fetchone()
    if not row or row.get("tmin") is None or row.get("tmax") is None:
        return 0.0
    return max(0.0, float(row["tmax"]) - float(row["tmin"]))


def _rows_to_points(rows: Sequence[dict]) -> list[TimeSeriesPoint]:
    return [TimeSeriesPoint(**row) for row in rows]


def fetch_timeseries_aggregate(
    test_run_ids: list[int],
    channel_names: list[str],
    *,
    start_time: str | None,
    end_time: str | None,
    max_points_per_series: int,
    test_table: str | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[TimeSeriesPoint]:
    """SQL bucket aggregates (avg) per (test_run_id, channel_name)."""
    if not test_run_ids or not channel_names:
        return []

    conn_kw = _conn_kwargs(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )
    duration = _query_time_span_seconds(
        test_run_ids, channel_names, start_time, end_time, test_table, conn_kw
    )
    bucket_s = bucket_interval_seconds(duration, max_points_per_series)
    test_ident = _test_table_ident(test_table)

    query = sql.SQL(
        """
    SELECT
      sr.test_run_id,
      tr.run_code AS test_run_code,
      c.channel_name,
      c.unit,
      to_timestamp(
        floor(extract(epoch from sr.time) / %s) * %s
      ) AT TIME ZONE 'UTC' AS time,
      avg(sr.value)::double precision AS value
    FROM sensor_readings sr
    INNER JOIN channels c ON c.id = sr.channel_id
    INNER JOIN {test_table} tr ON tr.id = sr.test_run_id
    WHERE sr.test_run_id = ANY(%s)
      AND c.channel_name = ANY(%s)
      AND (%s::timestamptz IS NULL OR sr.time >= %s::timestamptz)
      AND (%s::timestamptz IS NULL OR sr.time <= %s::timestamptz)
    GROUP BY
      sr.test_run_id,
      tr.run_code,
      c.channel_name,
      c.unit,
      floor(extract(epoch from sr.time) / %s)
    ORDER BY time ASC
    """
    ).format(test_table=test_ident)

    params = (
        bucket_s,
        bucket_s,
        test_run_ids,
        channel_names,
        start_time,
        start_time,
        end_time,
        end_time,
        bucket_s,
    )

    with get_conn(**conn_kw) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        rows: Sequence[dict] = cur.fetchall()

    points = _rows_to_points(rows)
    for pt in points:
        if pt.time.tzinfo is None:
            pt.time = pt.time.replace(tzinfo=timezone.utc)
    return points


def fetch_timeseries_raw(
    test_run_ids: list[int],
    channel_names: list[str],
    *,
    start_time: str | None,
    end_time: str | None,
    per_series_limit: int | None,
    test_table: str | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[TimeSeriesPoint]:
    """
    Fetch raw points with optional per-series ROW_NUMBER cap.

    Replaces the legacy global LIMIT which starved multi-channel queries.
    """
    if not test_run_ids or not channel_names:
        return []

    test_ident = _test_table_ident(test_table)
    conn_kw = _conn_kwargs(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )

    rn_filter = sql.SQL("WHERE rn <= %s") if per_series_limit and per_series_limit > 0 else sql.SQL("")

    query = sql.SQL(
        """
    SELECT
      test_run_id,
      test_run_code,
      channel_name,
      unit,
      time,
      value
    FROM (
      SELECT
        sr.test_run_id,
        tr.run_code AS test_run_code,
        c.channel_name,
        c.unit,
        sr.time,
        sr.value,
        ROW_NUMBER() OVER (
          PARTITION BY sr.test_run_id, sr.channel_id
          ORDER BY sr.time ASC
        ) AS rn
      FROM sensor_readings sr
      INNER JOIN channels c ON c.id = sr.channel_id
      INNER JOIN {test_table} tr ON tr.id = sr.test_run_id
      WHERE sr.test_run_id = ANY(%s)
        AND c.channel_name = ANY(%s)
        AND (%s::timestamptz IS NULL OR sr.time >= %s::timestamptz)
        AND (%s::timestamptz IS NULL OR sr.time <= %s::timestamptz)
    ) ranked
    {rn_filter}
    ORDER BY time ASC
    """
    ).format(test_table=test_ident, rn_filter=rn_filter)

    params: tuple
    if per_series_limit and per_series_limit > 0:
        params = (
            test_run_ids,
            channel_names,
            start_time,
            start_time,
            end_time,
            end_time,
            per_series_limit,
        )
    else:
        params = (test_run_ids, channel_names, start_time, start_time, end_time, end_time)

    with get_conn(**conn_kw) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        rows: Sequence[dict] = cur.fetchall()

    return _rows_to_points(rows)


def fetch_postgres_timeseries(
    test_run_ids: list[int],
    channel_names: list[str],
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = None,
    max_points: int | None = None,
    mode: QueryMode = "overview",
    aggregation_mode: str | None = "auto",
    test_table: str | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[TimeSeriesPoint]:
    """Route to aggregate, raw+LTTB, or raw fetch based on mode and caps."""
    if not test_run_ids or not channel_names:
        return []

    strategy = resolve_fetch_strategy(
        mode=mode,
        aggregation_mode=aggregation_mode,
        max_points=max_points,
    )
    common = dict(
        test_run_ids=test_run_ids,
        channel_names=channel_names,
        start_time=start_time,
        end_time=end_time,
        test_table=test_table,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )

    if strategy == "aggregate" and max_points:
        return fetch_timeseries_aggregate(**common, max_points_per_series=max_points)

    if strategy == "raw_lttb" and max_points:
        points = fetch_timeseries_raw(**common, per_series_limit=None)
        if len(points) > max_points:
            return _downsample_timeseries(points, max_points)
        return points

    per_series = None
    if limit:
        per_series = max(1, min(limit, 5_000_000))
    elif max_points:
        per_series = max_points
    return fetch_timeseries_raw(**common, per_series_limit=per_series)
