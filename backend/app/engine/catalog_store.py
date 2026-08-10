from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb

from ..models import (
    ChannelItem,
    RangeCreateRequest,
    RangeItem,
    RangeParameterItem,
    RangeParameterWrite,
    RangeRuleCreateRequest,
    RangeRuleItem,
    RangeUpdateRequest,
    ResultItem,
    ResultWriteRequest,
    TestParameterItem,
    TestRunItem,
)
from .session_store import artifact_dir

DEFAULT_CATALOG_DB_PATH = Path(__file__).resolve().parents[2] / ".nova_catalog.duckdb"
# Mutable default used by tests via monkeypatch; prefer get_catalog_db_path().
CATALOG_DB_PATH = DEFAULT_CATALOG_DB_PATH

_catalog_path_override: ContextVar[Path | None] = ContextVar("nova_catalog_path", default=None)


def get_catalog_db_path() -> Path:
    override = _catalog_path_override.get()
    if override is not None:
        return Path(override)
    return Path(CATALOG_DB_PATH)


def set_catalog_db_path(path: str | Path | None) -> Path:
    """Set the process-wide default catalog path (used when no request override)."""
    global CATALOG_DB_PATH
    if path is None or not str(path).strip():
        CATALOG_DB_PATH = DEFAULT_CATALOG_DB_PATH
    else:
        CATALOG_DB_PATH = Path(str(path)).expanduser().resolve()
    return Path(CATALOG_DB_PATH)


@contextmanager
def catalog_path_override(path: str | Path | None) -> Iterator[Path]:
    """Temporarily force catalog operations onto a specific DuckDB file."""
    resolved = Path(path).expanduser().resolve() if path else get_catalog_db_path()
    token = _catalog_path_override.set(resolved)
    try:
        yield resolved
    finally:
        _catalog_path_override.reset(token)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _artifact_test_id(artifact_id: str) -> int:
    return int(artifact_id[:12], 16)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _next_id(con: duckdb.DuckDBPyConnection, table: str, column: str) -> int:
    row = con.execute(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}").fetchone()
    return int(row[0] if row and row[0] is not None else 1)


def _tags_to_json(tags: list[str] | None) -> str:
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
    return json.dumps(cleaned)


def _tags_from_json(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(t).strip() for t in parsed if str(t).strip()]
    except Exception:
        pass
    return [text]


def _load_range_parameters(con: duckdb.DuckDBPyConnection, range_id: int) -> list[RangeParameterItem]:
    rows = con.execute(
        """
        SELECT range_id, key, value_text, value_num
        FROM range_parameters
        WHERE range_id = ?
        ORDER BY key
        """,
        [range_id],
    ).fetchall()
    return [
        RangeParameterItem(
            range_id=int(row[0]),
            key=str(row[1]),
            value_text=row[2],
            value_num=row[3],
        )
        for row in rows
    ]


def _replace_range_parameters(
    con: duckdb.DuckDBPyConnection,
    range_id: int,
    parameters: list[RangeParameterWrite] | None,
) -> None:
    con.execute("DELETE FROM range_parameters WHERE range_id = ?", [range_id])
    for param in parameters or []:
        con.execute(
            "INSERT INTO range_parameters (range_id, key, value_text, value_num) VALUES (?, ?, ?, ?)",
            [range_id, param.key, param.value_text, param.value_num],
        )


def _row_to_range_item(
    row: tuple[Any, ...],
    *,
    parameters: list[RangeParameterItem] | None = None,
    catalog_id: str | None = None,
    durability: str | None = "permanent",
) -> RangeItem:
    return RangeItem(
        range_id=int(row[0]),
        test_id=int(row[1]),
        catalog_id=catalog_id,
        durability=durability,
        name=str(row[2]),
        label=row[3],
        status=row[4],
        start_time=_parse_dt(row[5]) or _utc_now(),
        end_time=_parse_dt(row[6]) or _utc_now(),
        start_ms=row[7],
        end_ms=row[8],
        color=row[9],
        tags=_tags_from_json(row[10]),
        parent_range_id=int(row[11]) if row[11] is not None else None,
        source=str(row[12] or "user"),
        rule_id=row[13],
        notes=row[14],
        parameters=parameters or [],
    )


