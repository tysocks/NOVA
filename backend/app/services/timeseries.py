from collections import defaultdict
from collections.abc import Sequence

from psycopg2.extras import RealDictCursor

from ..config import settings
from ..db import get_conn
from ..models import (
    ChannelItem,
    DatabaseItem,
    TestRunItem,
    TimeSeriesDetailHint,
    TimeSeriesEnvelope,
    TimeSeriesPoint,
    TimeSeriesSeriesMeta,
)


def _lttb_series(points: list, threshold: int) -> list:
    """Largest-Triangle-Three-Buckets downsampling for a single sorted series."""
    n = len(points)
    if n == 0 or threshold <= 0:
        return points
    if threshold >= n:
        return points

    sampled = [points[0]]
    bucket_size = (n - 2) / (threshold - 2) if threshold > 2 else float(n)
    a = 0

    for i in range(threshold - 2):
        avg_range_start = int((i + 1) * bucket_size) + 1
        avg_range_end = min(int((i + 2) * bucket_size) + 1, n)
        avg_count = avg_range_end - avg_range_start
        avg_x = (avg_range_start + avg_range_end - 1) / 2.0
        avg_y = sum(points[j].value for j in range(avg_range_start, avg_range_end)) / avg_count

        range_start = int(i * bucket_size) + 1
        range_end = min(int((i + 1) * bucket_size) + 1, n)

        ax = float(a)
        ay = points[a].value
        max_area = -1.0
        next_a = range_start

        for j in range(range_start, range_end):
            area = abs((ax - avg_x) * (points[j].value - ay) - (ax - j) * (avg_y - ay)) * 0.5
            if area > max_area:
                max_area = area
                next_a = j

        sampled.append(points[next_a])
        a = next_a

    sampled.append(points[-1])
    return sampled


def _downsample_timeseries(points: list[TimeSeriesPoint], max_points: int) -> list[TimeSeriesPoint]:
    """Group by (test_run_id, channel_name) and apply LTTB per series."""
    series: dict[tuple, list[TimeSeriesPoint]] = defaultdict(list)
    for pt in points:
        series[(pt.test_run_id, pt.channel_name)].append(pt)

    result: list[TimeSeriesPoint] = []
    for pts in series.values():
        result.extend(_lttb_series(pts, max_points))
    return result


def plan_timeseries_points_cap(
    resolution_px: int | None,
    aggregation_mode: str | None,
    max_points: int | None,
) -> int | None:
    """
    Convert frontend viewport intent into a per-series point cap.
    max_points still hard-overrides for backward compatibility.
    """
    if max_points and max_points > 0:
        return max_points
    mode = (aggregation_mode or "auto").strip().lower()
    if mode in {"none", "raw"}:
        return None
    if not resolution_px or resolution_px <= 0:
        return None
    # 2x oversampling retains visual fidelity while avoiding huge payloads.
    return max(500, min(resolution_px * 2, 5_000_000))


def _build_series_meta(points: list[TimeSeriesPoint]) -> list[TimeSeriesSeriesMeta]:
    grouped: dict[tuple[int, str], list[TimeSeriesPoint]] = defaultdict(list)
    for pt in points:
        grouped[(pt.test_run_id, pt.channel_name)].append(pt)
    out: list[TimeSeriesSeriesMeta] = []
    for (test_run_id, channel_name), rows in grouped.items():
        rows.sort(key=lambda r: r.time)
        vals = [r.value for r in rows]
        out.append(
            TimeSeriesSeriesMeta(
                test_run_id=test_run_id,
                channel_name=channel_name,
                unit=rows[0].unit if rows else None,
                points=len(rows),
                min_value=min(vals) if vals else None,
                max_value=max(vals) if vals else None,
                first_time=rows[0].time if rows else None,
                last_time=rows[-1].time if rows else None,
            )
        )
    return out


