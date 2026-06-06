"""Apache Arrow IPC encoding for telemetry series."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import pyarrow as pa

from ..models import TimeSeriesPoint


def _time_to_epoch_ms(t: datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp() * 1000.0


def points_to_arrow_table(points: list[TimeSeriesPoint]) -> pa.Table:
    """
    Build a columnar table from row-oriented points.

    Columns: test_run_id, test_run_code, channel_name, unit, x_ms, y
    Rows are sorted by (test_run_id, channel_name, x_ms).
    """
    if not points:
        return pa.table(
            {
                "test_run_id": pa.array([], type=pa.int32()),
                "test_run_code": pa.array([], type=pa.string()),
                "channel_name": pa.array([], type=pa.string()),
                "unit": pa.array([], type=pa.null()),
                "x_ms": pa.array([], type=pa.float64()),
                "y": pa.array([], type=pa.float64()),
            }
        )

    ordered = sorted(points, key=lambda p: (p.test_run_id, p.channel_name, p.time))
    return pa.table(
        {
            "test_run_id": [p.test_run_id for p in ordered],
            "test_run_code": [p.test_run_code for p in ordered],
            "channel_name": [p.channel_name for p in ordered],
            "unit": [p.unit for p in ordered],
            "x_ms": [_time_to_epoch_ms(p.time) for p in ordered],
            "y": [float(p.value) for p in ordered],
        }
    )


def encode_series_arrow_ipc(points: list[TimeSeriesPoint]) -> bytes:
    """Serialize points to Arrow IPC stream bytes."""
    table = points_to_arrow_table(points)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def arrow_ipc_to_points(ipc_bytes: bytes) -> list[TimeSeriesPoint]:
    """Decode Arrow IPC stream bytes back into TimeSeriesPoint rows."""
    reader = pa.ipc.open_stream(ipc_bytes)
    table = reader.read_all()
    if table.num_rows == 0:
        return []

    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    required = {"test_run_id", "test_run_code", "channel_name", "x_ms", "y"}
    if not required.issubset(cols):
        raise ValueError(f"Arrow table missing required columns; got {list(cols)}")

    units = cols.get("unit", [None] * table.num_rows)
    out: list[TimeSeriesPoint] = []
    for i in range(table.num_rows):
        x_ms = cols["x_ms"][i]
        t = datetime.fromtimestamp(float(x_ms) / 1000.0, tz=timezone.utc)
        out.append(
            TimeSeriesPoint(
                test_run_id=int(cols["test_run_id"][i]),
                test_run_code=str(cols["test_run_code"][i]),
                channel_name=str(cols["channel_name"][i]),
                unit=units[i] if i < len(units) else None,
                time=t,
                value=float(cols["y"][i]),
            )
        )
    return out


def series_point_counts(points: list[TimeSeriesPoint]) -> dict[tuple[int, str], int]:
    """Count points per (test_run_id, channel_name)."""
    counts: dict[tuple[int, str], int] = defaultdict(int)
    for pt in points:
        counts[(pt.test_run_id, pt.channel_name)] += 1
    return dict(counts)
