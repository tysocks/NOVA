"""TDMS high-rate clocks must write Parquet without Arrow us-cast failures."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.engine.file_index import _coerce_utc_timestamps_us, _write_channel_parquet


def test_write_channel_parquet_accepts_sub_microsecond_noise(tmp_path: Path):
    start = pd.Timestamp("2025-04-26T12:00:00", tz="UTC")
    # Mimic float wf_increment=0.0005 producing non-exact us boundaries in ns.
    times = pd.to_datetime(
        [start + pd.to_timedelta(i * 0.0005, unit="s") for i in range(200)],
        utc=True,
    )
    values = pd.Series(range(200), dtype="float64")
    out = tmp_path / "chamber_pressure.parquet"
    n = _write_channel_parquet(out, times, values, unit="Pa")
    assert n == 200
    assert out.stat().st_size > 0
    import pyarrow.parquet as pq

    df = pq.read_table(out).to_pandas()
    assert abs(float(df["x_ms"].iloc[1] - df["x_ms"].iloc[0]) - 0.5) < 1e-6
    assert abs(float(df["x_ms"].iloc[-1] - df["x_ms"].iloc[0]) - 99.5) < 1e-3


def test_times_to_epoch_ms_handles_microsecond_datetime_dtype():
    from app.engine.file_index import _times_to_epoch_ms

    start = pd.Timestamp("2025-04-26T12:00:00", tz="UTC")
    times = pd.date_range(start=start, periods=5, freq=pd.Timedelta(microseconds=500))
    xs = _times_to_epoch_ms(times)
    assert abs(float(xs.iloc[1] - xs.iloc[0]) - 0.5) < 1e-9
    # Must be epoch milliseconds, not seconds.
    assert float(xs.iloc[0]) > 1e12


def test_coerce_utc_timestamps_us_floors_to_microseconds():
    messy = pd.Series(
        [pd.Timestamp(1745668800500499999, unit="ns", tz="UTC")],
    )
    cleaned = _coerce_utc_timestamps_us(messy)
    assert int(cleaned.astype("int64").iloc[0]) % 1000 == 0
