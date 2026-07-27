from pathlib import Path

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


def test_ingest_registers_duckdb_catalog(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "catalog.duckdb")

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("time_s,THRUST (N),P[psi]\n0.0,1.0,10.0\n1.0,2.0,11.0\n", encoding="utf-8")

    manifest = run_ingest("csv", str(csv_path), units_in_headers=True)
    test_id = int(manifest["test_run_id"])

    tests_response = client.get("/api/catalog/tests")
    assert tests_response.status_code == 200
    tests = tests_response.json()
    assert any(row["test_run_id"] == test_id and row["run_code"] == "sample" for row in tests)

    channels_response = client.get(f"/api/catalog/tests/{test_id}/channels")
    assert channels_response.status_code == 200
    channel_names = {row["channel_name"] for row in channels_response.json()}
    assert channel_names == {"THRUST", "P"}

    params_response = client.get(f"/api/catalog/tests/{test_id}/parameters")
    assert params_response.status_code == 200
    assert params_response.json() == []


def test_catalog_ranges_rules_and_results_api(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "catalog.duckdb")

    csv_path = tmp_path / "phases.csv"
    csv_path.write_text("time_s,value\n0.0,1.0\n1.0,2.0\n2.0,3.0\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path))
    test_id = int(manifest["test_run_id"])

    rule_response = client.post(
        "/api/catalog/range-rules",
        json={
            "name": "Rising Edge",
            "kind": "edge",
            "channel_name": "value",
            "config": '{"direction":"rising","threshold":1.5}',
            "default_label": "Rise",
        },
    )
    assert rule_response.status_code == 200
    rule = rule_response.json()
    assert rule["kind"] == "edge"

    range_response = client.post(
        "/api/catalog/ranges",
        json={
            "test_id": test_id,
            "name": "Steady State",
            "label": "SS",
            "start_time": "1970-01-01T00:00:00Z",
            "end_time": "1970-01-01T00:00:02Z",
            "source": "user",
            "parameters": [{"key": "phase", "value_text": "steady"}],
        },
    )
    assert range_response.status_code == 200
    range_row = range_response.json()
    assert range_row["test_id"] == test_id

    list_ranges_response = client.get("/api/catalog/ranges", params={"test_id": test_id})
    assert list_ranges_response.status_code == 200
    assert list_ranges_response.json()[0]["name"] == "Steady State"

    result_response = client.post(
        "/api/catalog/results",
        json={
            "test_id": test_id,
            "range_id": range_row["range_id"],
            "analysis_name": "steady_state_stats",
            "key": "avg_value",
            "value_num": 2.0,
            "value_text": "2.0",
            "unit": "arb",
        },
    )
    assert result_response.status_code == 200
    result_row = result_response.json()
    assert result_row["analysis_name"] == "steady_state_stats"

    results_response = client.get(
        "/api/catalog/results",
        params={"test_id": test_id, "analysis_name": "steady_state_stats"},
    )
    assert results_response.status_code == 200
    assert results_response.json()[0]["key"] == "avg_value"


def test_catalog_series_query_uses_absolute_parquet_uris(monkeypatch, tmp_path: Path):
    _patch_sessions(monkeypatch, tmp_path / "sessions")
    _patch_catalog(monkeypatch, tmp_path / "catalog.duckdb")

    csv_path = tmp_path / "query.csv"
    csv_path.write_text("time_s,value\n0.0,1.0\n1.0,2.0\n2.0,3.0\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path))
    test_id = int(manifest["test_run_id"])

    from app.engine.catalog_store import list_channel_parquet_entries

    entries = list_channel_parquet_entries(test_id, ["value"])
    assert len(entries) == 1
    assert Path(entries[0]["parquet_uri"]).is_absolute()
    assert Path(entries[0]["parquet_uri"]).is_file()

    response = client.post(
        "/api/v3/series/query?format=json",
        json={
            "sources": [
                {"type": "catalog", "test_id": test_id, "channel_names": ["value"]}
            ],
            "mode": "overview",
            "resolution_px": 500,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["row_count"] >= 1
    assert body["rows"][0]["channel_name"] == "value"
    assert body["rows"][0]["test_run_id"] == test_id
