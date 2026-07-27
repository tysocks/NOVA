"""Ingest CSV/H5/TDMS/Parquet/Arrow into per-channel Parquet artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..models import ChannelItem, TestRunItem
from ..services.file_sources import (
    _h5_datasets,
    _h5_frame,
    _tabular_channel_units,
)
from .session_store import (
    artifact_dir,
    artifact_id_for_path,
    data_dir,
    find_artifact_for_path,
    initial_manifest,
    load_manifest,
    sanitize_channel_filename,
    save_manifest,
)
from .catalog_store import register_ingested_artifact
from .polars_tabular import (
    normalize_csv_polars,
    normalize_tabular_polars,
    write_channel_parquets_from_polars,
)


def _times_to_epoch_ms(series: pd.Series) -> pd.Series:
    ns = series.astype("int64")
    return (ns // 1_000_000).astype("float64")


def _unit_from_field_metadata(metadata: dict | None) -> str | None:
    if not metadata:
        return None
    for key in (b"unit", b"units", "unit", "units"):
        if key in metadata:
            raw = metadata[key]
            if isinstance(raw, bytes):
                text = raw.decode("utf-8", errors="replace").strip()
            else:
                text = str(raw).strip()
            return text or None
    return None


def _read_unit_from_channel_parquet(parquet_path: Path) -> str | None:
    if not parquet_path.is_file() or parquet_path.stat().st_size == 0:
        return None
    try:
        import pyarrow.parquet as pq

        schema = pq.read_schema(parquet_path)
        for field in schema:
            if field.name == "y":
                return _unit_from_field_metadata(field.metadata)
    except Exception:
        return None
    return None


def _write_channel_parquet(
    out_path: Path,
    times: pd.Series,
    values: pd.Series,
    *,
    unit: str | None = None,
) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    sub = pd.DataFrame({"x_ms": _times_to_epoch_ms(times), "y": pd.to_numeric(values, errors="coerce")})
    sub = sub.dropna()
    if sub.empty:
        out_path.write_bytes(b"")
        return 0

    y_meta = {b"unit": str(unit).encode("utf-8")} if unit else None
    schema = pa.schema(
        [
            pa.field("x_ms", pa.float64()),
            pa.field("y", pa.float64(), metadata=y_meta),
        ]
    )
    table = pa.Table.from_pandas(sub, schema=schema, preserve_index=False)
    pq.write_table(table, out_path)
    return len(sub)


def _finalize_manifest(
    manifest: dict[str, Any],
    *,
    channels: list[dict[str, Any]],
    time_bounds: dict[str, float] | None,
    status: str = "ready",
    error: str | None = None,
) -> dict[str, Any]:
    manifest["status"] = status
    manifest["channels"] = channels
    manifest["time_bounds"] = time_bounds
    manifest["error"] = error
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def _ingest_dataframe(
    df: pd.DataFrame,
    artifact_id: str,
    *,
    run_code: str,
    unit_map: dict[str, str | None] | None = None,
    skip_columns: set[str] | None = None,
) -> dict[str, Any]:
    out_dir = data_dir(artifact_id)
    channel_rows: list[dict[str, Any]] = []
    tmin_ms: float | None = None
    tmax_ms: float | None = None
    units = unit_map or {}
    excluded = skip_columns or set()

    for col in df.columns:
        if col == "__time__" or col in excluded:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        sub = df[["__time__", col]].dropna()
        if sub.empty:
            continue
        fname = sanitize_channel_filename(col) + ".parquet"
        out_path = out_dir / fname
        n = _write_channel_parquet(out_path, sub["__time__"], sub[col], unit=units.get(col))
        if n == 0:
            continue
        xs = _times_to_epoch_ms(sub["__time__"])
        tmin_ms = float(xs.min()) if tmin_ms is None else min(tmin_ms, float(xs.min()))
        tmax_ms = float(xs.max()) if tmax_ms is None else max(tmax_ms, float(xs.max()))
        channel_rows.append(
            {
                "channel_name": col,
                "unit": units.get(col),
                "parquet": f"data/{fname}",
                "point_count": n,
            }
        )

    if not channel_rows:
        raise ValueError("Ingest produced no numeric channels.")

    return {
        "run_code": run_code,
        "channels": channel_rows,
        "time_bounds": {"start_ms": tmin_ms, "end_ms": tmax_ms} if tmin_ms is not None else None,
    }


def ingest_csv(
    file_path: str,
    artifact_id: str,
    *,
    units_in_headers: bool = False,
    time_index_channel: str | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    df, unit_map, time_col = normalize_csv_polars(
        file_path,
        units_in_headers=units_in_headers,
        time_col=time_index_channel,
    )
    skip = {"__time__", time_col}
    target = out_dir or data_dir(artifact_id)
    target.mkdir(parents=True, exist_ok=True)
    channel_rows, time_bounds = write_channel_parquets_from_polars(
        df,
        target,
        unit_map=unit_map,
        skip_columns=skip,
        sanitize_name=sanitize_channel_filename,
    )
    return {
        "run_code": Path(file_path).stem,
        "channels": channel_rows,
        "time_bounds": time_bounds,
        "data_dir": str(target.resolve()),
    }


def ingest_h5(
    file_path: str,
    artifact_id: str,
    *,
    time_index_channel: str | None = None,
) -> dict[str, Any]:
    df = _h5_frame(file_path, time_path=time_index_channel)
    unit_map = {row["path"]: row.get("unit") for row in _h5_datasets(file_path)}
    return _ingest_dataframe(
        df,
        artifact_id,
        run_code=Path(file_path).stem,
        unit_map=unit_map,
    )


def ingest_tabular(
    file_path: str,
    artifact_id: str,
    *,
    source_type: str,
    time_index_channel: str | None = None,
    units_in_headers: bool = False,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    df, unit_map, time_col = normalize_tabular_polars(
        file_path,
        source_type=source_type,
        time_col=time_index_channel,
        units_in_headers=units_in_headers,
    )
    skip = {"__time__", time_col}
    target = out_dir or data_dir(artifact_id)
    target.mkdir(parents=True, exist_ok=True)
    channel_rows, time_bounds = write_channel_parquets_from_polars(
        df,
        target,
        unit_map=unit_map,
        skip_columns=skip,
        sanitize_name=sanitize_channel_filename,
    )
    return {
        "run_code": Path(file_path).stem,
        "channels": channel_rows,
        "time_bounds": time_bounds,
        "data_dir": str(target.resolve()),
    }


def ingest_tdms(file_path: str, artifact_id: str) -> dict[str, Any]:
    from nptdms import TdmsFile

    tdms = TdmsFile.read(file_path)
    run_code = Path(file_path).stem
    out_dir = data_dir(artifact_id)
    channel_rows: list[dict[str, Any]] = []
    tmin_ms: float | None = None
    tmax_ms: float | None = None

    for group in tdms.groups():
        for ch in group.channels():
            name = f"{group.name}/{ch.name}"
            try:
                values = pd.Series(ch[:])  # type: ignore[index]
                tt: list = []
                if hasattr(ch, "time_track"):
                    try:
                        tt = list(ch.time_track())
                    except Exception:
                        tt = []
                wf_start = ch.properties.get("wf_start_time")
                wf_increment = ch.properties.get("wf_increment")
                if wf_start is not None:
                    st = pd.Timestamp(wf_start)
                    if st.tzinfo is None:
                        st = st.tz_localize(timezone.utc)
                    else:
                        st = st.tz_convert(timezone.utc)
                else:
                    st = pd.Timestamp.now(tz=timezone.utc)
                if len(tt) > 0:
                    n = min(len(values), len(tt))
                    times = pd.to_datetime(
                        [st + pd.to_timedelta(float(tt[i]), unit="s") for i in range(n)],
                        utc=True,
                    )
                    values = values.iloc[:n]
                else:
                    step_s = float(wf_increment) if wf_increment is not None else 0.001
                    n = len(values)
                    times = pd.to_datetime(
                        [st + pd.to_timedelta(i * step_s, unit="s") for i in range(n)],
                        utc=True,
                    )
                sub = pd.DataFrame({"__time__": times, "y": pd.to_numeric(values, errors="coerce")}).dropna()
                if sub.empty:
                    continue
                unit = str(ch.properties.get("unit_string", "")) or None
                fname = sanitize_channel_filename(name) + ".parquet"
                out_path = out_dir / fname
                npts = _write_channel_parquet(out_path, sub["__time__"], sub["y"], unit=unit)
                if npts == 0:
                    continue
                xs = _times_to_epoch_ms(sub["__time__"])
                tmin_ms = float(xs.min()) if tmin_ms is None else min(tmin_ms, float(xs.min()))
                tmax_ms = float(xs.max()) if tmax_ms is None else max(tmax_ms, float(xs.max()))
                channel_rows.append(
                    {
                        "channel_name": name,
                        "unit": unit,
                        "parquet": f"data/{fname}",
                        "point_count": npts,
                    }
                )
            except Exception:
                continue

    if not channel_rows:
        raise ValueError("TDMS ingest produced no readable channels.")

    return {
        "run_code": run_code,
        "channels": channel_rows,
        "time_bounds": {"start_ms": tmin_ms, "end_ms": tmax_ms} if tmin_ms is not None else None,
    }


def _manifest_should_refresh(
    manifest: dict[str, Any],
    *,
    source_type: str,
    file_path: str,
    units_in_headers: bool,
    time_index_channel: str | None,
) -> bool:
    if manifest.get("units_in_headers") != units_in_headers:
        return True
    if manifest.get("time_index_channel") != time_index_channel:
        return True

    rows = manifest.get("channels") or []
    if not rows:
        return True

    missing_units = [
        str(row.get("channel_name"))
        for row in rows
        if isinstance(row, dict) and row.get("channel_name") and not row.get("unit")
    ]
    if not missing_units:
        return False

    if source_type in {"parquet", "arrow"}:
        unit_map = _tabular_channel_units(file_path, source_type)
        for name in missing_units:
            if unit_map.get(name):
                return True

    return False


def run_ingest(
    source_type: str,
    file_path: str,
    *,
    units_in_headers: bool = False,
    time_index_channel: str | None = None,
    ingest_mode: str | None = None,
    parameters: dict[str, Any] | None = None,
    apply_range_rule_ids: list[int] | None = None,
    catalog_id: str | None = None,
) -> dict[str, Any]:
    """Ingest a file into Parquet artifacts and register in the DuckDB catalog.

    Temporary ingest always uses the hidden system local catalog.
    Permanent ingest requires a user project catalog_id.
    """
    from ..config import settings
    from ..services.database_library import LOCAL_CATALOG_ID, resolve_duckdb_profile
    from .catalog_store import catalog_path_override
    from .file_schema import validate_file_schema
    from .range_detect import apply_range_rule_to_test

    st = source_type.strip().lower()
    if st not in {"csv", "h5", "tdms", "parquet", "arrow"}:
        raise ValueError("source_type must be csv, h5, tdms, parquet, or arrow.")

    requested_mode = (ingest_mode or "").strip().lower() if ingest_mode else None
    if requested_mode and requested_mode not in {"temporary", "permanent"}:
        raise ValueError("ingest_mode must be temporary or permanent.")

    use_permanent = requested_mode == "permanent" or (
        catalog_id is not None
        and str(catalog_id) != LOCAL_CATALOG_ID
        and requested_mode != "temporary"
    )
    if use_permanent:
        if not catalog_id or str(catalog_id) == LOCAL_CATALOG_ID:
            raise ValueError("Permanent ingest requires a project catalog_id from Database Manager.")
        profile = resolve_duckdb_profile(catalog_id)
        mode = "permanent"
    else:
        profile = resolve_duckdb_profile(LOCAL_CATALOG_ID)
        mode = "temporary"

    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"File not found: {file_path}")

    resolved = str(path.resolve())
    validation = validate_file_schema(
        resolved,
        source_type=st,
        time_index_channel=time_index_channel,
        units_in_headers=units_in_headers,
    )
    if not validation.valid:
        raise ValueError(validation.errors[0] if validation.errors else validation.summary)
    existing = find_artifact_for_path(resolved)
    artifact_id = existing or artifact_id_for_path(st, resolved)
    if existing and mode == "temporary":
        manifest = load_manifest(existing)
        if manifest and manifest.get("status") == "ready":
            if not _manifest_should_refresh(
                manifest,
                source_type=st,
                file_path=resolved,
                units_in_headers=units_in_headers,
                time_index_channel=time_index_channel,
            ):
                return manifest

    manifest = initial_manifest(
        artifact_id=artifact_id,
        source_type=st,
        file_path=str(path.resolve()),
        units_in_headers=units_in_headers,
        time_index_channel=time_index_channel,
    )
    save_manifest(artifact_id, manifest)

    try:
        run_code = path.stem
        out_dir: Path | None = None
        if mode == "permanent":
            lake_root = Path(str(profile.get("parquet_root") or settings.parquet_root))
            out_dir = lake_root / run_code / "data"
            out_dir.mkdir(parents=True, exist_ok=True)

        if st == "csv":
            result = ingest_csv(
                str(path),
                artifact_id,
                units_in_headers=units_in_headers,
                time_index_channel=time_index_channel,
                out_dir=out_dir,
            )
        elif st == "h5":
            result = ingest_h5(str(path), artifact_id, time_index_channel=time_index_channel)
            if out_dir is not None:
                result = _copy_channels_to_dir(artifact_id, result, out_dir)
        elif st == "tdms":
            result = ingest_tdms(str(path), artifact_id)
            if out_dir is not None:
                result = _copy_channels_to_dir(artifact_id, result, out_dir)
        else:
            result = ingest_tabular(
                str(path),
                artifact_id,
                source_type=st,
                time_index_channel=time_index_channel,
                units_in_headers=units_in_headers,
                out_dir=out_dir,
            )
        manifest = _finalize_manifest(
            manifest,
            channels=result["channels"],
            time_bounds=result["time_bounds"],
            status="ready",
        )
        manifest["run_code"] = result["run_code"]
        manifest["durability"] = mode
        manifest["catalog_id"] = profile.get("id")

        channel_uris: dict[str, str] = {}
        data_root = Path(result.get("data_dir") or (out_dir or data_dir(artifact_id)))
        for row in result["channels"]:
            rel = str(row.get("parquet") or "")
            name = str(row.get("channel_name") or "")
            if not name or not rel:
                continue
            fname = Path(rel).name
            channel_uris[name] = str((data_root / fname).resolve())

        with catalog_path_override(profile["catalog_path"]):
            test_id = register_ingested_artifact(
                manifest,
                source_type=st,
                file_path=str(path.resolve()),
                parameters=parameters or {},
                durability=mode,
                channel_parquet_uris=channel_uris,
            )
            manifest["test_run_id"] = test_id

            applied_ranges = []
            for rule_id in apply_range_rule_ids or []:
                applied_ranges.extend(apply_range_rule_to_test(test_id, int(rule_id)))
            manifest["applied_ranges"] = [r.model_dump(mode="json") for r in applied_ranges]

        save_manifest(artifact_id, manifest)
        return manifest
    except Exception as exc:
        manifest = _finalize_manifest(manifest, channels=[], time_bounds=None, status="failed", error=str(exc))
        save_manifest(artifact_id, manifest)
        raise



def _copy_channels_to_dir(artifact_id: str, result: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Copy session-written channel parquet into a permanent lake directory."""
    import shutil

    src_root = data_dir(artifact_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    for row in result.get("channels") or []:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("parquet") or "")
        if not rel:
            continue
        src = src_root / Path(rel).name
        dst = out_dir / Path(rel).name
        if src.is_file():
            shutil.copy2(src, dst)
    result = dict(result)
    result["data_dir"] = str(out_dir.resolve())
    return result


