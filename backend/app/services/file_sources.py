from __future__ import annotations

import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..models import ChannelItem, TestRunItem, TimeSeriesPoint

logger = logging.getLogger(__name__)

DEFAULT_TIME_COLUMN_NAMES = ("timestamp_utc", "time", "timestamp", "datetime", "time_s", "x_ms", "TIME")
FILE_SOURCE_TYPES = frozenset({"csv", "h5", "tdms", "parquet", "arrow"})


def resolve_time_column_name(columns: list[str], time_col: str | None = None) -> str | None:
    """Return the column used as the shared time index, if present."""
    if time_col:
        if time_col in columns:
            return time_col
        lowered = {c.lower(): c for c in columns}
        if time_col.lower() in lowered:
            return lowered[time_col.lower()]
    lowered = {c.lower(): c for c in columns}
    for name in DEFAULT_TIME_COLUMN_NAMES:
        if name in columns:
            return name
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def detect_source_type(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    mapping = {
        ".csv": "csv",
        ".tdms": "tdms",
        ".h5": "h5",
        ".hdf5": "h5",
        ".parquet": "parquet",
        ".pq": "parquet",
        ".arrow": "arrow",
        ".arrows": "arrow",
        ".feather": "arrow",
    }
    source_type = mapping.get(suffix)
    if not source_type:
        raise ValueError(f"Unsupported file extension '{suffix or '(none)'}'.")
    return source_type


def _to_dt(series: pd.Series) -> pd.Series:
    out = pd.to_datetime(series, utc=True, errors="coerce")
    return out.dropna()


def _split_name_unit(raw: str) -> tuple[str, str | None]:
    txt = str(raw or "").strip()
    if not txt:
        return "", None
    # Common patterns:
    # - "THRUST (N)"
    # - "P[psi]"
    # - "mass_flow [kg/s]"
    m = pd.Series([txt]).str.extract(r"^\s*(.*?)\s*(?:\(([^)]+)\)|\[([^\]]+)\])\s*$").iloc[0]
    base = str(m[0]) if pd.notna(m[0]) else txt
    u1 = str(m[1]) if pd.notna(m[1]) else ""
    u2 = str(m[2]) if pd.notna(m[2]) else ""
    unit = (u1 or u2).strip() or None
    base = base.strip() or txt
    return base, unit


def _csv_frame(
    file_path: str,
    units_in_headers: bool = False,
    *,
    time_col: str | None = None,
) -> tuple[pd.DataFrame, dict[str, str | None]]:
    try:
        df = pd.read_csv(file_path)
    except Exception as exc:
        raise ValueError(f"Unable to read CSV contents: {exc}") from exc

    if df.empty:
        raise ValueError("CSV file has no data rows.")

    resolved_time_col = resolve_time_column_name(list(df.columns), time_col)
    if not resolved_time_col:
        raise ValueError(
            "CSV requires a time column (timestamp_utc/time/timestamp/datetime/time_s) "
            "or an explicit time_index_channel."
        )
    # Numeric time columns: infer unit (s/ms/ns) and drop padded zero slots.
    if pd.api.types.is_numeric_dtype(df[resolved_time_col]):
        time_raw = pd.to_numeric(df[resolved_time_col], errors="coerce")
        df["__time__"] = _numeric_times_to_datetime(time_raw)
        valid = df["__time__"].notna() & _epoch_valid_mask(time_raw)
        df = df[valid]
    else:
        df["__time__"] = pd.to_datetime(df[resolved_time_col], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["__time__"]).sort_values("__time__")
    if df.empty:
        raise ValueError(f"CSV time column '{resolved_time_col}' could not be parsed as timestamps.")

    unit_map: dict[str, str | None] = {}
    if units_in_headers:
        df, unit_map = _apply_units_in_headers(df, skip_columns={"__time__", resolved_time_col})

    return df, unit_map


def _apply_units_in_headers(
    df: pd.DataFrame,
    *,
    skip_columns: set[str],
) -> tuple[pd.DataFrame, dict[str, str | None]]:
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
        df = df.rename(columns=rename)
    return df, unit_map


def _h5_attr_unit(obj) -> str | None:
    for key in ("units", "unit", "Unit", "Units"):
        if key in obj.attrs:
            raw = obj.attrs[key]
            if isinstance(raw, bytes):
                text = raw.decode("utf-8", errors="replace").strip()
            else:
                text = str(raw).strip()
            if text:
                return text
    return None


_START_TIME_ATTR_KEYS = (
    "StartTime",
    "start_time",
    "starttime",
    "Start_Time",
    "t0",
    "T0",
    "Epoch",
    "epoch",
    "wf_start_time",
    "AbsoluteStart",
    "absolute_start",
)


def _parse_h5_start_time_attr(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        ts = pd.to_datetime(s, utc=True, errors="coerce")
        if pd.notna(ts):
            return float(ts.timestamp())
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if num > 1e17:
        return num / 1e9
    if num > 1e14:
        return num / 1e6
    if num > 1e11:
        return num / 1e3
    return num


def _h5_find_start_epoch_s(dataset) -> float | None:
    obj = dataset
    for _ in range(4):
        for key in _START_TIME_ATTR_KEYS:
            if key in obj.attrs:
                parsed = _parse_h5_start_time_attr(obj.attrs[key])
                if parsed is not None:
                    return parsed
        parent = getattr(obj, "parent", None)
        if parent is None or getattr(parent, "name", "/") == "/":
            break
        obj = parent
    return None


def _normalize_h5_time_unit(units: str | None) -> str | None:
    if not units:
        return None
    u = units.strip().lower()
    if u in {"s", "sec", "second", "seconds"}:
        return "s"
    if "nano" in u or u in {"ns", "nanoseconds"}:
        return "ns"
    if "milli" in u or u in {"ms", "milliseconds"}:
        return "ms"
    if "micro" in u or u in {"us", "usec", "microseconds", "µs"}:
        return "us"
    return None


def _h5_read_time_unit(dataset) -> str | None:
    units = _normalize_h5_time_unit(_h5_attr_unit(dataset))
    if units:
        return units
    parent = getattr(dataset, "parent", None)
    if parent is not None:
        return _normalize_h5_time_unit(_h5_attr_unit(parent))
    return None


def _numeric_times_to_datetime(
    time_raw: pd.Series,
    *,
    unit: str | None = None,
    start_epoch_s: float | None = None,
) -> pd.Series:
    mx = float(time_raw.max())
    mn = float(time_raw.min())
    span = mx - mn

    if unit == "ns":
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="ns")
    if unit == "ms":
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="ms")
    if unit == "us":
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="us")

    if mx > 1e17:
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="ns")
    if mx > 1e14:
        if span > 1e11:
            return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="us")
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="ns")
    if mx > 1e11:
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="ms")

    # Epoch unix seconds — typical absolute test timestamps (~2000–2040).
    if mn >= 1e8 and span <= 86400 * 366 * 50:
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="s")

    # Elapsed seconds from test start; add start offset when available.
    if mx <= 1e8:
        if start_epoch_s is not None:
            return pd.to_datetime(time_raw + start_epoch_s, utc=True, errors="coerce", unit="s")
        return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="s")

    return pd.to_datetime(time_raw, utc=True, errors="coerce", unit="s")


