"""Ingestion rule library: channel selection, calcs, and ranges for permanent ingest."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

_CHANNEL_MODES = {"all", "include"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_str_list(value: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in value if isinstance(value, list) else []:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for raw_k, raw_v in value.items():
        k = str(raw_k or "").strip()
        v = str(raw_v or "").strip()
        if not k or not v:
            continue
        out[k] = v
    return out


def _clean_calculated_channel(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Calculated channel must be an object")
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError("Calculated channel name is required")
    kind = str(row.get("kind") or "formula").strip().lower() or "formula"
    if kind not in {"rolling", "formula"}:
        raise ValueError(f"Calculated channel '{name}' kind must be rolling or formula")
    channels = _as_str_list(row.get("channels"))
    if not channels:
        raise ValueError(f"Calculated channel '{name}' requires input channels")
    out: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "unit": (str(row.get("unit")).strip() if row.get("unit") not in (None, "") else None),
        "channels": channels,
    }
    if kind == "rolling":
        op = str(row.get("op") or "mean").strip().lower() or "mean"
        window = int(row.get("window") or 0)
        if window < 1:
            raise ValueError(f"Calculated channel '{name}' window must be >= 1")
        out["op"] = op
        out["window"] = window
    else:
        formula = str(row.get("formula") or "A").strip() or "A"
        out["formula"] = formula
    return out


def _clean_channels_block(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or "all").strip().lower() or "all"
    if mode not in _CHANNEL_MODES:
        raise ValueError("channels.mode must be 'all' or 'include'")
    include = _as_str_list(data.get("include"))
    exclude = _as_str_list(data.get("exclude"))
    rename = _as_str_map(data.get("rename"))
    require = _as_str_list(data.get("require"))
    if mode == "include" and not include:
        raise ValueError("channels.include is required when mode is 'include'")
    return {
        "mode": mode,
        "include": include,
        "exclude": exclude,
        "rename": rename,
        "require": require,
    }


def clean_ingestion_rule(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Ingestion rule must be an object")
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError("Ingestion rule name is required")
    target = str(row.get("target_catalog_id") or "").strip()
    if not target or target == "local":
        raise ValueError("Ingestion rule requires a project target_catalog_id")

    calcs_raw = row.get("calculated_channels") or row.get("calculatedChannels") or []
    if not isinstance(calcs_raw, list):
        raise ValueError("calculated_channels must be a list")
    calculated = [_clean_calculated_channel(c) for c in calcs_raw]

    range_def_ids = _as_str_list(
        row.get("range_definition_ids")
        or row.get("rangeDefinitionIds")
        or row.get("range_definitions")
        or []
    )
    range_rule_ids: list[int] = []
    for item in row.get("apply_range_rule_ids") or row.get("applyRangeRuleIds") or []:
        try:
            range_rule_ids.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError("apply_range_rule_ids must be integers") from exc

    parameters: dict[str, Any] = {}
    raw_params = row.get("parameters")
    if isinstance(raw_params, dict):
        for k, v in raw_params.items():
            key = str(k or "").strip()
            if not key:
                continue
            parameters[key] = v

    icon = str(row.get("icon") or "").strip()
    rule: dict[str, Any] = {
        "id": str(row.get("id") or uuid.uuid4()),
        "name": name,
        "icon": icon,
        "target_catalog_id": target,
        "channels": _clean_channels_block(row.get("channels")),
        "calculated_channels": calculated,
        "range_definition_ids": range_def_ids,
        "apply_range_rule_ids": range_rule_ids,
        "time_index_channel": (
            str(row.get("time_index_channel")).strip()
            if row.get("time_index_channel") not in (None, "")
            else None
        ),
        "units_in_headers": bool(row.get("units_in_headers") or False),
        "parameters": parameters,
        "updated_at": str(row.get("updated_at") or _now_iso()),
    }
    created = str(row.get("created_at") or "").strip()
    rule["created_at"] = created or rule["updated_at"]
    note = str(row.get("note") or row.get("description") or "").strip()
    if note:
        rule["note"] = note
    return rule


def clean_ingestion_rule_rows(rows: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        rule = clean_ingestion_rule(row)
        rid = rule["id"]
        if rid in seen:
            raise ValueError(f"Duplicate ingestion rule id: {rid}")
        seen.add(rid)
        cleaned.append(rule)
    cleaned.sort(key=lambda r: str(r.get("name") or "").lower())
    return cleaned


def resolve_channel_selection(
    available: list[str],
    channels_block: dict[str, Any] | None,
) -> tuple[list[str], dict[str, str]]:
    """Return (source_names_to_keep, source_name -> stored_name)."""
    block = channels_block if isinstance(channels_block, dict) else {"mode": "all"}
    mode = str(block.get("mode") or "all").strip().lower() or "all"
    include = {str(x).strip() for x in (block.get("include") or []) if str(x).strip()}
    exclude = {str(x).strip() for x in (block.get("exclude") or []) if str(x).strip()}
    rename = _as_str_map(block.get("rename"))
    require = [str(x).strip() for x in (block.get("require") or []) if str(x).strip()]
    available_set = {str(x).strip() for x in available if str(x).strip()}
    reverse_rename = {v: k for k, v in rename.items()}

    # Include tokens may be source names or already-renamed targets.
    include_sources: set[str] = set()
    for token in include:
        if token in available_set:
            include_sources.add(token)
        elif token in reverse_rename and reverse_rename[token] in available_set:
            include_sources.add(reverse_rename[token])

    selected: list[str] = []
    for name in available:
        key = str(name or "").strip()
        if not key or key in exclude:
            continue
        if mode == "include" and key not in include_sources:
            continue
        selected.append(key)

    mapping = {src: rename.get(src, src) for src in selected}

    stored = set(mapping.values())
    missing: list[str] = []
    for req in require:
        if req in stored or req in mapping:
            continue
        missing.append(req)
    if missing:
        raise ValueError(f"Required channels missing after selection: {', '.join(missing)}")

    if not selected:
        raise ValueError("Channel selection produced no channels to ingest.")
    return selected, mapping