def _validate_catalog_parent_containment(
    *,
    con: duckdb.DuckDBPyConnection,
    test_id: int,
    range_id: int | None,
    parent_range_id: int | None,
    start_time: datetime,
    end_time: datetime,
) -> None:
    if parent_range_id is None:
        return
    if range_id is not None and int(parent_range_id) == int(range_id):
        raise ValueError("A range cannot be its own parent.")
    row = con.execute(
        """
        SELECT
          range_id,
          test_id,
          name,
          label,
          status,
          CAST(start_time AS VARCHAR),
          CAST(end_time AS VARCHAR),
          start_ms,
          end_ms,
          color,
          tags,
          parent_range_id,
          source,
          rule_id,
          notes
        FROM ranges
        WHERE range_id = ?
        LIMIT 1
        """,
        [parent_range_id],
    ).fetchone()
    if not row:
        raise ValueError(f"Parent range {parent_range_id} not found.")
    parent = _row_to_range_item(row)
    if parent.test_id != test_id:
        raise ValueError("Parent range must belong to the same source/test.")
    if start_time < parent.start_time or end_time > parent.end_time:
        raise ValueError("Child ranges must be fully contained within the selected parent range.")


def connect_catalog(catalog_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(catalog_path).expanduser().resolve() if catalog_path else get_catalog_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    ensure_catalog_schema(con)
    return con


def ensure_catalog_schema(con: duckdb.DuckDBPyConnection | None = None) -> None:
    owns_connection = con is None
    if con is None:
        path = get_catalog_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tests (
              test_id BIGINT PRIMARY KEY,
              run_code VARCHAR NOT NULL,
              start_time TIMESTAMPTZ,
              end_time TIMESTAMPTZ,
              duration_s DOUBLE,
              t0_utc TIMESTAMPTZ,
              source_type VARCHAR,
              source_uri VARCHAR,
              artifact_id VARCHAR,
              status VARCHAR,
              durability VARCHAR,
              created_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ
            )
            """
        )
        # Backward-compatible migration for catalogs created before durability existed.
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info('tests')").fetchall()}
        if "durability" not in cols:
            con.execute("ALTER TABLE tests ADD COLUMN durability VARCHAR")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS test_parameters (
              test_id BIGINT NOT NULL,
              key VARCHAR NOT NULL,
              value_text VARCHAR,
              value_num DOUBLE,
              PRIMARY KEY (test_id, key)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS channels (
              channel_id BIGINT PRIMARY KEY,
              test_id BIGINT NOT NULL,
              channel_name VARCHAR NOT NULL,
              display_name VARCHAR,
              unit VARCHAR,
              sample_rate_hz DOUBLE,
              parquet_uri VARCHAR NOT NULL,
              point_count BIGINT,
              UNIQUE (test_id, channel_name)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ranges (
              range_id BIGINT PRIMARY KEY,
              test_id BIGINT NOT NULL,
              name VARCHAR NOT NULL,
              label VARCHAR,
              status VARCHAR,
              start_time TIMESTAMPTZ NOT NULL,
              end_time TIMESTAMPTZ NOT NULL,
              start_ms DOUBLE,
              end_ms DOUBLE,
              color VARCHAR,
              tags VARCHAR,
              parent_range_id BIGINT,
              source VARCHAR NOT NULL,
              rule_id BIGINT,
              notes VARCHAR,
              created_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ
            )
            """
        )
        range_cols = {str(r[1]) for r in con.execute("PRAGMA table_info('ranges')").fetchall()}
        if "status" not in range_cols:
            con.execute("ALTER TABLE ranges ADD COLUMN status VARCHAR")
        if "tags" not in range_cols:
            con.execute("ALTER TABLE ranges ADD COLUMN tags VARCHAR")
        if "parent_range_id" not in range_cols:
            con.execute("ALTER TABLE ranges ADD COLUMN parent_range_id BIGINT")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS range_parameters (
              range_id BIGINT NOT NULL,
              key VARCHAR NOT NULL,
              value_text VARCHAR,
              value_num DOUBLE,
              PRIMARY KEY (range_id, key)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS range_rules (
              rule_id BIGINT PRIMARY KEY,
              name VARCHAR NOT NULL,
              description VARCHAR,
              kind VARCHAR NOT NULL,
              channel_name VARCHAR NOT NULL,
              config VARCHAR NOT NULL,
              default_label VARCHAR,
              default_color VARCHAR,
              created_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
              result_id BIGINT PRIMARY KEY,
              test_id BIGINT NOT NULL,
              range_id BIGINT,
              analysis_name VARCHAR NOT NULL,
              key VARCHAR NOT NULL,
              value_text VARCHAR,
              value_num DOUBLE,
              unit VARCHAR,
              created_at TIMESTAMPTZ,
              UNIQUE (test_id, range_id, analysis_name, key)
            )
            """
        )
    finally:
        if owns_connection:
            con.close()


def register_ingested_artifact(
    manifest: dict[str, Any],
    *,
    source_type: str,
    file_path: str,
    parameters: dict[str, Any] | None = None,
    durability: str = "temporary",
    channel_parquet_uris: dict[str, str] | None = None,
) -> int:
    artifact_id = str(manifest["artifact_id"])
    test_id = _artifact_test_id(artifact_id)
    root = artifact_dir(artifact_id)
    bounds = manifest.get("time_bounds") or {}
    start_ms = bounds.get("start_ms")
    end_ms = bounds.get("end_ms")
    start_time = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc) if start_ms is not None else None
    end_time = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc) if end_ms is not None else None
    duration_s = (end_time - start_time).total_seconds() if start_time and end_time else None
    t0_utc = _parse_dt(manifest.get("t0_utc")) or start_time
    now = _utc_now()
    con = connect_catalog()
    try:
        created_row = con.execute(
            "SELECT CAST(created_at AS VARCHAR) FROM tests WHERE test_id = ?",
            [test_id],
        ).fetchone()
        created_at = _parse_dt(created_row[0]) if created_row and created_row[0] is not None else now
        con.execute(
            """
            INSERT OR REPLACE INTO tests (
              test_id, run_code, start_time, end_time, duration_s, t0_utc,
              source_type, source_uri, artifact_id, status, durability, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                test_id,
                str(manifest.get("run_code") or Path(file_path).stem),
                start_time,
                end_time,
                duration_s,
                t0_utc,
                source_type,
                str(Path(file_path).resolve()),
                artifact_id,
                str(manifest.get("status") or "ready"),
                durability,
                created_at,
                now,
            ],
        )

        con.execute("DELETE FROM test_parameters WHERE test_id = ?", [test_id])
        for key, raw_value in (parameters or {}).items():
            text = None if raw_value is None else str(raw_value)
            num = None
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                num = float(raw_value)
            con.execute(
                "INSERT INTO test_parameters (test_id, key, value_text, value_num) VALUES (?, ?, ?, ?)",
                [test_id, str(key), text, num],
            )

        con.execute("DELETE FROM channels WHERE test_id = ?", [test_id])
        next_channel_id = _next_id(con, "channels", "channel_id")
        for idx, row in enumerate(manifest.get("channels") or []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("channel_name") or "")
            rel = str(row.get("parquet") or "")
            if channel_parquet_uris and name in channel_parquet_uris:
                parquet_uri = str(channel_parquet_uris[name])
            else:
                parquet_uri = str((root / rel).resolve()) if rel else ""
            con.execute(
                """
                INSERT INTO channels (
                  channel_id, test_id, channel_name, display_name, unit, sample_rate_hz, parquet_uri, point_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    next_channel_id + idx,
                    test_id,
                    name,
                    str(row.get("display_name") or row.get("source_name") or name),
                    row.get("unit"),
                    float(row["sample_rate_hz"]) if row.get("sample_rate_hz") is not None else None,
                    parquet_uri,
                    int(row.get("point_count") or 0),
                ],
            )
    finally:
        con.close()
    return test_id


def get_test_by_artifact_id(artifact_id: str) -> dict[str, Any] | None:
    con = connect_catalog()
    try:
        row = con.execute(
            """
            SELECT
              test_id,
              run_code,
              CAST(start_time AS VARCHAR),
              CAST(end_time AS VARCHAR),
              duration_s,
              artifact_id,
              status
            FROM tests
            WHERE artifact_id = ?
            LIMIT 1
            """,
            [artifact_id],
        ).fetchone()
        if not row:
            return None
        return {
            "test_id": int(row[0]),
            "run_code": str(row[1]),
            "start_time": _parse_dt(row[2]),
            "end_time": _parse_dt(row[3]),
            "duration_s": row[4],
            "artifact_id": row[5],
            "status": row[6],
        }
    finally:
        con.close()


def get_test_by_id(test_id: int) -> dict[str, Any] | None:
    con = connect_catalog()
    try:
        row = con.execute(
            """
            SELECT
              test_id,
              run_code,
              CAST(start_time AS VARCHAR),
              CAST(end_time AS VARCHAR),
              duration_s,
              artifact_id,
              status
            FROM tests
            WHERE test_id = ?
            LIMIT 1
            """,
            [test_id],
        ).fetchone()
        if not row:
            return None
        return {
            "test_id": int(row[0]),
            "run_code": str(row[1]),
            "start_time": _parse_dt(row[2]),
            "end_time": _parse_dt(row[3]),
            "duration_s": row[4],
            "artifact_id": row[5],
            "status": row[6],
        }
    finally:
        con.close()


def list_channel_parquet_entries(
    test_id: int,
    channel_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    con = connect_catalog()
    try:
        if channel_names:
            placeholders = ", ".join(["?"] * len(channel_names))
            rows = con.execute(
                f"""
                SELECT channel_name, unit, parquet_uri, point_count
                FROM channels
                WHERE test_id = ? AND channel_name IN ({placeholders})
                ORDER BY channel_name
                """,
                [test_id, *channel_names],
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT channel_name, unit, parquet_uri, point_count
                FROM channels
                WHERE test_id = ?
                ORDER BY channel_name
                """,
                [test_id],
            ).fetchall()
        return [
            {
                "channel_name": str(row[0]),
                "unit": row[1],
                "parquet_uri": str(row[2] or ""),
                "point_count": int(row[3] or 0),
            }
            for row in rows
        ]
    finally:
        con.close()


