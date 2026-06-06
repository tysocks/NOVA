from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.duckdb_source import fetch_artifact_timeseries
from app.engine.file_index import run_ingest
from app.main import app

client = TestClient(app)


@pytest.fixture
def sessions_tmp(monkeypatch, tmp_path: Path):
    root = tmp_path / "sessions"

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
    return root


def test_ingest_csv_and_query_via_duckdb(sessions_tmp, tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("time_s,THRUST (N),P[psi]\n0.0,1.0,10.0\n0.5,2.0,11.0\n1.0,3.0,12.0\n", encoding="utf-8")

    manifest = run_ingest("csv", str(csv_path), units_in_headers=True)
    assert manifest["status"] == "ready"
    artifact_id = manifest["artifact_id"]
    assert len(manifest["channels"]) == 2

    points = fetch_artifact_timeseries(
        artifact_id,
        ["THRUST", "P"],
        max_points=100,
        mode="overview",
    )
    assert len(points) >= 2
    names = {p.channel_name for p in points}
    assert names == {"THRUST", "P"}
    thrust_units = {p.unit for p in points if p.channel_name == "THRUST"}
    assert thrust_units == {"N"}


def test_ingest_h5_api(sessions_tmp, tmp_path: Path):
    import h5py

    h5_path = tmp_path / "sample.h5"
    with h5py.File(h5_path, "w") as h5:
        telem = h5.create_group("telemetry")
        telem.create_dataset("TIME", data=[0.0, 0.5, 1.0])
        telem.create_dataset("THRUST", data=[10.0, 11.0, 12.0])

    response = client.post(
        "/api/v3/ingest/file",
        json={"source_type": "h5", "file_path": str(h5_path)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["artifact_id"]

    status = client.get(f"/api/v3/ingest/{body['artifact_id']}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "ready"

    channels = client.get(f"/api/v3/ingest/{body['artifact_id']}/channels")
    assert channels.status_code == 200
    assert any(c["channel_name"] == "telemetry/THRUST" for c in channels.json())


def test_v3_series_query_file_source_json(sessions_tmp, tmp_path: Path):
    csv_path = tmp_path / "q.csv"
    csv_path.write_text("time_s,value\n0.0,1\n1.0,2\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path))

    response = client.post(
        "/api/v3/series/query?format=json",
        json={
            "sources": [
                {"type": "file", "artifact_id": manifest["artifact_id"], "channel_names": ["value"]}
            ],
            "mode": "overview",
            "resolution_px": 500,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["row_count"] >= 1
    assert body["rows"][0]["channel_name"] == "value"


def test_run_ingest_is_idempotent_for_same_path(sessions_tmp, tmp_path: Path):
    csv_path = tmp_path / "idem.csv"
    csv_path.write_text("time_s,value\n0.0,1\n1.0,2\n", encoding="utf-8")
    m1 = run_ingest("csv", str(csv_path))
    m2 = run_ingest("csv", str(csv_path))
    assert m1["artifact_id"] == m2["artifact_id"]
    assert m2["status"] == "ready"


def test_ingest_lookup_by_path_api(sessions_tmp, tmp_path: Path):
    csv_path = tmp_path / "lookup.csv"
    csv_path.write_text("time_s,value\n0.0,1\n", encoding="utf-8")
    manifest = run_ingest("csv", str(csv_path))
    response = client.get(
        "/api/v3/ingest/by-path",
        params={"file_path": str(csv_path), "source_type": "csv"},
    )
    assert response.status_code == 200
    assert response.json()["artifact_id"] == manifest["artifact_id"]


def test_file_timeseries_uses_artifact_when_indexed(sessions_tmp, tmp_path: Path, monkeypatch):
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text("time_s,value\n0.0,1\n1.0,2\n", encoding="utf-8")
    run_ingest("csv", str(csv_path))

    from app.services.file_sources import file_timeseries

    rows = file_timeseries("csv", str(csv_path), ["value"], limit=10_000)
    assert len(rows) == 2

    # Legacy warning only when no artifact index exists
    other = tmp_path / "other.csv"
    other.write_text("time_s,value\n0.0,5\n", encoding="utf-8")
    with pytest.warns(UserWarning, match="legacy iterrows"):
        rows2 = file_timeseries("csv", str(other), ["value"], limit=10_000)
    assert len(rows2) == 1
