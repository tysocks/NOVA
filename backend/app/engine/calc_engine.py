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
    out: list[TimeSeriesPoint] = []
    op = (spec.op or "mean").strip().lower()
    window = max(1, int(spec.window or 1))

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

    def __init__(self, expr: str, var_count: int, band_pass: _BandPassState) -> None:
        self.band_pass = band_pass
        self.vars = [chr(ord("A") + i) for i in range(var_count)]
        cleaned = expr.strip()
        if cleaned.startswith("="):
            cleaned = cleaned[1:].strip()
        cleaned = cleaned.upper()
        cleaned = re.sub(r"\bBAND_PASS_FILTER\b", "band_pass_filter", cleaned)
        for name in self.ALLOWED_NAMES:
            cleaned = re.sub(rf"\b{name}\b", f"__{name.lower()}__", cleaned)
        for i, letter in enumerate(self.vars):
            cleaned = re.sub(rf"\b{letter}\b", f"v[{i}]", cleaned)
        self.code = compile(cleaned, "<formula>", "eval")

    def eval_row(self, values: list[float], dt_s: float, bp_index: int) -> float | None:
        env: dict[str, Any] = {"v": values, "math": math}

        def band_pass_filter(x: float, low: float, high: float) -> float:
            return self.band_pass.filter(float(x), float(low), float(high), f"bp{bp_index}", dt_s)

        for name, fn in self.ALLOWED_NAMES.items():
            env[f"__{name.lower()}__"] = fn
        env["band_pass_filter"] = band_pass_filter
        try:
            result = eval(self.code, {"__builtins__": {}}, env)
            val = float(result)
            return val if math.isfinite(val) else None
        except Exception:
            return None


def _eval_formula(base: list[TimeSeriesPoint], spec: CalculatedChannelSpec) -> list[TimeSeriesPoint]:
    if len(spec.channels or []) < 2 or not spec.formula:
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

    try:
        evaluator = _FormulaEvaluator(spec.formula, len(dep_names), _BandPassState())
    except Exception as exc:
        raise ValueError(f"Invalid formula: {exc}") from exc

    out: list[TimeSeriesPoint] = []
    prev_ms: float | None = None
    bp_i = 0
    for t_key in sorted(by_time.keys(), key=lambda k: by_time[k]["ts"]):
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