def list_catalog_tests(limit: int = 500) -> list[TestRunItem]:
    con = connect_catalog()
    try:
        rows = con.execute(
            """
            SELECT
              t.test_id,
              t.run_code,
              CAST(t.start_time AS VARCHAR),
              CAST(t.end_time AS VARCHAR),
              t.duration_s,
              CAST(t.t0_utc AS VARCHAR),
              (
                SELECT p.value_text
                FROM test_parameters p
                WHERE p.test_id = t.test_id AND p.key = 'ui_icon'
                LIMIT 1
              ) AS ui_icon
            FROM tests t
            ORDER BY COALESCE(t.start_time, t.created_at) DESC, t.test_id DESC
            LIMIT ?
            """,
            [max(1, min(limit, 10000))],
        ).fetchall()
        return [
            TestRunItem(
                test_run_id=int(row[0]),
                run_code=str(row[1]),
                start_time=_parse_dt(row[2]) or _utc_now(),
                end_time=_parse_dt(row[3]),
                duration_s=row[4],
                t0_utc=_parse_dt(row[5]),
                icon=(str(row[6]).strip() if row[6] is not None and str(row[6]).strip() else None),
            )
            for row in rows
        ]
    finally:
        con.close()


def update_catalog_test(
    test_id: int,
    *,
    run_code: str | None = None,
    icon: str | None = None,
) -> dict[str, Any] | None:
    """Rename and/or set UI icon for a catalog test. Returns updated row or None."""
    existing = get_test_by_id(test_id)
    if not existing:
        return None
    con = connect_catalog()
    try:
        if run_code is not None:
            cleaned = str(run_code).strip()
            if not cleaned:
                raise ValueError("run_code cannot be empty")
            con.execute(
                "UPDATE tests SET run_code = ?, updated_at = ? WHERE test_id = ?",
                [cleaned, _utc_now(), test_id],
            )
            existing["run_code"] = cleaned
        if icon is not None:
            cleaned_icon = str(icon).strip()
            con.execute(
                "DELETE FROM test_parameters WHERE test_id = ? AND key = ?",
                [test_id, "ui_icon"],
            )
            if cleaned_icon:
                con.execute(
                    "INSERT INTO test_parameters (test_id, key, value_text, value_num) VALUES (?, ?, ?, ?)",
                    [test_id, "ui_icon", cleaned_icon, None],
                )
            existing["icon"] = cleaned_icon or None
        else:
            row = con.execute(
                "SELECT value_text FROM test_parameters WHERE test_id = ? AND key = 'ui_icon' LIMIT 1",
                [test_id],
            ).fetchone()
            existing["icon"] = str(row[0]).strip() if row and row[0] is not None and str(row[0]).strip() else None
        return existing
    finally:
        con.close()


