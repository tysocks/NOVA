from datetime import datetime, timedelta, timezone

import pyarrow as pa

from app.engine.arrow_codec import (
    arrow_ipc_to_points,
    encode_series_arrow_ipc,
    points_to_arrow_table,
    series_point_counts,
)
from app.engine.query_planner import plan_points_cap
from app.models import TimeSeriesPoint

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _sample_points() -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="run-1",
            channel_name="ch_a",
            unit="psi",
            time=BASE,
            value=10.0,
        ),
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="run-1",
            channel_name="ch_a",
            unit="psi",
            time=BASE + timedelta(seconds=1),
            value=20.0,
        ),
        TimeSeriesPoint(
            test_run_id=2,
            test_run_code="run-2",
            channel_name="ch_b",
            unit=None,
            time=BASE + timedelta(seconds=2),
            value=30.0,
        ),
    ]


def test_points_to_arrow_table_empty():
    table = points_to_arrow_table([])
    assert table.num_rows == 0
    assert set(table.column_names) == {
        "test_run_id",
        "test_run_code",
        "channel_name",
        "unit",
        "x_ms",
        "y",
    }


def test_arrow_round_trip_preserves_values():
    original = _sample_points()
    ipc = encode_series_arrow_ipc(original)
    restored = arrow_ipc_to_points(ipc)
    assert len(restored) == len(original)
    for a, b in zip(restored, sorted(original, key=lambda p: (p.test_run_id, p.channel_name, p.time))):
        assert a.test_run_id == b.test_run_id
        assert a.test_run_code == b.test_run_code
        assert a.channel_name == b.channel_name
        assert a.unit == b.unit
        assert abs(a.value - b.value) < 1e-9
        assert abs(a.time.timestamp() - b.time.timestamp()) < 1e-6


def test_arrow_ipc_is_valid_stream():
    ipc = encode_series_arrow_ipc(_sample_points())
    reader = pa.ipc.open_stream(ipc)
    table = reader.read_all()
    assert table.num_rows == 3


def test_series_point_counts():
    counts = series_point_counts(_sample_points())
    assert counts[(1, "ch_a")] == 2
    assert counts[(2, "ch_b")] == 1


def test_plan_points_cap_detail_mode_higher_than_overview():
    overview = plan_points_cap(resolution_px=700, aggregation_mode="auto", max_points=None, mode="overview")
    detail = plan_points_cap(resolution_px=700, aggregation_mode="auto", max_points=None, mode="detail")
    assert overview == 1400
    assert detail == 2800


def test_plan_points_cap_raw_mode():
    assert plan_points_cap(resolution_px=800, aggregation_mode="auto", max_points=None, mode="raw") is None
