"""Tests for temporary sidecar ranges and permanent catalog ranges."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.engine.file_index import run_ingest
from app.engine.range_store import (
    create_temp_range,
    list_temp_ranges,
    restore_ranges_from_durable_sidecar,
    session_ranges_path,
)
from app.main import app
from app.models import RangeCreateRequest

client = TestClient(app)


def _patch_sessions(monkeypatch, root: Path) -> None:
    def _ensure_root() -> Path:
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _artifact_dir(aid: str) -> Path:
        d = root / aid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _data_dir(aid: str) -> Path:
        d = root / aid / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("app.engine.session_store.SESSIONS_ROOT", root)
    monkeypatch.setattr("app.engine.session_store.ensure_sessions_root", _ensure_root)
    monkeypatch.setattr("app.engine.session_store.artifact_dir", _artifact_dir)
    monkeypatch.setattr("app.engine.session_store.data_dir", _data_dir)
    monkeypatch.setattr(
        "app.engine.session_store.manifest_path",
        lambda aid: root / aid / "manifest.json",
    )
    monkeypatch.setattr("app.engine.range_store.artifact_dir", _artifact_dir)


def _patch_catalog(monkeypatch, path: Path) -> None:
    monkeypatch.setattr("app.engine.catalog_store.CATALOG_DB_PATH", path)


def test_temp_ranges_are_session_only(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    csv_path = tmp_path / "burn.csv"
    csv_path.write_text("time_s,THRUST\n0.0,1.0\n1.0,2.0\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path), ingest_mode="temporary")
    artifact_id = manifest["artifact_id"]

    start = datetime.now(timezone.utc)
    end = start + timedelta(seconds=1)
    created = create_temp_range(
        RangeCreateRequest(
            artifact_id=artifact_id,
            file_path=str(csv_path),
            durability="temporary",
            name="Ignition",
            status="completed",
            start_time=start,
            end_time=end,
            color="#22c55e",
            tags=["burn", "auto"],
            parameters=[{"key": "note", "value_text": "sidecar"}],
        )
    )
    assert created.range_id == 2
    assert session_ranges_path(artifact_id).is_file()

    listed = list_temp_ranges(artifact_id, source_path=str(csv_path))
    assert len(listed) == 2
    custom = next(r for r in listed if r.name == "Ignition")
    assert custom.tags == ["burn", "auto"]
    assert custom.parameters[0].key == "note"

    # Simulate session clear: temp ranges should not come back.
    session_ranges_path(artifact_id).unlink()
    assert not restore_ranges_from_durable_sidecar(artifact_id, str(csv_path))
    restored = list_temp_ranges(artifact_id)
    assert restored == []


def test_child_ranges_must_be_contained(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    csv_path = tmp_path / "burn.csv"
    csv_path.write_text("time_s,THRUST\n0.0,1.0\n1.0,2.0\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path), ingest_mode="temporary")
    artifact_id = manifest["artifact_id"]

    parent_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parent_end = parent_start + timedelta(seconds=10)
    parent = create_temp_range(
        RangeCreateRequest(
            artifact_id=artifact_id,
            file_path=str(csv_path),
            durability="temporary",
            name="Parent",
            start_time=parent_start,
            end_time=parent_end,
        )
    )

    bad = client.post(
        "/api/ranges",
        json={
            "artifact_id": artifact_id,
            "file_path": str(csv_path),
            "durability": "temporary",
            "name": "Child",
            "parent_range_id": parent.range_id,
            "start_time": (parent_start - timedelta(seconds=1)).isoformat(),
            "end_time": (parent_start + timedelta(seconds=1)).isoformat(),
        },
    )
    assert bad.status_code == 400, bad.text
    assert "fully contained" in bad.json()["detail"]


def test_unified_ranges_api_permanent_and_temp(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    catalog = tmp_path / "proj" / "catalog.duckdb"
    lake = tmp_path / "proj" / "parquet"
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    monkeypatch.setattr("app.config.settings.parquet_root", str(tmp_path / "local_parquet"))

    library_file = tmp_path / "library.json"
    monkeypatch.setattr("app.main.DATABASE_LIBRARY_FILE", library_file)
    monkeypatch.setattr("app.services.database_library.DATABASE_LIBRARY_FILE", library_file)
    client.post(
        "/api/database-library",
        json={
            "databases": [
                {
                    "id": "proj",
                    "type": "duckdb",
                    "name": "Project",
                    "catalog_path": str(catalog),
                    "parquet_root": str(lake),
                    "is_default": True,
                }
            ],
            "active_catalog_id": "proj",
        },
    )

    csv_path = tmp_path / "hotfire.csv"
    csv_path.write_text("time_s,THRUST\n0.0,10.0\n1.0,20.0\n", encoding="utf-8")
    temp_manifest = run_ingest("csv", str(csv_path), ingest_mode="temporary")
    perm_manifest = run_ingest(
        "csv",
        str(csv_path),
        ingest_mode="permanent",
        catalog_id="proj",
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=2)

    temp_resp = client.post(
        "/api/ranges",
        json={
            "artifact_id": temp_manifest["artifact_id"],
            "file_path": str(csv_path),
            "durability": "temporary",
            "name": "Temp Window",
            "status": "in_progress",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "color": "#3b82f6",
            "tags": ["temp"],
        },
    )
    assert temp_resp.status_code == 200, temp_resp.text
    temp_body = temp_resp.json()
    assert temp_body["durability"] == "temporary"
    assert temp_body["status"] == "in_progress"

    perm_resp = client.post(
        "/api/ranges",
        json={
            "test_id": perm_manifest["test_run_id"],
            "catalog_id": "proj",
            "durability": "permanent",
            "name": "Perm Window",
            "status": "success",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "color": "#ef4444",
            "tags": ["perm"],
            "parameters": [{"key": "prop", "value_text": "LOX"}],
        },
    )
    assert perm_resp.status_code == 200, perm_resp.text
    perm_body = perm_resp.json()
    assert perm_body["durability"] == "permanent"
    assert perm_body["parameters"][0]["key"] == "prop"

    queried = client.post(
        "/api/ranges/query",
        json={
            "sources": [
                {
                    "artifact_id": temp_manifest["artifact_id"],
                    "file_path": str(csv_path),
                    "durability": "temporary",
                    "source_id": "s1",
                },
                {
                    "test_id": perm_manifest["test_run_id"],
                    "catalog_id": "proj",
                    "durability": "permanent",
                    "source_id": "s2",
                },
            ]
        },
    )
    assert queried.status_code == 200, queried.text
    rows = queried.json()
    names = {r["name"] for r in rows}
    assert {"Temp Window", "Perm Window", "hotfire"}.issubset(names)

    patch = client.patch(
        f"/api/ranges/{perm_body['range_id']}",
        json={
            "test_id": perm_manifest["test_run_id"],
            "catalog_id": "proj",
            "durability": "permanent",
            "status": "completed",
            "tags": ["perm", "reviewed"],
        },
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["status"] == "completed"
    assert "reviewed" in patch.json()["tags"]

    deleted = client.request(
        "DELETE",
        f"/api/ranges/{temp_body['range_id']}",
        json={
            "artifact_id": temp_manifest["artifact_id"],
            "file_path": str(csv_path),
            "durability": "temporary",
        },
    )
    assert deleted.status_code == 200, deleted.text
    remaining = list_temp_ranges(temp_manifest["artifact_id"], source_path=str(csv_path))
    assert len(remaining) == 1
    assert remaining[0].name == "hotfire"


def test_ingest_creates_default_full_span_range(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    csv_path = tmp_path / "full_span.csv"
    csv_path.write_text("time_s,THRUST\n0.0,10.0\n0.5,20.0\n1.0,30.0\n", encoding="utf-8")

    manifest = run_ingest("csv", str(csv_path), ingest_mode="temporary")
    rows = list_temp_ranges(manifest["artifact_id"], source_path=str(csv_path))
    assert len(rows) == 1
    assert rows[0].name == "full_span"
    assert rows[0].start_ms == manifest["time_bounds"]["start_ms"]
    assert rows[0].end_ms == manifest["time_bounds"]["end_ms"]
