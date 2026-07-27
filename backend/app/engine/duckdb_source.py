"""Query indexed Parquet channel files via DuckDB (Arrow/Polars transforms)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import polars as pl

from ..models import TimeSeriesPoint
from ..services.timeseries import _downsample_timeseries
from .polars_series import concat_frames, frame_from_points, frame_from_xy, points_from_frame
from .postgres_source import bucket_interval_seconds
from .query_planner import QueryMode
from .session_store import artifact_dir, load_manifest


def _iso_to_epoch_ms(iso: str | None) -> float | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def _channel_entry(manifest: dict, channel_name: str) -> dict | None:
    for row in manifest.get("channels") or []:
        if isinstance(row, dict) and row.get("channel_name") == channel_name:
            return row
    return None


def _resolve_parquet_path(uri: str, *, artifact_id: str | None = None) -> Path | None:
    text = str(uri or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        return path
    if not path.is_absolute() and artifact_id:
        candidate = artifact_dir(artifact_id) / text
        if candidate.is_file():
            return candidate
    return path if path.exists() else None


def _arrow_to_xy_frame(arrow_obj) -> pl.DataFrame:
    """DuckDB may return a Table or RecordBatchReader depending on version."""
    if arrow_obj is None:
        return pl.DataFrame(schema={"x_ms": pl.Float64, "y": pl.Float64})
    if hasattr(arrow_obj, "read_all"):
        table = arrow_obj.read_all()
    else:
        table = arrow_obj
    if getattr(table, "num_rows", 0) == 0:
        return pl.DataFrame(schema={"x_ms": pl.Float64, "y": pl.Float64})
    return pl.from_arrow(table)


def _query_channel_aggregate(
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    *,
    start_ms: float | None,
    end_ms: float | None,
    bucket_ms: float,
) -> pl.DataFrame:
    path_sql = str(parquet_path).replace("\\", "/")
    filters = []
    if start_ms is not None:
        filters.append(f"x_ms >= {start_ms}")
    if end_ms is not None:
        filters.append(f"x_ms <= {end_ms}")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"""
    SELECT
      floor(x_ms / {bucket_ms}) * {bucket_ms} AS x_ms,
      avg(y)::DOUBLE AS y
    FROM read_parquet('{path_sql}')
    {where}
    GROUP BY 1
    ORDER BY 1
    """
    return _arrow_to_xy_frame(con.execute(sql).arrow())


def _query_channel_raw(
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    *,
    start_ms: float | None,
    end_ms: float | None,
) -> pl.DataFrame:
    if not parquet_path.is_file() or parquet_path.stat().st_size == 0:
        return pl.DataFrame(schema={"x_ms": pl.Float64, "y": pl.Float64})
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
    return _arrow_to_xy_frame(con.execute(sql).arrow())


def _fetch_channel_frame(
    con: duckdb.DuckDBPyConnection,
    *,
    channel_name: str,
    parquet_path: Path,
    unit: str | None,
    test_run_id: int,
    run_code: str,
    strategy: str,
    start_ms: float | None,
    end_ms: float | None,
    duration_s: float,
    max_points: int | None,
) -> pl.DataFrame:
    if not unit:
        from .file_index import _read_unit_from_channel_parquet

        unit = _read_unit_from_channel_parquet(parquet_path)

    if strategy == "aggregate" and max_points:
        bucket_ms = bucket_interval_seconds(duration_s, max_points) * 1000.0
        xy = _query_channel_aggregate(
            con, parquet_path, start_ms=start_ms, end_ms=end_ms, bucket_ms=bucket_ms
        )
    else:
        xy = _query_channel_raw(con, parquet_path, start_ms=start_ms, end_ms=end_ms)

    if xy.is_empty():
        return frame_from_xy(
            test_run_id=test_run_id,
            test_run_code=run_code,
            channel_name=channel_name,
            unit=unit,
            xs=[],
            ys=[],
        )
    return frame_from_xy(
        test_run_id=test_run_id,
        test_run_code=run_code,
        channel_name=channel_name,
        unit=unit,
        xs=xy["x_ms"].cast(pl.Float64).to_list(),
        ys=xy["y"].cast(pl.Float64, strict=False).to_list(),
    )


def fetch_catalog_timeseries_frame(
    test_id: int,
    channel_names: list[str],
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    max_points: int | None = None,
    mode: QueryMode = "overview",
    aggregation_mode: str | None = "auto",
) -> pl.DataFrame:
    """Load series as a Polars frame using catalog channel parquet URIs."""
    from .catalog_store import get_test_by_id, list_channel_parquet_entries
    from .query_planner import resolve_fetch_strategy

    test = get_test_by_id(test_id)
    if not test or str(test.get("status") or "") != "ready":
        raise ValueError(f"Catalog test '{test_id}' is not ready.")

    entries = list_channel_parquet_entries(test_id, channel_names)
    if not entries:
        raise ValueError(f"No catalog channels found for test '{test_id}'.")

    strategy = resolve_fetch_strategy(
        mode=mode,
        aggregation_mode=aggregation_mode,
        max_points=max_points,
    )
    start_ms = _iso_to_epoch_ms(start_time)
    end_ms = _iso_to_epoch_ms(end_time)

    start_dt = test.get("start_time")
    end_dt = test.get("end_time")
    span_start = start_ms if start_ms is not None else (start_dt.timestamp() * 1000.0 if start_dt else None)
    span_end = end_ms if end_ms is not None else (end_dt.timestamp() * 1000.0 if end_dt else None)
    duration_s = 0.0
    if span_start is not None and span_end is not None:
        duration_s = max(0.0, (float(span_end) - float(span_start)) / 1000.0)
    elif test.get("duration_s") is not None:
        duration_s = float(test["duration_s"])

    run_code = str(test.get("run_code") or "run")
    artifact_id = test.get("artifact_id")
    frames: list[pl.DataFrame] = []
    con = duckdb.connect()
    try:
        for entry in entries:
            parquet_path = _resolve_parquet_path(
                str(entry.get("parquet_uri") or ""),
                artifact_id=str(artifact_id) if artifact_id else None,
            )
            if parquet_path is None:
                continue
            frames.append(
                _fetch_channel_frame(
                    con,
                    channel_name=str(entry["channel_name"]),
                    parquet_path=parquet_path,
                    unit=entry.get("unit"),
                    test_run_id=test_id,
                    run_code=run_code,
                    strategy=strategy,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_s=duration_s,
                    max_points=max_points,
                )
            )
    finally:
        con.close()

    df = concat_frames(frames)
    if strategy == "raw_lttb" and max_points and df.height > max_points:
        points = points_from_frame(df)
        return frame_from_points(_downsample_timeseries(points, max_points))
    return df


def fetch_catalog_timeseries(
    test_id: int,
    channel_names: list[str],
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    max_points: int | None = None,
    mode: QueryMode = "overview",
    aggregation_mode: str | None = "auto",
) -> list[TimeSeriesPoint]:
    """Compatibility wrapper: catalog query returning TimeSeriesPoint rows."""
    df = fetch_catalog_timeseries_frame(
        test_id,
        channel_names,
        start_time=start_time,
        end_time=end_time,
        max_points=max_points,
        mode=mode,
        aggregation_mode=aggregation_mode,
    )
    return points_from_frame(df)


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
    """Load series from an ingested artifact.

    Prefers the DuckDB catalog channel index; falls back to session manifest.
    """
    from .catalog_store import get_test_by_artifact_id
    from .query_planner import resolve_fetch_strategy

    catalog_test = get_test_by_artifact_id(artifact_id)
    if catalog_test and str(catalog_test.get("status") or "") == "ready":
        return fetch_catalog_timeseries(
            int(catalog_test["test_id"]),
            channel_names,
            start_time=start_time,
            end_time=end_time,
            max_points=max_points,
            mode=mode,
            aggregation_mode=aggregation_mode,
        )

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

    frames: list[pl.DataFrame] = []
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
            frames.append(
                _fetch_channel_frame(
                    con,
                    channel_name=ch_name,
                    parquet_path=parquet_path,
                    unit=entry.get("unit"),
                    test_run_id=test_run_id,
                    run_code=run_code,
                    strategy=strategy,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_s=duration_s,
                    max_points=max_points,
                )
            )
    finally:
        con.close()

    df = concat_frames(frames)
    points = points_from_frame(df)
    if strategy == "raw_lttb" and max_points and len(points) > max_points:
        return _downsample_timeseries(points, max_points)
    return points