def _epoch_valid_mask(time_raw: pd.Series) -> pd.Series:
    """Drop unset/zero slots when an epoch-scale TIME buffer is partially filled."""
    mx = float(time_raw.max())
    if mx <= 1e8:
        return pd.Series(True, index=time_raw.index)
    mn = float(time_raw.min())
    if mn >= 1e8:
        return pd.Series(True, index=time_raw.index)
    return time_raw > 1e8


def _h5_times_to_datetime(time_raw: pd.Series, dataset) -> pd.Series:
    unit = _h5_read_time_unit(dataset)
    start_s = None
    if float(time_raw.max()) <= 1e8:
        start_s = _h5_find_start_epoch_s(dataset)
    return _numeric_times_to_datetime(time_raw, unit=unit, start_epoch_s=start_s)


def _h5_epoch_valid_mask(time_raw: pd.Series) -> pd.Series:
    return _epoch_valid_mask(time_raw)


def _h5_datasets(file_path: str) -> list[dict]:
    try:
        import h5py
    except Exception as exc:
        raise ValueError(f"Unable to read H5 contents: {exc}") from exc

    rows: list[dict] = []
    try:
        with h5py.File(file_path, "r") as h5:
            def _visit(name: str, obj) -> None:
                if not isinstance(obj, h5py.Dataset):
                    return
                path = name
                is_time = path.endswith("TIME") or path.split("/")[-1].lower() in {
                    c.lower() for c in DEFAULT_TIME_COLUMN_NAMES
                }
                values = obj[()]
                s = pd.Series(values)
                is_numeric = s.ndim == 1 and pd.to_numeric(s, errors="coerce").notna().sum() > 0
                rows.append(
                    {
                        "path": path,
                        "is_time_candidate": bool(is_time and is_numeric),
                        "is_channel": bool(is_numeric and not is_time),
                        "unit": _h5_attr_unit(obj),
                    }
                )

            h5.visititems(_visit)
    except Exception as exc:
        raise ValueError(f"Unable to read H5 contents: {exc}") from exc
    return rows


