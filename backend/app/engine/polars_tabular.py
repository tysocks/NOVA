"""Polars-based tabular normalization for CSV / Parquet / Arrow ingest."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from ..services.file_sources import (
    csv_units_from_row,
    resolve_time_column_name,
    _arrow_table,
    _split_name_unit,
    _tabular_channel_units,
)


def _epoch_valid_expr(time_col: str) -> pl.Expr:
    """Drop unset/zero slots when an epoch-scale TIME buffer is partially filled."""
    mx = pl.col(time_col).max()
    mn = pl.col(time_col).min()
    return (
        pl.when(mx <= 1e8)
        .then(pl.lit(True))
        .when(mn >= 1e8)
        .then(pl.lit(True))
        .otherwise(pl.col(time_col) > 1e8)
    )


def _looks_relative_numeric(df: pl.DataFrame, time_col: str, *, force_unit: str | None) -> bool:
    """Elapsed-time numerics (e.g. time_s from 0) should be anchored to ingest wall clock."""
    try:
        stats = df.select(
            pl.col(time_col).cast(pl.Float64, strict=False).min().alias("mn"),
            pl.col(time_col).cast(pl.Float64, strict=False).max().alias("mx"),
        ).row(0)
        mn = float(stats[0]) if stats[0] is not None else 0.0
        mx = float(stats[1]) if stats[1] is not None else 0.0
    except Exception:
        return False
    name = str(time_col or "").strip().lower()
    if force_unit == "ms":
        # Absolute epoch ms are ~1e12+; elapsed ms stay much smaller.
        return mx < 1e11 and mn >= 0
    if force_unit in {"s", "us", "ns"}:
        # Forced absolute unit: only treat as relative when clearly elapsed-scale.
        if force_unit == "s":
            return mx <= 1e8 and mn >= 0
        if force_unit == "us":
            return mx < 1e14 and mn >= 0
        return mx < 1e17 and mn >= 0
    if name in {"time_s", "time", "t", "elapsed_s", "elapsed", "seconds", "sec"} and mx <= 1e8:
        return True
    return mx <= 1e8 and mn >= 0


def _epoch_datetime_us(raw: pl.Expr, *, scale_to_us: float) -> pl.Expr:
    """UTC datetime from numeric times, rounded to microseconds (keeps sub-second steps)."""
    return pl.from_epoch((raw * scale_to_us).round().cast(pl.Int64), time_unit="us")


def _numeric_times_to_datetime_expr(time_col: str, *, force_unit: str | None = None) -> pl.Expr:
    raw = pl.col(time_col).cast(pl.Float64, strict=False)
    mx = raw.max()
    mn = raw.min()
    span = mx - mn

    # Always go through microseconds. Casting float seconds to Int64 first
    # collapsed short relative series (e.g. 0.00, 0.02, …, 0.50 s) onto one stamp.
    if force_unit == "ms":
        return _epoch_datetime_us(raw, scale_to_us=1_000.0)
    if force_unit == "ns":
        return _epoch_datetime_us(raw, scale_to_us=0.001)
    if force_unit == "us":
        return _epoch_datetime_us(raw, scale_to_us=1.0)
    if force_unit == "s":
        return _epoch_datetime_us(raw, scale_to_us=1_000_000.0)

    return (
        pl.when(mx > 1e17)
        .then(_epoch_datetime_us(raw, scale_to_us=0.001))
        .when((mx > 1e14) & (span > 1e11))
        .then(_epoch_datetime_us(raw, scale_to_us=1.0))
        .when(mx > 1e14)
        .then(_epoch_datetime_us(raw, scale_to_us=0.001))
        .when(mx > 1e11)
        .then(_epoch_datetime_us(raw, scale_to_us=1_000.0))
        .otherwise(_epoch_datetime_us(raw, scale_to_us=1_000_000.0))
    )


def _apply_units_in_headers_polars(
    df: pl.DataFrame,
    *,
    skip_columns: set[str],
) -> tuple[pl.DataFrame, dict[str, str | None]]:
    rename: dict[str, str] = {}
    counts: dict[str, int] = {}
    unit_map: dict[str, str | None] = {}
    for col in df.columns:
        if col in skip_columns:
            continue
        base, unit = _split_name_unit(col)
        if not base or not unit:
            continue
        key = base
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            key = f"{base}_{counts[base]}"
        rename[col] = key
        unit_map[key] = unit
    if rename:
        df = df.rename(rename)
    return df, unit_map


def anchor_relative_time_to_ingest_utc(
    df: pl.DataFrame,
    *,
    was_relative: bool,
    t0_utc: datetime | None = None,
) -> tuple[pl.DataFrame, datetime]:
    """Shift relative __time__ so the first sample equals ingest wall-clock UTC."""
    if df.is_empty() or "__time__" not in df.columns:
        raise ValueError("Frame has no __time__ column to anchor.")
    t0 = t0_utc or datetime.now(timezone.utc)
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    if not was_relative:
        return df, t0
    min_t = df.select(pl.col("__time__").min()).item()
    if min_t is None:
        return df, t0
    if getattr(min_t, "tzinfo", None) is None:
        min_t = min_t.replace(tzinfo=timezone.utc)
    delta = t0 - min_t
    df = df.with_columns((pl.col("__time__") + delta).alias("__time__"))
    return df, t0


def _normalize_time_frame(
    df: pl.DataFrame,
    *,
    time_col: str | None,
    source_label: str,
    force_ms_for_x_ms: bool = False,
) -> tuple[pl.DataFrame, str, bool]:
    if df.is_empty():
        raise ValueError(f"{source_label} file has no data rows.")

    resolved_time_col = resolve_time_column_name(list(df.columns), time_col)
    if not resolved_time_col:
        raise ValueError(
            f"{source_label} requires a time column "
            "(timestamp_utc/time/timestamp/datetime/time_s/x_ms/TIME/SECONDS) "
            "or an explicit time_index_channel."
        )

    dtype = df.schema[resolved_time_col]
    was_relative = False
    if dtype.is_numeric():
        force_unit = "ms" if force_ms_for_x_ms and resolved_time_col == "x_ms" else None
        was_relative = _looks_relative_numeric(df, resolved_time_col, force_unit=force_unit)
        df = df.with_columns(
            _numeric_times_to_datetime_expr(resolved_time_col, force_unit=force_unit).alias("__time__")
        )
        df = df.filter(pl.col("__time__").is_not_null() & _epoch_valid_expr(resolved_time_col))
    elif dtype == pl.Datetime or str(dtype).startswith("Datetime"):
        df = df.with_columns(pl.col(resolved_time_col).dt.replace_time_zone("UTC").alias("__time__"))
        df = df.filter(pl.col("__time__").is_not_null())
    else:
        df = df.with_columns(
            pl.col(resolved_time_col)
            .cast(pl.Utf8, strict=False)
            .str.to_datetime(time_zone="UTC", strict=False, exact=False)
            .alias("__time__")
        )
        df = df.filter(pl.col("__time__").is_not_null())

    df = df.sort("__time__")
    if df.is_empty():
        raise ValueError(f"{source_label} time column '{resolved_time_col}' could not be parsed as timestamps.")
    return df, resolved_time_col, was_relative


def _strip_polars_columns(df: pl.DataFrame) -> pl.DataFrame:
    rename = {c: str(c).strip() for c in df.columns if str(c).strip() != str(c)}
    return df.rename(rename) if rename else df


def _read_csv_polars(
    file_path: str,
    *,
    units_in_row: bool,
    n_rows: int | None = None,
) -> pl.DataFrame:
    """Read a CSV the same way ingest does. ``n_rows`` limits data rows only."""
    try:
        read_kwargs: dict[str, Any] = {
            "infer_schema_length": 10000,
            "ignore_errors": False,
        }
        if units_in_row:
            read_kwargs["skip_rows_after_header"] = 1
        if n_rows is not None:
            read_kwargs["n_rows"] = int(n_rows)
        df = pl.read_csv(file_path, **read_kwargs)
    except Exception as exc:
        raise ValueError(f"Unable to read CSV contents: {exc}") from exc
    return _strip_polars_columns(df)


def _prepare_csv_frame(
    df: pl.DataFrame,
    file_path: str,
    *,
    units_in_headers: bool,
    units_in_row: bool,
    time_col: str | None,
) -> tuple[pl.DataFrame, dict[str, str | None], str, bool]:
    """Strip is already done. Resolve time, attach units, rename header units.

    Does not anchor relative time. Header units overwrite a units-row unit.
    """
    df, resolved_time_col, was_relative = _normalize_time_frame(
        df,
        time_col=time_col,
        source_label="CSV",
    )
    unit_map: dict[str, str | None] = {}
    if units_in_row:
        unit_map.update(csv_units_from_row(file_path, list(df.columns)))
    if units_in_headers:
        df, header_map = _apply_units_in_headers_polars(
            df,
            skip_columns={"__time__", resolved_time_col},
        )
        for col, unit in header_map.items():
            if unit:
                unit_map[col] = unit
    return df, unit_map, resolved_time_col, was_relative


def normalize_csv_polars(
    file_path: str,
    *,
    units_in_headers: bool = False,
    units_in_row: bool = False,
    time_col: str | None = None,
) -> tuple[pl.DataFrame, dict[str, str | None], str, datetime]:
    df = _read_csv_polars(file_path, units_in_row=units_in_row)
    df, unit_map, resolved_time_col, was_relative = _prepare_csv_frame(
        df,
        file_path,
        units_in_headers=units_in_headers,
        units_in_row=units_in_row,
        time_col=time_col,
    )
    df, t0_utc = anchor_relative_time_to_ingest_utc(df, was_relative=was_relative)
    return df, unit_map, resolved_time_col, t0_utc


def _clean_preview_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    text = str(unit).strip()
    return text or None


def _json_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _json_utc(value: Any) -> str | None:
    if value is None:
        return None
    dt = value
    if not isinstance(dt, datetime) and hasattr(value, "to_pydatetime"):
        dt = value.to_pydatetime()
    if not isinstance(dt, datetime):
        text = str(value).strip()
        return text or None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _preview_error(message: str, *, truncated_rows: bool = False) -> dict[str, Any]:
    return {
        "columns": [],
        "rows": [],
        "time_kind": None,
        "time_column": None,
        "sampled_rows": 0,
        "truncated_rows": truncated_rows,
        "truncated_cols": False,
        "column_count": 0,
        "warnings": [],
        "error": message,
        "schema_summary": f"CSV schema check failed: {message}",
    }


def _preview_rows(
    df: pl.DataFrame,
    columns: list[dict[str, Any]],
    *,
    time_kind: str,
) -> list[list[Any]]:
    series: list[list[Any]] = []
    for col in columns:
        if col["role"] == "time" and time_kind == "absolute":
            values = [_json_utc(v) for v in df.get_column("__time__").to_list()]
        else:
            values = [_json_number(v) for v in df.get_column(col["source_name"]).to_list()]
        series.append(values)
    if not series:
        return []
    return [list(items) for items in zip(*series)]


def preview_csv_table(
    file_path: str,
    *,
    units_in_headers: bool = False,
    units_in_row: bool = False,
    time_col: str | None = None,
    channels: dict[str, Any] | None = None,
    limit_rows: int = 50,
    limit_cols: int = 40,
) -> dict[str, Any]:
    """Sample a CSV with the ingest reader and return a display table.

    Reads ``limit_rows + 1`` data rows to detect truncation, then applies the
    same units-row skip, header rename, and time resolution as ingest. Relative
    time stays as the file's elapsed numbers. Known read/selection failures
    come back as ``error`` instead of raising.
    """
    from ..services.ingestion_rule_library import resolve_channel_selection

    limit_rows = max(1, min(int(limit_rows or 50), 200))
    limit_cols = max(1, min(int(limit_cols or 40), 80))
    truncated_rows = False
    try:
        raw = _read_csv_polars(
            file_path,
            units_in_row=units_in_row,
            n_rows=limit_rows + 1,
        )
        truncated_rows = raw.height > limit_rows
        if truncated_rows:
            raw = raw.head(limit_rows)
        df, unit_map, resolved_time_col, was_relative = _prepare_csv_frame(
            raw,
            file_path,
            units_in_headers=units_in_headers,
            units_in_row=units_in_row,
            time_col=time_col,
        )
        available: list[str] = []
        for col in df.columns:
            if col in {"__time__", resolved_time_col}:
                continue
            dtype = df.schema[col]
            if getattr(dtype, "is_numeric", lambda: False)():
                available.append(col)
        if not available:
            raise ValueError("No numeric channel columns found besides the time index.")
        if channels is not None:
            selected, mapping = resolve_channel_selection(available, channels)
        else:
            selected = available
            mapping = {name: name for name in available}

        time_kind = "relative" if was_relative else "absolute"
        time_meta = {
            "name": resolved_time_col,
            "unit": _clean_preview_unit(unit_map.get(resolved_time_col)),
            "source_name": resolved_time_col,
            "role": "time",
        }
        channel_columns: list[dict[str, Any]] = []
        for source in selected:
            channel_columns.append(
                {
                    "name": str(mapping.get(source, source)),
                    "unit": _clean_preview_unit(unit_map.get(source)),
                    "source_name": source,
                    "role": "channel",
                }
            )
        # limit_cols caps channel columns. Time stays first and is not part of the cap.
        column_count = 1 + len(channel_columns)
        truncated_cols = len(channel_columns) > limit_cols
        shown = [time_meta, *channel_columns[:limit_cols]]
        rows = _preview_rows(df, shown, time_kind=time_kind)
        warnings: list[str] = []
        if df.height < 2:
            warnings.append("CSV has fewer than 2 data rows.")
        schema_summary = "CSV file passed schema checks."
        if warnings:
            schema_summary += f" {len(warnings)} warning(s)."
        return {
            "columns": shown,
            "rows": rows,
            "time_kind": time_kind,
            "time_column": resolved_time_col,
            "sampled_rows": len(rows),
            "truncated_rows": truncated_rows,
            "truncated_cols": truncated_cols,
            "column_count": column_count,
            "warnings": warnings,
            "error": None,
            "schema_summary": schema_summary,
        }
    except ValueError as exc:
        return _preview_error(str(exc), truncated_rows=truncated_rows)


def normalize_tabular_polars(
    file_path: str,
    *,
    source_type: str,
    time_col: str | None = None,
    units_in_headers: bool = False,
) -> tuple[pl.DataFrame, dict[str, str | None], str, datetime]:
    try:
        if source_type == "parquet":
            df = pl.read_parquet(file_path)
        else:
            table = _arrow_table(file_path)
            df = pl.from_arrow(table)
    except Exception as exc:
        raise ValueError(f"Unable to read {source_type.upper()} contents: {exc}") from exc

    df, resolved_time_col, was_relative = _normalize_time_frame(
        df,
        time_col=time_col,
        source_label=source_type.upper(),
        force_ms_for_x_ms=True,
    )
    df, t0_utc = anchor_relative_time_to_ingest_utc(df, was_relative=was_relative)
    unit_map = _tabular_channel_units(file_path, source_type)
    if units_in_headers:
        df, header_unit_map = _apply_units_in_headers_polars(
            df,
            skip_columns={"__time__", resolved_time_col},
        )
        for col, unit in header_unit_map.items():
            if unit:
                unit_map[col] = unit
    return df, unit_map, resolved_time_col, t0_utc


def write_channel_parquets_from_polars(
    df: pl.DataFrame,
    out_dir: Path,
    *,
    unit_map: dict[str, str | None] | None = None,
    skip_columns: set[str] | None = None,
    include_columns: set[str] | None = None,
    channel_rename: dict[str, str] | None = None,
    sanitize_name,
) -> tuple[list[dict[str, Any]], dict[str, float] | None]:
    """Write per-channel Parquet files with timestamp_utc, x_ms, y."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    units = unit_map or {}
    excluded = skip_columns or set()
    include = include_columns
    rename = channel_rename or {}
    channel_rows: list[dict[str, Any]] = []
    tmin_ms: float | None = None
    tmax_ms: float | None = None

    with_ms = df.with_columns(
        (pl.col("__time__").dt.epoch("us").cast(pl.Float64) / 1000.0).alias("x_ms"),
        pl.col("__time__").alias("timestamp_utc"),
    )

    for col in with_ms.columns:
        if col in {"__time__", "x_ms", "timestamp_utc"} or col in excluded:
            continue
        if include is not None and col not in include:
            continue
        dtype = with_ms.schema[col]
        if not dtype.is_numeric():
            continue

        sub = (
            with_ms.select(
                pl.col("timestamp_utc"),
                pl.col("x_ms"),
                pl.col(col).cast(pl.Float64, strict=False).alias("y"),
            )
            .drop_nulls(subset=["x_ms", "y"])
        )
        if sub.is_empty():
            continue

        dest_name = str(rename.get(col, col))
        fname = sanitize_name(dest_name) + ".parquet"
        out_path = out_dir / fname
        unit = units.get(col)
        y_meta = {b"unit": str(unit).encode("utf-8")} if unit else None
        schema = pa.schema(
            [
                pa.field("timestamp_utc", pa.timestamp("us", tz="UTC")),
                pa.field("x_ms", pa.float64()),
                pa.field("y", pa.float64(), metadata=y_meta),
            ]
        )
        table = sub.to_arrow().cast(schema)
        pq.write_table(table, out_path)
        n = sub.height
        xs_min = float(sub["x_ms"].min())
        xs_max = float(sub["x_ms"].max())
        tmin_ms = xs_min if tmin_ms is None else min(tmin_ms, xs_min)
        tmax_ms = xs_max if tmax_ms is None else max(tmax_ms, xs_max)
        channel_rows.append(
            {
                "channel_name": dest_name,
                "source_name": col,
                "unit": unit,
                "parquet": f"data/{fname}",
                "point_count": n,
                "kind": "raw",
            }
        )

    if not channel_rows:
        raise ValueError("Ingest produced no numeric channels.")

    bounds = {"start_ms": tmin_ms, "end_ms": tmax_ms} if tmin_ms is not None else None
    return channel_rows, bounds
