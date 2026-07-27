"""Polars-based range detection rules for catalog ranges."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from ..models import RangeCreateRequest, RangeItem, RangeRuleItem
from .catalog_store import create_range, get_range_rule, list_channel_parquet_entries


def _load_channel_frame(parquet_uri: str) -> pl.DataFrame:
    path = Path(parquet_uri)
    if not path.is_file():
        raise ValueError(f"Channel parquet not found: {parquet_uri}")
    df = pl.read_parquet(path)
    if "x_ms" not in df.columns or "y" not in df.columns:
        raise ValueError(f"Channel parquet missing x_ms/y columns: {parquet_uri}")
    return df.select(
        pl.col("x_ms").cast(pl.Float64),
        pl.col("y").cast(pl.Float64, strict=False),
    ).drop_nulls().sort("x_ms")


def _ms_to_dt(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _parse_config(config: str) -> dict[str, Any]:
    try:
        data = json.loads(config)
    except Exception as exc:
        raise ValueError(f"Invalid range rule config JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Range rule config must be a JSON object.")
    return data


def _detect_threshold_ranges(df: pl.DataFrame, config: dict[str, Any]) -> list[tuple[float, float]]:
    op = str(config.get("op") or ">").strip()
    value = float(config.get("value"))
    sustain_ms = float(config.get("sustain_ms") or 0.0)

    if op == ">=":
        mask = pl.col("y") >= value
    elif op == ">":
        mask = pl.col("y") > value
    elif op == "<=":
        mask = pl.col("y") <= value
    elif op == "<":
        mask = pl.col("y") < value
    else:
        raise ValueError(f"Unsupported threshold op: {op}")

    flagged = df.with_columns(mask.alias("active"))
    rows = flagged.select(["x_ms", "active"]).to_dicts()
    ranges: list[tuple[float, float]] = []
    start: float | None = None
    prev_ms: float | None = None
    for row in rows:
        x_ms = float(row["x_ms"])
        active = bool(row["active"])
        if active and start is None:
            start = x_ms
        if not active and start is not None and prev_ms is not None:
            if (prev_ms - start) >= sustain_ms:
                ranges.append((start, prev_ms))
            start = None
        prev_ms = x_ms
    if start is not None and prev_ms is not None and (prev_ms - start) >= sustain_ms:
        ranges.append((start, prev_ms))
    return ranges


def _detect_edge_ranges(df: pl.DataFrame, config: dict[str, Any]) -> list[tuple[float, float]]:
    direction = str(config.get("direction") or "rising").strip().lower()
    threshold = float(config.get("threshold"))
    window_ms = float(config.get("window_ms") or 0.0)

    ys = df["y"].to_list()
    xs = df["x_ms"].to_list()
    ranges: list[tuple[float, float]] = []
    for i in range(1, len(ys)):
        prev_y = float(ys[i - 1])
        cur_y = float(ys[i])
        crossed = False
        if direction == "rising":
            crossed = prev_y < threshold <= cur_y
        elif direction == "falling":
            crossed = prev_y > threshold >= cur_y
        else:
            raise ValueError("edge direction must be 'rising' or 'falling'")
        if crossed:
            start = float(xs[i])
            end = start + max(0.0, window_ms)
            ranges.append((start, end if end > start else start + 1.0))
    return ranges


def _detect_formula_ranges(df: pl.DataFrame, config: dict[str, Any]) -> list[tuple[float, float]]:
    """Supported formulas: max / min over optional window, returning that sample as a point range."""
    expr = str(config.get("expr") or "max").strip().lower()
    window_ms = float(config.get("window_ms") or 0.0)
    if expr not in {"max", "min"}:
        raise ValueError("formula expr currently supports 'max' or 'min' only")
    if df.is_empty():
        return []
    if expr == "max":
        idx = int(df["y"].arg_max())
    else:
        idx = int(df["y"].arg_min())
    center = float(df["x_ms"][idx])
    half = max(0.0, window_ms) / 2.0
    start = center - half
    end = center + half if half > 0 else center + 1.0
    return [(start, end)]


def detect_ranges_for_rule(rule: RangeRuleItem, parquet_uri: str) -> list[tuple[float, float]]:
    df = _load_channel_frame(parquet_uri)
    config = _parse_config(rule.config)
    kind = rule.kind.strip().lower()
    if kind == "threshold":
        return _detect_threshold_ranges(df, config)
    if kind == "edge":
        return _detect_edge_ranges(df, config)
    if kind == "formula":
        return _detect_formula_ranges(df, config)
    raise ValueError(f"Unsupported range rule kind: {rule.kind}")


def apply_range_rule_to_test(test_id: int, rule_id: int) -> list[RangeItem]:
    rule = get_range_rule(rule_id)
    if not rule:
        raise ValueError(f"Range rule '{rule_id}' not found.")
    entries = list_channel_parquet_entries(test_id, [rule.channel_name])
    if not entries:
        raise ValueError(f"Channel '{rule.channel_name}' not found for test '{test_id}'.")
    parquet_uri = entries[0]["parquet_uri"]
    detected = detect_ranges_for_rule(rule, parquet_uri)
    created: list[RangeItem] = []
    for idx, (start_ms, end_ms) in enumerate(detected, start=1):
        name = rule.default_label or rule.name
        if len(detected) > 1:
            name = f"{name} {idx}"
        created.append(
            create_range(
                RangeCreateRequest(
                    test_id=test_id,
                    name=name,
                    label=rule.default_label,
                    color=rule.default_color,
                    start_time=_ms_to_dt(start_ms),
                    end_time=_ms_to_dt(end_ms),
                    source="rule",
                    rule_id=rule.rule_id,
                )
            )
        )
    return created
