"""Schema validation and channel list time-index exclusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.file_probe import probe_file_with_validation
from app.engine.file_schema import validate_file_schema
from app.services.file_sources import file_channels

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(scope="module")
def rocket_parquet() -> str:
    path = EXAMPLES / "example_rocket_test.parquet"
    if not path.is_file():
        pytest.skip("Run examples/generate_tabular_examples.py first")
    return str(path)


@pytest.fixture(scope="module")
def rocket_csv(rocket_parquet: str) -> str:
    path = EXAMPLES / "example_rocket_test.csv"
    if not path.is_file():
        pytest.skip("Run examples/generate_tabular_examples.py first")
    return str(path)


def test_parquet_schema_valid(rocket_parquet: str) -> None:
    result = validate_file_schema(rocket_parquet, source_type="parquet")
    assert result.valid is True
    assert not result.errors


def test_csv_schema_valid(rocket_csv: str) -> None:
    result = validate_file_schema(rocket_csv, source_type="csv")
    assert result.valid is True
    assert not result.errors


def test_probe_includes_schema_validation(rocket_parquet: str) -> None:
    result = probe_file_with_validation(rocket_parquet)
    assert result.schema_validation is not None
    assert result.schema_validation.valid is True
    assert "time_s" not in {c.channel_name for c in result.channels}


def test_file_channels_excludes_time_index(rocket_parquet: str) -> None:
    channels = file_channels("parquet", rocket_parquet)
    names = {c.channel_name for c in channels}
    assert "time_s" not in names
    assert "thrust_n" in names


def test_csv_missing_time_column_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("value_only\n1\n2\n", encoding="utf-8")
    result = validate_file_schema(str(bad), source_type="csv")
    assert result.valid is False
    assert any("time column" in e.lower() for e in result.errors)
