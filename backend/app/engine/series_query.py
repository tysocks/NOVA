"""Execute v3 series queries (PostgreSQL + indexed file artifacts)."""

from __future__ import annotations

from ..models import (
    CalculatedChannelSpec,
    FileSeriesSource,
    PostgresSeriesSource,
    SeriesQueryRequest,
    SeriesQueryResponseMeta,
    TimeSeriesDetailHint,
    TimeSeriesPoint,
    TimeSeriesSeriesMeta,
)
from .calc_engine import apply_calculated_channels
from .calc_graph import order_calculated_channels
from ..services.query_router import resolve_overlay_targets
from ..services.timeseries import build_series_meta
from .arrow_codec import encode_series_arrow_ipc, series_point_counts
from .duckdb_source import fetch_artifact_timeseries
from .postgres_source import fetch_postgres_timeseries
from .query_planner import plan_points_cap, resolve_fetch_strategy


def _parse_time_range(time_range: list[str | None] | None) -> tuple[str | None, str | None]:
    if not time_range:
        return None, None
    start = time_range[0] if len(time_range) > 0 else None
    end = time_range[1] if len(time_range) > 1 else None
    return start, end


def _fetch_postgres_source(
    source: PostgresSeriesSource,
    *,
    start_time: str | None,
    end_time: str | None,
    limit: int | None,
    max_points: int | None,
    mode: str,
    aggregation_mode: str,
    src_label: str,
    target_db: str | None,
) -> tuple[list[TimeSeriesPoint], list[TimeSeriesSeriesMeta]]:
    if not source.test_run_ids:
        return [], []
    if not source.channel_names:
        return [], []

    points = fetch_postgres_timeseries(
        source.test_run_ids,
        source.channel_names,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        max_points=max_points,
        mode=mode,
        aggregation_mode=aggregation_mode,
        test_table=source.test_table,
        db_name=target_db or source.db_name,
        db_host=source.db_host,
        db_port=source.db_port,
        db_user=source.db_user,
        db_password=source.db_password,
        db_sslmode=source.db_sslmode,
    )
    meta = build_series_meta(points, source=src_label, database=target_db or source.db_name)
    return points, meta


def _fetch_file_source(
    source: FileSeriesSource,
    *,
    start_time: str | None,
    end_time: str | None,
    max_points: int | None,
    mode: str,
    aggregation_mode: str,
) -> tuple[list[TimeSeriesPoint], list[TimeSeriesSeriesMeta]]:
    if not source.channel_names:
        return [], []

    points = fetch_artifact_timeseries(
        source.artifact_id,
        source.channel_names,
        start_time=start_time,
        end_time=end_time,
        max_points=max_points,
        mode=mode,
        aggregation_mode=aggregation_mode,
    )
    meta = build_series_meta(points, source="file", database=source.artifact_id)
    return points, meta


def execute_series_query(request: SeriesQueryRequest) -> tuple[bytes, SeriesQueryResponseMeta]:
    """
    Run a v3 query and return (Arrow IPC bytes, response metadata).

    Supports postgres and indexed file (artifact) sources.
    """
    cap = plan_points_cap(
        resolution_px=request.resolution_px,
        aggregation_mode=request.aggregation_mode,
        max_points=request.max_points,
        mode=request.mode,
    )
    start_time, end_time = _parse_time_range(request.time_range)

    all_points: list[TimeSeriesPoint] = []
    all_meta: list[TimeSeriesSeriesMeta] = []
    detail_hint: TimeSeriesDetailHint | None = None
    strategies: set[str] = set()

    for source in request.sources:
        if isinstance(source, FileSeriesSource):
            strategy = resolve_fetch_strategy(
                mode=request.mode,
                aggregation_mode=request.aggregation_mode,
                max_points=cap,
            )
            strategies.add(strategy)
            points, meta = _fetch_file_source(
                source,
                start_time=start_time,
                end_time=end_time,
                max_points=cap,
                mode=request.mode,
                aggregation_mode=request.aggregation_mode,
            )
            all_points.extend(points)
            all_meta.extend(meta)
        elif isinstance(source, PostgresSeriesSource):
            targets = resolve_overlay_targets(
                source=request.source,
                overlay_mode=request.overlay_mode,
                db_name=source.db_name,
            )
            for src_label, target_db in targets:
                strategy = resolve_fetch_strategy(
                    mode=request.mode,
                    aggregation_mode=request.aggregation_mode,
                    max_points=cap,
                )
                strategies.add(strategy)
                points, meta = _fetch_postgres_source(
                    source,
                    start_time=start_time,
                    end_time=end_time,
                    limit=request.limit,
                    max_points=cap,
                    mode=request.mode,
                    aggregation_mode=request.aggregation_mode,
                    src_label=src_label,
                    target_db=target_db,
                )
                all_points.extend(points)
                all_meta.extend(meta)

    if cap and all_points:
        counts = series_point_counts(all_points)
        if any(n >= cap for n in counts.values()):
            sorted_pts = sorted(all_points, key=lambda p: p.time)
            detail_hint = TimeSeriesDetailHint(
                reason="downsampled_for_viewport",
                recommended_start=sorted_pts[0].time if sorted_pts else None,
                recommended_end=sorted_pts[-1].time if sorted_pts else None,
            )

    calc_specs = order_calculated_channels(request.calculated_channels or [])
    if calc_specs:
        derived = apply_calculated_channels(all_points, calc_specs)
        all_points.extend(derived)
        all_meta.extend(build_series_meta(derived, source="calculated", database=None))

    all_points.sort(key=lambda p: p.time)
    ipc = encode_series_arrow_ipc(all_points)
    if len(strategies) == 1:
        fetch_strategy = next(iter(strategies))
    elif strategies:
        fetch_strategy = "mixed"
    else:
        fetch_strategy = resolve_fetch_strategy(
            mode=request.mode,
            aggregation_mode=request.aggregation_mode,
            max_points=cap,
        )

    return ipc, SeriesQueryResponseMeta(
        row_count=len(all_points),
        series_meta=all_meta,
        detail_hint=detail_hint,
        points_cap_per_series=cap,
        mode=request.mode,
        fetch_strategy=fetch_strategy,
    )
