from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.engine.postgres_source import (
    bucket_interval_seconds,
    engine_enabled,
    fetch_postgres_timeseries,
    fetch_timeseries_aggregate,
)
from app.engine.query_planner import resolve_fetch_strategy
from app.models import TimeSeriesPoint
from app.services.timeseries import _downsample_timeseries

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_bucket_interval_seconds_scales_with_duration():
    assert bucket_interval_seconds(100.0, 100) == 1.0
    assert bucket_interval_seconds(10.0, 10_000) == 0.001


def test_resolve_fetch_strategy_overview_aggregate():
    assert resolve_fetch_strategy(mode="overview", aggregation_mode="auto", max_points=1400) == "aggregate"


def test_resolve_fetch_strategy_detail_lttb():
    assert resolve_fetch_strategy(mode="detail", aggregation_mode="auto", max_points=2800) == "raw_lttb"


def test_resolve_fetch_strategy_raw_mode():
    assert resolve_fetch_strategy(mode="raw", aggregation_mode="auto", max_points=1000) == "raw"


def test_engine_enabled_defaults_on():
    with patch.dict("os.environ", {}, clear=True):
        assert engine_enabled() is True


def test_engine_enabled_opt_out_env():
    with patch.dict("os.environ", {"NOVA_LEGACY_ROW_ENGINE": "1"}, clear=True):
        assert engine_enabled() is False
    with patch.dict("os.environ", {"NOVA_USE_ENGINE": "0"}, clear=True):
        assert engine_enabled() is False
    with patch.dict("os.environ", {"NOVA_USE_ENGINE": "1"}, clear=True):
        assert engine_enabled() is True


def test_fetch_timeseries_aggregate_executes_bucket_sql():
    mock_row = {
        "test_run_id": 1,
        "test_run_code": "run-1",
        "channel_name": "ch_a",
        "unit": "psi",
        "time": BASE,
        "value": 15.0,
    }
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"tmin": 0.0, "tmax": 100.0}
    mock_cursor.fetchall.return_value = [mock_row]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("app.engine.postgres_source.get_conn") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        points = fetch_timeseries_aggregate(
            [1],
            ["ch_a"],
            start_time=None,
            end_time=None,
            max_points_per_series=50,
            db_name="test_db",
        )

    assert len(points) == 1
    assert points[0].value == 15.0
    execute_calls = [c[0] for c in mock_cursor.execute.call_args_list]
    assert any("avg(sr.value)" in str(call) for call in execute_calls)
    assert any("GROUP BY" in str(call) for call in execute_calls)


def test_fetch_postgres_overview_uses_aggregate():
    with patch("app.engine.postgres_source.fetch_timeseries_aggregate") as mock_agg:
        mock_agg.return_value = []
        fetch_postgres_timeseries(
            [1],
            ["ch_a"],
            max_points=500,
            mode="overview",
            db_name="db",
        )
        mock_agg.assert_called_once()


def test_fetch_postgres_detail_uses_raw_then_lttb():
    dense = [
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="r",
            channel_name="ch",
            unit=None,
            time=BASE + timedelta(seconds=i),
            value=float(i),
        )
        for i in range(20)
    ]
    with patch("app.engine.postgres_source.fetch_timeseries_raw", return_value=dense):
        out = fetch_postgres_timeseries(
            [1],
            ["ch"],
            max_points=5,
            mode="detail",
            db_name="db",
        )
    assert len(out) <= 5
    manual = _downsample_timeseries(dense, 5)
    assert len(out) == len(manual)


def test_fetch_postgres_raw_strategy_no_aggregate():
    with patch("app.engine.postgres_source.fetch_timeseries_raw") as mock_raw:
        mock_raw.return_value = []
        fetch_postgres_timeseries([1], ["ch"], mode="raw", db_name="db")
        mock_raw.assert_called_once()
        assert mock_raw.call_args.kwargs.get("per_series_limit") is None


def test_get_timeseries_routes_through_engine_by_default():
    from app.services.timeseries import get_timeseries

    with patch.dict("os.environ", {}, clear=True):
        with patch("app.engine.postgres_source.fetch_postgres_timeseries") as mock_fetch:
            mock_fetch.return_value = []
            get_timeseries([1], ["ch_a"], max_points=500, db_name="test_db")
            mock_fetch.assert_called_once()
            assert mock_fetch.call_args.kwargs["mode"] == "overview"
