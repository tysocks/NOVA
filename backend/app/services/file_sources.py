from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..models import ChannelItem, TestRunItem, TimeSeriesPoint


def _to_dt(series: pd.Series) -> pd.Series:
    out = pd.to_datetime(series, utc=True, errors="coerce")
    return out.dropna()


def _csv_frame(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path)
    time_col = None
    for c in ("timestamp_utc", "time", "timestamp", "datetime"):
        if c in df.columns:
            time_col = c
            break
    if not time_col:
        raise ValueError("CSV requires a time column (timestamp_utc/time/timestamp/datetime).")
    # Use mixed parsing so ISO strings with and without fractional seconds are both accepted.
    df["__time__"] = pd.to_datetime(df[time_col], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["__time__"]).sort_values("__time__")
    return df


def file_tests(source_type: str, file_path: str) -> list[TestRunItem]:
    p = Path(file_path)
    if source_type == "csv":
        df = _csv_frame(file_path)
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


def file_channels(source_type: str, file_path: str) -> list[ChannelItem]:
    if source_type == "csv":
        df = _csv_frame(file_path)
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


def file_timeseries(source_type: str, file_path: str, channel_names: list[str], limit: int = 5_000_000) -> list[TimeSeriesPoint]:
    if source_type == "csv":
        df = _csv_frame(file_path)
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
