import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import DATABASE_LIBRARY_FILE, app

client = TestClient(app)


@pytest.fixture
def library_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_database_library.json"
    monkeypatch.setattr("app.main.DATABASE_LIBRARY_FILE", path)
    monkeypatch.setattr("app.services.database_library.DATABASE_LIBRARY_FILE", path)
    return path


def test_get_database_library_empty_has_no_local_catalog(library_file: Path, monkeypatch):
    library_file.unlink(missing_ok=True)
    monkeypatch.setattr("app.config.settings.enable_postgres", False)
    monkeypatch.setattr("app.services.database_library.settings.enable_postgres", False)

    response = client.get("/api/database-library")

    assert response.status_code == 200
    body = response.json()
    assert body["databases"] == []
    assert body.get("active_catalog_id") in (None, "")
    assert library_file.exists()
    assert not any(str(r.get("name") or "").lower() == "local catalog" for r in body["databases"])


def test_library_strips_legacy_local_catalog(library_file: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.enable_postgres", False)
    monkeypatch.setattr("app.services.database_library.settings.enable_postgres", False)
    library_file.write_text(
        json.dumps(
            {
                "databases": [
                    {
                        "id": "local",
                        "type": "duckdb",
                        "name": "Local Catalog",
                        "catalog_path": str(Path("backend/.nova_catalog.duckdb")),
                        "default_ingest_mode": "temporary",
                    },
                    {
                        "id": "proj",
                        "type": "duckdb",
                        "name": "Project",
                        "catalog_path": "D:/proj/catalog.duckdb",
                        "parquet_root": "D:/proj/parquet",
                        "default_ingest_mode": "temporary",
                        "is_default": True,
                    },
                ],
                "active_catalog_id": "local",
            }
        ),
        encoding="utf-8",
    )
    response = client.get("/api/database-library")
    assert response.status_code == 200
    rows = response.json()["databases"]
    assert len(rows) == 1
    assert rows[0]["id"] == "proj"
    assert rows[0]["default_ingest_mode"] == "permanent"
    assert response.json()["active_catalog_id"] == "proj"

def test_save_and_load_duckdb_and_postgres_library(library_file: Path):
    payload = {
        "databases": [
            {
                "id": "cat-1",
                "type": "duckdb",
                "name": "Project A",
                "catalog_path": "D:/proj-a/catalog.duckdb",
                "parquet_root": "D:/proj-a/parquet",
                "default_ingest_mode": "permanent",
                "is_default": True,
                "tags": ["lab", "hotfire"],
                "updated_at": "2026-07-27T10:00:00.000Z",
            },
            {
                "id": "profile-1",
                "type": "postgres",
                "name": "Lab DB",
                "host": "db.example.com",
                "port": 5432,
                "user": "nova",
                "password": "secret",
                "sslmode": "disable",
                "tags": ["legacy"],
                "updated_at": "2026-07-26T12:00:00.000Z",
            },
        ],
        "active_catalog_id": "cat-1",
    }
    save = client.post("/api/database-library", json=payload)
    assert save.status_code == 200
    assert save.json()["ok"] is True
    assert save.json()["count"] == 2

    load = client.get("/api/database-library")
    assert load.status_code == 200
    rows = load.json()["databases"]
    assert len(rows) == 2
    duck = next(r for r in rows if r["type"] == "duckdb")
    assert duck["name"] == "Project A"
    assert duck["tags"] == ["lab", "hotfire"]
    assert duck["updated_at"] == "2026-07-27T10:00:00.000Z"
    assert duck["catalog_path"].replace("\\", "/").endswith("proj-a/catalog.duckdb") or "proj-a" in duck["catalog_path"]
    pg = next(r for r in rows if r["type"] == "postgres")
    assert pg["host"] == "db.example.com"
    assert pg["tags"] == ["legacy"]
    assert pg["updated_at"] == "2026-07-26T12:00:00.000Z"
    assert load.json()["active_catalog_id"] == "cat-1"


def test_save_database_library_rejects_non_list(library_file: Path):
    response = client.post("/api/database-library", json={"databases": "bad"})
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_save_database_library_rejects_duplicate_names(library_file: Path, tmp_path: Path):
    a = tmp_path / "a.duckdb"
    b = tmp_path / "b.duckdb"
    response = client.post(
        "/api/database-library",
        json={
            "databases": [
                {"id": "a", "type": "duckdb", "name": "Rocket", "catalog_path": str(a), "is_default": True},
                {"id": "b", "type": "duckdb", "name": "rocket", "catalog_path": str(b)},
            ],
            "active_catalog_id": "a",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "already exists" in str(body.get("error") or "").lower()
    # Library file should remain empty / not contain duplicates from this rejected write.
    if library_file.exists():
        loaded = client.get("/api/database-library").json()
        names = [str(r.get("name") or "").lower() for r in loaded.get("databases") or []]
        assert names.count("rocket") <= 1


def test_test_duckdb_catalog_connection(library_file: Path, tmp_path: Path):
    catalog = tmp_path / "proj" / "catalog.duckdb"
    payload = {
        "type": "duckdb",
        "name": "Tmp",
        "catalog_path": str(catalog),
        "parquet_root": str(tmp_path / "proj" / "parquet"),
    }
    response = client.post("/api/database-library/test", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["test_count"] == 0
    assert catalog.is_file()


def test_set_active_catalog(library_file: Path, tmp_path: Path):
    a = tmp_path / "a.duckdb"
    b = tmp_path / "b.duckdb"
    client.post(
        "/api/database-library",
        json={
            "databases": [
                {"id": "a", "type": "duckdb", "name": "A", "catalog_path": str(a), "is_default": True},
                {"id": "b", "type": "duckdb", "name": "B", "catalog_path": str(b)},
            ],
            "active_catalog_id": "a",
        },
    )
    res = client.post("/api/database-library/active", json={"catalog_id": "b"})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["active_catalog_id"] == "b"
    loaded = client.get("/api/database-library").json()
    assert loaded["active_catalog_id"] == "b"


def test_test_database_connection_success(library_file: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.enable_postgres", True)
    from app.models import DatabaseItem

    with patch("app.main.list_databases", return_value=[DatabaseItem(name="lab", is_default=True)]):
        response = client.post(
            "/api/database-library/test",
            json={
                "type": "postgres",
                "host": "db.example.com",
                "port": 5432,
                "user": "nova",
                "password": "secret",
                "sslmode": "disable",
            },
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "database_count": 1}


def test_multi_catalog_isolation(library_file: Path, tmp_path: Path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr("app.engine.session_store.SESSIONS_ROOT", sessions)
    monkeypatch.setattr("app.engine.session_store.ensure_sessions_root", lambda: sessions.mkdir(parents=True, exist_ok=True) or sessions)
    monkeypatch.setattr("app.engine.session_store.artifact_dir", lambda aid: (sessions / aid).mkdir(parents=True, exist_ok=True) or (sessions / aid))
    monkeypatch.setattr("app.engine.session_store.data_dir", lambda aid: (sessions / aid / "data").mkdir(parents=True, exist_ok=True) or (sessions / aid / "data"))
    monkeypatch.setattr("app.engine.session_store.manifest_path", lambda aid: sessions / aid / "manifest.json")

    cat_a = tmp_path / "a" / "catalog.duckdb"
    cat_b = tmp_path / "b" / "catalog.duckdb"
    client.post(
        "/api/database-library",
        json={
            "databases": [
                {"id": "a", "type": "duckdb", "name": "A", "catalog_path": str(cat_a), "is_default": True},
                {"id": "b", "type": "duckdb", "name": "B", "catalog_path": str(cat_b)},
            ],
            "active_catalog_id": "a",
        },
    )

    csv_a = tmp_path / "run_a.csv"
    csv_a.write_text("time_s,X\n0,1\n1,2\n", encoding="utf-8")
    csv_b = tmp_path / "run_b.csv"
    csv_b.write_text("time_s,Y\n0,3\n1,4\n", encoding="utf-8")

    from app.engine.file_index import run_ingest

    ma = run_ingest("csv", str(csv_a), catalog_id="a")
    mb = run_ingest("csv", str(csv_b), catalog_id="b")
    assert ma["status"] == "ready"
    assert mb["status"] == "ready"

    tests_a = client.get("/api/catalog/tests?catalog_id=a").json()
    tests_b = client.get("/api/catalog/tests?catalog_id=b").json()
    codes_a = {t["run_code"] for t in tests_a}
    codes_b = {t["run_code"] for t in tests_b}
    assert "run_a" in codes_a
    assert "run_b" not in codes_a
    assert "run_b" in codes_b
    assert "run_a" not in codes_b