def delete_catalog_test(test_id: int, *, remove_parquet: bool = True) -> bool:
    """Delete a test and related catalog rows. Optionally remove channel parquet files."""
    con = connect_catalog()
    parquet_uris: list[str] = []
    try:
        exists = con.execute("SELECT 1 FROM tests WHERE test_id = ? LIMIT 1", [test_id]).fetchone()
        if not exists:
            return False
        if remove_parquet:
            parquet_uris = [
                str(row[0])
                for row in con.execute(
                    "SELECT parquet_uri FROM channels WHERE test_id = ? AND parquet_uri IS NOT NULL",
                    [test_id],
                ).fetchall()
                if row and row[0]
            ]
        # Cascade-ish cleanup across catalog tables.
        range_ids = [
            int(row[0])
            for row in con.execute("SELECT range_id FROM ranges WHERE test_id = ?", [test_id]).fetchall()
            if row and row[0] is not None
        ]
        for range_id in range_ids:
            con.execute("DELETE FROM range_parameters WHERE range_id = ?", [range_id])
        con.execute("DELETE FROM ranges WHERE test_id = ?", [test_id])
        con.execute("DELETE FROM results WHERE test_id = ?", [test_id])
        con.execute("DELETE FROM channels WHERE test_id = ?", [test_id])
        con.execute("DELETE FROM test_parameters WHERE test_id = ?", [test_id])
        con.execute("DELETE FROM tests WHERE test_id = ?", [test_id])
    finally:
        con.close()

    if remove_parquet:
        import shutil

        for uri in parquet_uris:
            try:
                path = Path(str(uri))
                if path.is_file():
                    path.unlink(missing_ok=True)
                    # Remove empty run data dir and run folder when possible.
                    data_dir = path.parent
                    run_dir = data_dir.parent if data_dir.name == "data" else data_dir
                    if data_dir.is_dir() and not any(data_dir.iterdir()):
                        data_dir.rmdir()
                    if run_dir.is_dir() and not any(run_dir.iterdir()):
                        shutil.rmtree(run_dir, ignore_errors=True)
            except Exception:
                continue
    return True


