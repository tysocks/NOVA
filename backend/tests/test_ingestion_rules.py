"""Tests for permanent ingestion rules and library CRUD."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.file_index import run_ingest
from app.main import app
from app.services.ingestion_rule_library import clean_ingestion_rule, resolve_channel_selection

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


def _patch_catalog(monkeypatch, path: Path) -> None:
    monkeypatch.setattr("app.engine.catalog_store.CATALOG_DB_PATH", path)


@pytest.fixture
def ingestion_library_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_ingestion_library.json"
    monkeypatch.setattr("app.main.INGESTION_LIBRARY_FILE", path)
    return path


def test_resolve_channel_selection_all_exclude_rename():
    selected, mapping = resolve_channel_selection(
        ["thrust", "pressure", "notes"],
        {
            "mode": "all",
            "exclude": ["notes"],
            "rename": {"thrust": "thrust_n"},
            "require": ["thrust_n"],
        },
    )
    assert selected == ["thrust", "pressure"]
    assert mapping == {"thrust": "thrust_n", "pressure": "pressure"}


def test_resolve_channel_selection_include_mode():
    selected, mapping = resolve_channel_selection(
        ["a", "b", "c"],
        {"mode": "include", "include": ["a", "RenamedB"], "rename": {"b": "RenamedB"}, "require": []},
    )
    assert set(selected) == {"a", "b"}
    assert mapping["b"] == "RenamedB"


def test_clean_ingestion_rule_requires_project_catalog():
    with pytest.raises(ValueError, match="target_catalog_id"):
        clean_ingestion_rule({"name": "x", "target_catalog_id": "local"})


def test_ingestion_library_roundtrip(ingestion_library_file: Path):
    payload = {
        "rules": [
            {
                "id": "rule-1",
                "name": "Hotfire",
                "target_catalog_id": "proj",
                "channels": {
                    "mode": "all",
                    "exclude": ["flag"],
                    "rename": {"P": "pressure"},
                    "require": ["pressure"],
                },
                "calculated_channels": [
                    {
                        "kind": "formula",
                        "name": "p2",
                        "unit": "bar",
                        "channels": ["pressure"],
                        "formula": "A*2",
                    }
                ],
                "range_definition_ids": ["rd-1"],
            }
        ]
    }
    save = client.post("/api/ingestion-library", json=payload)
    assert save.status_code == 200
    assert save.json()["ok"] is True

    load = client.get("/api/ingestion-library")
    rows = load.json()["rules"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Hotfire"
    assert rows[0]["channels"]["rename"]["P"] == "pressure"
    assert rows[0]["calculated_channels"][0]["name"] == "p2"


def test_permanent_rule_ingest_filters_renames_and_calcs(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    catalog = tmp_path / "proj" / "catalog.duckdb"
    lake = tmp_path / "proj" / "parquet"
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    monkeypatch.setattr("app.config.settings.parquet_root", str(tmp_path / "local_parquet"))
    monkeypatch.setattr("app.config.settings.default_ingest_mode", "temporary")

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

    csv_path = tmp_path / "runA.csv"
    csv_path.write_text(
        "time_s,thrust,pressure,noise\n"
        "0.0,10,1.0,9\n"
        "0.1,20,1.5,8\n"
        "0.2,30,2.0,7\n",
        encoding="utf-8",
    )
    rule = clean_ingestion_rule(
        {
            "id": "r1",
            "name": "Filter rename calc",
            "target_catalog_id": "proj",
            "channels": {
                "mode": "include",
                "include": ["thrust", "pressure"],
                "rename": {"thrust": "thrust_n"},
                "require": ["thrust_n"],
            },
            "calculated_channels": [
                {
                    "kind": "formula",
                    "name": "thrust_x2",
                    "unit": "N",
                    "channels": ["thrust_n"],
                    "formula": "A*2",
                }
            ],
        }
    )

    manifest = run_ingest(
        "csv",
        str(csv_path),
        ingest_mode="permanent",
        catalog_id="proj",
        ingestion_rule=rule,
    )
    assert manifest["status"] == "ready"
    assert manifest["durability"] == "permanent"
    names = {c["channel_name"] for c in manifest["channels"]}
    assert "thrust_n" in names
    assert "pressure" in names
    assert "thrust_x2" in names
    assert "noise" not in names

    assert (lake / "runA" / "data" / "thrust_n.parquet").is_file()
    assert (lake / "runA" / "data" / "thrust_x2.parquet").is_file()
    assert (lake / "runA" / "meta.json").is_file()
    meta = json.loads((lake / "runA" / "meta.json").read_text(encoding="utf-8"))
    assert meta["rule_id"] == "r1"
    assert any(c["name"] == "thrust_x2" and c.get("kind") == "calculated" for c in meta["channels"])

    channels = client.get(f"/api/catalog/tests/{manifest['test_run_id']}/channels?catalog_id=proj").json()
    catalog_names = {c["channel_name"] for c in channels}
    assert "thrust_n" in catalog_names
    assert "thrust_x2" in catalog_names
    assert "noise" not in catalog_names


def test_sequential_rule_ingest_keeps_both_runs(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    catalog = tmp_path / "proj" / "catalog.duckdb"
    lake = tmp_path / "proj" / "parquet"
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    monkeypatch.setattr("app.config.settings.parquet_root", str(tmp_path / "local_parquet"))
    monkeypatch.setattr("app.config.settings.default_ingest_mode", "temporary")

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

    rule = clean_ingestion_rule(
        {
            "id": "r-batch",
            "name": "Batch",
            "target_catalog_id": "proj",
            "channels": {"mode": "all"},
        }
    )
    first = tmp_path / "HFR-0004_run.csv"
    second = tmp_path / "HFR-0005_run.csv"
    first.write_text("time_s,thrust\n0.0,1\n0.1,2\n", encoding="utf-8")
    second.write_text("time_s,thrust\n0.0,3\n0.1,4\n", encoding="utf-8")

    m1 = run_ingest("csv", str(first), ingest_mode="permanent", catalog_id="proj", ingestion_rule=rule)
    m2 = run_ingest("csv", str(second), ingest_mode="permanent", catalog_id="proj", ingestion_rule=rule)
    assert m1["status"] == "ready"
    assert m2["status"] == "ready"
    assert m1["test_run_id"] != m2["test_run_id"]

    tests = client.get("/api/catalog/tests?catalog_id=proj").json()
    codes = {t["run_code"] for t in tests}
    assert "HFR-0004_run" in codes
    assert "HFR-0005_run" in codes
    by_code = {t["run_code"]: t for t in tests}
    assert str(first.resolve()) in str(by_code["HFR-0004_run"].get("source_uri") or "")
    assert str(second.resolve()) in str(by_code["HFR-0005_run"].get("source_uri") or "")


def test_temporary_ingest_unaffected_by_rules(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")

    csv_path = tmp_path / "temp.csv"
    csv_path.write_text("time_s,a,b\n0.0,1,2\n0.5,3,4\n1.0,5,6\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path), ingest_mode="temporary")
    assert manifest["durability"] == "temporary"
    assert manifest.get("catalog_id") == "local"
    names = {c["channel_name"] for c in manifest["channels"]}
    assert names == {"a", "b"}
