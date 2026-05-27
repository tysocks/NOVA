from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..models import ChannelItem, TestRunItem, TimeSeriesPoint


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


def _csv_frame(file_path: str, units_in_headers: bool = False) -> tuple[pd.DataFrame, dict[str, str | None]]:
    try:
        df = pd.read_csv(file_path)
    except Exception as exc:
        raise ValueError(f"Unable to read CSV contents: {exc}") from exc

    if df.empty:
        raise ValueError("CSV file has no data rows.")

    time_col = None
    for c in ("timestamp_utc", "time", "timestamp", "datetime", "time_s"):
        if c in df.columns:
            time_col = c
            break
    if not time_col:
        raise ValueError("CSV requires a time column (timestamp_utc/time/timestamp/datetime/time_s).")
    # Numeric time columns are interpreted as seconds, not nanoseconds.
    # This keeps simulation-style elapsed seconds (0..N) readable on plots.
    if pd.api.types.is_numeric_dtype(df[time_col]):
        df["__time__"] = pd.to_datetime(df[time_col], utc=True, errors="coerce", unit="s")
    else:
        # Use mixed parsing so ISO strings with and without fractional seconds are both accepted.
        df["__time__"] = pd.to_datetime(df[time_col], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["__time__"]).sort_values("__time__")
    if df.empty:
        raise ValueError(f"CSV time column '{time_col}' could not be parsed as timestamps.")

    unit_map: dict[str, str | None] = {}
    if units_in_headers:
        # Rename numeric columns by stripping unit suffix/prefix patterns.
        rename: dict[str, str] = {}
        counts: dict[str, int] = {}
        for col in df.columns:
            if col in {"__time__", time_col}:
                continue
            base, unit = _split_name_unit(col)
            if not base:
                continue
            # Ensure uniqueness after stripping units.
            key = base
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > 1:
                key = f"{base}_{counts[base]}"
            rename[col] = key
            unit_map[key] = unit
        if rename:
            df = df.rename(columns=rename)

    return df, unit_map


def _h5_frame(file_path: str) -> pd.DataFrame:
    try:
        import h5py
    except Exception as exc:
        raise ValueError(f"Unable to read H5 contents: {exc}") from exc

    try:
        with h5py.File(file_path, "r") as h5:
            if "telemetry/TIME" not in h5:
                raise ValueError("H5 requires dataset 'telemetry/TIME' for the time axis.")

            time_raw = pd.to_numeric(pd.Series(h5["telemetry/TIME"][()]), errors="coerce")
            if time_raw.isna().all():
                raise ValueError("H5 dataset 'telemetry/TIME' contains no numeric values.")

            frame: dict[str, pd.Series] = {
                "__time__": pd.to_datetime(time_raw, utc=True, errors="coerce", unit="s")
            }

            def _collect(name: str, obj) -> None:
                if not isinstance(obj, h5py.Dataset):
                    return
                if name == "telemetry/TIME":
                    return
                values = obj[()]
                s = pd.Series(values)
                if s.ndim != 1 or len(s) != len(time_raw):
                    return
                s = pd.to_numeric(s, errors="coerce")
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
        raise ValueError("H5 file has no numeric channels aligned with telemetry/TIME.")
    return df


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

    return []


def file_channels(source_type: str, file_path: str, units_in_headers: bool = False) -> list[ChannelItem]:
    if source_type == "csv":
        df, unit_map = _csv_frame(file_path, units_in_headers=units_in_headers)
        channels: list[ChannelItem] = []
        idx = 1
        for col in df.columns:
            if col == "__time__":
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
        df = _h5_frame(file_path)
        channels: list[ChannelItem] = []
        idx = 1
        for col in df.columns:
            if col == "__time__":
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                channels.append(ChannelItem(channel_id=idx, channel_name=col, display_name=col, unit=None))
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

    return []


def file_timeseries(
    source_type: str,
    file_path: str,
    channel_names: list[str],
    limit: int = 5_000_000,
    units_in_headers: bool = False,
) -> list[TimeSeriesPoint]:
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

    return []