def _h5_frame(file_path: str, *, time_path: str | None = None) -> pd.DataFrame:
    try:
        import h5py
    except Exception as exc:
        raise ValueError(f"Unable to read H5 contents: {exc}") from exc

    resolved_time = time_path or "telemetry/TIME"
    try:
        with h5py.File(file_path, "r") as h5:
            if resolved_time not in h5:
                raise ValueError(f"H5 time dataset '{resolved_time}' was not found.")

            time_ds = h5[resolved_time]
            time_raw_full = pd.to_numeric(pd.Series(time_ds[()]), errors="coerce")
            if time_raw_full.isna().all():
                raise ValueError(f"H5 dataset '{resolved_time}' contains no numeric values.")

            time_dt_full = _h5_times_to_datetime(time_raw_full, time_ds)
            valid = time_dt_full.notna() & _h5_epoch_valid_mask(time_raw_full)
            if not valid.any():
                raise ValueError(f"H5 dataset '{resolved_time}' contains no valid timestamps.")

            time_dt = time_dt_full[valid].reset_index(drop=True)
            frame: dict[str, pd.Series] = {"__time__": time_dt}
            n_full = len(time_raw_full)

            def _collect(name: str, obj) -> None:
                if not isinstance(obj, h5py.Dataset):
                    return
                if name == resolved_time:
                    return
                values = obj[()]
                s = pd.Series(values)
                if s.ndim != 1 or len(s) != n_full:
                    return
                s = pd.to_numeric(s, errors="coerce")
                s = s[valid].reset_index(drop=True)
                if s.notna().sum() == 0:
                    return
                frame[name] = s

            h5.visititems(_collect)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Unable to read H5 contents: {exc}") from exc

    df = pd.DataFrame(frame)
    df = df.dropna(subset=["__time__"]).sort_values("__time__")
    if len(df.columns) <= 1:
        raise ValueError("H5 file has no numeric channels aligned with the selected time dataset.")
    return df


def _parquet_table(file_path: str):
    import pyarrow.parquet as pq

    return pq.read_table(file_path)


def _arrow_table(file_path: str):
    import pyarrow as pa
    import pyarrow.ipc as ipc

    path = Path(file_path)
    with path.open("rb") as fh:
        magic = fh.read(6)
        fh.seek(0)
        if magic == b"ARROW1":
            try:
                reader = ipc.open_file(fh)
                return reader.read_all()
            except Exception:
                fh.seek(0)
                reader = ipc.open_stream(fh)
                return reader.read_all()
        if magic[:4] == b"FEA1":
            import pyarrow.feather as feather

            return feather.read_table(path)
        try:
            reader = ipc.open_file(fh)
            return reader.read_all()
        except Exception:
            fh.seek(0)
            return pa.parquet.read_table(path)


def _tabular_frame_from_arrow(
    file_path: str,
    *,
    time_col: str | None = None,
    source_type: str,
) -> pd.DataFrame:
    table = _parquet_table(file_path) if source_type == "parquet" else _arrow_table(file_path)
    df = table.to_pandas()
    if df.empty:
        raise ValueError(f"{source_type.upper()} file has no rows.")

    resolved_time_col = resolve_time_column_name(list(df.columns), time_col)
    if not resolved_time_col:
        raise ValueError(
            f"{source_type.upper()} requires a time column or explicit time_index_channel."
        )

    if pd.api.types.is_numeric_dtype(df[resolved_time_col]):
        if resolved_time_col == "x_ms":
            df["__time__"] = pd.to_datetime(df[resolved_time_col], utc=True, errors="coerce", unit="ms")
        else:
            df["__time__"] = pd.to_datetime(df[resolved_time_col], utc=True, errors="coerce", unit="s")
    else:
        df["__time__"] = pd.to_datetime(df[resolved_time_col], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["__time__"]).sort_values("__time__")
    if df.empty:
        raise ValueError(f"Time column '{resolved_time_col}' could not be parsed.")
    return df


def _tabular_channel_units(file_path: str, source_type: str) -> dict[str, str | None]:
    table = _parquet_table(file_path) if source_type == "parquet" else _arrow_table(file_path)
    units: dict[str, str | None] = {}
    for field in table.schema:
        meta = field.metadata
        unit = None
        if meta:
            for key in (b"unit", b"units", "unit", "units"):
                if key in meta:
                    raw = meta[key]
                    unit = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
                    unit = unit or None
                    break
        units[field.name] = unit
    return units


