from datetime import datetime, timedelta, timezone

from app.models import TimeSeriesPoint
from app.services.timeseries import _build_series_meta, plan_timeseries_points_cap


BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_plan_timeseries_points_cap_prefers_explicit_max_points():
    assert plan_timeseries_points_cap(resolution_px=800, aggregation_mode="auto", max_points=1234) == 1234


def test_plan_timeseries_points_cap_raw_mode_disables_downsample():
    assert plan_timeseries_points_cap(resolution_px=800, aggregation_mode="raw", max_points=None) is None


def test_plan_timeseries_points_cap_uses_resolution_for_auto():
    assert plan_timeseries_points_cap(resolution_px=700, aggregation_mode="auto", max_points=None) == 1400


def test_build_series_meta_groups_by_test_and_channel():
    pts = [
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
            unit="C",
            time=BASE + timedelta(seconds=2),
            value=30.0,
        ),
    ]
    meta = _build_series_meta(pts)
    keyed = {(m.test_run_id, m.channel_name): m for m in meta}
    assert keyed[(1, "ch_a")].points == 2
    assert keyed[(1, "ch_a")].min_value == 10.0
    assert keyed[(1, "ch_a")].max_value == 20.0
    assert keyed[(2, "ch_b")].points == 1
