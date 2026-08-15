"""Vectorized calculated channel evaluation (Phase 5)."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

from ..models import CalculatedChannelSpec, TimeSeriesPoint


def _series_key(pt: TimeSeriesPoint) -> tuple[int, str]:
    return (pt.test_run_id, pt.channel_name)


def _group_series(points: list[TimeSeriesPoint]) -> dict[tuple[int, str], list[TimeSeriesPoint]]:
    grouped: dict[tuple[int, str], list[TimeSeriesPoint]] = defaultdict(list)
    for pt in points:
        grouped[_series_key(pt)].append(pt)
    for key in grouped:
        grouped[key].sort(key=lambda p: p.time)
    return grouped


def _rolling_op(op: str, arr: np.ndarray, window: int) -> np.ndarray:
    w = max(1, int(window))
    if arr.size == 0:
        return arr
    if op == "mean":
        kernel = np.ones(w, dtype=float) / w
        return np.convolve(arr, kernel, mode="same")
    if op == "sum":
        kernel = np.ones(w, dtype=float)
        return np.convolve(arr, kernel, mode="same")
    if op == "min":
        out = np.empty_like(arr)
        for i in range(arr.size):
            out[i] = np.min(arr[max(0, i - w + 1) : i + 1])
        return out
    if op == "max":
        out = np.empty_like(arr)
        for i in range(arr.size):
            out[i] = np.max(arr[max(0, i - w + 1) : i + 1])
        return out
    if op == "std":
        out = np.full(arr.shape, np.nan)
        for i in range(arr.size):
            sl = arr[max(0, i - w + 1) : i + 1]
            out[i] = float(np.std(sl)) if sl.size > 1 else 0.0
        return out
    raise ValueError(f"Unsupported rolling op: {op}")


def _eval_rolling(
    base: list[TimeSeriesPoint],
    spec: CalculatedChannelSpec,
    grouped: dict[tuple[int, str], list[TimeSeriesPoint]],
) -> list[TimeSeriesPoint]:
    if not spec.channels:
        return []
    src_name = spec.channels[0].split("|")[-1] if "|" in spec.channels[0] else spec.channels[0]
    op = (spec.op or "mean").strip().lower()
    window = max(1, int(spec.window or 1))
    _ = grouped

    # Prefer Polars rolling transforms for the hot path.
    try:
        from .polars_series import apply_rolling_polars, frame_from_points, points_from_frame

        df = frame_from_points(base)
        rolled = apply_rolling_polars(
            df,
            source_channel=src_name,
            name=spec.name,
            unit=spec.unit,
            op=op,
            window=window,
        )
        return points_from_frame(rolled)
    except Exception:
        pass

    out: list[TimeSeriesPoint] = []
    by_test: dict[tuple[int, str], list[TimeSeriesPoint]] = defaultdict(list)
    for pt in base:
        if pt.channel_name != src_name:
            continue
        by_test[(pt.test_run_id, pt.test_run_code)].append(pt)

    for (_tid, run_code), rows in by_test.items():
        rows.sort(key=lambda p: p.time)
        vals = np.array([float(r.value) for r in rows], dtype=float)
        rolled = _rolling_op(op, vals, window)
        for row, v in zip(rows, rolled):
            if not np.isfinite(v):
                continue
            out.append(
                TimeSeriesPoint(
                    test_run_id=row.test_run_id,
                    test_run_code=row.test_run_code,
                    channel_name=spec.name,
                    unit=spec.unit,
                    time=row.time,
                    value=float(v),
                )
            )
    return out


class _BandPassState:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, float]] = {}

    def filter(self, x: float, low: float, high: float, key: str, dt_s: float) -> float:
        if not all(map(math.isfinite, (x, low, high))) or low <= 0 or high <= 0 or high <= low:
            return x
        dt = max(1e-6, dt_s)
        st = self.states.get(key) or {"lp_high": x, "lp_low": x}
        tau_high = 1.0 / (2.0 * math.pi * high)
        tau_low = 1.0 / (2.0 * math.pi * low)
        a_high = dt / (tau_high + dt)
        a_low = dt / (tau_low + dt)
        st["lp_high"] = st["lp_high"] + a_high * (x - st["lp_high"])
        st["lp_low"] = st["lp_low"] + a_low * (x - st["lp_low"])
        self.states[key] = st
        return st["lp_high"] - st["lp_low"]


class _RollingWindowState:
    """Causal trailing-window rolling stats for formula functions."""

    def __init__(self) -> None:
        self.bufs: dict[str, list[float]] = {}

    @staticmethod
    def _reduce(op: str, buf: list[float]) -> float:
        if not buf:
            return float("nan")
        if op in ("mean", "smooth"):
            return sum(buf) / len(buf)
        if op == "sum":
            return sum(buf)
        if op == "min":
            return min(buf)
        if op == "max":
            return max(buf)
        if op == "std":
            m = sum(buf) / len(buf)
            var = sum((v - m) * (v - m) for v in buf) / len(buf)
            return math.sqrt(var)
        if op == "rms":
            return math.sqrt(sum(v * v for v in buf) / len(buf))
        if op == "peak":
            return max(abs(v) for v in buf)
        return float("nan")

    def apply(self, op: str, x: float, window: int, key: str) -> float:
        n = max(1, int(window))
        if not math.isfinite(x):
            return float("nan")
        buf = self.bufs.setdefault(key, [])
        buf.append(float(x))
        if len(buf) > n:
            del buf[:-n]
        return self._reduce(op, buf)


class _TrapzState:
    """Cumulative trapezoidal integral for TRAPZ()."""

    def __init__(self) -> None:
        self.states: dict[str, dict[str, float]] = {}

    def integrate(self, x: float, dt_s: float, key: str) -> float:
        st = self.states.setdefault(key, {"prev_x": float("nan"), "sum": 0.0})
        if not math.isfinite(x):
            return st["sum"]
        if math.isfinite(st["prev_x"]) and dt_s > 0:
            st["sum"] += (st["prev_x"] + float(x)) * 0.5 * dt_s
        st["prev_x"] = float(x)
        return st["sum"]


def _finite_minmax(xs: list[float]) -> tuple[float, float] | None:
    finite = [x for x in xs if math.isfinite(x)]
    if not finite:
        return None
    return min(finite), max(finite)


def _threshold_pair(lo: float, hi: float, xs: list[float]) -> tuple[float, float] | None:
    """Map (lo, hi) to absolute levels. Values in [0, 1] with lo < hi are span fractions."""
    span = _finite_minmax(xs)
    if span is None:
        return None
    ymin, ymax = span
    width = ymax - ymin
    if 0 <= lo <= 1 and 0 <= hi <= 1 and lo < hi:
        if width <= 0:
            return None
        return ymin + lo * width, ymin + hi * width
    if lo < hi:
        return float(lo), float(hi)
    return None


def _times_from_dts(dts: list[float]) -> list[float]:
    t = 0.0
    out: list[float] = []
    for dt in dts:
        t += max(0.0, float(dt) if math.isfinite(dt) else 0.0)
        out.append(t)
    return out


def rise_series(xs: list[float], dts: list[float], lo: float = 0.1, hi: float = 0.9) -> list[float]:
    """Seconds from lo→hi crossing on each rising step; holds the last completed value."""
    n = len(xs)
    out = [0.0] * n
    levels = _threshold_pair(lo, hi, xs)
    if not levels or n == 0:
        return out
    lo_abs, hi_abs = levels
    times = _times_from_dts(dts if len(dts) == n else [0.0] * n)
    t0: float | None = None
    last = 0.0
    prev = float("nan")
    for i, x in enumerate(xs):
        if not math.isfinite(x):
            out[i] = last
            continue
        t = times[i]
        if t0 is None:
            if (not math.isfinite(prev) or prev < lo_abs) and x >= lo_abs:
                t0 = t
        else:
            if x >= hi_abs:
                last = max(0.0, t - t0)
                t0 = None
            elif x < lo_abs:
                t0 = None
        out[i] = last
        prev = x
    return out


def fall_series(xs: list[float], dts: list[float], lo: float = 0.1, hi: float = 0.9) -> list[float]:
    """Seconds from hi→lo crossing on each falling step; holds the last completed value."""
    n = len(xs)
    out = [0.0] * n
    levels = _threshold_pair(lo, hi, xs)
    if not levels or n == 0:
        return out
    lo_abs, hi_abs = levels
    times = _times_from_dts(dts if len(dts) == n else [0.0] * n)
    t0: float | None = None
    last = 0.0
    prev = float("nan")
    for i, x in enumerate(xs):
        if not math.isfinite(x):
            out[i] = last
            continue
        t = times[i]
        if t0 is None:
            if (not math.isfinite(prev) or prev > hi_abs) and x <= hi_abs:
                t0 = t
        else:
            if x <= lo_abs:
                last = max(0.0, t - t0)
                t0 = None
            elif x > hi_abs:
                t0 = None
        out[i] = last
        prev = x
    return out


def settling_series(
    xs: list[float],
    dts: list[float],
    band: float = 0.02,
    hold_s: float = 0.0,
) -> list[float]:
    """Seconds from leaving the start band until staying within the final band."""
    n = len(xs)
    out = [0.0] * n
    span = _finite_minmax(xs)
    if not span or n == 0:
        return out
    finite = [x for x in xs if math.isfinite(x)]
    initial, final = finite[0], finite[-1]
    width = abs(final - initial)
    if width <= 0:
        width = span[1] - span[0]
    if 0 < band <= 1:
        band_abs = band * width if width > 0 else 0.0
    else:
        band_abs = abs(float(band))
    hold = max(0.0, float(hold_s) if math.isfinite(hold_s) else 0.0)
    times = _times_from_dts(dts if len(dts) == n else [0.0] * n)
    t0: float | None = None
    in_since: float | None = None
    last = 0.0
    settled = False
    for i, x in enumerate(xs):
        if not math.isfinite(x):
            out[i] = last
            continue
        t = times[i]
        if t0 is None and abs(x - initial) > band_abs:
            t0 = t
        if t0 is not None and not settled:
            if abs(x - final) <= band_abs:
                if in_since is None:
                    in_since = t
                if (t - in_since) >= hold:
                    last = max(0.0, t - t0)
                    settled = True
            else:
                in_since = None
        elif settled and abs(x - final) > band_abs:
            settled = False
            in_since = None
        out[i] = last
    return out


class _StepAnalysisState:
    """Scan-then-apply state for RISE / FALL / SETTLING formula functions."""

    def __init__(self) -> None:
        self.calls: dict[str, dict[str, Any]] = {}
        self.out: dict[str, list[float]] = {}

    def record(
        self,
        key: str,
        kind: str,
        x: float,
        dt_s: float,
        lo: float,
        hi: float,
        band: float,
        hold_s: float,
    ) -> None:
        slot = self.calls.setdefault(
            key,
            {
                "kind": kind,
                "xs": [],
                "dts": [],
                "lo": lo,
                "hi": hi,
                "band": band,
                "hold_s": hold_s,
            },
        )
        slot["xs"].append(float(x) if math.isfinite(x) else float("nan"))
        slot["dts"].append(max(0.0, float(dt_s) if math.isfinite(dt_s) else 0.0))

    def finalize(self) -> None:
        self.out = {}
        for key, slot in self.calls.items():
            kind = slot["kind"]
            xs = slot["xs"]
            dts = slot["dts"]
            if kind == "rise":
                self.out[key] = rise_series(xs, dts, slot["lo"], slot["hi"])
            elif kind == "fall":
                self.out[key] = fall_series(xs, dts, slot["lo"], slot["hi"])
            else:
                self.out[key] = settling_series(xs, dts, slot["band"], slot["hold_s"])

    def result(self, key: str, index: int) -> float:
        arr = self.out.get(key) or []
        if 0 <= index < len(arr):
            val = arr[index]
            return float(val) if math.isfinite(val) else 0.0
        return 0.0


class _FormulaEvaluator:
    ALLOWED_NAMES = {
        "ABS": abs,
        "SQRT": math.sqrt,
        "POW": pow,
        "EXP": math.exp,
        "LOG": math.log,
        "LOG10": math.log10,
        "SIN": math.sin,
        "COS": math.cos,
        "TAN": math.tan,
        "ASIN": math.asin,
        "ACOS": math.acos,
        "ATAN": math.atan,
        "ROUND": round,
        "FLOOR": math.floor,
        "CEIL": math.ceil,
        "MIN": min,
        "MAX": max,
        "CLAMP": lambda x, lo, hi: min(max(x, lo), hi),
    }
    # Formula token -> rolling op name (trailing window of N samples).
    ROLLING_FUNCS = {
        "SMOOTH": "smooth",
        "ROLLING_MEAN": "mean",
        "ROLLING_SUM": "sum",
        "ROLLING_MIN": "min",
        "ROLLING_MAX": "max",
        "ROLLING_STD": "std",
        "RMS": "rms",
        "PEAK": "peak",
    }

    STEP_FUNCS = ("RISE", "FALL", "SETTLING")

    def __init__(
        self,
        expr: str,
        var_count: int,
        band_pass: _BandPassState,
        rolling: _RollingWindowState,
        trapz: _TrapzState,
        step: _StepAnalysisState | None = None,
        scan: bool = False,
    ) -> None:
        self.band_pass = band_pass
        self.rolling = rolling
        self.trapz = trapz
        self.step = step
        self.scan = scan
        self.vars = [chr(ord("A") + i) for i in range(var_count)]
        cleaned = expr.strip()
        if cleaned.startswith("="):
            cleaned = cleaned[1:].strip()
        cleaned = cleaned.upper()
        cleaned = re.sub(r"\bBAND_PASS_FILTER\b", "band_pass_filter", cleaned)
        cleaned = re.sub(r"\bTRAPZ\b", "trapz", cleaned)
        cleaned = re.sub(r"\bSETTLING\b", "settling", cleaned)
        cleaned = re.sub(r"\bRISE\b", "rise", cleaned)
        cleaned = re.sub(r"\bFALL\b", "fall", cleaned)
        # Longer rolling tokens first so ROLLING_MEAN is not partially mishandled.
        for name in sorted(self.ROLLING_FUNCS.keys(), key=len, reverse=True):
            cleaned = re.sub(rf"\b{name}\b", f"rolling_{self.ROLLING_FUNCS[name]}", cleaned)
        for name in self.ALLOWED_NAMES:
            cleaned = re.sub(rf"\b{name}\b", f"__{name.lower()}__", cleaned)
        for i, letter in enumerate(self.vars):
            cleaned = re.sub(rf"\b{letter}\b", f"v[{i}]", cleaned)
        self.code = compile(cleaned, "<formula>", "eval")

    def eval_row(self, values: list[float], dt_s: float, bp_index: int) -> float | None:
        env: dict[str, Any] = {"v": values, "math": math}
        call_i = [0]

        def band_pass_filter(x: float, low: float, high: float) -> float:
            call_i[0] += 1
            return self.band_pass.filter(
                float(x), float(low), float(high), f"bp{call_i[0]}", dt_s
            )

        def trapz(x: float) -> float:
            call_i[0] += 1
            return self.trapz.integrate(float(x), dt_s, f"trapz{call_i[0]}")

        def _step_fn(kind: str):
            def fn(
                x: float,
                a: float | None = None,
                b: float | None = None,
            ) -> float:
                call_i[0] += 1
                key = f"{kind}{call_i[0]}"
                if kind == "settling":
                    band = 0.02 if a is None else float(a)
                    hold_s = 0.0 if b is None else float(b)
                    lo, hi = 0.1, 0.9
                else:
                    lo = 0.1 if a is None else float(a)
                    hi = 0.9 if b is None else float(b)
                    band, hold_s = 0.02, 0.0
                if self.scan and self.step is not None:
                    self.step.record(key, kind, float(x), dt_s, lo, hi, band, hold_s)
                    return 0.0
                if self.step is not None:
                    return self.step.result(key, bp_index)
                return 0.0

            return fn

        def _rolling_fn(op: str):
            def fn(x: float, window: float = 1) -> float:
                call_i[0] += 1
                return self.rolling.apply(op, float(x), int(window), f"{op}{call_i[0]}")

            return fn

        for name, fn in self.ALLOWED_NAMES.items():
            env[f"__{name.lower()}__"] = fn
        env["band_pass_filter"] = band_pass_filter
        env["trapz"] = trapz
        env["rise"] = _step_fn("rise")
        env["fall"] = _step_fn("fall")
        env["settling"] = _step_fn("settling")
        for op in set(self.ROLLING_FUNCS.values()):
            env[f"rolling_{op}"] = _rolling_fn(op)
        try:
            result = eval(self.code, {"__builtins__": {}}, env)
            val = float(result)
            return val if math.isfinite(val) else None
        except Exception:
            return None


def _eval_formula(base: list[TimeSeriesPoint], spec: CalculatedChannelSpec) -> list[TimeSeriesPoint]:
    if len(spec.channels or []) < 1 or not spec.formula:
        return []

    dep_names = []
    for ch in spec.channels:
        dep_names.append(ch.split("|")[-1] if "|" in ch else ch)

    by_time: dict[str, dict[str, Any]] = {}
    for dep_idx, dep_name in enumerate(dep_names):
        for pt in base:
            if pt.channel_name != dep_name:
                continue
            t_key = pt.time.isoformat()
            slot = by_time.setdefault(
                t_key,
                {"vals": [float("nan")] * len(dep_names), "sample": pt, "ts": pt.time.timestamp()},
            )
            slot["vals"][dep_idx] = float(pt.value)
            ts = pt.time.timestamp()
            if ts < slot["ts"]:
                slot["sample"] = pt
                slot["ts"] = ts

    formula_u = str(spec.formula or "").upper()
    needs_step = any(re.search(rf"\b{name}\b", formula_u) for name in _FormulaEvaluator.STEP_FUNCS)
    ordered_keys = sorted(by_time.keys(), key=lambda k: by_time[k]["ts"])

    def _make_evaluator(*, scan: bool, step: _StepAnalysisState | None) -> _FormulaEvaluator:
        try:
            return _FormulaEvaluator(
                spec.formula,
                len(dep_names),
                _BandPassState(),
                _RollingWindowState(),
                _TrapzState(),
                step=step,
                scan=scan,
            )
        except Exception as exc:
            raise ValueError(f"Invalid formula: {exc}") from exc

    def _run(evaluator: _FormulaEvaluator) -> list[TimeSeriesPoint]:
        out: list[TimeSeriesPoint] = []
        prev_ms: float | None = None
        bp_i = 0
        for t_key in ordered_keys:
            slot = by_time[t_key]
            if any(not math.isfinite(v) for v in slot["vals"]):
                continue
            curr_ms = slot["ts"] * 1000.0
            dt_s = 0.0 if prev_ms is None else max(0.0, (curr_ms - prev_ms) / 1000.0)
            prev_ms = curr_ms
            v = evaluator.eval_row(slot["vals"], dt_s, bp_i)
            bp_i += 1
            if v is None:
                continue
            sample = slot["sample"]
            out.append(
                TimeSeriesPoint(
                    test_run_id=sample.test_run_id,
                    test_run_code=sample.test_run_code,
                    channel_name=spec.name,
                    unit=spec.unit,
                    time=sample.time,
                    value=float(v),
                )
            )
        return out

    if needs_step:
        step = _StepAnalysisState()
        _run(_make_evaluator(scan=True, step=step))
        step.finalize()
        return _run(_make_evaluator(scan=False, step=step))
    return _run(_make_evaluator(scan=False, step=None))


def apply_calculated_channels(
    base_points: list[TimeSeriesPoint],
    specs: list[CalculatedChannelSpec],
) -> list[TimeSeriesPoint]:
    """Append calculated channel points to base series."""
    if not specs:
        return []
    grouped = _group_series(base_points)
    _ = grouped  # reserved for future dependency analysis
    derived: list[TimeSeriesPoint] = []
    for spec in specs:
        if spec.kind == "rolling":
            derived.extend(_eval_rolling(base_points, spec, grouped))
        elif spec.kind == "formula":
            derived.extend(_eval_formula(base_points, spec))
    return derived
