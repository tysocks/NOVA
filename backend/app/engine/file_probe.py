"""Inspect data files before ingest: channels, time index, unit metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import FileProbeChannel, FileProbeResponse, FileUnitsMetadataReport
from ..services.file_sources import (
    DEFAULT_TIME_COLUMN_NAMES,
    _arrow_table,
    _csv_frame,
    _h5_datasets,
    _parquet_table,
    _split_name_unit,
    detect_source_type,
    resolve_time_column_name,
)

_UNIT_META_KEYS = ("unit", "units", "Unit", "Units", "unit_string")


def _decode_meta_value(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace").strip()
    else:
        text = str(raw).strip()
    return text or None


def _field_unit_from_metadata(meta: dict | None) -> str | None:
    if not meta:
        return None
    for key in _UNIT_META_KEYS:
        for candidate in (key, key.encode("utf-8") if isinstance(key, str) else key):
            if candidate in meta:
                unit = _decode_meta_value(meta[candidate])
                if unit:
                    return unit
    return None


def _pick_default_time(candidates: list[str]) -> str | None:
    lowered = {c.lower(): c for c in candidates}
    for name in DEFAULT_TIME_COLUMN_NAMES:
        if name in lowered:
            return lowered[name]
    return candidates[0] if candidates else None


def _probe_csv(
    file_path: str,
    *,
    units_in_headers: bool,
    time_index_channel: str | None,
) -> FileProbeResponse:
    import pandas as pd

    header_df = pd.read_csv(file_path, nrows=0)
    raw_columns = list(header_df.columns)
    df, unit_map = _csv_frame(file_path, units_in_headers=units_in_headers, time_col=time_index_channel)
    time_col = time_index_channel
    if not time_col:
        for c in DEFAULT_TIME_COLUMN_NAMES:
            if c in raw_columns:
                time_col = c
                break

    channels: list[FileProbeChannel] = []
    with_units: list[str] = []
    without_units: list[str] = []

    if units_in_headers:
        for col in df.columns:
            if col == "__time__" or col == time_col:
                continue
            if not pd.api.types.is_numeric_dtype(df[col]):
                continue
            unit = unit_map.get(col)
            channels.append(FileProbeChannel(channel_name=col, unit=unit, unit_from_metadata=bool(unit)))
            if unit:
                with_units.append(col)
            else:
                without_units.append(col)
    else:
        numeric_cols: set[str] = set()
        for col in raw_columns:
            if col == time_col:
                continue
            try:
                sample = pd.read_csv(file_path, usecols=[col], nrows=8)[col]
                if pd.api.types.is_numeric_dtype(sample):
                    numeric_cols.add(col)
            except Exception:
                continue
        for col in numeric_cols:
            _, unit = _split_name_unit(col)
            channels.append(
                FileProbeChannel(channel_name=col, unit=unit, unit_from_metadata=bool(unit))
            )
            if unit:
                with_units.append(col)
            else:
                without_units.append(col)

    return FileProbeResponse(
        source_type="csv",
        file_path=file_path,
        time_index_candidates=raw_columns,
        time_index_default=time_col or _pick_default_time(raw_columns),
        channels=channels,
        units_metadata=_units_report(
            supports_unit_metadata=True,
            parse_units_from_header=True,
            with_units=with_units,
            without_units=without_units,
            format_label="CSV",
        ),
    )


def _probe_h5(file_path: str, *, time_index_channel: str | None) -> FileProbeResponse:
    datasets = _h5_datasets(file_path)
    time_candidates = [d["path"] for d in datasets if d.get("is_time_candidate")]
    channel_rows = [d for d in datasets if d.get("is_channel")]
    default_time = time_index_channel or _pick_default_time(time_candidates) or (
        "telemetry/TIME" if "telemetry/TIME" in time_candidates else None
    )
    channels: list[FileProbeChannel] = []
    with_units: list[str] = []
    without_units: list[str] = []
    for row in channel_rows:
        name = str(row["path"])
        unit = row.get("unit")
        channels.append(
            FileProbeChannel(
                channel_name=name,
                unit=unit,
                unit_from_metadata=bool(unit),
            )
        )
        if unit:
            with_units.append(name)
        else:
            without_units.append(name)

    return FileProbeResponse(
        source_type="h5",
        file_path=file_path,
        time_index_candidates=time_candidates,
        time_index_default=default_time,
        channels=channels,
        units_metadata=_units_report(
            supports_unit_metadata=True,
            parse_units_from_header=False,
            with_units=with_units,
            without_units=without_units,
            format_label="HDF5",
        ),
    )


def _probe_tdms(file_path: str) -> FileProbeResponse:
    from nptdms import TdmsFile

    tdms = TdmsFile.read(file_path)
    channels: list[FileProbeChannel] = []
    with_units: list[str] = []
    without_units: list[str] = []
    time_candidates: list[str] = []
    for group in tdms.groups():
        for ch in group.channels():
            name = f"{group.name}/{ch.name}"
            unit = str(ch.properties.get("unit_string", "") or "").strip() or None
            channels.append(
                FileProbeChannel(
                    channel_name=name,
                    unit=unit,
                    unit_from_metadata=bool(unit),
                )
            )
            if unit:
                with_units.append(name)
            else:
                without_units.append(name)
            try:
                if len(ch.time_track()) > 0:
                    time_candidates.append(name)
            except Exception:
                pass

    return FileProbeResponse(
        source_type="tdms",
        file_path=file_path,
        time_index_candidates=time_candidates or [c.channel_name for c in channels[:1]],
        time_index_default=(time_candidates[0] if time_candidates else (channels[0].channel_name if channels else None)),
        channels=channels,
        units_metadata=_units_report(
            supports_unit_metadata=True,
            parse_units_from_header=False,
            with_units=with_units,
            without_units=without_units,
            format_label="TDMS",
        ),
    )


def _probe_parquet(file_path: str, *, time_index_channel: str | None, units_in_headers: bool = False) -> FileProbeResponse:
    table = _parquet_table(file_path)
    return _probe_arrow_table(
        file_path,
        table,
        source_type="parquet",
        time_index_channel=time_index_channel,
        format_label="Parquet",
        units_in_headers=units_in_headers,
    )


def _probe_arrow(file_path: str, *, time_index_channel: str | None, units_in_headers: bool = False) -> FileProbeResponse:
    table = _arrow_table(file_path)
    return _probe_arrow_table(
        file_path,
        table,
        source_type="arrow",
        time_index_channel=time_index_channel,
        format_label="Arrow",
        units_in_headers=units_in_headers,
    )


def _probe_arrow_table(
    file_path: str,
    table: Any,
    *,
    source_type: str,
    time_index_channel: str | None,
    format_label: str,
    units_in_headers: bool = False,
) -> FileProbeResponse:
    import pyarrow as pa
    import pyarrow.compute as pc

    if not isinstance(table, pa.Table):
        table = pa.table(table)

    candidates = table.column_names
    time_col = time_index_channel or _pick_default_time(candidates)
    channels: list[FileProbeChannel] = []
    with_units: list[str] = []
    without_units: list[str] = []

    for idx, name in enumerate(candidates):
        if name == time_col:
            continue
        field = table.schema.field(idx)
        col = table.column(name)
        if not (pa.types.is_floating(col.type) or pa.types.is_integer(col.type)):
            continue
        if pc.sum(pc.cast(pc.is_valid(col), pa.int64())).as_py() == 0:
            continue
        if units_in_headers:
            base, header_unit = _split_name_unit(name)
            channel_name = base or name
            unit = header_unit or _field_unit_from_metadata(field.metadata)
        else:
            channel_name = name
            unit = _field_unit_from_metadata(field.metadata)
            if not unit:
                _, unit = _split_name_unit(name)
        channels.append(
            FileProbeChannel(
                channel_name=channel_name,
                unit=unit,
                unit_from_metadata=bool(unit),
            )
        )
        if unit:
            with_units.append(channel_name)
        else:
            without_units.append(channel_name)

    return FileProbeResponse(
        source_type=source_type,
        file_path=file_path,
        time_index_candidates=candidates,
        time_index_default=time_col,
        channels=channels,
        units_metadata=_units_report(
            supports_unit_metadata=True,
            parse_units_from_header=units_in_headers and format_label == "CSV",
            with_units=with_units,
            without_units=without_units,
            format_label=format_label,
        ),
    )


def _units_report(
    *,
    supports_unit_metadata: bool,
    parse_units_from_header: bool,
    with_units: list[str],
    without_units: list[str],
    format_label: str,
) -> FileUnitsMetadataReport:
    total = len(with_units) + len(without_units)
    if not supports_unit_metadata:
        summary = f"{format_label} does not expose channel unit metadata."
        return FileUnitsMetadataReport(
            supports_unit_metadata=False,
            parse_units_from_header=False,
            channels_with_units=[],
            channels_without_units=without_units,
            all_channels_have_units=False,
            summary=summary,
        )
    if total == 0:
        summary = f"No numeric channels found in {format_label} file."
        return FileUnitsMetadataReport(
            supports_unit_metadata=True,
            parse_units_from_header=parse_units_from_header,
            channels_with_units=[],
            channels_without_units=[],
            all_channels_have_units=False,
            summary=summary,
        )
    if not without_units:
        summary = f"All {total} channel(s) have units defined in {format_label} metadata."
        flag = "ok"
    elif not with_units:
        summary = f"No units found in {format_label} metadata for {total} channel(s)."
        flag = "missing"
    else:
        summary = (
            f"{len(with_units)}/{total} channel(s) have units in {format_label} metadata; "
            f"{len(without_units)} missing."
        )
        flag = "partial"
    return FileUnitsMetadataReport(
        supports_unit_metadata=True,
        parse_units_from_header=parse_units_from_header,
        channels_with_units=with_units,
        channels_without_units=without_units,
        all_channels_have_units=len(without_units) == 0,
        summary=summary,
        flag=flag,
    )


def probe_file(
    file_path: str,
    *,
    units_in_headers: bool = False,
    time_index_channel: str | None = None,
) -> FileProbeResponse:
    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"File not found: {file_path}")

    resolved = str(path.resolve())
    source_type = detect_source_type(resolved)

    if source_type == "csv":
        return _probe_csv(resolved, units_in_headers=units_in_headers, time_index_channel=time_index_channel)
    if source_type == "h5":
        return _probe_h5(resolved, time_index_channel=time_index_channel)
    if source_type == "tdms":
        return _probe_tdms(resolved)
    if source_type == "parquet":
        return _probe_parquet(
            resolved,
            time_index_channel=time_index_channel,
            units_in_headers=units_in_headers,
        )
    if source_type == "arrow":
        return _probe_arrow(
            resolved,
            time_index_channel=time_index_channel,
            units_in_headers=units_in_headers,
        )

    raise ValueError("Unsupported file type. Use CSV, TDMS, HDF5, Parquet, or Arrow.")


def probe_file_with_validation(
    file_path: str,
    *,
    units_in_headers: bool = False,
    time_index_channel: str | None = None,
) -> FileProbeResponse:
    from .file_schema import validate_file_schema

    result = probe_file(
        file_path,
        units_in_headers=units_in_headers,
        time_index_channel=time_index_channel,
    )
    validation = validate_file_schema(
        file_path,
        source_type=result.source_type,
        time_index_channel=time_index_channel or result.time_index_default,
        units_in_headers=units_in_headers,
    )
    return result.model_copy(update={"schema_validation": validation})
