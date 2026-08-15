"""Tests for the headless NOVA CLI."""

from __future__ import annotations

import json
from pathlib import Path

from app.cli import build_parser, main


SAMPLE_SERIES = [
    {
        "name": "Thrust",
        "unit": "N",
        "source": "HFR-0001",
        "x": [1_704_067_200_000, 1_704_067_200_100],
        "y": [10.5, 11.0],
    }
]


def test_cli_parser_has_ingest_and_export():
    parser = build_parser()
    args = parser.parse_args(["export-series", "--series", "in.json", "--out", "out.csv"])
    assert args.command == "export-series"
    assert args.format == "csv"


def test_cli_export_series_csv(tmp_path: Path):
    series_path = tmp_path / "series.json"
    out_path = tmp_path / "burn.csv"
    series_path.write_text(json.dumps(SAMPLE_SERIES), encoding="utf-8")
    code = main(["export-series", "--series", str(series_path), "--format", "csv", "--out", str(out_path)])
    assert code == 0
    text = out_path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "time_utc,source,channel,value,unit"
    assert "Thrust" in text


def test_cli_export_series_wrapped_payload(tmp_path: Path):
    series_path = tmp_path / "series.json"
    out_path = tmp_path / "burn.parquet"
    series_path.write_text(json.dumps({"series": SAMPLE_SERIES}), encoding="utf-8")
    code = main(["export-series", "--series", str(series_path), "--format", "parquet", "--out", str(out_path)])
    assert code == 0
    assert out_path.read_bytes()[:4] == b"PAR1"
