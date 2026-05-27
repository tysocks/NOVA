from pathlib import Path

import h5py
import pytest
from fastapi import HTTPException

from app.main import file_tests_api
from app.services.file_sources import file_channels, file_tests, file_timeseries


def test_file_tests_accepts_time_s_column(tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("time_s,value\n0.0,1\n1.0,2\n", encoding="utf-8")

    rows = file_tests("csv", str(csv_path))

    assert len(rows) == 1
    assert rows[0].run_code == "sample"
    assert rows[0].duration_s == pytest.approx(1.0)


def test_file_tests_raises_for_unreadable_csv(tmp_path: Path):
    missing_path = tmp_path / "missing.csv"

    with pytest.raises(ValueError) as exc_info:
        file_tests("csv", str(missing_path))
    assert "Unable to read CSV contents:" in str(exc_info.value)


def test_file_api_returns_parser_error_detail_for_user(tmp_path: Path):
    csv_path = tmp_path / "missing_time.csv"
    csv_path.write_text("value\n1\n2\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        file_tests_api(source_type="csv", file_path=str(csv_path))
    assert exc_info.value.status_code == 400
    assert "CSV requires a time column" in str(exc_info.value.detail)


def test_h5_file_source_uses_telemetry_time_and_loads_channels(tmp_path: Path):
    h5_path = tmp_path / "sample.h5"
    with h5py.File(h5_path, "w") as h5:
        telem = h5.create_group("telemetry")
        telem.create_dataset("TIME", data=[0.0, 0.5, 1.0])
        telem.create_dataset("THRUST", data=[10.0, 11.0, 12.0])
        states = h5.create_group("states")
        states.create_dataset("chamber.P", data=[100.0, 101.0, 102.0])

    tests = file_tests("h5", str(h5_path))
    assert len(tests) == 1
    assert tests[0].duration_s == pytest.approx(1.0)

    channels = file_channels("h5", str(h5_path))
    names = {c.channel_name for c in channels}
    assert "telemetry/THRUST" in names
    assert "states/chamber.P" in names
    assert "telemetry/TIME" not in names

    rows = file_timeseries("h5", str(h5_path), ["telemetry/THRUST"])
    assert [r.value for r in rows] == [10.0, 11.0, 12.0]


def test_csv_units_in_headers_parses_channel_units(tmp_path: Path):
    csv_path = tmp_path / "units.csv"
    csv_path.write_text(
        "time_s,THRUST (N),P[psi]\n0.0,1.0,10.0\n1.0,2.0,11.0\n",
        encoding="utf-8",
    )

    channels = file_channels("csv", str(csv_path), units_in_headers=True)
    by_name = {c.channel_name: c for c in channels}
    assert by_name["THRUST"].unit == "N"
    assert by_name["P"].unit == "psi"

    rows = file_timeseries("csv", str(csv_path), ["THRUST", "P"], units_in_headers=True)
    assert {r.channel_name for r in rows} == {"THRUST", "P"}
    assert {r.unit for r in rows if r.channel_name == "THRUST"} == {"N"}
    assert {r.unit for r in rows if r.channel_name == "P"} == {"psi"}


def test_h5_file_source_errors_without_telemetry_time(tmp_path: Path):
    h5_path = tmp_path / "missing_time.h5"
    with h5py.File(h5_path, "w") as h5:
        telem = h5.create_group("telemetry")
        telem.create_dataset("THRUST", data=[10.0, 11.0, 12.0])

    with pytest.raises(ValueError) as exc_info:
        file_tests("h5", str(h5_path))
    assert "telemetry/TIME" in str(exc_info.value)
