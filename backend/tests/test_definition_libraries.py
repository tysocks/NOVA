from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import (
    app,
)

client = TestClient(app)


@pytest.fixture
def config_library_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_config_library.json"
    monkeypatch.setattr("app.main.CONFIG_LIBRARY_FILE", path)
    return path


@pytest.fixture
def range_def_library_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".nova_range_definition_library.json"
    monkeypatch.setattr("app.main.RANGE_DEFINITION_LIBRARY_FILE", path)
    return path


def test_config_library_roundtrip(config_library_file: Path):
    payload = {
        "configs": [
            {
                "id": "cfg-1",
                "name": "Hotfire",
                "channels": ["Thrust", "Chamber_P"],
                "calculatedChannels": [{"name": "OF", "formula": "A/B", "channels": ["Ox", "Fuel"]}],
                "channelAliases": [{"name": "PT", "members": ["PT1", "PT2"]}],
                "masks": [
                    {"id": "m1", "kind": "range", "name": "burn", "rangeName": "Burn", "includeMissingSources": False},
                    {"id": "m2", "kind": "formula", "name": "thrust_hi", "channels": ["Thrust"], "formula": "A", "op": ">", "valueA": "100"},
                ],
            }
        ]
    }
    save = client.post("/api/config-library", json=payload)
    assert save.status_code == 200
    assert save.json() == {"ok": True, "count": 1}

    load = client.get("/api/config-library")
    rows = load.json()["configs"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Hotfire"
    assert rows[0]["channels"] == ["Thrust", "Chamber_P"]
    assert len(rows[0]["masks"]) == 2
    assert rows[0]["masks"][0]["kind"] == "range"
    assert rows[0]["masks"][0]["rangeName"] == "Burn"
    assert rows[0]["masks"][0]["includeMissingSources"] is False
    assert "payload" not in rows[0]


def test_config_library_migrates_legacy_payload(config_library_file: Path):
    config_library_file.write_text(
        '{"configs":[{"id":"old","name":"Legacy","savedAt":"2020-01-01T00:00:00Z","payload":{"app":{"masks":[{"id":"m","kind":"range","name":"t","rangeName":"Burn","includeMissingSources":true}],"calculatedChannels":[],"channelAliases":[]},"activeSelections":{"addedChannelIds":["A","B"]}}}]}',
        encoding="utf-8",
    )
    load = client.get("/api/config-library")
    row = load.json()["configs"][0]
    assert row["channels"] == ["A", "B"]
    assert row["masks"][0]["name"] == "t"
    assert row["masks"][0]["rangeName"] == "Burn"
    assert row["masks"][0]["includeMissingSources"] is True


def test_range_definition_library_roundtrip(range_def_library_file: Path):
    payload = {
        "definitions": [
            {
                "id": "rd-1",
                "name": "Hotfire tree",
                "nodes": [
                    {
                        "id": "n1",
                        "name": "Burn",
                        "parent_id": None,
                        "start": {"expression": "Thrust > 10", "occurrence": "first", "edge": "rising"},
                        "end": {"expression": "Thrust < 5", "occurrence": "last", "edge": "falling"},
                    },
                    {
                        "id": "n2",
                        "name": "Steady",
                        "parent_id": "n1",
                        "start": {
                            "channels": ["Thrust"],
                            "formula": "A",
                            "op": ">",
                            "value": 50,
                            "occurrence": "first",
                            "edge": "none",
                        },
                        "end": {
                            "channels": ["Thrust"],
                            "formula": "A",
                            "op": "<",
                            "value": 40,
                            "occurrence": "first",
                            "edge": "none",
                        },
                    },
                ],
            }
        ]
    }
    save = client.post("/api/range-definition-library", json=payload)
    assert save.status_code == 200
    assert save.json()["ok"] is True
    load = client.get("/api/range-definition-library")
    defs = load.json()["definitions"]
    assert len(defs) == 1
    assert defs[0]["nodes"][1]["parent_id"] == "n1"
    start = defs[0]["nodes"][1]["start"]
    assert start["channels"] == ["Thrust"]
    assert start["op"] == ">"
    assert start["value"] == 50
    assert start["expression"].startswith("Thrust") or start["expression"].startswith("A")


def test_range_definition_recovers_channel_from_expression(range_def_library_file: Path):
    payload = {
        "definitions": [
            {
                "name": "recover",
                "nodes": [
                    {
                        "id": "n1",
                        "name": "Burn",
                        "parent_id": None,
                        "start": {"expression": "Thrust > 10", "occurrence": "first", "edge": "rising"},
                        "end": {"expression": "Thrust < 5", "occurrence": "last", "edge": "falling"},
                    }
                ],
            }
        ]
    }
    save = client.post("/api/range-definition-library", json=payload)
    assert save.json()["ok"] is True
    defs = client.get("/api/range-definition-library").json()["definitions"]
    start = defs[0]["nodes"][0]["start"]
    assert start["channels"] == ["Thrust"]
    assert start["value"] == 10


def test_range_definition_ignores_formula_letter_as_channel(range_def_library_file: Path):
    payload = {
        "definitions": [
            {
                "name": "letter",
                "nodes": [
                    {
                        "id": "n1",
                        "name": "Burn",
                        "parent_id": None,
                        "start": {"expression": "A > 10", "occurrence": "first", "edge": "rising"},
                        "end": {"expression": "A < 5", "occurrence": "last", "edge": "falling"},
                    }
                ],
            }
        ]
    }
    save = client.post("/api/range-definition-library", json=payload)
    assert save.json()["ok"] is True
    defs = client.get("/api/range-definition-library").json()["definitions"]
    start = defs[0]["nodes"][0]["start"]
    assert start["channels"] == []
    assert start["expression"] == "A > 10"


def test_range_definition_rejects_cycle(range_def_library_file: Path):
    payload = {
        "definitions": [
            {
                "name": "bad",
                "nodes": [
                    {"id": "a", "name": "A", "parent_id": "b", "start": {"expression": "x>1"}, "end": {"expression": "x<1"}},
                    {"id": "b", "name": "B", "parent_id": "a", "start": {"expression": "x>1"}, "end": {"expression": "x<1"}},
                ],
            }
        ]
    }
    save = client.post("/api/range-definition-library", json=payload)
    assert save.json()["ok"] is False
