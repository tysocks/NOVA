"""Configuration library: reusable channel/mask setups for new datasets."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_str_list(value: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _clean_mask(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    kind = str(row.get("kind") or "formula").strip().lower() or "formula"
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    mask: dict[str, Any] = {
        "id": str(row.get("id") or uuid.uuid4()),
        "kind": kind,
        "name": name,
        "channels": _as_str_list(row.get("channels")),
        "op": str(row.get("op") or "=").strip() or "=",
        "valueA": row.get("valueA") if row.get("valueA") is not None else None,
        "valueB": row.get("valueB") if row.get("valueB") is not None else None,
    }
    if kind == "formula":
        mask["formula"] = str(row.get("formula") or "").strip()
    if kind == "rolling":
        mask["sourceChannel"] = str(row.get("sourceChannel") or "").strip()
        mask["rollingOp"] = str(row.get("rollingOp") or "").strip()
        mask["window"] = row.get("window")
    if kind == "range":
        range_name = str(row.get("rangeName") or "").strip()
        if not range_name:
            return None
        mask["rangeName"] = range_name
        mask["includeMissingSources"] = bool(row.get("includeMissingSources"))
        mask["channels"] = []
        mask["op"] = "="
        mask["valueA"] = None
        mask["valueB"] = None
    return mask


def _extract_legacy_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Support older configs that nested state under ``payload`` / ``app``."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    selections = payload.get("activeSelections") if isinstance(payload.get("activeSelections"), dict) else {}
    channels = row.get("channels")
    if channels is None:
        channels = selections.get("addedChannelIds") or app.get("channels")
    calculated = row.get("calculatedChannels")
    if calculated is None:
        calculated = app.get("calculatedChannels") or payload.get("calculatedChannels")
    aliases = row.get("channelAliases")
    if aliases is None:
        aliases = app.get("channelAliases") or payload.get("channelAliases")
    masks = row.get("masks")
    if masks is None:
        masks = app.get("masks") or payload.get("masks")
    return {
        "channels": channels,
        "calculatedChannels": calculated,
        "channelAliases": aliases,
        "masks": masks,
    }


def clean_config_row(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "").strip() or "Unnamed Config"
    legacy = _extract_legacy_payload(row) if ("payload" in row or "app" in row) else {
        "channels": row.get("channels"),
        "calculatedChannels": row.get("calculatedChannels"),
        "channelAliases": row.get("channelAliases"),
        "masks": row.get("masks"),
    }
    masks = [m for m in (_clean_mask(x) for x in _as_list(legacy["masks"])) if m]
    calculated = [c for c in _as_list(legacy["calculatedChannels"]) if isinstance(c, dict)]
    aliases = [a for a in _as_list(legacy["channelAliases"]) if isinstance(a, dict)]
    updated = str(row.get("updated_at") or row.get("savedAt") or "").strip() or _now_iso()
    return {
        "id": str(row.get("id") or uuid.uuid4()),
        "name": name,
        "icon": str(row.get("icon") or "").strip(),
        "updated_at": updated,
        "channels": _as_str_list(legacy["channels"]),
        "calculatedChannels": calculated,
        "channelAliases": aliases,
        "masks": masks,
    }


def clean_config_library_rows(rows: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cleaned.append(clean_config_row(row))
    return cleaned
