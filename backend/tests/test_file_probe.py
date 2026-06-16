from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.engine.file_probe import probe_file
from app.engine.file_index import manifest_to_channels, run_ingest
from app.main import app

client = TestClient(app)


def test_probe_csv_detects_header_units(tmp_path: Path):
    csv_path = tmp_path / "units.csv"
    csv_path.write_text(
        "time_s,THRUST (N),P[psi]\n0.0,1.0,10.0\n0.5,2.0,11.0\n",
        encoding="utf-8",
    )

    result = probe_file(str(csv_path))
    assert result.source_type == "csv"
    assert result.time_index_default == "time_s"
    assert result.units_metadata.parse_units_from_header is True
    assert result.units_metadata.flag == "ok"
    assert set(result.units_metadata.channels_with_units) == {"THRUST (N)", "P[psi]"}


def test_probe_parquet_schema_units(tmp_path: Path):
    table = pa.table(
        {
            "time_s": [0.0, 1.0],
            "THRUST": [1.0, 2.0],
        },
        schema=pa.schema(
            [
                pa.field("time_s", pa.float64()),
                pa.field("THRUST", pa.float64(), metadata={b"unit": b"N"}),
            ]
        ),
    )
    parquet_path = tmp_path / "sample.parquet"
    pq.write_table(table, parquet_path)

    result = probe_file(str(parquet_path))
    assert result.source_type == "parquet"
    assert result.units_metadata.flag == "ok"
    assert result.units_metadata.channels_with_units == ["THRUST"]
    assert result.units_metadata.parse_units_from_header is False


def test_probe_parquet_units_in_headers_keeps_schema_units(tmp_path: Path):
    table = pa.table(
        {
            "time_s": [0.0, 1.0],
            "thrust_n": [100.0, 110.0],
        },
        schema=pa.schema(
            [
                pa.field("time_s", pa.float64()),
                pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"}),
            ]
        ),
    )
    parquet_path = tmp_path / "schema_units.parquet"
    pq.write_table(table, parquet_path)

    result = probe_file(str(parquet_path), units_in_headers=True)
    assert result.units_metadata.flag == "ok"
    assert result.units_metadata.channels_with_units == ["thrust_n"]
    assert result.channels[0].unit == "N"


def test_ingest_parquet_units_in_headers_preserves_schema_units(sessions_tmp, tmp_path: Path):
    table = pa.table(
        {"time_s": [0.0, 1.0], "thrust_n": [10.0, 12.0]},
        schema=pa.schema(
            [
                pa.field("time_s", pa.float64()),
                pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"}),
            ]
        ),
    )
    parquet_path = tmp_path / "units.parquet"
    pq.write_table(table, parquet_path)

    manifest = run_ingest(
        "parquet",
        str(parquet_path),
        time_index_channel="time_s",
        units_in_headers=True,
    )
    assert manifest["status"] == "ready"
    by_name = {c["channel_name"]: c for c in manifest["channels"]}
    assert by_name["thrust_n"]["unit"] == "N"


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


def test_ingest_parquet_with_time_index(sessions_tmp, tmp_path: Path):
    table = pa.table({"time_s": [0.0, 1.0], "P": [10.0, 12.0]})
    parquet_path = tmp_path / "wide.parquet"
    pq.write_table(table, parquet_path)

    manifest = run_ingest(
        "parquet",
        str(parquet_path),
        time_index_channel="time_s",
    )
    assert manifest["status"] == "ready"
    assert len(manifest["channels"]) == 1
    assert manifest["channels"][0]["channel_name"] == "P"


def test_probe_arrow_feather_file(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.feather as feather

    table = pa.table(
        {"time_s": [0.0, 0.5, 1.0], "THRUST (N)": [100.0, 110.0, 120.0]},
        schema=pa.schema(
            [
                pa.field("time_s", pa.float64()),
                pa.field("THRUST (N)", pa.float64(), metadata={b"unit": b"N"}),
            ]
        ),
    )
    arrow_path = tmp_path / "sample.arrow"
    feather.write_feather(table, arrow_path)

    result = probe_file(str(arrow_path))
    assert result.source_type == "arrow"
    assert result.time_index_default == "time_s"
    assert result.units_metadata.channels_with_units == ["THRUST (N)"]


def test_manifest_to_channels_backfills_units_from_source_file(sessions_tmp, tmp_path: Path):
    table = pa.table(
        {"time_s": [0.0, 1.0], "thrust_n": [10.0, 12.0]},
        schema=pa.schema(
            [
                pa.field("time_s", pa.float64()),
                pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"}),
            ]
        ),
    )
    parquet_path = tmp_path / "backfill.parquet"
    pq.write_table(table, parquet_path)

    manifest = run_ingest("parquet", str(parquet_path), time_index_channel="time_s")
    manifest["channels"][0]["unit"] = None
    channels = manifest_to_channels(manifest)
    by_name = {c.channel_name: c.unit for c in channels}
    assert by_name["thrust_n"] == "N"


def test_ingest_refreshes_stale_manifest_missing_units(sessions_tmp, tmp_path: Path):
    table = pa.table(
        {"time_s": [0.0, 1.0], "thrust_n": [10.0, 12.0]},
        schema=pa.schema(
            [
                pa.field("time_s", pa.float64()),
                pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"}),
            ]
        ),
    )
    parquet_path = tmp_path / "refresh.parquet"
    pq.write_table(table, parquet_path)

    first = run_ingest(
        "parquet",
        str(parquet_path),
        time_index_channel="time_s",
        units_in_headers=True,
    )
    first["channels"][0]["unit"] = None
    from app.engine.session_store import save_manifest

    save_manifest(first["artifact_id"], first)

    second = run_ingest(
        "parquet",
        str(parquet_path),
        time_index_channel="time_s",
        units_in_headers=False,
    )
    assert second["channels"][0]["unit"] == "N"


def test_probe_api_endpoint(tmp_path: Path):
    csv_path = tmp_path / "probe.csv"
    csv_path.write_text("time_s,CH\n0,1\n", encoding="utf-8")
    response = client.post(
        "/api/v3/file/probe",
        json={"file_path": str(csv_path)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_type"] == "csv"
    assert "time_s" in body["time_index_candidates"]