def get_timeseries_envelope(
    test_run_ids: list[int],
    channel_names: list[str],
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = None,
    max_points: int | None = None,
    resolution_px: int | None = None,
    aggregation_mode: str | None = None,
    t0_mode: str | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> TimeSeriesEnvelope:
    cap = plan_timeseries_points_cap(
        resolution_px=resolution_px,
        aggregation_mode=aggregation_mode,
        max_points=max_points,
    )
    points = get_timeseries(
        test_run_ids=test_run_ids,
        channel_names=channel_names,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        max_points=cap,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )
    detail_hint = None
    if cap and len(points) >= cap:
        detail_hint = TimeSeriesDetailHint(
            reason="downsampled_for_viewport",
            recommended_start=points[0].time if points else None,
            recommended_end=points[-1].time if points else None,
        )
    # t0_mode is accepted as part of v2 contract; data transform remains frontend-side.
    _ = t0_mode
    return TimeSeriesEnvelope(
        overview=points,
        series_meta=_build_series_meta(points),
        detail_hint=detail_hint,
    )


def list_databases(
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[DatabaseItem]:
    # Names come from PostgreSQL's pg_database catalog (cluster-wide), not from
    # RedscaleDB or another vendor. Every server has template DBs and usually a
    # `postgres` maintenance database; we hide those from the picker only.
    sql = """
    SELECT datname
    FROM pg_database
    WHERE datistemplate = false
      AND datallowconn = true
      AND datname NOT IN ('postgres', 'template0', 'template1')
    ORDER BY datname
    """
    with get_conn(
        db_name="postgres",
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        rows: Sequence[dict] = cur.fetchall()
    return [
        DatabaseItem(name=row["datname"], is_default=(row["datname"] == settings.db_name))
        for row in rows
    ]


def list_tests(
    limit: int | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[TestRunItem]:
    query_limit = max(1, min(limit or settings.default_limit, 5000000))
    sql = """
    SELECT
      tr.id AS test_run_id,
      tr.run_code,
      tr.start_time,
      tr.end_time,
      tr.duration_s,
      t0.first_time AS t0_utc
    FROM test_runs tr
    LEFT JOIN (
      SELECT test_run_id, MIN(time) AS first_time
      FROM sensor_readings
      GROUP BY test_run_id
    ) t0 ON t0.test_run_id = tr.id
    ORDER BY start_time DESC
    LIMIT %s
    """
    with get_conn(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (query_limit,))
        rows: Sequence[dict] = cur.fetchall()
    return [TestRunItem(**row) for row in rows]


def list_channels(
    limit: int | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[ChannelItem]:
    query_limit = max(1, min(limit or settings.default_limit, 5000000))
    sql = """
    SELECT
      id AS channel_id,
      channel_name,
      display_name,
      unit,
      sample_rate_hz,
      valid_min,
      valid_max
    FROM channels
    ORDER BY channel_name
    LIMIT %s
    """
    with get_conn(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (query_limit,))
        rows: Sequence[dict] = cur.fetchall()
    return [ChannelItem(**row) for row in rows]


def get_timeseries(
    test_run_ids: list[int],
    channel_names: list[str],
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = None,
    max_points: int | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[TimeSeriesPoint]:
    if not test_run_ids:
        return []
    if not channel_names:
        return []

    query_limit = max(1, min(limit or settings.default_limit, 5000000))
    sql = """
    SELECT
      sr.test_run_id,
      tr.run_code AS test_run_code,
      c.channel_name,
      c.unit,
      sr.time,
      sr.value
    FROM sensor_readings sr
    INNER JOIN channels c ON c.id = sr.channel_id
    INNER JOIN test_runs tr ON tr.id = sr.test_run_id
    WHERE sr.test_run_id = ANY(%s)
      AND c.channel_name = ANY(%s)
      AND (%s::timestamptz IS NULL OR sr.time >= %s::timestamptz)
      AND (%s::timestamptz IS NULL OR sr.time <= %s::timestamptz)
    ORDER BY sr.time ASC
    LIMIT %s
    """
    params = (test_run_ids, channel_names, start_time, start_time, end_time, end_time, query_limit)

    with get_conn(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows: Sequence[dict] = cur.fetchall()

    points = [TimeSeriesPoint(**row) for row in rows]

    if max_points and max_points > 0 and len(points) > max_points:
        points = _downsample_timeseries(points, max_points)

    return points


def list_channels_for_tests(
    test_run_ids: list[int],
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[ChannelItem]:
    if not test_run_ids:
        return []
    sql = """
    SELECT DISTINCT
      c.id AS channel_id,
      c.channel_name,
      c.display_name,
      c.unit,
      c.sample_rate_hz,
      c.valid_min,
      c.valid_max
    FROM sensor_readings sr
    INNER JOIN channels c ON c.id = sr.channel_id
    WHERE sr.test_run_id = ANY(%s)
    ORDER BY c.channel_name
    """
    with get_conn(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (test_run_ids,))
        rows: Sequence[dict] = cur.fetchall()
    return [ChannelItem(**row) for row in rows]


def list_test_metadata(
    test_run_ids: list[int],
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[dict]:
    if not test_run_ids:
        return []
    sql = """
    SELECT tr.*
    FROM test_runs tr
    WHERE tr.id = ANY(%s)
    ORDER BY tr.start_time DESC NULLS LAST, tr.id DESC
    """
    with get_conn(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (test_run_ids,))
        rows: Sequence[dict] = cur.fetchall()
    return list(rows)