def get_ingest_status(artifact_id: str) -> dict[str, Any] | None:
    return load_manifest(artifact_id)


def manifest_to_tests(manifest: dict[str, Any]) -> list[TestRunItem]:
    bounds = manifest.get("time_bounds") or {}
    start_ms = bounds.get("start_ms")
    end_ms = bounds.get("end_ms")
    if start_ms is None or end_ms is None:
        now = datetime.now(timezone.utc)
        return [
            TestRunItem(
                test_run_id=int(manifest.get("test_run_id", 1)),
                run_code=str(manifest.get("run_code", "run")),
                start_time=now,
                end_time=now,
                duration_s=0.0,
                t0_utc=now,
            )
        ]
    t0 = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
    t1 = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    return [
        TestRunItem(
            test_run_id=int(manifest.get("test_run_id", 1)),
            run_code=str(manifest.get("run_code", "run")),
            start_time=t0,
            end_time=t1,
            duration_s=(t1 - t0).total_seconds(),
            t0_utc=t0,
        )
    ]


def manifest_to_channels(manifest: dict[str, Any]) -> list[ChannelItem]:
    artifact_id = str(manifest.get("artifact_id") or "")
    source_type = str(manifest.get("source_type") or "")
    file_path = str(manifest.get("file_path") or "")
    file_unit_map: dict[str, str | None] = {}
    if source_type in {"parquet", "arrow"} and file_path and Path(file_path).is_file():
        file_unit_map = _tabular_channel_units(file_path, source_type)

    channels: list[ChannelItem] = []
    time_index = str(manifest.get("time_index_channel") or "")
    for idx, row in enumerate(manifest.get("channels") or [], start=1):
        if not isinstance(row, dict):
            continue
        name = row.get("channel_name")
        if not name:
            continue
        if time_index and str(name) == time_index:
            continue
        unit = row.get("unit")
        if not unit:
            unit = file_unit_map.get(str(name))
        if not unit and artifact_id:
            rel = row.get("parquet")
            if rel:
                unit = _read_unit_from_channel_parquet(artifact_dir(artifact_id) / str(rel))
        channels.append(
            ChannelItem(
                channel_id=idx,
                channel_name=str(name),
                display_name=str(name),
                unit=unit,
            )
        )
    return channels
