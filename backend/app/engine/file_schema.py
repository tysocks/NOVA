"""Validate data file layout before ingest."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..models import FileSchemaValidation
from ..services.file_sources import (
    DEFAULT_TIME_COLUMN_NAMES,
    _arrow_table,
    _csv_frame,
    _h5_datasets,
    _h5_frame,
    _parquet_table,
    _tabular_frame_from_arrow,
    detect_source_type,
    resolve_time_column_name,
)

_FORMAT_SUMMARIES = {
    "csv": (
        "CSV: header row required; one time column (e.g. time_s, timestamp_utc, TIME); "
        "one or more numeric channel columns. Optional units in headers: THRUST (N), P[psi]."
    ),
    "parquet": (
        "Parquet: columnar table; one numeric or datetime time column; "
        "one or more numeric channel columns. Optional unit per field in schema metadata."
    ),
    "arrow": (
        "Arrow/Feather: same layout as Parquet — shared time column plus numeric channels. "
        "Optional unit per field in schema metadata."
    ),
    "h5": (
        "HDF5: numeric 1-D datasets; shared TIME dataset (default telemetry/TIME) aligned "
        "with channels. Optional units/start-time dataset or group attributes."
    ),
    "tdms": (
        "TDMS: channel groups with wf_start_time + time track (or wf_increment). "
        "No shared time index column — timestamps are per channel."
    ),
}


def _result(
    source_type: str,
    *,
    valid: bool,
    errors: list[str],
    warnings: list[str] | None = None,
) -> FileSchemaValidation:
    errs = errors or []
    warns = warnings or []
    summary = _FORMAT_SUMMARIES.get(source_type, "")
    if valid:
        msg = f"{source_type.upper()} file passed schema checks."
        if warns:
            msg += f" {len(warns)} warning(s)."
    else:
        msg = f"{source_type.upper()} schema check failed: {errs[0]}"
    return FileSchemaValidation(
        valid=valid,
        errors=errs,
        warnings=warns,
        summary=msg,
        format_requirements=summary,
    )


def _validate_csv(file_path: str, *, time_index_channel: str | None, units_in_headers: bool) -> FileSchemaValidation:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        header = pd.read_csv(file_path, nrows=0)
    except Exception as exc:
        return _result("csv", valid=False, errors=[f"Cannot read CSV header: {exc}"])

    columns = list(header.columns)
    if not columns:
        return _result("csv", valid=False, errors=["CSV file has no header columns."])

    time_col = resolve_time_column_name(columns, time_index_channel)
    if not time_col:
        names = ", ".join(DEFAULT_TIME_COLUMN_NAMES)
        return _result(
            "csv",
            valid=False,
            errors=[f"Missing time column. Use one of: {names}, or set time_index_channel."],
        )

    try:
        df, _ = _csv_frame(file_path, units_in_headers=units_in_headers, time_col=time_index_channel)
    except Exception as exc:
        return _result("csv", valid=False, errors=[str(exc)])

    time_col = resolve_time_column_name(list(df.columns), time_index_channel)
    channel_cols = [
        c for c in df.columns if c not in {"__time__", time_col} and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not channel_cols:
        return _result("csv", valid=False, errors=["No numeric channel columns found besides the time index."])

    if len(df) < 2:
        warnings.append("CSV has fewer than 2 data rows.")

    return _result("csv", valid=True, errors=[], warnings=warnings)


def _validate_tabular(
    file_path: str,
    *,
    source_type: str,
    time_index_channel: str | None,
) -> FileSchemaValidation:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        table = _parquet_table(file_path) if source_type == "parquet" else _arrow_table(file_path)
    except Exception as exc:
        return _result(source_type, valid=False, errors=[f"Cannot read {source_type.upper()} file: {exc}"])

    if table.num_rows == 0:
        return _result(source_type, valid=False, errors=["File has no rows."])

    columns = table.column_names
    time_col = resolve_time_column_name(columns, time_index_channel)
    if not time_col:
        names = ", ".join(DEFAULT_TIME_COLUMN_NAMES)
        return _result(
            source_type,
            valid=False,
            errors=[f"Missing time column. Use one of: {names}, or set time_index_channel."],
        )

    try:
        df = _tabular_frame_from_arrow(file_path, time_col=time_index_channel, source_type=source_type)
    except Exception as exc:
        return _result(source_type, valid=False, errors=[str(exc)])

    time_col = resolve_time_column_name(list(df.columns), time_index_channel)
    channel_cols = [
        c for c in df.columns if c not in {"__time__", time_col} and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not channel_cols:
        return _result(source_type, valid=False, errors=["No numeric channel columns found besides the time index."])

    if table.num_rows < 2:
        warnings.append("File has fewer than 2 rows.")

    return _result(source_type, valid=True, errors=[], warnings=warnings)


def _validate_h5(file_path: str, *, time_index_channel: str | None) -> FileSchemaValidation:
    warnings: list[str] = []
    datasets = _h5_datasets(file_path)
    time_candidates = [d["path"] for d in datasets if d.get("is_time_candidate")]
    resolved_time = time_index_channel or (
        "telemetry/TIME" if "telemetry/TIME" in time_candidates else (time_candidates[0] if time_candidates else None)
    )
    if not resolved_time:
        return _result("h5", valid=False, errors=["No TIME dataset found. Expected telemetry/TIME or a time-like dataset."])

    try:
        df = _h5_frame(file_path, time_path=resolved_time)
    except Exception as exc:
        return _result("h5", valid=False, errors=[str(exc)])

    channel_cols = [c for c in df.columns if c != "__time__" and pd.api.types.is_numeric_dtype(df[c])]
    if not channel_cols:
        return _result("h5", valid=False, errors=["No numeric channels aligned with the selected TIME dataset."])

    if len(df) < 2:
        warnings.append("TIME series has fewer than 2 samples.")

    return _result("h5", valid=True, errors=[], warnings=warnings)


def _validate_tdms(file_path: str) -> FileSchemaValidation:
    from nptdms import TdmsFile

    warnings: list[str] = []
    try:
        tdms = TdmsFile.read(file_path)
    except Exception as exc:
        return _result("tdms", valid=False, errors=[f"Cannot read TDMS file: {exc}"])

    count = 0
    for group in tdms.groups():
        for ch in group.channels():
            try:
                if len(ch[:]) > 0:  # type: ignore[index]
                    count += 1
            except Exception:
                continue

    if count == 0:
        return _result("tdms", valid=False, errors=["TDMS file has no readable channels."])

    if count < 2:
        warnings.append("TDMS file has only one channel.")

    return _result("tdms", valid=True, errors=[], warnings=warnings)


def validate_file_schema(
    file_path: str,
    *,
    source_type: str | None = None,
    time_index_channel: str | None = None,
    units_in_headers: bool = False,
) -> FileSchemaValidation:
    path = Path(file_path)
    if not path.is_file():
        return FileSchemaValidation(
            valid=False,
            errors=[f"File not found: {file_path}"],
            summary="File not found.",
        )

    st = (source_type or detect_source_type(str(path.resolve()))).strip().lower()
    resolved = str(path.resolve())

    if st == "csv":
        return _validate_csv(resolved, time_index_channel=time_index_channel, units_in_headers=units_in_headers)
    if st == "parquet":
        return _validate_tabular(resolved, source_type="parquet", time_index_channel=time_index_channel)
    if st == "arrow":
        return _validate_tabular(resolved, source_type="arrow", time_index_channel=time_index_channel)
    if st == "h5":
        return _validate_h5(resolved, time_index_channel=time_index_channel)
    if st == "tdms":
        return _validate_tdms(resolved)

    return FileSchemaValidation(
        valid=False,
        errors=["Unsupported file type."],
        summary="Unsupported file type.",
    )