def list_catalog_channels(test_id: int) -> list[ChannelItem]:
    con = connect_catalog()
    try:
        rows = con.execute(
            """
            SELECT channel_id, channel_name, display_name, unit, sample_rate_hz
            FROM channels
            WHERE test_id = ?
            ORDER BY channel_name
            """,
            [test_id],
        ).fetchall()
        return [
            ChannelItem(
                channel_id=int(row[0]),
                channel_name=str(row[1]),
                display_name=row[2],
                unit=row[3],
                sample_rate_hz=row[4],
            )
            for row in rows
        ]
    finally:
        con.close()


def list_test_parameters(test_id: int) -> list[TestParameterItem]:
    con = connect_catalog()
    try:
        rows = con.execute(
            """
            SELECT test_id, key, value_text, value_num
            FROM test_parameters
            WHERE test_id = ?
            ORDER BY key
            """,
            [test_id],
        ).fetchall()
        return [
            TestParameterItem(
                test_id=int(row[0]),
                key=str(row[1]),
                value_text=row[2],
                value_num=row[3],
            )
            for row in rows
        ]
    finally:
        con.close()


def _find_catalog_source_range_id(
    con: duckdb.DuckDBPyConnection,
    test_id: int,
    *,
    exclude_id: int | None = None,
) -> int | None:
    rows = con.execute(
        "SELECT range_id FROM ranges WHERE test_id = ? AND source = 'source' ORDER BY range_id",
        [test_id],
    ).fetchall()
    for row in rows:
        rid = int(row[0])
        if exclude_id is not None and rid == int(exclude_id):
            continue
        return rid
    return None


def _reparent_catalog_roots_under_source(
    con: duckdb.DuckDBPyConnection,
    test_id: int,
    source_range_id: int,
) -> None:
    con.execute(
        """
        UPDATE ranges
        SET parent_range_id = NULL
        WHERE range_id = ? AND test_id = ?
        """,
        [source_range_id, test_id],
    )
    con.execute(
        """
        UPDATE ranges AS child
        SET parent_range_id = ?
        FROM ranges AS src
        WHERE src.range_id = ?
          AND src.test_id = ?
          AND child.test_id = ?
          AND child.range_id <> ?
          AND child.parent_range_id IS NULL
          AND child.start_time >= src.start_time
          AND child.end_time <= src.end_time
        """,
        [source_range_id, source_range_id, test_id, test_id, source_range_id],
    )


def _coerce_catalog_parent_to_source_range(
    con: duckdb.DuckDBPyConnection,
    test_id: int,
    *,
    start_time: datetime,
    end_time: datetime,
    exclude_id: int | None = None,
) -> int | None:
    source_id = _find_catalog_source_range_id(con, test_id, exclude_id=exclude_id)
    if source_id is None:
        return None
    try:
        _validate_catalog_parent_containment(
            con=con,
            test_id=test_id,
            range_id=exclude_id,
            parent_range_id=source_id,
            start_time=start_time,
            end_time=end_time,
        )
    except ValueError:
        return None
    return source_id


