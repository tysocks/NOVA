"""Ingest CSV/H5/TDMS into per-channel Parquet artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..models import ChannelItem, TestRunItem
from ..services.file_sources import _csv_frame, _h5_frame
from .session_store import (
    artifact_id_for_path,
    data_dir,
    find_artifact_for_path,
    initial_manifest,
    load_manifest,
    sanitize_channel_filename,
    save_manifest,
)


def _times_to_epoch_ms(series: pd.Series) -> pd.Series:
    ns = series.astype("int64")
    return (ns // 1_000_000).astype("float64")


def _write_channel_parquet(
    out_path: Path,
    times: pd.Series,
    values: pd.Series,
) -> int:
    sub = pd.DataFrame({"x_ms": _times_to_epoch_ms(times), "y": pd.to_numeric(values, errors="coerce")})
    sub = sub.dropna()
    if sub.empty:
        out_path.write_bytes(b"")  # placeholder; duckdb may skip
        return 0
    sub.to_parquet(out_path, index=False)
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


def _csv_time_column(df: pd.DataFrame) -> str | None:
    for c in ("timestamp_utc", "time", "timestamp", "datetime", "time_s"):
        if c in df.columns:
            return c
    return None


def ingest_csv(
    file_path: str,
    artifact_id: str,
    *,
    units_in_headers: bool = False,
) -> dict[str, Any]:
    df, unit_map = _csv_frame(file_path, units_in_headers=units_in_headers)
    time_col = _csv_time_column(df)
    run_code = Path(file_path).stem
    out_dir = data_dir(artifact_id)
    channel_rows: list[dict[str, Any]] = []
    tmin_ms: float | None = None
    tmax_ms: float | None = None

    for col in df.columns:
        if col == "__time__" or col == time_col:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        sub = df[["__time__", col]].dropna()
        if sub.empty:
            continue
        fname = sanitize_channel_filename(col) + ".parquet"
        out_path = out_dir / fname
        n = _write_channel_parquet(out_path, sub["__time__"], sub[col])
        if n == 0:
            continue
        xs = _times_to_epoch_ms(sub["__time__"])
        tmin_ms = float(xs.min()) if tmin_ms is None else min(tmin_ms, float(xs.min()))
        tmax_ms = float(xs.max()) if tmax_ms is None else max(tmax_ms, float(xs.max()))
        channel_rows.append(
            {
                "channel_name": col,
                "unit": unit_map.get(col),
                "parquet": f"data/{fname}",
                "point_count": n,
            }
        )

    if not channel_rows:
        raise ValueError("CSV ingest produced no numeric channels.")

    return {
        "run_code": run_code,
        "channels": channel_rows,
        "time_bounds": {"start_ms": tmin_ms, "end_ms": tmax_ms} if tmin_ms is not None else None,
    }


def ingest_h5(file_path: str, artifact_id: str) -> dict[str, Any]:
    df = _h5_frame(file_path)
    run_code = Path(file_path).stem
    out_dir = data_dir(artifact_id)
    channel_rows: list[dict[str, Any]] = []
    tmin_ms: float | None = None
    tmax_ms: float | None = None

    for col in df.columns:
        if col == "__time__":
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        sub = df[["__time__", col]].dropna()
        if sub.empty:
            continue
        fname = sanitize_channel_filename(col) + ".parquet"
        out_path = out_dir / fname
        n = _write_channel_parquet(out_path, sub["__time__"], sub[col])
        if n == 0:
            continue
        xs = _times_to_epoch_ms(sub["__time__"])
        tmin_ms = float(xs.min()) if tmin_ms is None else min(tmin_ms, float(xs.min()))
        tmax_ms = float(xs.max()) if tmax_ms is None else max(tmax_ms, float(xs.max()))
        channel_rows.append(
            {
                "channel_name": col,
                "unit": None,
                "parquet": f"data/{fname}",
                "point_count": n,
            }
        )

    if not channel_rows:
        raise ValueError("H5 ingest produced no numeric channels.")

    return {
        "run_code": run_code,
        "channels": channel_rows,
        "time_bounds": {"start_ms": tmin_ms, "end_ms": tmax_ms} if tmin_ms is not None else None,
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
                fname = sanitize_channel_filename(name) + ".parquet"
                out_path = out_dir / fname
                npts = _write_channel_parquet(out_path, sub["__time__"], sub["y"])
                if npts == 0:
                    continue
                xs = _times_to_epoch_ms(sub["__time__"])
                tmin_ms = float(xs.min()) if tmin_ms is None else min(tmin_ms, float(xs.min()))
                tmax_ms = float(xs.max()) if tmax_ms is None else max(tmax_ms, float(xs.max()))
                unit = str(ch.properties.get("unit_string", "")) or None
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


def run_ingest(
    source_type: str,
    file_path: str,
    *,
    units_in_headers: bool = False,
) -> dict[str, Any]:
    """Ingest a file into .nova_sessions/{artifact_id}/ and return manifest."""
    st = source_type.strip().lower()
    if st not in {"csv", "h5", "tdms"}:
        raise ValueError("source_type must be csv, h5, or tdms.")

    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"File not found: {file_path}")

    resolved = str(path.resolve())
    existing = find_artifact_for_path(resolved)
    if existing:
        manifest = load_manifest(existing)
        if manifest and manifest.get("status") == "ready":
            return manifest

    artifact_id = artifact_id_for_path(st, resolved)
    manifest = initial_manifest(
        artifact_id=artifact_id,
        source_type=st,
        file_path=str(path.resolve()),
        units_in_headers=units_in_headers,
    )
    save_manifest(artifact_id, manifest)

    try:
        if st == "csv":
            result = ingest_csv(str(path), artifact_id, units_in_headers=units_in_headers)
        elif st == "h5":
            result = ingest_h5(str(path), artifact_id)
        else:
            result = ingest_tdms(str(path), artifact_id)
        manifest = _finalize_manifest(
            manifest,
            channels=result["channels"],
            time_bounds=result["time_bounds"],
            status="ready",
        )
        manifest["run_code"] = result["run_code"]
        save_manifest(artifact_id, manifest)
        return manifest
    except Exception as exc:
        manifest = _finalize_manifest(manifest, channels=[], time_bounds=None, status="failed", error=str(exc))
        save_manifest(artifact_id, manifest)
        raise


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
    channels: list[ChannelItem] = []
    for idx, row in enumerate(manifest.get("channels") or [], start=1):
        if not isinstance(row, dict):
            continue
        name = row.get("channel_name")
        if not name:
            continue
        channels.append(
            ChannelItem(
                channel_id=idx,
                channel_name=str(name),
                display_name=str(name),
                unit=row.get("unit"),
            )
        )
    return channels
