"""Temporary range sidecar store (session ranges.json + durable .nova_ranges.json)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import (
    RangeCreateRequest,
    RangeItem,
    RangeParameterItem,
    RangeParameterWrite,
    RangeUpdateRequest,
)
from .session_store import artifact_dir, load_manifest


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _normalize_tags(tags: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        text = str(tag or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def session_ranges_path(artifact_id: str) -> Path:
    return artifact_dir(artifact_id) / "ranges.json"


def durable_ranges_path(source_path: str | Path) -> Path:
    path = Path(source_path)
    return path.with_name(f"{path.stem}.nova_ranges.json")


def _empty_doc(artifact_id: str, source_path: str | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "artifact_id": artifact_id,
        "source_path": source_path,
        "ranges": [],
        "updated_at": _utc_now().isoformat(),
    }


def _read_doc(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    ranges = data.get("ranges")
    if ranges is None:
        data["ranges"] = []
    elif not isinstance(ranges, list):
        return None
    return data


def _write_doc(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(doc)
    doc["updated_at"] = _utc_now().isoformat()
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")


def _params_from_raw(range_id: int, raw: Any) -> list[RangeParameterItem]:
    out: list[RangeParameterItem] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append(RangeParameterItem(range_id=range_id, key=str(key), value_num=float(value)))
            else:
                out.append(
                    RangeParameterItem(
                        range_id=range_id,
                        key=str(key),
                        value_text=None if value is None else str(value),
                    )
                )
        return out
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            out.append(
                RangeParameterItem(
                    range_id=range_id,
                    key=key,
                    value_text=row.get("value_text"),
                    value_num=row.get("value_num"),
                )
            )
    return out


def _params_to_raw(parameters: list[RangeParameterWrite] | list[RangeParameterItem] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for param in parameters or []:
        out.append(
            {
                "key": param.key,
                "value_text": param.value_text,
                "value_num": param.value_num,
            }
        )
    return out


def _row_to_item(
    row: dict[str, Any],
    *,
    artifact_id: str,
    source_path: str | None = None,
) -> RangeItem:
    range_id = int(row.get("range_id") or 0)
    start = _parse_dt(row.get("start_time")) or _utc_now()
    end = _parse_dt(row.get("end_time")) or _utc_now()
    start_ms = row.get("start_ms")
    end_ms = row.get("end_ms")
    if start_ms is None:
        start_ms = start.timestamp() * 1000.0
    if end_ms is None:
        end_ms = end.timestamp() * 1000.0
    return RangeItem(
        range_id=range_id,
        test_id=None,
        artifact_id=artifact_id,
        durability="temporary",
        name=str(row.get("name") or f"range {range_id}"),
        label=row.get("label"),
        status=row.get("status"),
        start_time=start,
        end_time=end,
        start_ms=float(start_ms) if start_ms is not None else None,
        end_ms=float(end_ms) if end_ms is not None else None,
        color=row.get("color"),
        tags=_normalize_tags(row.get("tags") if isinstance(row.get("tags"), list) else []),
        parent_range_id=int(row["parent_range_id"]) if row.get("parent_range_id") is not None else None,
        source=str(row.get("source") or "user"),
        rule_id=row.get("rule_id"),
        notes=row.get("notes"),
        parameters=_params_from_raw(range_id, row.get("parameters") or row.get("metadata")),
    )


def _item_to_row(item: RangeItem) -> dict[str, Any]:
    return {
        "range_id": item.range_id,
        "name": item.name,
        "label": item.label,
        "status": item.status,
        "start_time": item.start_time.astimezone(timezone.utc).isoformat(),
        "end_time": item.end_time.astimezone(timezone.utc).isoformat(),
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "color": item.color,
        "tags": list(item.tags or []),
        "parent_range_id": item.parent_range_id,
        "source": item.source,
        "rule_id": item.rule_id,
        "notes": item.notes,
        "parameters": _params_to_raw(item.parameters),
    }


def _resolve_source_path(artifact_id: str, source_path: str | None = None) -> str | None:
    if source_path:
        return str(Path(source_path).expanduser().resolve())
    manifest = load_manifest(artifact_id) or {}
    fp = manifest.get("file_path")
    return str(Path(fp).expanduser().resolve()) if fp else None


def _load_or_create(artifact_id: str, source_path: str | None = None) -> dict[str, Any]:
    path = session_ranges_path(artifact_id)
    doc = _read_doc(path)
    resolved = _resolve_source_path(artifact_id, source_path)
    if doc is None:
        doc = _empty_doc(artifact_id, resolved)
    else:
        doc["artifact_id"] = artifact_id
        if resolved:
            doc["source_path"] = resolved
    return doc


def _persist(artifact_id: str, doc: dict[str, Any], source_path: str | None = None) -> None:
    resolved = _resolve_source_path(artifact_id, source_path or doc.get("source_path"))
    if resolved:
        doc["source_path"] = resolved
    _write_doc(session_ranges_path(artifact_id), doc)


def restore_ranges_from_durable_sidecar(artifact_id: str, source_path: str) -> bool:
    """Temporary file-source ranges are session-only and do not restore across app instances."""
    return False


def is_source_range_row(row: dict[str, Any] | RangeItem | None) -> bool:
    if row is None:
        return False
    if isinstance(row, RangeItem):
        return str(row.source or "") == "source"
    return str(row.get("source") or "") == "source"


def _find_source_range_id(rows: list[dict[str, Any]], *, exclude_id: int | None = None) -> int | None:
    for row in rows:
        if not isinstance(row, dict) or not is_source_range_row(row):
            continue
        rid = int(row.get("range_id") or -1)
        if exclude_id is not None and rid == int(exclude_id):
            continue
        return rid
    return None


def _reparent_root_ranges_under_source(rows: list[dict[str, Any]], source_range_id: int) -> bool:
    changed = False
    source_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict) and int(row.get("range_id") or -1) == int(source_range_id)
        ),
        None,
    )
    if source_row is None:
        return False
    source_item = _row_to_item(source_row, artifact_id=str(source_row.get("artifact_id") or ""))
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        rid = int(row.get("range_id") or -1)
        if rid == int(source_range_id):
            if row.get("parent_range_id") is not None:
                rows[i] = dict(row)
                rows[i]["parent_range_id"] = None
                changed = True
            continue
        if row.get("parent_range_id") is not None:
            continue
        child = _row_to_item(row, artifact_id="")
        if child.start_time < source_item.start_time or child.end_time > source_item.end_time:
            continue
        rows[i] = dict(row)
        rows[i]["parent_range_id"] = int(source_range_id)
        changed = True
    return changed


def _coerce_parent_to_source_range(
    rows: list[dict[str, Any]],
    *,
    start_time: datetime,
    end_time: datetime,
    exclude_id: int | None = None,
) -> int | None:
    source_id = _find_source_range_id(rows, exclude_id=exclude_id)
    if source_id is None:
        return None
    try:
        _validate_parent_containment(
            rows=rows,
            artifact_id="",
            range_id=exclude_id,
            parent_range_id=source_id,
            start_time=start_time,
            end_time=end_time,
        )
    except ValueError:
        return None
    return source_id


def ensure_temp_source_range(
    *,
    artifact_id: str,
    file_path: str | None,
    name: str,
    start_time: datetime,
    end_time: datetime,
    tags: list[str] | None = None,
) -> RangeItem:
    """Ensure a locked full-span source range exists and owns other root ranges."""
    doc = _load_or_create(artifact_id, file_path)
    rows = [row for row in (doc.get("ranges") or []) if isinstance(row, dict)]
    source_id = _find_source_range_id(rows)
    changed = False
    if source_id is None:
        # Promote the earliest legacy full-span root range (ingest used to create these as user ranges).
        legacy = next(
            (
                row
                for row in rows
                if isinstance(row, dict)
                and row.get("parent_range_id") is None
                and abs(float(row.get("start_ms") or 0.0) - float(start_time.timestamp() * 1000.0)) < 1e-6
                and abs(float(row.get("end_ms") or 0.0) - float(end_time.timestamp() * 1000.0)) < 1e-6
            ),
            None,
        )
        if legacy is None and rows:
            legacy = next(
                (
                    row
                    for row in rows
                    if isinstance(row, dict) and row.get("parent_range_id") is None
                ),
                None,
            )
            if legacy is not None and int(legacy.get("range_id") or -1) != 1:
                legacy = None
        if legacy is not None:
            idx = rows.index(legacy)
            promoted = dict(legacy)
            promoted["source"] = "source"
            promoted["parent_range_id"] = None
            promoted["name"] = name or str(promoted.get("name") or "Source")
            promoted["start_time"] = start_time.astimezone(timezone.utc).isoformat()
            promoted["end_time"] = end_time.astimezone(timezone.utc).isoformat()
            promoted["start_ms"] = float(start_time.timestamp() * 1000.0)
            promoted["end_ms"] = float(end_time.timestamp() * 1000.0)
            if tags is not None:
                promoted["tags"] = _normalize_tags(tags)
            rows[idx] = promoted
            source_id = int(promoted["range_id"])
            changed = True
        else:
            created = create_temp_range(
                RangeCreateRequest(
                    artifact_id=artifact_id,
                    file_path=file_path,
                    durability="temporary",
                    name=name,
                    start_time=start_time,
                    end_time=end_time,
                    tags=_normalize_tags(tags),
                    source="source",
                )
            )
            return created

    if source_id is not None and _reparent_root_ranges_under_source(rows, source_id):
        changed = True
    if changed:
        doc["ranges"] = rows
        _persist(artifact_id, doc, file_path)
    item = next(
        (
            _row_to_item(row, artifact_id=artifact_id, source_path=doc.get("source_path"))
            for row in rows
            if isinstance(row, dict) and int(row.get("range_id") or -1) == int(source_id)
        ),
        None,
    )
    if item is None:
        raise ValueError("Failed to ensure source range.")
    return item


def _validate_parent_containment(
    *,
    rows: list[dict[str, Any]],
    artifact_id: str,
    range_id: int | None,
    parent_range_id: int | None,
    start_time: datetime,
    end_time: datetime,
) -> None:
    if parent_range_id is None:
        return
    if range_id is not None and int(parent_range_id) == int(range_id):
        raise ValueError("A range cannot be its own parent.")
    parent_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict) and int(row.get("range_id") or -1) == int(parent_range_id)
        ),
        None,
    )
    if parent_row is None:
        raise ValueError(f"Parent range {parent_range_id} not found.")
    parent = _row_to_item(parent_row, artifact_id=artifact_id)
    if start_time < parent.start_time or end_time > parent.end_time:
        raise ValueError("Child ranges must be fully contained within the selected parent range.")


def list_temp_ranges(artifact_id: str, *, source_path: str | None = None) -> list[RangeItem]:
    doc = _load_or_create(artifact_id, source_path)
    resolved = doc.get("source_path")
    rows = [row for row in (doc.get("ranges") or []) if isinstance(row, dict)]
    if rows and _find_source_range_id(rows) is None:
        # Promote legacy ingest default (range_id 1, root) to a source range.
        legacy = next(
            (
                row
                for row in rows
                if isinstance(row, dict)
                and int(row.get("range_id") or -1) == 1
                and row.get("parent_range_id") is None
                and str(row.get("source") or "user") == "user"
            ),
            None,
        )
        if legacy is not None:
            idx = rows.index(legacy)
            promoted = dict(legacy)
            promoted["source"] = "source"
            rows[idx] = promoted
            _reparent_root_ranges_under_source(rows, 1)
            doc["ranges"] = rows
            _persist(artifact_id, doc, source_path or resolved)
    items = [
        _row_to_item(row, artifact_id=artifact_id, source_path=resolved)
        for row in (doc.get("ranges") or [])
        if isinstance(row, dict)
    ]
    items.sort(key=lambda r: (r.start_ms or 0.0, r.range_id))
    return items


def ensure_source_range_from_manifest(
    artifact_id: str,
    *,
    source_path: str | None = None,
) -> RangeItem | None:
    """Create/promote the locked source range for an already-indexed temporary artifact."""
    doc = _load_or_create(artifact_id, source_path)
    rows = [row for row in (doc.get("ranges") or []) if isinstance(row, dict)]
    existing_id = _find_source_range_id(rows)
    if existing_id is not None:
        return next(
            (
                _row_to_item(row, artifact_id=artifact_id, source_path=doc.get("source_path"))
                for row in rows
                if int(row.get("range_id") or -1) == int(existing_id)
            ),
            None,
        )
    manifest = load_manifest(artifact_id) or {}
    bounds = manifest.get("time_bounds") or {}
    start_ms = bounds.get("start_ms")
    end_ms = bounds.get("end_ms")
    if start_ms is None or end_ms is None:
        return None
    fp = Path(str(source_path or manifest.get("file_path") or "source"))
    name = str(fp.stem or fp.name or manifest.get("run_code") or "Source")
    return ensure_temp_source_range(
        artifact_id=artifact_id,
        file_path=str(source_path or manifest.get("file_path") or ""),
        name=name,
        start_time=datetime.fromtimestamp(float(start_ms) / 1000.0, tz=timezone.utc),
        end_time=datetime.fromtimestamp(float(end_ms) / 1000.0, tz=timezone.utc),
    )


def create_temp_range(request: RangeCreateRequest) -> RangeItem:
    artifact_id = str(request.artifact_id or "").strip()
    if not artifact_id:
        raise ValueError("artifact_id is required for temporary ranges.")
    if request.end_time <= request.start_time:
        raise ValueError("end_time must be after start_time.")
    doc = _load_or_create(artifact_id, request.file_path)
    rows = [row for row in (doc.get("ranges") or []) if isinstance(row, dict)]
    if request.source == "source" and _find_source_range_id(rows) is not None:
        raise ValueError("A source range already exists for this source.")
    next_id = 1
    for row in rows:
        if isinstance(row, dict) and row.get("range_id") is not None:
            next_id = max(next_id, int(row["range_id"]) + 1)
    start_ms = request.start_time.timestamp() * 1000.0
    end_ms = request.end_time.timestamp() * 1000.0
    parent_range_id = request.parent_range_id
    if request.source == "source":
        parent_range_id = None
    elif parent_range_id is None:
        parent_range_id = _coerce_parent_to_source_range(
            rows,
            start_time=request.start_time,
            end_time=request.end_time,
        )
    item = RangeItem(
        range_id=next_id,
        test_id=None,
        artifact_id=artifact_id,
        durability="temporary",
        name=request.name,
        label=request.label,
        status=request.status,
        start_time=request.start_time,
        end_time=request.end_time,
        start_ms=start_ms,
        end_ms=end_ms,
        color=request.color,
        tags=_normalize_tags(request.tags),
        parent_range_id=parent_range_id,
        source=request.source,
        rule_id=request.rule_id,
        notes=request.notes,
        parameters=[
            RangeParameterItem(
                range_id=next_id,
                key=p.key,
                value_text=p.value_text,
                value_num=p.value_num,
            )
            for p in request.parameters
        ],
    )
    _validate_parent_containment(
        rows=rows,
        artifact_id=artifact_id,
        range_id=None,
        parent_range_id=item.parent_range_id,
        start_time=item.start_time,
        end_time=item.end_time,
    )
    rows.append(_item_to_row(item))
    if request.source == "source":
        _reparent_root_ranges_under_source(rows, item.range_id)
    doc["ranges"] = rows
    _persist(artifact_id, doc, request.file_path)
    return item


def update_temp_range(range_id: int, request: RangeUpdateRequest) -> RangeItem:
    artifact_id = str(request.artifact_id or "").strip()
    if not artifact_id:
        raise ValueError("artifact_id is required for temporary ranges.")
    doc = _load_or_create(artifact_id, request.file_path)
    rows = [row for row in (doc.get("ranges") or []) if isinstance(row, dict)]
    idx = next((i for i, row in enumerate(rows) if int(row.get("range_id") or -1) == range_id), None)
    if idx is None:
        raise ValueError(f"Range {range_id} not found.")
    existing = _row_to_item(rows[idx], artifact_id=artifact_id, source_path=doc.get("source_path"))
    name = request.name if request.name is not None else existing.name
    label = request.label if request.label is not None else existing.label
    status = request.status if request.status is not None else existing.status
    # Source ranges always keep their full-span bounds.
    if is_source_range_row(existing):
        start_time = existing.start_time
        end_time = existing.end_time
    else:
        start_time = request.start_time if request.start_time is not None else existing.start_time
        end_time = request.end_time if request.end_time is not None else existing.end_time
    color = request.color if request.color is not None else existing.color
    tags = _normalize_tags(request.tags if request.tags is not None else existing.tags)
    parent_range_id = existing.parent_range_id
    if "parent_range_id" in request.model_fields_set:
        parent_range_id = request.parent_range_id
    if is_source_range_row(existing):
        if parent_range_id is not None:
            raise ValueError("Source ranges cannot have a parent.")
        parent_range_id = None
    elif parent_range_id is None:
        parent_range_id = _coerce_parent_to_source_range(
            rows,
            start_time=start_time,
            end_time=end_time,
            exclude_id=range_id,
        )
    notes = request.notes if request.notes is not None else existing.notes
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time.")
    if request.parameters is not None:
        parameters = [
            RangeParameterItem(
                range_id=range_id,
                key=p.key,
                value_text=p.value_text,
                value_num=p.value_num,
            )
            for p in request.parameters
        ]
    else:
        parameters = existing.parameters
    item = RangeItem(
        range_id=range_id,
        test_id=None,
        artifact_id=artifact_id,
        durability="temporary",
        name=name,
        label=label,
        status=status,
        start_time=start_time,
        end_time=end_time,
        start_ms=start_time.timestamp() * 1000.0,
        end_ms=end_time.timestamp() * 1000.0,
        color=color,
        tags=tags,
        parent_range_id=parent_range_id,
        source=existing.source,
        rule_id=existing.rule_id,
        notes=notes,
        parameters=parameters,
    )
    _validate_parent_containment(
        rows=rows,
        artifact_id=artifact_id,
        range_id=range_id,
        parent_range_id=item.parent_range_id,
        start_time=item.start_time,
        end_time=item.end_time,
    )
    rows[idx] = _item_to_row(item)
    doc["ranges"] = rows
    _persist(artifact_id, doc, request.file_path)
    return item


def delete_temp_range(range_id: int, *, artifact_id: str, source_path: str | None = None) -> bool:
    doc = _load_or_create(artifact_id, source_path)
    rows = [row for row in (doc.get("ranges") or []) if isinstance(row, dict)]
    target = next((row for row in rows if int(row.get("range_id") or -1) == range_id), None)
    if target is None:
        return False
    if is_source_range_row(target):
        raise ValueError("Source ranges cannot be deleted.")
    source_id = _find_source_range_id(rows, exclude_id=range_id)
    keep: list[dict[str, Any]] = []
    for row in rows:
        rid = int(row.get("range_id") or -1)
        if rid == range_id:
            continue
        if row.get("parent_range_id") is not None and int(row["parent_range_id"]) == range_id:
            row = dict(row)
            row["parent_range_id"] = source_id
        keep.append(row)
    doc["ranges"] = keep
    _persist(artifact_id, doc, source_path)
    return True