def ensure_catalog_source_range(
    *,
    test_id: int,
    catalog_id: str | None,
    name: str,
    start_time: datetime,
    end_time: datetime,
    tags: list[str] | None = None,
) -> RangeItem:
    existing = list_ranges(test_id, catalog_id=catalog_id)
    source = next((r for r in existing if str(r.source or "") == "source"), None)
    if source is None:
        legacy = next(
            (
                r
                for r in existing
                if r.parent_range_id is None
                and r.start_ms is not None
                and r.end_ms is not None
                and abs(float(r.start_ms) - float(start_time.timestamp() * 1000.0)) < 1e-6
                and abs(float(r.end_ms) - float(end_time.timestamp() * 1000.0)) < 1e-6
            ),
            None,
        )
        if legacy is None and existing:
            candidate = next((r for r in existing if r.parent_range_id is None), None)
            if candidate is not None and int(candidate.range_id) == 1:
                legacy = candidate
        if legacy is not None:
            con = connect_catalog()
            now = _utc_now()
            try:
                con.execute(
                    """
                    UPDATE ranges SET
                      name = ?, start_time = ?, end_time = ?, start_ms = ?, end_ms = ?,
                      parent_range_id = NULL, source = 'source', tags = ?, updated_at = ?
                    WHERE range_id = ?
                    """,
                    [
                        name or legacy.name,
                        start_time,
                        end_time,
                        float(start_time.timestamp() * 1000.0),
                        float(end_time.timestamp() * 1000.0),
                        _tags_to_json(tags if tags is not None else legacy.tags),
                        now,
                        legacy.range_id,
                    ],
                )
                _reparent_catalog_roots_under_source(con, test_id, int(legacy.range_id))
            finally:
                con.close()
            refreshed = get_range(int(legacy.range_id), catalog_id=catalog_id)
            if refreshed is None:
                raise ValueError("Failed to promote source range.")
            return refreshed
        return create_range(
            RangeCreateRequest(
                test_id=test_id,
                catalog_id=catalog_id,
                durability="permanent",
                name=name,
                start_time=start_time,
                end_time=end_time,
                tags=list(tags or []),
                source="source",
            )
        )

    con = connect_catalog()
    try:
        _reparent_catalog_roots_under_source(con, test_id, int(source.range_id))
    finally:
        con.close()
    refreshed = get_range(int(source.range_id), catalog_id=catalog_id)
    return refreshed or source


