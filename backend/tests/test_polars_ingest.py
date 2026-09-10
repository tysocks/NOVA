from pathlib import Path
from datetime import datetime, timezone

from app.engine.file_index import run_ingest
from app.engine.polars_tabular import normalize_csv_polars, normalize_tabular_polars


def _patch_sessions_and_catalog(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    catalog = tmp_path / "catalog.duckdb"

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
    monkeypatch.setattr("app.engine.catalog_store.CATALOG_DB_PATH", catalog)


def test_normalize_csv_polars_units_and_time(tmp_path: Path):
    csv_path = tmp_path / "units.csv"
    csv_path.write_text(
        "time_s,THRUST (N),P[psi]\n0.0,1.0,10.0\n1.0,2.0,11.0\n",
        encoding="utf-8",
    )
    df, unit_map, time_col, t0_utc = normalize_csv_polars(str(csv_path), units_in_headers=True)
    assert time_col == "time_s"
    assert "__time__" in df.columns
    assert "THRUST" in df.columns
    assert unit_map["THRUST"] == "N"
    assert unit_map["P"] == "psi"
    assert df.height == 2
    assert t0_utc.tzinfo is not None
    # Relative time_s is anchored so first sample ≈ ingest wall clock.
    first = df["__time__"][0]
    first_dt = first if isinstance(first, datetime) else first.to_python()
    if getattr(first_dt, "tzinfo", None) is None:
        first_dt = first_dt.replace(tzinfo=timezone.utc)
    delta_s = abs((first_dt - t0_utc).total_seconds())
    assert delta_s < 2.0


def test_polars_csv_ingest_end_to_end(monkeypatch, tmp_path: Path):
    _patch_sessions_and_catalog(monkeypatch, tmp_path)
    csv_path = tmp_path / "rocket.csv"
    csv_path.write_text(
        "time_s,THRUST (N)\n0.0,1500.0\n0.1,1500.2\n0.2,1501.0\n",
        encoding="utf-8",
    )
    manifest = run_ingest("csv", str(csv_path), units_in_headers=True)
    assert manifest["status"] == "ready"
    assert len(manifest["channels"]) == 1
    assert manifest["channels"][0]["channel_name"] == "THRUST"
    assert manifest["channels"][0]["unit"] == "N"
    assert manifest["channels"][0]["point_count"] == 3
    assert manifest.get("t0_utc")

    import pyarrow.parquet as pq

    parquet = tmp_path / "sessions" / manifest["artifact_id"] / "data" / "THRUST.parquet"
    table = pq.read_table(parquet)
    assert "timestamp_utc" in table.column_names
    assert "x_ms" in table.column_names
    assert "y" in table.column_names
    xs = [float(v.as_py()) for v in table.column("x_ms")]
    # Anchored away from Unix epoch for relative time_s.
    assert xs[0] > 1e11
    assert len(set(round(x, 3) for x in xs)) == 3
    assert abs((max(xs) - min(xs)) - 200.0) < 1.0


def test_polars_parquet_ingest(monkeypatch, tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    _patch_sessions_and_catalog(monkeypatch, tmp_path)
    schema = pa.schema(
        [
            pa.field("time_s", pa.float64()),
            pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"}),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array([0.0, 1.0, 2.0], type=pa.float64()),
            pa.array([1.0, 2.0, 3.0], type=pa.float64()),
        ],
        schema=schema,
    )
    parquet_path = tmp_path / "wide.parquet"
    pq.write_table(table, parquet_path)

    df, unit_map, time_col, t0_utc = normalize_tabular_polars(
        str(parquet_path),
        source_type="parquet",
        time_col="time_s",
    )
    assert time_col == "time_s"
    assert unit_map.get("thrust_n") == "N"
    assert df.height == 3
    assert t0_utc.tzinfo is not None

    manifest = run_ingest("parquet", str(parquet_path), time_index_channel="time_s")
    assert manifest["status"] == "ready"
    assert any(ch["channel_name"] == "thrust_n" for ch in manifest["channels"])


def test_parquet_subsecond_relative_time_stays_distinct(monkeypatch, tmp_path: Path):
    """Simulation dumps like 26 steps of 0.02 s must not collapse onto one timestamp."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _patch_sessions_and_catalog(monkeypatch, tmp_path)
    n = 26
    times = [i * 0.02 for i in range(n)]
    values = [500000.0 + i for i in range(n)]
    schema = pa.schema(
        [
            pa.field("time", pa.float64(), metadata={b"units": b"s", b"kind": b"time"}),
            pa.field("measurement.pressure", pa.float64(), metadata={b"units": b"Pa"}),
        ]
    )
    table = pa.Table.from_arrays(
        [pa.array(times, type=pa.float64()), pa.array(values, type=pa.float64())],
        schema=schema,
    )
    parquet_path = tmp_path / "closed_loop.parquet"
    pq.write_table(table, parquet_path)

    df, _unit_map, time_col, t0_utc = normalize_tabular_polars(
        str(parquet_path),
        source_type="parquet",
    )
    assert time_col == "time"
    assert df.height == n
    assert df["__time__"].n_unique() == n
    first = df["__time__"][0]
    last = df["__time__"][-1]
    first_dt = first if isinstance(first, datetime) else first.to_python()
    last_dt = last if isinstance(last, datetime) else last.to_python()
    if getattr(first_dt, "tzinfo", None) is None:
        first_dt = first_dt.replace(tzinfo=timezone.utc)
    if getattr(last_dt, "tzinfo", None) is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    assert abs((last_dt - first_dt).total_seconds() - 0.50) < 1e-6
    assert abs((first_dt - t0_utc).total_seconds()) < 2.0

    manifest = run_ingest("parquet", str(parquet_path))
    assert manifest["status"] == "ready"
    ch = next(c for c in manifest["channels"] if c["channel_name"] == "measurement.pressure")
    assert ch["point_count"] == n

    out = tmp_path / "sessions" / manifest["artifact_id"] / "data" / "measurement.pressure.parquet"
    xs = [float(v.as_py()) for v in pq.read_table(out).column("x_ms")]
    assert len(xs) == n
    assert len(set(round(x, 3) for x in xs)) == n
    assert abs((max(xs) - min(xs)) - 500.0) < 1.0


def test_gfssp_csv_units_in_row_ingest(monkeypatch, tmp_path: Path):
    _patch_sessions_and_catalog(monkeypatch, tmp_path)
    csv_path = tmp_path / "inputFN.CSV"
    csv_path.write_text(
        "TIME,P1        ,T1        \n"
        "SECONDS,kPa,DEG_C\n"
        "0.200000E-01,0.500000E+02,0.250000E+02\n"
        "0.400000E-01,0.500000E+02,0.240884E+02\n"
        "0.500000E+00,0.935425E+02,0.468283E+01\n",
        encoding="utf-8",
    )
    df, unit_map, time_col, t0_utc = normalize_csv_polars(str(csv_path), units_in_row=True)
    assert time_col == "TIME"
    assert df.height == 3
    assert df["__time__"].n_unique() == 3
    assert unit_map.get("P1") == "kPa"
    assert unit_map.get("T1") == "DEG_C"

    first = df["__time__"][0]
    last = df["__time__"][-1]
    first_dt = first if isinstance(first, datetime) else first.to_python()
    last_dt = last if isinstance(last, datetime) else last.to_python()
    assert abs((last_dt - first_dt).total_seconds() - 0.48) < 1e-6

    manifest = run_ingest("csv", str(csv_path), units_in_row=True)
    assert manifest["status"] == "ready"
    by_name = {c["channel_name"]: c for c in manifest["channels"]}
    assert "TIME" not in by_name
    assert by_name["P1"]["unit"] == "kPa"
    assert by_name["P1"]["point_count"] == 3
