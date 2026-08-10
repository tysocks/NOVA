"""Unit preference library: categories, preferred units, and conversion formulas."""

from __future__ import annotations

import ast
import operator
import uuid
from typing import Any

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_ALLOWED_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _unit_key(symbol: str | None) -> str:
    return str(symbol or "").strip().lower()


def _eval_ast(node: ast.AST, x: float) -> float:
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body, x)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id == "x":
        return float(x)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_ast(node.left, x), _eval_ast(node.right, x))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_eval_ast(node.operand, x))
    raise ValueError(f"unsupported expression: {ast.dump(node)}")


def eval_unit_formula(expr: str, x: float) -> float:
    tree = ast.parse(str(expr or "").strip(), mode="eval")
    out = _eval_ast(tree, x)
    if not isinstance(out, (int, float)) or not float(out) == float(out):
        raise ValueError("formula must return a finite number")
    return float(out)


def validate_unit_formula(expr: str) -> str:
    cleaned = str(expr or "").strip()
    if not cleaned:
        raise ValueError("formula is required")
    # Smoke-test at 0 and 1 so obviously broken expressions fail on save.
    for probe in (0.0, 1.0, 100.0):
        eval_unit_formula(cleaned, probe)
    return cleaned


def _clean_translation(row: dict[str, Any]) -> dict[str, str]:
    symbol = str(row.get("symbol") or "").strip()
    to_preferred = validate_unit_formula(str(row.get("to_preferred") or ""))
    if not symbol:
        raise ValueError("translation symbol is required")
    return {"symbol": symbol, "to_preferred": to_preferred}


def _clean_category(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "").strip()
    preferred = str(row.get("preferred") or "").strip()
    if not name:
        raise ValueError("category name is required")
    if not preferred:
        raise ValueError(f"preferred unit is required for '{name}'")
    raw_units = row.get("units")
    units_in: list = raw_units if isinstance(raw_units, list) else []
    units: list[dict[str, str]] = []
    seen: set[str] = set()
    pref_key = _unit_key(preferred)
    if pref_key:
        seen.add(pref_key)
    for unit_row in units_in:
        if not isinstance(unit_row, dict):
            continue
        cleaned = _clean_translation(unit_row)
        key = _unit_key(cleaned["symbol"])
        if not key or key in seen:
            continue
        seen.add(key)
        units.append(cleaned)
    return {
        "id": str(row.get("id") or uuid.uuid4()),
        "name": name,
        "icon": str(row.get("icon") or "").strip(),
        "preferred": preferred,
        "units": units,
    }


def clean_unit_library_rows(rows: list) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cleaned.append(_clean_category(row))
    return cleaned


def default_unit_library_rows() -> list[dict[str, Any]]:
    return clean_unit_library_rows(
        [
            {
                "name": "Temperature",
                "preferred": "K",
                "units": [
                    {"symbol": "C", "to_preferred": "x + 273"},
                    {"symbol": "F", "to_preferred": "(x - 32) * 5 / 9 + 273"},
                ],
            }
        ]
    )


def convert_value_to_preferred(value: float, from_unit: str | None, categories: list[dict[str, Any]]) -> float:
    if not isinstance(value, (int, float)) or not float(value) == float(value):
        return value
    key = _unit_key(from_unit)
    if not key:
        return float(value)
    for cat in categories:
        if _unit_key(cat.get("preferred")) == key:
            return float(value)
        for unit in cat.get("units") or []:
            if _unit_key(unit.get("symbol")) == key:
                return eval_unit_formula(str(unit.get("to_preferred") or ""), float(value))
    return float(value)


def resolve_display_unit(raw_unit: str | None, categories: list[dict[str, Any]]) -> str | None:
    key = _unit_key(raw_unit)
    if not key:
        return str(raw_unit).strip() if raw_unit else None
    for cat in categories:
        if _unit_key(cat.get("preferred")) == key:
            return str(cat.get("preferred") or "").strip() or None
        for unit in cat.get("units") or []:
            if _unit_key(unit.get("symbol")) == key:
                return str(cat.get("preferred") or "").strip() or None
    return str(raw_unit).strip() or None
