import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import UNIT_LIBRARY_FILE, app
from app.services.unit_library import convert_value_to_preferred, default_unit_library_rows, resolve_display_unit

client = TestClient(app)


@pytest.fixture
def unit_library_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_unit_library.json"
    monkeypatch.setattr("app.main.UNIT_LIBRARY_FILE", path)
    return path


def test_get_unit_library_seeds_defaults(unit_library_file: Path):
    unit_library_file.unlink(missing_ok=True)
    response = client.get("/api/unit-library")
    assert response.status_code == 200
    body = response.json()
    assert len(body["categories"]) == 1
    assert body["categories"][0]["name"] == "Temperature"
    assert body["categories"][0]["preferred"] == "K"
    assert unit_library_file.exists()


def test_save_and_load_unit_library(unit_library_file: Path):
    payload = {
        "categories": [
            {
                "id": "cat-1",
                "name": "Pressure",
                "preferred": "Pa",
                "units": [{"symbol": "psi", "to_preferred": "x * 6894.76"}],
            }
        ]
    }
    save = client.post("/api/unit-library", json=payload)
    assert save.status_code == 200
    assert save.json() == {"ok": True, "count": 1}

    load = client.get("/api/unit-library")
    rows = load.json()["categories"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Pressure"
    assert rows[0]["units"][0]["symbol"] == "psi"


def test_save_unit_library_rejects_invalid_formula(unit_library_file: Path):
    payload = {
        "categories": [
            {
                "name": "Temperature",
                "preferred": "K",
                "units": [{"symbol": "C", "to_preferred": "import os"}],
            }
        ]
    }
    response = client.post("/api/unit-library", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "error" in body


def test_temperature_conversions():
    cats = default_unit_library_rows()
    assert resolve_display_unit("C", cats) == "K"
    assert resolve_display_unit("F", cats) == "K"
    assert convert_value_to_preferred(0.0, "C", cats) == pytest.approx(273.0)
    assert convert_value_to_preferred(32.0, "F", cats) == pytest.approx(273.0)
