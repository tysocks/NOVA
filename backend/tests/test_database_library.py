import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import DATABASE_LIBRARY_FILE, app

client = TestClient(app)


@pytest.fixture
def library_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_database_library.json"
    monkeypatch.setattr("app.main.DATABASE_LIBRARY_FILE", path)
    return path


def test_get_database_library_empty_seeds_from_settings(library_file: Path):
    library_file.unlink(missing_ok=True)
    with patch("app.main.settings") as mock_settings:
        mock_settings.redscale_host = "red.local"
        mock_settings.redscale_port = 5432
        mock_settings.redscale_user = "red_user"
        mock_settings.redscale_password = "red_pass"
        mock_settings.redscale_sslmode = "disable"
        mock_settings.bluescale_host = "blue.local"
        mock_settings.bluescale_port = 5433
        mock_settings.bluescale_user = "blue_user"
        mock_settings.bluescale_password = "blue_pass"
        mock_settings.bluescale_sslmode = "require"

        response = client.get("/api/database-library")

    assert response.status_code == 200
    body = response.json()
    assert len(body["databases"]) == 2
    names = {row["name"] for row in body["databases"]}
    assert names == {"RedScale", "BlueScale"}
    assert library_file.exists()
    saved = json.loads(library_file.read_text(encoding="utf-8"))
    assert len(saved["databases"]) == 2


def test_save_and_load_database_library(library_file: Path):
    payload = {
        "databases": [
            {
                "id": "profile-1",
                "type": "postgres",
                "name": "Lab DB",
                "host": "db.example.com",
                "port": 5432,
                "user": "nova",
                "password": "secret",
                "sslmode": "disable",
            }
        ]
    }
    save = client.post("/api/database-library", json=payload)
    assert save.status_code == 200
    assert save.json() == {"ok": True, "count": 1}

    load = client.get("/api/database-library")
    assert load.status_code == 200
    rows = load.json()["databases"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Lab DB"
    assert rows[0]["host"] == "db.example.com"
    assert rows[0]["password"] == "secret"


def test_save_database_library_rejects_non_list(library_file: Path):
    response = client.post("/api/database-library", json={"databases": "bad"})
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_test_database_connection_success(library_file: Path):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"datname": "hfr_test_data"}]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("app.services.timeseries.get_conn") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        response = client.post(
            "/api/database-library/test",
            json={
                "host": "localhost",
                "port": 5432,
                "user": "pipeline",
                "password": "test",
                "sslmode": "disable",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["database_count"] == 1


def test_test_database_connection_failure(library_file: Path):
    with patch("app.main.list_databases", side_effect=Exception("connection refused")):
        response = client.post(
            "/api/database-library/test",
            json={
                "host": "localhost",
                "port": 5432,
                "user": "pipeline",
                "password": "bad",
                "sslmode": "disable",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]


def test_test_database_connection_requires_host_and_user():
    response = client.post("/api/database-library/test", json={"host": "", "user": ""})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "required" in body["error"].lower()
