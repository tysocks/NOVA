from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import user_settings

client = TestClient(app)


def test_library_paths_default_and_override(tmp_path: Path):
    listed = client.get("/api/library-paths").json()["libraries"]
    assert "configurations" in listed
    assert "units" in listed
    assert "range_definitions" in listed
    assert "ingestion_rules" in listed
    assert "limits" in listed
    assert "directory" in listed["units"]

    dest_dir = tmp_path / "libs"
    save = client.post("/api/library-paths", json={"libraries": {"units": str(dest_dir)}})
    assert save.status_code == 200
    body = save.json()
    assert body["ok"] is True
    dest = dest_dir / ".nova_unit_library.json"
    assert Path(body["libraries"]["units"]["path"]) == dest.resolve()
    assert Path(body["libraries"]["units"]["directory"]) == dest_dir.resolve()

    payload = {
        "categories": [
            {
                "id": "temp",
                "name": "Temperature",
                "preferred": "K",
                "units": [{"symbol": "C", "to_preferred": "x + 273.15"}],
            }
        ]
    }
    written = client.post("/api/unit-library", json=payload)
    assert written.status_code == 200
    assert dest.exists()
    loaded = client.get("/api/unit-library").json()["categories"]
    assert loaded[0]["preferred"] == "K"


def test_limit_library_roundtrip(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_limit_library.json"
    monkeypatch.setattr("app.main.LIMIT_LIBRARY_FILE", path)
    payload = {
        "limits": [
            {
                "id": "lim-1",
                "name": "Chamber",
                "unit": "kPa",
                "thresholds": [{"value": 100, "direction": "upper", "type": "warn"}],
            }
        ]
    }
    save = client.post("/api/limit-library", json=payload)
    assert save.status_code == 200
    assert save.json()["ok"] is True
    load = client.get("/api/limit-library").json()["limits"]
    assert load[0]["name"] == "Chamber"
    assert path.exists()


def test_set_library_paths_copies_existing(tmp_path: Path):
    src = user_settings.library_path("limits")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text('{"limits":[{"id":"a","name":"A"}]}', encoding="utf-8")
    dest_dir = tmp_path / "moved"
    result = user_settings.set_library_paths({"limits": str(dest_dir)})
    dest = dest_dir / ".nova_limit_library.json"
    assert Path(result["limits"]["path"]) == dest.resolve()
    assert Path(result["limits"]["directory"]) == dest_dir.resolve()
    assert dest.exists()
    assert "A" in dest.read_text(encoding="utf-8")


def test_set_library_paths_accepts_legacy_json_file(tmp_path: Path):
    dest = tmp_path / "legacy" / "my_units.json"
    result = user_settings.set_library_paths({"units": str(dest)}, copy_existing=False)
    assert Path(result["units"]["path"]) == dest.resolve()
    assert Path(result["units"]["directory"]) == dest.parent.resolve()


def test_set_ingestion_and_limits_folders(tmp_path: Path):
    ingest_dir = tmp_path / "ingest"
    limits_dir = tmp_path / "limits"
    result = user_settings.set_library_paths(
        {"ingestion_rules": str(ingest_dir), "limits": str(limits_dir)},
        copy_existing=False,
    )
    assert Path(result["ingestion_rules"]["directory"]) == ingest_dir.resolve()
    assert Path(result["limits"]["directory"]) == limits_dir.resolve()
    saved = user_settings.load_user_settings()["libraries"]
    assert Path(saved["ingestion_rules"]) == ingest_dir.resolve()
    assert Path(saved["limits"]) == limits_dir.resolve()


def test_default_library_dir_is_user_data(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(user_settings, "_fallback_getters", {})
    monkeypatch.setattr(user_settings, "USER_SETTINGS_FILE", tmp_path / ".nova_user_settings.json")
    monkeypatch.setenv("NOVA_USER_DATA_DIR", str(tmp_path / "userdata"))
    dest = user_settings.default_library_path("units")
    assert dest.parent == (tmp_path / "userdata" / "libraries")
    assert dest.name == ".nova_unit_library.json"
    assert dest.parent.exists()
