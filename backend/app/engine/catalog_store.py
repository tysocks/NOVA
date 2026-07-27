from __future__ import annotations

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
    RangeRuleCreateRequest,
    RangeRuleItem,
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
              start_time TIMESTAMPTZ NOT NULL,
              end_time TIMESTAMPTZ NOT NULL,
              start_ms DOUBLE,
              end_ms DOUBLE,
              color VARCHAR,
              source VARCHAR NOT NULL,
              rule_id BIGINT,
              notes VARCHAR,
              created_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ
            )
            """
        )
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
                start_time,
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
                    name,
                    row.get("unit"),
                    None,
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
              test_id,
              run_code,
              CAST(start_time AS VARCHAR),
              CAST(end_time AS VARCHAR),
              duration_s,
              CAST(t0_utc AS VARCHAR)
            FROM tests
            ORDER BY COALESCE(start_time, created_at) DESC, test_id DESC
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
            )
            for row in rows
        ]
    finally:
        con.close()


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


def create_range(request: RangeCreateRequest) -> RangeItem:
    con = connect_catalog()
    now = _utc_now()
    try:
        range_id = _next_id(con, "ranges", "range_id")
        start_ms = request.start_time.timestamp() * 1000.0
        end_ms = request.end_time.timestamp() * 1000.0
        con.execute(
            """
            INSERT INTO ranges (
              range_id, test_id, name, label, start_time, end_time, start_ms, end_ms,
              color, source, rule_id, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                range_id,
                request.test_id,
                request.name,
                request.label,
                request.start_time,
                request.end_time,
                start_ms,
                end_ms,
                request.color,
                request.source,
                request.rule_id,
                request.notes,
                now,
                now,
            ],
        )
        for param in request.parameters:
            con.execute(
                "INSERT INTO range_parameters (range_id, key, value_text, value_num) VALUES (?, ?, ?, ?)",
                [range_id, param.key, param.value_text, param.value_num],
            )
        return RangeItem(
            range_id=range_id,
            test_id=request.test_id,
            name=request.name,
            label=request.label,
            start_time=request.start_time,
            end_time=request.end_time,
            start_ms=start_ms,
            end_ms=end_ms,
            color=request.color,
            source=request.source,
            rule_id=request.rule_id,
            notes=request.notes,
        )
    finally:
        con.close()


def list_ranges(test_id: int) -> list[RangeItem]:
    con = connect_catalog()
    try:
        rows = con.execute(
            """
            SELECT
              range_id,
              test_id,
              name,
              label,
              CAST(start_time AS VARCHAR),
              CAST(end_time AS VARCHAR),
              start_ms,
              end_ms,
              color,
              source,
              rule_id,
              notes
            FROM ranges
            WHERE test_id = ?
            ORDER BY start_ms, range_id
            """,
            [test_id],
        ).fetchall()
        return [
            RangeItem(
                range_id=int(row[0]),
                test_id=int(row[1]),
                name=str(row[2]),
                label=row[3],
                start_time=_parse_dt(row[4]) or _utc_now(),
                end_time=_parse_dt(row[5]) or _utc_now(),
                start_ms=row[6],
                end_ms=row[7],
                color=row[8],
                source=str(row[9]),
                rule_id=row[10],
                notes=row[11],
            )
            for row in rows
        ]
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
