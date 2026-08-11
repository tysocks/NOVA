"""Build CSV / Parquet payloads from plotted series for product export."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq


def _iso_utc_from_x(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.endswith("Z") or "+" in text[10:] or text.endswith("UTC"):
            return text
        try:
            numeric = float(text)
        except ValueError:
            return text
        value = numeric
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not (numeric == numeric):  # NaN
        return ""
    ms = numeric if numeric > 1e11 else numeric * 1000.0
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def iter_export_rows(series: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in series or []:
        if not isinstance(item, dict):
            continue
        xs = item.get("x") or item.get("x_ms") or []
        ys = item.get("y") or []
        name = str(item.get("name") or item.get("channel_name") or "channel")
        unit = str(item.get("unit") or "")
        source = str(item.get("source") or item.get("test_run_code") or "")
        count = min(len(xs), len(ys))
        for i in range(count):
            y = _safe_float(ys[i])
            if y is None:
                continue
            rows.append(
                {
                    "time_utc": _iso_utc_from_x(xs[i]),
                    "source": source,
                    "channel": name,
                    "value": y,
                    "unit": unit,
                }
            )
    return rows


def build_series_csv(series: list[dict]) -> bytes:
    rows = iter_export_rows(series)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["time_utc", "source", "channel", "value", "unit"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def build_series_parquet(series: list[dict]) -> bytes:
    rows = iter_export_rows(series)
    table = pa.table(
        {
            "time_utc": [r["time_utc"] for r in rows],
            "source": [r["source"] for r in rows],
            "channel": [r["channel"] for r in rows],
            "value": pa.array([r["value"] for r in rows], type=pa.float64()),
            "unit": [r["unit"] for r in rows],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()
