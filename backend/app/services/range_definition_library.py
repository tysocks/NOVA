"""Range definition library: formula-based range trees for ingestion."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

_OCCURRENCES = {"first", "last", "all"}
_EDGES = {"rising", "falling", "either", "none"}
_OPS = {"=", ">", "<", "!=", ">=", "<=", "between", "outside"}


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


def _build_expression(channels: list[str], formula: str, op: str, value: Any, value_b: Any = None) -> str:
    expr = (formula or "").strip() or ("A" if channels else "")
    # Prefer a recoverable channel name when the formula is the trivial single-letter form.
    if len(channels) == 1 and (not expr or expr.upper() == "A"):
        expr = channels[0]
    elif not expr and channels:
        expr = channels[0]
    op = (op or ">").strip() or ">"
    if op in {"between", "outside"} and value_b is not None and str(value_b).strip() != "":
        return f"{expr} {op} {value}..{value_b}"
    return f"{expr} {op} {value}".strip()


def _clean_condition(raw: Any, *, label: str) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    channels = _as_str_list(data.get("channels"))
    formula = str(data.get("formula") or "").strip()
    op = str(data.get("op") or ">").strip() or ">"
    if op not in _OPS:
        raise ValueError(f"{label} op must be one of {sorted(_OPS)}")
    value = data.get("value", data.get("valueA"))
    value_b = data.get("value_b", data.get("valueB"))
    expression = str(data.get("expression") or data.get("formula_text") or "").strip()

    # Recover channel name from expression when channels were stripped by an older saver.
    if not channels and expression:
        m = re.match(
            r"^([A-Za-z_][\w.\-]*)\s*(>=|<=|!=|=|>|<|between|outside)\s*(.+)$",
            expression,
            flags=re.IGNORECASE,
        )
        if m:
            token = m.group(1)
            # Single letters are formula vars (A/B/C), not channel names.
            if not re.fullmatch(r"[A-Za-z]", token):
                channels = [token]
                if not formula:
                    formula = "A"
                op = m.group(2)
                rest = m.group(3).strip()
                if op in {"between", "outside"} and value is None:
                    parts = re.split(r"\.\.|to|,", rest, maxsplit=1, flags=re.IGNORECASE)
                    if parts:
                        value = parts[0].strip()
                    if len(parts) > 1:
                        value_b = parts[1].strip()
                elif value is None:
                    value = rest

    if channels:
        if value is None or str(value).strip() == "":
            # Allow legacy expression-only rows that also listed channels.
            if not expression:
                raise ValueError(f"{label} value is required")
        else:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} value must be a number") from exc
            if op in {"between", "outside"}:
                try:
                    value_b = float(value_b)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{label} max value is required for {op}") from exc
            else:
                value_b = None
            if not formula:
                formula = "A"
            expression = _build_expression(channels, formula, op, value, value_b)
    else:
        if not expression:
            raise ValueError(f"{label} requires channels or an expression")
        # Keep optional numeric fields when present for apply-time evaluation.
        if value is not None and str(value).strip() != "":
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        else:
            value = None
        if value_b is not None and str(value_b).strip() != "":
            try:
                value_b = float(value_b)
            except (TypeError, ValueError):
                value_b = None
        else:
            value_b = None

    occurrence = str(data.get("occurrence") or "first").strip().lower() or "first"
    if occurrence not in _OCCURRENCES:
        raise ValueError(f"{label} occurrence must be first, last, or all")
    edge = str(data.get("edge") or "none").strip().lower() or "none"
    if edge not in _EDGES:
        raise ValueError(f"{label} edge must be rising, falling, either, or none")

    out: dict[str, Any] = {
        "expression": expression,
        "occurrence": occurrence,
        "edge": edge,
        "channels": channels,
        "formula": formula or ("A" if channels else ""),
        "op": op,
    }
    if value is not None:
        out["value"] = value
    if value_b is not None:
        out["value_b"] = value_b
    return out


def _clean_node(row: Any, *, def_name: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"Invalid node in range definition '{def_name}'")
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError(f"Range node name is required in '{def_name}'")
    parent_id = row.get("parent_id")
    parent_id = str(parent_id).strip() if parent_id not in (None, "") else None
    node: dict[str, Any] = {
        "id": str(row.get("id") or uuid.uuid4()),
        "name": name,
        "parent_id": parent_id,
        "start": _clean_condition(row.get("start"), label=f"'{name}' start"),
        "end": _clean_condition(row.get("end"), label=f"'{name}' end"),
    }
    label = str(row.get("label") or "").strip()
    if label:
        node["label"] = label
    color = str(row.get("color") or "").strip()
    if color:
        node["color"] = color
    return node


def _validate_tree(nodes: list[dict[str, Any]], *, def_name: str) -> None:
    ids = {n["id"] for n in nodes}
    if len(ids) != len(nodes):
        raise ValueError(f"Duplicate node ids in range definition '{def_name}'")
    for node in nodes:
        parent_id = node.get("parent_id")
        if parent_id is None:
            continue
        if parent_id not in ids:
            raise ValueError(
                f"Node '{node['name']}' parent_id '{parent_id}' not found in '{def_name}'"
            )
        if parent_id == node["id"]:
            raise ValueError(f"Node '{node['name']}' cannot be its own parent")
    children: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for node in nodes:
        if node.get("parent_id"):
            children[node["parent_id"]].append(node["id"])
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(nid: str) -> None:
        if nid in visiting:
            raise ValueError(f"Cycle detected in range definition '{def_name}'")
        if nid in visited:
            return
        visiting.add(nid)
        for child in children.get(nid, []):
            walk(child)
        visiting.remove(nid)
        visited.add(nid)

    for nid in ids:
        walk(nid)


def clean_range_definition(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError("range definition name is required")
    raw_nodes = row.get("nodes")
    nodes_in = raw_nodes if isinstance(raw_nodes, list) else []
    nodes = [_clean_node(n, def_name=name) for n in nodes_in]
    _validate_tree(nodes, def_name=name)
    return {
        "id": str(row.get("id") or uuid.uuid4()),
        "name": name,
        "icon": str(row.get("icon") or "").strip(),
        "description": str(row.get("description") or "").strip() or None,
        "updated_at": str(row.get("updated_at") or "").strip() or _now_iso(),
        "nodes": nodes,
    }


def clean_range_definition_rows(rows: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cleaned.append(clean_range_definition(row))
    return cleaned
