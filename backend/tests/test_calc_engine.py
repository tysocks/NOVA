from datetime import datetime, timedelta, timezone

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


def test_calc_graph_orders_dependencies():
    specs = [
        CalculatedChannelSpec(kind="formula", name="B", channels=["A", "raw"], formula="A + 1"),
        CalculatedChannelSpec(kind="rolling", name="A", channels=["raw"], op="mean", window=2),
    ]
    ordered = order_calculated_channels(specs)
    assert ordered[0].name == "A"
    assert ordered[1].name == "B"
