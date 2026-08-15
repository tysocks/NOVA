from datetime import datetime, timedelta, timezone
import math

from app.engine.calc_engine import apply_calculated_channels
from app.engine.calc_graph import order_calculated_channels
from app.models import CalculatedChannelSpec, TimeSeriesPoint

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _row(ch: str, val: float, sec: int, tid: int = 1) -> TimeSeriesPoint:
    return TimeSeriesPoint(
        test_run_id=tid,
        test_run_code="run-1",
        channel_name=ch,
        unit=None,
        time=BASE + timedelta(seconds=sec),
        value=val,
    )


def test_rolling_mean_calculation():
    base = [_row("A", float(i), i) for i in range(5)]
    specs = [
        CalculatedChannelSpec(
            kind="rolling",
            name="A_mean",
            channels=["A"],
            op="mean",
            window=3,
        )
    ]
    out = apply_calculated_channels(base, specs)
    assert len(out) == 5
    assert all(p.channel_name == "A_mean" for p in out)


def test_formula_smooth_single_channel():
    base = [_row("A", float(i), i) for i in range(5)]
    specs = [
        CalculatedChannelSpec(
            kind="formula",
            name="A_smooth",
            channels=["A"],
            formula="SMOOTH(A, 3)",
        )
    ]
    out = apply_calculated_channels(base, specs)
    assert len(out) == 5
    # Trailing window: at i=2 values are 0,1,2 → mean 1.0
    by_sec = {int((p.time - BASE).total_seconds()): p.value for p in out}
    assert by_sec[0] == 0.0
    assert by_sec[1] == 0.5
    assert by_sec[2] == 1.0
    assert by_sec[4] == 3.0


def test_formula_addition():
    base = [
        _row("A", 1.0, 0),
        _row("B", 2.0, 0),
        _row("A", 3.0, 1),
        _row("B", 4.0, 1),
    ]
    specs = [
        CalculatedChannelSpec(
            kind="formula",
            name="sumAB",
            channels=["A", "B"],
            formula="A + B",
        )
    ]
    out = apply_calculated_channels(base, specs)
    values = {p.time.isoformat(): p.value for p in out}
    assert values[BASE.isoformat()] == 3.0
    assert values[(BASE + timedelta(seconds=1)).isoformat()] == 7.0


def test_formula_trapz_integral():
    base = [_row("A", float(i), i) for i in range(4)]
    specs = [
        CalculatedChannelSpec(
            kind="formula",
            name="A_int",
            channels=["A"],
            formula="TRAPZ(A)",
        )
    ]
    out = apply_calculated_channels(base, specs)
    assert len(out) == 4
    values = [p.value for p in out]
    assert values[0] == 0.0
    assert values[-1] > values[0]


def test_formula_rms_window():
    base = [_row("A", float((i % 3) + 1), i) for i in range(6)]
    specs = [
        CalculatedChannelSpec(
            kind="formula",
            name="A_rms",
            channels=["A"],
            formula="RMS(A, 3)",
        )
    ]
    out = apply_calculated_channels(base, specs)
    assert len(out) == 6
    assert all(math.isfinite(p.value) for p in out)


def test_formula_peak_window():
    base = [_row("A", float((-1) ** i * (i + 1)), i) for i in range(5)]
    specs = [
        CalculatedChannelSpec(
            kind="formula",
            name="A_peak",
            channels=["A"],
            formula="PEAK(A, 3)",
        )
    ]
    out = apply_calculated_channels(base, specs)
    assert len(out) == 5
    assert max(p.value for p in out) >= 1.0


def test_formula_rise_time():
    # 10%→90% of 0..100 is 10→90; 1 Hz samples.
    vals = [0, 0, 0, 10, 50, 90, 100, 100, 100]
    base = [_row("A", float(v), i) for i, v in enumerate(vals)]
    specs = [
        CalculatedChannelSpec(kind="formula", name="A_rise", channels=["A"], formula="RISE(A)")
    ]
    out = apply_calculated_channels(base, specs)
    by_sec = {int((p.time - BASE).total_seconds()): p.value for p in out}
    assert by_sec[0] == 0.0
    assert by_sec[4] == 0.0
    assert by_sec[5] == 2.0
    assert by_sec[8] == 2.0


def test_formula_rise_absolute_levels():
    vals = [0, 0, 10, 50, 90, 100]
    base = [_row("A", float(v), i) for i, v in enumerate(vals)]
    specs = [
        CalculatedChannelSpec(
            kind="formula", name="A_rise", channels=["A"], formula="RISE(A, 10, 90)"
        )
    ]
    out = apply_calculated_channels(base, specs)
    by_sec = {int((p.time - BASE).total_seconds()): p.value for p in out}
    assert by_sec[4] == 2.0


def test_formula_fall_time():
    vals = [100, 100, 90, 50, 10, 0, 0]
    base = [_row("A", float(v), i) for i, v in enumerate(vals)]
    specs = [
        CalculatedChannelSpec(kind="formula", name="A_fall", channels=["A"], formula="FALL(A)")
    ]
    out = apply_calculated_channels(base, specs)
    by_sec = {int((p.time - BASE).total_seconds()): p.value for p in out}
    assert by_sec[0] == 0.0
    assert by_sec[4] == 2.0
    assert by_sec[6] == 2.0


def test_formula_settling_time():
    vals = [0, 0, 0, 80, 110, 95, 100, 100, 100]
    base = [_row("A", float(v), i) for i, v in enumerate(vals)]
    specs = [
        CalculatedChannelSpec(
            kind="formula", name="A_set", channels=["A"], formula="SETTLING(A)"
        )
    ]
    out = apply_calculated_channels(base, specs)
    by_sec = {int((p.time - BASE).total_seconds()): p.value for p in out}
    assert by_sec[0] == 0.0
    assert by_sec[5] == 0.0
    assert by_sec[6] == 3.0
    assert by_sec[8] == 3.0


def test_formula_settling_hold():
    vals = [0, 0, 80, 100, 100, 100]
    base = [_row("A", float(v), i) for i, v in enumerate(vals)]
    specs = [
        CalculatedChannelSpec(
            kind="formula", name="A_set", channels=["A"], formula="SETTLING(A, 0.02, 1)"
        )
    ]
    out = apply_calculated_channels(base, specs)
    by_sec = {int((p.time - BASE).total_seconds()): p.value for p in out}
    # Leaves 0 at t=2; enters final band at t=3; hold 1 s completes at t=4 → 2 s.
    assert by_sec[3] == 0.0
    assert by_sec[4] == 2.0


def test_calc_graph_orders_dependencies():
    specs = [
        CalculatedChannelSpec(kind="formula", name="B", channels=["A", "raw"], formula="A + 1"),
        CalculatedChannelSpec(kind="rolling", name="A", channels=["raw"], op="mean", window=2),
    ]
    ordered = order_calculated_channels(specs)
    assert ordered[0].name == "A"
    assert ordered[1].name == "B"
