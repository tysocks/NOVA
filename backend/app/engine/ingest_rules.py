"""Post-ingest helpers: channel remap, calc materialization, meta.json, range defs."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from ..models import CalculatedChannelSpec, TimeSeriesPoint
from .calc_engine import apply_calculated_channels
from .calc_graph import order_calculated_channels
from .catalog_store import create_range, list_channel_parquet_entries
from .range_detect import _detect_threshold_ranges, _load_channel_frame
from .session_store import sanitize_channel_filename


def apply_channel_rename_and_filter(
    result: dict[str, Any],
    *,
    selected: list[str] | None,
    rename: dict[str, str] | None,
    data_root: Path,
) -> dict[str, Any]:
    """Filter/rename channel rows and parquet files in an ingest result."""
    selected_set = {str(x) for x in (selected or [])} if selected is not None else None
    rename_map = {str(k): str(v) for k, v in (rename or {}).items() if str(k).strip() and str(v).strip()}
    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)

    new_channels: list[dict[str, Any]] = []
    for row in result.get("channels") or []:
        if not isinstance(row, dict):
            continue
        src_name = str(row.get("channel_name") or "").strip()
        if not src_name:
            continue
        if selected_set is not None and src_name not in selected_set:
            # Delete unused parquet to save space when written ahead of filter.
            rel = str(row.get("parquet") or "")
            if rel:
                old = data_root / Path(rel).name
                if old.is_file():
                    try:
                        old.unlink()
                    except OSError:
                        pass
            continue

        dest_name = rename_map.get(src_name, src_name)
        rel = str(row.get("parquet") or "")
        old_path = data_root / Path(rel).name if rel else None
        new_fname = sanitize_channel_filename(dest_name) + ".parquet"
        new_path = data_root / new_fname
        if old_path and old_path.is_file():
            if old_path.resolve() != new_path.resolve():
                if new_path.exists():
                    new_path.unlink()
                shutil.move(str(old_path), str(new_path))
        new_channels.append(
            {
                **row,
                "channel_name": dest_name,
                "source_name": src_name,
                "parquet": f"data/{new_fname}",
                "kind": row.get("kind") or "raw",
            }
        )

    if not new_channels:
        raise ValueError("Channel filter/rename produced no channels.")
    out = dict(result)
    out["channels"] = new_channels
    out["data_dir"] = str(data_root.resolve())
    return out


def _points_from_parquet(
    *,
    channel_name: str,
    parquet_path: Path,
    unit: str | None,
    test_run_id: int,
    run_code: str,
) -> list[TimeSeriesPoint]:
    df = pl.read_parquet(parquet_path)
    if "x_ms" not in df.columns or "y" not in df.columns:
        return []
    rows = (
        df.select(
            pl.col("x_ms").cast(pl.Float64),
            pl.col("y").cast(pl.Float64, strict=False),
        )
        .drop_nulls()
        .sort("x_ms")
        .to_dicts()
    )
    out: list[TimeSeriesPoint] = []
    for row in rows:
        ms = float(row["x_ms"])
        out.append(
            TimeSeriesPoint(
                test_run_id=test_run_id,
                test_run_code=run_code,
                channel_name=channel_name,
                unit=unit,
                time=datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc),
                value=float(row["y"]),
            )
        )
    return out


def materialize_calculated_channels(
    result: dict[str, Any],
    specs: list[dict[str, Any]] | list[CalculatedChannelSpec],
    *,
    test_run_id: int = 1,
) -> dict[str, Any]:
    """Evaluate calc specs against ingested channels and write permanent parquet files."""
    if not specs:
        return result
    data_root = Path(result.get("data_dir") or "")
    if not data_root.is_dir():
        raise ValueError("Cannot materialize calculated channels without data_dir")

    typed: list[CalculatedChannelSpec] = []
    for raw in specs:
        if isinstance(raw, CalculatedChannelSpec):
            typed.append(raw)
        else:
            typed.append(CalculatedChannelSpec(**raw))
    ordered = order_calculated_channels(typed)

    run_code = str(result.get("run_code") or "run")
    channel_index = {
        str(r.get("channel_name")): r
        for r in (result.get("channels") or [])
        if isinstance(r, dict) and r.get("channel_name")
    }

    base_points: list[TimeSeriesPoint] = []
    needed: set[str] = set()
    for spec in ordered:
        needed.update(str(c) for c in (spec.channels or []))
    for name in needed:
        row = channel_index.get(name)
        if not row:
            continue
        path = data_root / Path(str(row.get("parquet") or "")).name
        if not path.is_file():
            continue
        base_points.extend(
            _points_from_parquet(
                channel_name=name,
                parquet_path=path,
                unit=row.get("unit"),
                test_run_id=test_run_id,
                run_code=run_code,
            )
        )

    # Evaluate one-by-one so later calcs can depend on earlier materialized names.
    working = list(base_points)
    new_rows = list(result.get("channels") or [])
    for spec in ordered:
        derived = apply_calculated_channels(working, [spec])
        if not derived:
            raise ValueError(f"Calculated channel '{spec.name}' produced no points")
        times = [p.time for p in derived]
        values = [p.value for p in derived]
        fname = sanitize_channel_filename(spec.name) + ".parquet"
        out_path = data_root / fname
        import pandas as pd
        from .file_index import _write_channel_parquet

        n = _write_channel_parquet(
            out_path,
            pd.Series(times),
            pd.Series(values),
            unit=spec.unit,
        )
        if n <= 0:
            raise ValueError(f"Calculated channel '{spec.name}' failed to write parquet")
        row = {
            "channel_name": spec.name,
            "unit": spec.unit,
            "parquet": f"data/{fname}",
            "point_count": n,
            "kind": "calculated",
            "formula": spec.formula if spec.kind == "formula" else f"{spec.op}(window={spec.window})",
            "inputs": list(spec.channels or []),
        }
        new_rows.append(row)
        channel_index[spec.name] = row
        working.extend(derived)

    out = dict(result)
    out["channels"] = new_rows
    return out


def write_ingest_meta(
    lake_run_dir: Path,
    *,
    manifest: dict[str, Any],
    result: dict[str, Any],
    rule: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    applied_ranges: list[dict[str, Any]] | None = None,
) -> Path:
    """Write meta.json beside the run's data/ folder for fast channel discovery."""
    lake_run_dir = Path(lake_run_dir)
    lake_run_dir.mkdir(parents=True, exist_ok=True)
    channels_meta: list[dict[str, Any]] = []
    for row in result.get("channels") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("channel_name") or "").strip()
        if not name:
            continue
        channels_meta.append(
            {
                "name": name,
                "unit": row.get("unit"),
                "parquet": str(row.get("parquet") or f"data/{sanitize_channel_filename(name)}.parquet"),
                "point_count": int(row.get("point_count") or 0),
                "sample_rate_hz": row.get("sample_rate_hz"),
                "source_name": row.get("source_name") or name,
                "kind": row.get("kind") or "raw",
                **(
                    {"formula": row.get("formula"), "inputs": row.get("inputs")}
                    if row.get("kind") == "calculated"
                    else {}
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "run_code": str(result.get("run_code") or manifest.get("run_code") or ""),
        "source_uri": str(manifest.get("file_path") or ""),
        "source_type": str(manifest.get("source_type") or ""),
        "artifact_id": str(manifest.get("artifact_id") or ""),
        "test_id": manifest.get("test_run_id"),
        "ingested_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "durability": str(manifest.get("durability") or "permanent"),
        "time_bounds": result.get("time_bounds") or manifest.get("time_bounds"),
        "rule_id": (rule or {}).get("id"),
        "rule_name": (rule or {}).get("name"),
        "channels": channels_meta,
        "ranges": applied_ranges or [],
        "parameters": parameters or {},
    }
    path = lake_run_dir / "meta.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _condition_to_threshold_config(cond: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    channels = [str(c).strip() for c in (cond.get("channels") or []) if str(c).strip()]
    if not channels:
        return None
    op = str(cond.get("op") or ">").strip()
    if op not in {">", ">=", "<", "<="}:
        return None
    if cond.get("value") is None:
        return None
    return channels[0], {"op": op, "value": float(cond["value"]), "sustain_ms": 0.0}


def apply_range_definitions_to_test(
    test_id: int,
    definitions: list[dict[str, Any]],
) -> list[Any]:
    """Apply simple threshold-style range definition nodes to a permanent catalog test."""
    from ..models import RangeCreateRequest

    created: list[Any] = []
    entries = {e["channel_name"]: e for e in list_channel_parquet_entries(test_id)}

    for definition in definitions or []:
        nodes = list(definition.get("nodes") or [])
        # Parents first when parent_id is null.
        nodes_sorted = sorted(nodes, key=lambda n: 0 if not n.get("parent_id") else 1)
        created_by_node: dict[str, list[Any]] = {}
        for node in nodes_sorted:
            if not isinstance(node, dict):
                continue
            start = node.get("start") if isinstance(node.get("start"), dict) else {}
            end = node.get("end") if isinstance(node.get("end"), dict) else {}
            start_cfg = _condition_to_threshold_config(start)
            end_cfg = _condition_to_threshold_config(end)
            if not start_cfg or not end_cfg:
                continue
            start_ch, start_thr = start_cfg
            end_ch, end_thr = end_cfg
            if start_ch not in entries or end_ch not in entries:
                continue
            start_df = _load_channel_frame(str(entries[start_ch]["parquet_uri"]))
            end_df = _load_channel_frame(str(entries[end_ch]["parquet_uri"]))
            start_ranges = _detect_threshold_ranges(start_df, start_thr)
            end_ranges = _detect_threshold_ranges(end_df, end_thr)
            # Pair: start at beginning of first active region; end at end of first later region.
            starts = [s for s, _ in start_ranges]
            ends = [e for _, e in end_ranges]
            occ_s = str(start.get("occurrence") or "first")
            occ_e = str(end.get("occurrence") or "first")
            if not starts or not ends:
                continue
            start_ms = starts[-1] if occ_s == "last" else starts[0]
            later_ends = [e for e in ends if e > start_ms]
            if not later_ends:
                continue
            end_ms = later_ends[-1] if occ_e == "last" else later_ends[0]
            if end_ms <= start_ms:
                continue

            parent_range_id = None
            parent_id = node.get("parent_id")
            if parent_id and str(parent_id) in created_by_node and created_by_node[str(parent_id)]:
                parent_range_id = created_by_node[str(parent_id)][0].range_id

            item = create_range(
                RangeCreateRequest(
                    test_id=test_id,
                    name=str(node.get("name") or "range"),
                    label=(str(node.get("label")).strip() if node.get("label") else None),
                    color=(str(node.get("color")).strip() if node.get("color") else None),
                    start_time=datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc),
                    end_time=datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc),
                    parent_range_id=parent_range_id,
                    source="rule",
                )
            )
            created.append(item)
            created_by_node.setdefault(str(node.get("id")), []).append(item)
    return created
