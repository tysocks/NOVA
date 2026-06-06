from pathlib import Path

import h5py
import pytest

from app.engine.file_index import run_ingest
from app.engine.series_query import execute_series_query
from app.models import FileSeriesSource, SeriesQueryRequest


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


def test_v3_query_h5_artifact_overview(sessions_tmp, tmp_path: Path, monkeypatch):
    root = tmp_path / "sessions"

    def _artifact_dir(aid: str) -> Path:
        d = root / aid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _data_dir(aid: str) -> Path:
        d = root / aid / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("app.engine.session_store.SESSIONS_ROOT", root)
    monkeypatch.setattr("app.engine.session_store.ensure_sessions_root", lambda: root.mkdir(parents=True, exist_ok=True) or root)
    monkeypatch.setattr("app.engine.session_store.artifact_dir", _artifact_dir)
    monkeypatch.setattr("app.engine.session_store.data_dir", _data_dir)
    monkeypatch.setattr(
        "app.engine.session_store.manifest_path",
        lambda aid: root / aid / "manifest.json",
    )

    h5_path = tmp_path / "plot.h5"
    with h5py.File(h5_path, "w") as h5:
        telem = h5.create_group("telemetry")
        telem.create_dataset("TIME", data=[0.0, 0.5, 1.0])
        telem.create_dataset("THRUST", data=[10.0, 11.0, 12.0])

    manifest = run_ingest("h5", str(h5_path))
    request = SeriesQueryRequest(
        sources=[
            FileSeriesSource(
                artifact_id=manifest["artifact_id"],
                channel_names=["telemetry/THRUST"],
            )
        ],
        mode="overview",
        resolution_px=500,
    )
    ipc, meta = execute_series_query(request)
    assert meta.row_count >= 1
    assert meta.fetch_strategy == "aggregate"
    assert len(ipc) > 0
