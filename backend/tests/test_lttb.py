from datetime import datetime, timedelta, timezone

import pytest

from app.services.timeseries import _lttb_series
from app.models import TimeSeriesPoint

_BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_pts(values: list[float], channel: str = "ch") -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="run1",
            channel_name=channel,
            time=_BASE + timedelta(seconds=i),
            value=v,
        )
        for i, v in enumerate(values)
    ]


def test_lttb_identity_when_under_threshold():
    pts = _make_pts([float(i) for i in range(5)])
    result = _lttb_series(pts, 10)
    assert result == pts


def test_lttb_identity_when_equal_threshold():
    pts = _make_pts([float(i) for i in range(10)])
    result = _lttb_series(pts, 10)
    assert result == pts


def test_lttb_reduces_to_threshold():
    pts = _make_pts([float(i % 10) for i in range(100)])
    result = _lttb_series(pts, 10)
    assert len(result) == 10


def test_lttb_keeps_first_and_last():
    pts = _make_pts([float(i) for i in range(50)])
    result = _lttb_series(pts, 10)
    assert result[0] == pts[0]
    assert result[-1] == pts[-1]


def test_lttb_minimum_threshold_of_2():
    pts = _make_pts([float(i) for i in range(20)])
    result = _lttb_series(pts, 2)
    assert len(result) == 2
    assert result[0] == pts[0]
    assert result[-1] == pts[-1]


def test_lttb_empty_input():
    assert _lttb_series([], 10) == []


def test_lttb_single_point():
    pts = _make_pts([1.0])
    assert _lttb_series(pts, 10) == pts
