"""Polars/Arrow helpers for series query transforms."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import polars as pl
import pyarrow as pa

from ..models import TimeSeriesPoint


SERIES_COLUMNS = (
    "test_run_id",
    "test_run_code",
    "channel_name",
    "unit",
    "x_ms",
    "y",
)


def empty_series_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "test_run_id": pl.Int32,
            "test_run_code": pl.Utf8,
            "channel_name": pl.Utf8,
            "unit": pl.Utf8,
            "x_ms": pl.Float64,
            "y": pl.Float64,
        }
    )


def frame_from_xy(
    *,
    test_run_id: int,
    test_run_code: str,
    channel_name: str,
    unit: str | None,
    xs: list[float],
    ys: list[float],
) -> pl.DataFrame:
    n = len(xs)
    if n == 0:
        return empty_series_frame()
    return pl.DataFrame(
        {
            "test_run_id": [int(test_run_id)] * n,
            "test_run_code": [str(test_run_code)] * n,
            "channel_name": [str(channel_name)] * n,
            "unit": [unit] * n,
            "x_ms": xs,
            "y": ys,
        }
    )


def concat_frames(frames: list[pl.DataFrame]) -> pl.DataFrame:
    nonempty = [f for f in frames if f is not None and f.height > 0]
    if not nonempty:
        return empty_series_frame()
    return pl.concat(nonempty, how="vertical_relaxed").sort(
        ["test_run_id", "channel_name", "x_ms"]
    )


def points_from_frame(df: pl.DataFrame) -> list[TimeSeriesPoint]:
    if df.is_empty():
        return []
    rows = df.select(list(SERIES_COLUMNS)).to_dicts()
    out: list[TimeSeriesPoint] = []
    for row in rows:
        x_ms = float(row["x_ms"])
        out.append(
            TimeSeriesPoint(
                test_run_id=int(row["test_run_id"]),
                test_run_code=str(row["test_run_code"]),
                channel_name=str(row["channel_name"]),
                unit=row.get("unit"),
                time=datetime.fromtimestamp(x_ms / 1000.0, tz=timezone.utc),
                value=float(row["y"]),
            )
        )
    return out


def frame_from_points(points: list[TimeSeriesPoint]) -> pl.DataFrame:
    if not points:
        return empty_series_frame()
    return pl.DataFrame(
        {
            "test_run_id": [p.test_run_id for p in points],
            "test_run_code": [p.test_run_code for p in points],
            "channel_name": [p.channel_name for p in points],
            "unit": [p.unit for p in points],
            "x_ms": [
                (p.time if p.time.tzinfo else p.time.replace(tzinfo=timezone.utc)).timestamp()
                * 1000.0
                for p in points
            ],
            "y": [float(p.value) for p in points],
        }
    ).sort(["test_run_id", "channel_name", "x_ms"])


def frame_to_arrow(df: pl.DataFrame) -> pa.Table:
    if df.is_empty():
        return pa.table(
            {
                "test_run_id": pa.array([], type=pa.int32()),
                "test_run_code": pa.array([], type=pa.string()),
                "channel_name": pa.array([], type=pa.string()),
                "unit": pa.array([], type=pa.null()),
                "x_ms": pa.array([], type=pa.float64()),
                "y": pa.array([], type=pa.float64()),
            }
        )
    return df.select(list(SERIES_COLUMNS)).to_arrow()


def series_payload_from_frame(df: pl.DataFrame) -> list[dict[str, Any]]:
    """Columnar series list for format=series JSON responses."""
    if df.is_empty():
        return []
    out: list[dict[str, Any]] = []
    for (tid, code, ch, unit), group in df.group_by(
        ["test_run_id", "test_run_code", "channel_name", "unit"], maintain_order=True
    ):
        g = group.sort("x_ms")
        out.append(
            {
                "test_run_id": int(tid),
                "test_run_code": str(code),
                "channel_name": str(ch),
                "unit": unit,
                "x_ms": g["x_ms"].to_list(),
                "y": g["y"].to_list(),
            }
        )
    return out


def apply_rolling_polars(
    df: pl.DataFrame,
    *,
    source_channel: str,
    name: str,
    unit: str | None,
    op: str,
    window: int,
) -> pl.DataFrame:
    """Vectorized rolling calc for a single source channel, per test_run_id."""
    w = max(1, int(window))
    op_l = (op or "mean").strip().lower()
    src = df.filter(pl.col("channel_name") == source_channel).sort(
        ["test_run_id", "x_ms"]
    )
    if src.is_empty():
        return empty_series_frame()

    if op_l == "mean":
        expr = pl.col("y").rolling_mean(window_size=w, min_samples=1)
    elif op_l == "sum":
        expr = pl.col("y").rolling_sum(window_size=w, min_samples=1)
    elif op_l == "min":
        expr = pl.col("y").rolling_min(window_size=w, min_samples=1)
    elif op_l == "max":
        expr = pl.col("y").rolling_max(window_size=w, min_samples=1)
    elif op_l == "std":
        expr = pl.col("y").rolling_std(window_size=w, min_samples=1)
    else:
        raise ValueError(f"Unsupported rolling op: {op}")

    rolled = (
        src.with_columns(expr.over("test_run_id").alias("y"))
        .with_columns(
            pl.lit(name).alias("channel_name"),
            pl.lit(unit).alias("unit"),
        )
        .drop_nulls(["y"])
        .filter(pl.col("y").is_finite())
    )
    return rolled.select(list(SERIES_COLUMNS))
