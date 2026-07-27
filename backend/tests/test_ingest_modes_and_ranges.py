from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.file_index import run_ingest
from app.main import app

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


def test_permanent_ingest_writes_lake_and_registers_params(monkeypatch, tmp_path: Path):
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

    csv_path = tmp_path / "burn.csv"
    csv_path.write_text(
        "time_s,THRUST\n0.0,10.0\n0.5,100.0\n1.0,20.0\n1.5,15.0\n",
        encoding="utf-8",
    )

    manifest = run_ingest(
        "csv",
        str(csv_path),
        ingest_mode="permanent",
        catalog_id="proj",
        parameters={"propellant": "LOX/RP1", "chamber_p": 50},
    )
    assert manifest["status"] == "ready"
    assert manifest["durability"] == "permanent"
    assert manifest["catalog_id"] == "proj"
    test_id = int(manifest["test_run_id"])

    pq = lake / "burn" / "data" / "THRUST.parquet"
    assert pq.is_file()

    params = client.get(f"/api/catalog/tests/{test_id}/parameters?catalog_id=proj").json()
    keys = {p["key"]: p for p in params}
    assert keys["propellant"]["value_text"] == "LOX/RP1"
    assert keys["chamber_p"]["value_num"] == 50.0

    # Catalog query path
    channels = client.get(f"/api/catalog/tests/{test_id}/channels?catalog_id=proj").json()
    assert {c["channel_name"] for c in channels} == {"THRUST"}

    q = client.post(
        "/api/v3/series/query?format=series",
        json={
            "sources": [{"type": "catalog", "test_id": test_id, "channel_names": ["THRUST"], "catalog_id": "proj"}],
            "mode": "detail",
            "aggregation_mode": "raw",
            "resolution_px": 800,
        },
    )
    assert q.status_code == 200, q.text
    payload = q.json()
    assert payload["series"]
    assert payload["series"][0]["channel_name"] == "THRUST"
    assert len(payload["series"][0]["x_ms"]) == 4


def test_temporary_ingest_uses_local_and_is_idempotent(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")

    csv_path = tmp_path / "pulse.csv"
    csv_path.write_text(
        "time_s,THRUST\n0.0,1.0\n0.2,1.0\n0.4,50.0\n0.6,55.0\n0.8,2.0\n1.0,1.0\n",
        encoding="utf-8",
    )
    first = run_ingest("csv", str(csv_path))
    second = run_ingest("csv", str(csv_path))
    assert first["status"] == "ready"
    assert first["durability"] == "temporary"
    assert first["catalog_id"] == "local"
    assert second["artifact_id"] == first["artifact_id"]
    assert second["test_run_id"] == first["test_run_id"]


def test_permanent_ingest_rejects_missing_project_catalog(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "local.duckdb")
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("time_s,X\n0,1\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="project catalog_id"):
        run_ingest("csv", str(csv_path), ingest_mode="permanent")


def test_apply_threshold_range_rule(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "catalog.duckdb")

    csv_path = tmp_path / "pulse.csv"
    csv_path.write_text(
        "time_s,THRUST\n0.0,1.0\n0.2,1.0\n0.4,50.0\n0.6,55.0\n0.8,2.0\n1.0,1.0\n",
        encoding="utf-8",
    )
    manifest = run_ingest("csv", str(csv_path))
    test_id = int(manifest["test_run_id"])

    rule = client.post(
        "/api/catalog/range-rules",
        json={
            "name": "Hot",
            "kind": "threshold",
            "channel_name": "THRUST",
            "config": '{"op":">","value":10,"sustain_ms":0}',
            "default_label": "Hot",
            "default_color": "#ef4444",
        },
    ).json()

    applied = client.post(
        "/api/catalog/range-rules/apply",
        json={"test_id": test_id, "rule_id": rule["rule_id"]},
    )
    assert applied.status_code == 200, applied.text
    ranges = applied.json()
    assert len(ranges) >= 1
    assert ranges[0]["source"] == "rule"
    assert ranges[0]["label"] == "Hot"

    listed = client.get(f"/api/catalog/ranges?test_id={test_id}").json()
    assert len(listed) >= 1