def file_tests(source_type: str, file_path: str) -> list[TestRunItem]:
    p = Path(file_path)
    if source_type == "csv":
        df, _ = _csv_frame(file_path, units_in_headers=False)
        if df.empty:
            return []
        t0 = df["__time__"].iloc[0].to_pydatetime()
        t1 = df["__time__"].iloc[-1].to_pydatetime()
        dur = (t1 - t0).total_seconds()
        return [TestRunItem(test_run_id=1, run_code=p.stem, start_time=t0, end_time=t1, duration_s=dur, t0_utc=t0)]

    if source_type == "h5":
        df = _h5_frame(file_path)
        if df.empty:
            return []
        t0 = df["__time__"].iloc[0].to_pydatetime()
        t1 = df["__time__"].iloc[-1].to_pydatetime()
        dur = (t1 - t0).total_seconds()
        return [TestRunItem(test_run_id=1, run_code=p.stem, start_time=t0, end_time=t1, duration_s=dur, t0_utc=t0)]

    if source_type == "tdms":
        from nptdms import TdmsFile

        tdms = TdmsFile.read(file_path)
        first_time: datetime | None = None
        last_time: datetime | None = None
        for group in tdms.groups():
            for ch in group.channels():
                try:
                    tt = ch.time_track()
                    if len(tt) == 0:
                        continue
                    wf_start = ch.properties.get("wf_start_time")
                    if wf_start is None:
                        continue
                    st = pd.Timestamp(wf_start).tz_convert(timezone.utc).to_pydatetime()
                    en = st + pd.to_timedelta(float(tt[-1]), unit="s")
                    first_time = st if first_time is None or st < first_time else first_time
                    last_time = en if last_time is None or en > last_time else last_time
                except Exception:
                    continue
        if first_time is None or last_time is None:
            now = datetime.now(timezone.utc)
            first_time = now
            last_time = now
        return [TestRunItem(test_run_id=1, run_code=p.stem, start_time=first_time, end_time=last_time, duration_s=(last_time-first_time).total_seconds(), t0_utc=first_time)]

    if source_type in {"parquet", "arrow"}:
        df = _tabular_frame_from_arrow(file_path, source_type=source_type)
        if df.empty:
            return []
        t0 = df["__time__"].iloc[0].to_pydatetime()
        t1 = df["__time__"].iloc[-1].to_pydatetime()
        dur = (t1 - t0).total_seconds()
        return [TestRunItem(test_run_id=1, run_code=p.stem, start_time=t0, end_time=t1, duration_s=dur, t0_utc=t0)]

    return []


def file_channels(
    source_type: str,
    file_path: str,
    units_in_headers: bool = False,
    *,
    time_index_channel: str | None = None,
) -> list[ChannelItem]:
    if source_type == "csv":
        df, unit_map = _csv_frame(
            file_path,
            units_in_headers=units_in_headers,
            time_col=time_index_channel,
        )
        time_col = resolve_time_column_name(list(df.columns), time_index_channel)
        channels: list[ChannelItem] = []
        idx = 1
        for col in df.columns:
            if col in {"__time__", time_col}:
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                channels.append(
                    ChannelItem(
                        channel_id=idx,
                        channel_name=col,
                        display_name=col,
                        unit=unit_map.get(col),
                    )
                )
                idx += 1
        return channels

    if source_type == "h5":
        datasets = {row["path"]: row for row in _h5_datasets(file_path)}
        df = _h5_frame(file_path)
        channels: list[ChannelItem] = []
        idx = 1
        for col in df.columns:
            if col == "__time__":
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                unit = datasets.get(col, {}).get("unit")
                channels.append(ChannelItem(channel_id=idx, channel_name=col, display_name=col, unit=unit))
                idx += 1
        return channels

    if source_type == "tdms":
        from nptdms import TdmsFile

        tdms = TdmsFile.read(file_path)
        channels: list[ChannelItem] = []
        idx = 1
        for group in tdms.groups():
            for ch in group.channels():
                channels.append(ChannelItem(channel_id=idx, channel_name=f"{group.name}/{ch.name}", display_name=ch.name, unit=str(ch.properties.get("unit_string", "")) or None))
                idx += 1
        return channels

    if source_type in {"parquet", "arrow"}:
        df = _tabular_frame_from_arrow(
            file_path,
            source_type=source_type,
            time_col=time_index_channel,
        )
        time_col = resolve_time_column_name(list(df.columns), time_index_channel)
        unit_map = _tabular_channel_units(file_path, source_type)
        channels: list[ChannelItem] = []
        idx = 1
        for col in df.columns:
            if col in {"__time__", time_col}:
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                channels.append(
                    ChannelItem(
                        channel_id=idx,
                        channel_name=col,
                        display_name=col,
                        unit=unit_map.get(col),
                    )
                )
                idx += 1
        return channels

    return []


