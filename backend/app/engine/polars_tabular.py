"""Polars-based tabular normalization for CSV / Parquet / Arrow ingest."""

from __future__ import annotations

import polars as pl

from ..services.file_sources import (
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


def _numeric_times_to_datetime_expr(time_col: str, *, force_unit: str | None = None) -> pl.Expr:
    raw = pl.col(time_col).cast(pl.Float64, strict=False)
    mx = raw.max()
    mn = raw.min()
    span = mx - mn

    if force_unit == "ms":
        return pl.from_epoch(raw.cast(pl.Int64), time_unit="ms")
    if force_unit == "ns":
        return pl.from_epoch(raw.cast(pl.Int64), time_unit="ns")
    if force_unit == "us":
        return pl.from_epoch(raw.cast(pl.Int64), time_unit="us")
    if force_unit == "s":
        return pl.from_epoch(raw.cast(pl.Int64), time_unit="s")

    return (
        pl.when(mx > 1e17)
        .then(pl.from_epoch(raw.cast(pl.Int64), time_unit="ns"))
        .when((mx > 1e14) & (span > 1e11))
        .then(pl.from_epoch(raw.cast(pl.Int64), time_unit="us"))
        .when(mx > 1e14)
        .then(pl.from_epoch(raw.cast(pl.Int64), time_unit="ns"))
        .when(mx > 1e11)
        .then(pl.from_epoch(raw.cast(pl.Int64), time_unit="ms"))
        .when((mn >= 1e8) & (span <= 86400 * 366 * 50))
        .then(pl.from_epoch(raw.cast(pl.Int64), time_unit="s"))
        .otherwise(pl.from_epoch(raw.cast(pl.Int64), time_unit="s"))
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


def _normalize_time_frame(
    df: pl.DataFrame,
    *,
    time_col: str | None,
    source_label: str,
    force_ms_for_x_ms: bool = False,
) -> tuple[pl.DataFrame, str]:
    if df.is_empty():
        raise ValueError(f"{source_label} file has no data rows.")

    resolved_time_col = resolve_time_column_name(list(df.columns), time_col)
    if not resolved_time_col:
        raise ValueError(
            f"{source_label} requires a time column "
            "(timestamp_utc/time/timestamp/datetime/time_s/x_ms/TIME) "
            "or an explicit time_index_channel."
        )

    dtype = df.schema[resolved_time_col]
    if dtype.is_numeric():
        force_unit = "ms" if force_ms_for_x_ms and resolved_time_col == "x_ms" else None
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
    return df, resolved_time_col


def normalize_csv_polars(
    file_path: str,
    *,
    units_in_headers: bool = False,
    time_col: str | None = None,
) -> tuple[pl.DataFrame, dict[str, str | None], str]:
    try:
        df = pl.read_csv(file_path, infer_schema_length=10000, ignore_errors=False)
    except Exception as exc:
        raise ValueError(f"Unable to read CSV contents: {exc}") from exc

    df, resolved_time_col = _normalize_time_frame(
        df,
        time_col=time_col,
        source_label="CSV",
    )
    unit_map: dict[str, str | None] = {}
    if units_in_headers:
        df, unit_map = _apply_units_in_headers_polars(
            df,
            skip_columns={"__time__", resolved_time_col},
        )
    return df, unit_map, resolved_time_col


def normalize_tabular_polars(
    file_path: str,
    *,
    source_type: str,
    time_col: str | None = None,
    units_in_headers: bool = False,
) -> tuple[pl.DataFrame, dict[str, str | None], str]:
    try:
        if source_type == "parquet":
            df = pl.read_parquet(file_path)
        else:
            table = _arrow_table(file_path)
            df = pl.from_arrow(table)
    except Exception as exc:
        raise ValueError(f"Unable to read {source_type.upper()} contents: {exc}") from exc

    df, resolved_time_col = _normalize_time_frame(
        df,
        time_col=time_col,
        source_label=source_type.upper(),
        force_ms_for_x_ms=True,
    )
    unit_map = _tabular_channel_units(file_path, source_type)
    if units_in_headers:
        df, header_unit_map = _apply_units_in_headers_polars(
            df,
            skip_columns={"__time__", resolved_time_col},
        )
        for col, unit in header_unit_map.items():
            if unit:
                unit_map[col] = unit
    return df, unit_map, resolved_time_col


def write_channel_parquets_from_polars(
    df: pl.DataFrame,
    out_dir: Path,
    *,
    unit_map: dict[str, str | None] | None = None,
    skip_columns: set[str] | None = None,
    sanitize_name,
) -> tuple[list[dict[str, Any]], dict[str, float] | None]:
    """Write per-channel Parquet files with columns x_ms, y. Returns channel rows + bounds."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    units = unit_map or {}
    excluded = skip_columns or set()
    channel_rows: list[dict[str, Any]] = []
    tmin_ms: float | None = None
    tmax_ms: float | None = None

    # Convert Datetime[us, UTC] -> epoch ms
    with_ms = df.with_columns(
        (pl.col("__time__").dt.epoch("ms").cast(pl.Float64)).alias("x_ms")
    )

    for col in with_ms.columns:
        if col in {"__time__", "x_ms"} or col in excluded:
            continue
        dtype = with_ms.schema[col]
        if not dtype.is_numeric():
            continue

        sub = (
            with_ms.select(
                pl.col("x_ms"),
                pl.col(col).cast(pl.Float64, strict=False).alias("y"),
            )
            .drop_nulls()
        )
        if sub.is_empty():
            continue

        fname = sanitize_name(col) + ".parquet"
        out_path = out_dir / fname
        unit = units.get(col)
        y_meta = {b"unit": str(unit).encode("utf-8")} if unit else None
        schema = pa.schema(
            [
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
                "channel_name": col,
                "unit": unit,
                "parquet": f"data/{fname}",
                "point_count": n,
            }
        )

    if not channel_rows:
        raise ValueError("Ingest produced no numeric channels.")

    bounds = {"start_ms": tmin_ms, "end_ms": tmax_ms} if tmin_ms is not None else None
    return channel_rows, bounds
