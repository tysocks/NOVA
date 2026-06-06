"""Query indexed Parquet channel files via DuckDB."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb

from ..models import TimeSeriesPoint
from ..services.timeseries import _downsample_timeseries
from .postgres_source import bucket_interval_seconds
from .query_planner import FetchStrategy, QueryMode
from .session_store import artifact_dir, load_manifest


def _iso_to_epoch_ms(iso: str | None) -> float | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def _epoch_ms_to_datetime(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _channel_entry(manifest: dict, channel_name: str) -> dict | None:
    for row in manifest.get("channels") or []:
        if isinstance(row, dict) and row.get("channel_name") == channel_name:
            return row
    return None


def _query_channel_aggregate(
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    *,
    start_ms: float | None,
    end_ms: float | None,
    bucket_s: float,
) -> list[tuple[float, float]]:
    path_sql = str(parquet_path).replace("\\", "/")
    filters = []
    if start_ms is not None:
        filters.append(f"x_ms >= {start_ms}")
    if end_ms is not None:
        filters.append(f"x_ms <= {end_ms}")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"""
    SELECT
      floor(x_ms / {bucket_s}) * {bucket_s} AS bucket_ms,
      avg(y)::DOUBLE AS y
    FROM read_parquet('{path_sql}')
    {where}
    GROUP BY 1
    ORDER BY 1
    """
    rows = con.execute(sql).fetchall()
    return [(float(r[0]), float(r[1])) for r in rows]


def _query_channel_raw(
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    *,
    start_ms: float | None,
    end_ms: float | None,
) -> list[tuple[float, float]]:
    if not parquet_path.is_file() or parquet_path.stat().st_size == 0:
        return []
    path_sql = str(parquet_path).replace("\\", "/")
    filters = []
    if start_ms is not None:
        filters.append(f"x_ms >= {start_ms}")
    if end_ms is not None:
        filters.append(f"x_ms <= {end_ms}")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"""
    SELECT x_ms, y
    FROM read_parquet('{path_sql}')
    {where}
    ORDER BY x_ms
    """
    rows = con.execute(sql).fetchall()
    return [(float(r[0]), float(r[1])) for r in rows]


def fetch_artifact_timeseries(
    artifact_id: str,
    channel_names: list[str],
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    max_points: int | None = None,
    mode: QueryMode = "overview",
    aggregation_mode: str | None = "auto",
) -> list[TimeSeriesPoint]:
    """Load series from an ingested artifact using DuckDB over Parquet."""
    from .query_planner import resolve_fetch_strategy

    manifest = load_manifest(artifact_id)
    if not manifest or manifest.get("status") != "ready":
        raise ValueError(f"Artifact '{artifact_id}' is not ready.")

    strategy = resolve_fetch_strategy(
        mode=mode,
        aggregation_mode=aggregation_mode,
        max_points=max_points,
    )
    start_ms = _iso_to_epoch_ms(start_time)
    end_ms = _iso_to_epoch_ms(end_time)
    bounds = manifest.get("time_bounds") or {}
    span_start = start_ms if start_ms is not None else bounds.get("start_ms")
    span_end = end_ms if end_ms is not None else bounds.get("end_ms")
    duration_s = 0.0
    if span_start is not None and span_end is not None:
        duration_s = max(0.0, (float(span_end) - float(span_start)) / 1000.0)

    run_code = str(manifest.get("run_code", "run"))
    test_run_id = int(manifest.get("test_run_id", 1))
    root = artifact_dir(artifact_id)

    points: list[TimeSeriesPoint] = []
    con = duckdb.connect()

    try:
        for ch_name in channel_names:
            entry = _channel_entry(manifest, ch_name)
            if not entry:
                continue
            rel = entry.get("parquet")
            if not rel:
                continue
            parquet_path = root / str(rel)
            unit = entry.get("unit")

            if strategy == "aggregate" and max_points:
                bucket_s = bucket_interval_seconds(duration_s, max_points)
                rows = _query_channel_aggregate(
                    con, parquet_path, start_ms=start_ms, end_ms=end_ms, bucket_s=bucket_s
                )
            else:
                rows = _query_channel_raw(con, parquet_path, start_ms=start_ms, end_ms=end_ms)

            for x_ms, y in rows:
                points.append(
                    TimeSeriesPoint(
                        test_run_id=test_run_id,
                        test_run_code=run_code,
                        channel_name=ch_name,
                        unit=unit,
                        time=_epoch_ms_to_datetime(x_ms),
                        value=float(y),
                    )
                )
    finally:
        con.close()

    if strategy == "raw_lttb" and max_points and len(points) > max_points:
        return _downsample_timeseries(points, max_points)

    points.sort(key=lambda p: p.time)
    return points