def file_timeseries(
    source_type: str,
    file_path: str,
    channel_names: list[str],
    limit: int = 5_000_000,
    units_in_headers: bool = False,
) -> list[TimeSeriesPoint]:
    from ..engine.duckdb_source import fetch_artifact_timeseries
    from ..engine.session_store import find_artifact_for_path

    artifact_id = find_artifact_for_path(file_path)
    if artifact_id:
        points = fetch_artifact_timeseries(
            artifact_id,
            channel_names,
            mode="raw",
            max_points=None,
        )
        if limit and len(points) > limit:
            return points[:limit]
        return points

    warnings.warn(
        "file_timeseries used legacy iterrows path; ingest via POST /api/v3/ingest/file first.",
        stacklevel=2,
    )
    logger.warning("Legacy file_timeseries for %s (no artifact index)", file_path)

    if source_type == "csv":
        df, unit_map = _csv_frame(file_path, units_in_headers=units_in_headers)
        run_code = Path(file_path).stem
        rows: list[TimeSeriesPoint] = []
        for c in channel_names:
            if c not in df.columns:
                continue
            sub = df[["__time__", c]].dropna().iloc[:limit]
            for _, r in sub.iterrows():
                rows.append(
                    TimeSeriesPoint(
                        test_run_id=1,
                        test_run_code=run_code,
                        channel_name=c,
                        unit=unit_map.get(c),
                        time=r["__time__"].to_pydatetime(),
                        value=float(r[c]),
                    )
                )
        rows.sort(key=lambda x: x.time)
        return rows[:limit]

    if source_type == "h5":
        df = _h5_frame(file_path)
        run_code = Path(file_path).stem
        rows: list[TimeSeriesPoint] = []
        for c in channel_names:
            if c not in df.columns:
                continue
            sub = df[["__time__", c]].dropna().iloc[:limit]
            for _, r in sub.iterrows():
                rows.append(TimeSeriesPoint(test_run_id=1, test_run_code=run_code, channel_name=c, unit=None, time=r["__time__"].to_pydatetime(), value=float(r[c])))
        rows.sort(key=lambda x: x.time)
        return rows[:limit]

    if source_type == "tdms":
        from nptdms import TdmsFile

        tdms = TdmsFile.read(file_path)
        run_code = Path(file_path).stem
        selected = set(channel_names)
        out: list[TimeSeriesPoint] = []
        for group in tdms.groups():
            for ch in group.channels():
                name = f"{group.name}/{ch.name}"
                if name not in selected:
                    continue
                try:
                    values = ch[:]  # type: ignore[index]
                    tt = []
                    if hasattr(ch, "time_track"):
                        try:
                            tt = ch.time_track()
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
                        iter_times = [st + pd.to_timedelta(float(tt[i]), unit="s") for i in range(n)]
                    else:
                        step_s = float(wf_increment) if wf_increment is not None else 0.001
                        n = len(values)
                        iter_times = [st + pd.to_timedelta(i * step_s, unit="s") for i in range(n)]
                    for i in range(min(n, limit)):
                        t = iter_times[i].to_pydatetime()
                        out.append(TimeSeriesPoint(test_run_id=1, test_run_code=run_code, channel_name=name, unit=str(ch.properties.get("unit_string", "")) or None, time=t, value=float(values[i])))
                except Exception:
                    continue
        out.sort(key=lambda x: x.time)
        return out[:limit]

    if source_type in {"parquet", "arrow"}:
        df = _tabular_frame_from_arrow(file_path, source_type=source_type)
        unit_map = _tabular_channel_units(file_path, source_type)
        run_code = Path(file_path).stem
        rows: list[TimeSeriesPoint] = []
        for c in channel_names:
            if c not in df.columns:
                continue
            sub = df[["__time__", c]].dropna().iloc[:limit]
            for _, r in sub.iterrows():
                rows.append(
                    TimeSeriesPoint(
                        test_run_id=1,
                        test_run_code=run_code,
                        channel_name=c,
                        unit=unit_map.get(c),
                        time=r["__time__"].to_pydatetime(),
                        value=float(r[c]),
                    )
                )
        rows.sort(key=lambda x: x.time)
        return rows[:limit]

    return []