def create_range(request: RangeCreateRequest) -> RangeItem:
    if request.test_id is None:
        raise ValueError("test_id is required for catalog ranges.")
    con = connect_catalog()
    now = _utc_now()
    try:
        if request.source == "source" and _find_catalog_source_range_id(con, int(request.test_id)) is not None:
            raise ValueError("A source range already exists for this source.")
        range_id = _next_id(con, "ranges", "range_id")
        start_ms = request.start_time.timestamp() * 1000.0
        end_ms = request.end_time.timestamp() * 1000.0
        tags_json = _tags_to_json(request.tags)
        parent_range_id = request.parent_range_id
        if request.source == "source":
            parent_range_id = None
        elif parent_range_id is None:
            parent_range_id = _coerce_catalog_parent_to_source_range(
                con,
                int(request.test_id),
                start_time=request.start_time,
                end_time=request.end_time,
            )
        _validate_catalog_parent_containment(
            con=con,
            test_id=request.test_id,
            range_id=None,
            parent_range_id=parent_range_id,
            start_time=request.start_time,
            end_time=request.end_time,
        )
        con.execute(
            """
            INSERT INTO ranges (
              range_id, test_id, name, label, status, start_time, end_time, start_ms, end_ms,
              color, tags, parent_range_id, source, rule_id, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                range_id,
                request.test_id,
                request.name,
                request.label,
                request.status,
                request.start_time,
                request.end_time,
                start_ms,
                end_ms,
                request.color,
                tags_json,
                parent_range_id,
                request.source,
                request.rule_id,
                request.notes,
                now,
                now,
            ],
        )
        if request.source == "source":
            _reparent_catalog_roots_under_source(con, int(request.test_id), range_id)
        _replace_range_parameters(con, range_id, request.parameters)
        params = _load_range_parameters(con, range_id)
        return RangeItem(
            range_id=range_id,
            test_id=request.test_id,
            catalog_id=request.catalog_id,
            durability="permanent",
            name=request.name,
            label=request.label,
            status=request.status,
            start_time=request.start_time,
            end_time=request.end_time,
            start_ms=start_ms,
            end_ms=end_ms,
            color=request.color,
            tags=list(request.tags or []),
            parent_range_id=parent_range_id,
            source=request.source,
            rule_id=request.rule_id,
            notes=request.notes,
            parameters=params,
        )
    finally:
        con.close()


def list_ranges(test_id: int, *, catalog_id: str | None = None) -> list[RangeItem]:
    con = connect_catalog()
    try:
        has_source = con.execute(
            "SELECT 1 FROM ranges WHERE test_id = ? AND source = 'source' LIMIT 1",
            [test_id],
        ).fetchone()
        if not has_source:
            legacy = con.execute(
                """
                SELECT range_id FROM ranges
                WHERE test_id = ? AND range_id = 1 AND parent_range_id IS NULL AND source = 'user'
                LIMIT 1
                """,
                [test_id],
            ).fetchone()
            if legacy:
                con.execute(
                    "UPDATE ranges SET source = 'source' WHERE range_id = ?",
                    [int(legacy[0])],
                )
                _reparent_catalog_roots_under_source(con, int(test_id), int(legacy[0]))
        rows = con.execute(
            """
            SELECT
              range_id,
              test_id,
              name,
              label,
              status,
              CAST(start_time AS VARCHAR),
              CAST(end_time AS VARCHAR),
              start_ms,
              end_ms,
              color,
              tags,
              parent_range_id,
              source,
              rule_id,
              notes
            FROM ranges
            WHERE test_id = ?
            ORDER BY start_ms, range_id
            """,
            [test_id],
        ).fetchall()
        out: list[RangeItem] = []
        for row in rows:
            params = _load_range_parameters(con, int(row[0]))
            out.append(_row_to_range_item(row, parameters=params, catalog_id=catalog_id))
        return out
    finally:
        con.close()


def get_range(range_id: int, *, catalog_id: str | None = None) -> RangeItem | None:
    con = connect_catalog()
    try:
        row = con.execute(
            """
            SELECT
              range_id,
              test_id,
              name,
              label,
              status,
              CAST(start_time AS VARCHAR),
              CAST(end_time AS VARCHAR),
              start_ms,
              end_ms,
              color,
              tags,
              parent_range_id,
              source,
              rule_id,
              notes
            FROM ranges
            WHERE range_id = ?
            LIMIT 1
            """,
            [range_id],
        ).fetchone()
        if not row:
            return None
        params = _load_range_parameters(con, int(row[0]))
        return _row_to_range_item(row, parameters=params, catalog_id=catalog_id)
    finally:
        con.close()


def update_range(range_id: int, request: RangeUpdateRequest) -> RangeItem:
    existing = get_range(range_id, catalog_id=request.catalog_id)
    if existing is None:
        raise ValueError(f"Range {range_id} not found.")
    name = request.name if request.name is not None else existing.name
    label = request.label if request.label is not None else existing.label
    status = request.status if request.status is not None else existing.status
    if str(existing.source or "") == "source":
        start_time = existing.start_time
        end_time = existing.end_time
    else:
        start_time = request.start_time if request.start_time is not None else existing.start_time
        end_time = request.end_time if request.end_time is not None else existing.end_time
    color = request.color if request.color is not None else existing.color
    tags = request.tags if request.tags is not None else existing.tags
    parent_range_id = existing.parent_range_id
    if "parent_range_id" in request.model_fields_set:
        parent_range_id = request.parent_range_id
    if str(existing.source or "") == "source":
        if parent_range_id is not None:
            raise ValueError("Source ranges cannot have a parent.")
        parent_range_id = None
    notes = request.notes if request.notes is not None else existing.notes
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time.")
    start_ms = start_time.timestamp() * 1000.0
    end_ms = end_time.timestamp() * 1000.0
    now = _utc_now()
    con = connect_catalog()
    try:
        if str(existing.source or "") != "source" and parent_range_id is None and existing.test_id is not None:
            parent_range_id = _coerce_catalog_parent_to_source_range(
                con,
                int(existing.test_id),
                start_time=start_time,
                end_time=end_time,
                exclude_id=range_id,
            )
        _validate_catalog_parent_containment(
            con=con,
            test_id=int(existing.test_id or 0),
            range_id=range_id,
            parent_range_id=parent_range_id,
            start_time=start_time,
            end_time=end_time,
        )
        con.execute(
            """
            UPDATE ranges SET
              name = ?, label = ?, status = ?, start_time = ?, end_time = ?,
              start_ms = ?, end_ms = ?, color = ?, tags = ?, parent_range_id = ?,
              notes = ?, updated_at = ?
            WHERE range_id = ?
            """,
            [
                name,
                label,
                status,
                start_time,
                end_time,
                start_ms,
                end_ms,
                color,
                _tags_to_json(tags),
                parent_range_id,
                notes,
                now,
                range_id,
            ],
        )
        if request.parameters is not None:
            _replace_range_parameters(con, range_id, request.parameters)
        params = _load_range_parameters(con, range_id)
        return RangeItem(
            range_id=range_id,
            test_id=existing.test_id,
            catalog_id=request.catalog_id or existing.catalog_id,
            durability="permanent",
            name=name,
            label=label,
            status=status,
            start_time=start_time,
            end_time=end_time,
            start_ms=start_ms,
            end_ms=end_ms,
            color=color,
            tags=list(tags or []),
            parent_range_id=parent_range_id,
            source=existing.source,
            rule_id=existing.rule_id,
            notes=notes,
            parameters=params,
        )
    finally:
        con.close()


def delete_range(range_id: int) -> bool:
    con = connect_catalog()
    try:
        existing = con.execute(
            "SELECT range_id, source, test_id FROM ranges WHERE range_id = ?",
            [range_id],
        ).fetchone()
        if not existing:
            return False
        if str(existing[1] or "") == "source":
            raise ValueError("Source ranges cannot be deleted.")
        test_id = int(existing[2]) if existing[2] is not None else None
        fallback_parent = (
            _find_catalog_source_range_id(con, test_id, exclude_id=range_id)
            if test_id is not None
            else None
        )
        con.execute("DELETE FROM range_parameters WHERE range_id = ?", [range_id])
        con.execute(
            "UPDATE ranges SET parent_range_id = ? WHERE parent_range_id = ?",
            [fallback_parent, range_id],
        )
        con.execute("DELETE FROM ranges WHERE range_id = ?", [range_id])
        return True
    finally:
        con.close()

def create_range_rule(request: RangeRuleCreateRequest) -> RangeRuleItem:
    con = connect_catalog()
    now = _utc_now()
    try:
        rule_id = _next_id(con, "range_rules", "rule_id")
        con.execute(
            """
            INSERT INTO range_rules (
              rule_id, name, description, kind, channel_name, config, default_label, default_color, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rule_id,
                request.name,
                request.description,
                request.kind,
                request.channel_name,
                request.config,
                request.default_label,
                request.default_color,
                now,
                now,
            ],
        )
        return RangeRuleItem(
            rule_id=rule_id,
            name=request.name,
            description=request.description,
            kind=request.kind,
            channel_name=request.channel_name,
            config=request.config,
            default_label=request.default_label,
            default_color=request.default_color,
        )
    finally:
        con.close()


def list_range_rules() -> list[RangeRuleItem]:
    con = connect_catalog()
    try:
        rows = con.execute(
            """
            SELECT rule_id, name, description, kind, channel_name, config, default_label, default_color
            FROM range_rules
            ORDER BY name, rule_id
            """
        ).fetchall()
        return [
            RangeRuleItem(
                rule_id=int(row[0]),
                name=str(row[1]),
                description=row[2],
                kind=str(row[3]),
                channel_name=str(row[4]),
                config=str(row[5]),
                default_label=row[6],
                default_color=row[7],
            )
            for row in rows
        ]
    finally:
        con.close()


def get_range_rule(rule_id: int) -> RangeRuleItem | None:
    con = connect_catalog()
    try:
        row = con.execute(
            """
            SELECT rule_id, name, description, kind, channel_name, config, default_label, default_color
            FROM range_rules
            WHERE rule_id = ?
            LIMIT 1
            """,
            [rule_id],
        ).fetchone()
        if not row:
            return None
        return RangeRuleItem(
            rule_id=int(row[0]),
            name=str(row[1]),
            description=row[2],
            kind=str(row[3]),
            channel_name=str(row[4]),
            config=str(row[5]),
            default_label=row[6],
            default_color=row[7],
        )
    finally:
        con.close()


def write_result(request: ResultWriteRequest) -> ResultItem:
    con = connect_catalog()
    now = _utc_now()
    try:
        existing = con.execute(
            """
            SELECT result_id
            FROM results
            WHERE test_id = ? AND ((range_id IS NULL AND ? IS NULL) OR range_id = ?)
              AND analysis_name = ? AND key = ?
            """,
            [request.test_id, request.range_id, request.range_id, request.analysis_name, request.key],
        ).fetchone()
        if existing:
            result_id = int(existing[0])
            con.execute(
                """
                UPDATE results
                SET value_text = ?, value_num = ?, unit = ?, created_at = ?
                WHERE result_id = ?
                """,
                [request.value_text, request.value_num, request.unit, now, result_id],
            )
        else:
            result_id = _next_id(con, "results", "result_id")
            con.execute(
                """
                INSERT INTO results (
                  result_id, test_id, range_id, analysis_name, key, value_text, value_num, unit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result_id,
                    request.test_id,
                    request.range_id,
                    request.analysis_name,
                    request.key,
                    request.value_text,
                    request.value_num,
                    request.unit,
                    now,
                ],
            )
        return ResultItem(
            result_id=result_id,
            test_id=request.test_id,
            range_id=request.range_id,
            analysis_name=request.analysis_name,
            key=request.key,
            value_text=request.value_text,
            value_num=request.value_num,
            unit=request.unit,
        )
    finally:
        con.close()


def list_results(test_id: int | None = None, analysis_name: str | None = None) -> list[ResultItem]:
    con = connect_catalog()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if test_id is not None:
            clauses.append("test_id = ?")
            params.append(test_id)
        if analysis_name:
            clauses.append("analysis_name = ?")
            params.append(analysis_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = con.execute(
            f"""
            SELECT result_id, test_id, range_id, analysis_name, key, value_text, value_num, unit
            FROM results
            {where}
            ORDER BY test_id, analysis_name, key, result_id
            """,
            params,
        ).fetchall()
        return [
            ResultItem(
                result_id=int(row[0]),
                test_id=int(row[1]),
                range_id=row[2],
                analysis_name=str(row[3]),
                key=str(row[4]),
                value_text=row[5],
                value_num=row[6],
                unit=row[7],
            )
            for row in rows
        ]
    finally:
        con.close()
