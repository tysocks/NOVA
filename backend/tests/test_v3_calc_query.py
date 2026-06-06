from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engine.arrow_codec import arrow_ipc_to_points
from app.main import app
from app.models import TimeSeriesPoint

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)

client = TestClient(app)


def test_v3_series_query_applies_calculated_channels():
    mock_points = [
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="HFR-0001",
            channel_name="A",
            unit=None,
            time=BASE,
            value=1.0,
        ),
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="HFR-0001",
            channel_name="B",
            unit=None,
            time=BASE,
            value=2.0,
        ),
    ]

    with patch("app.engine.series_query.fetch_postgres_timeseries", return_value=mock_points):
        response = client.post(
            "/api/v3/series/query",
            json={
                "sources": [
                    {
                        "type": "postgres",
                        "test_run_ids": [1],
                        "channel_names": ["A", "B"],
                        "db_name": "test_db",
                    }
                ],
                "mode": "overview",
                "resolution_px": 500,
                "calculated_channels": [
                    {
                        "kind": "formula",
                        "name": "sumAB",
                        "channels": ["A", "B"],
                        "formula": "A + B",
                    }
                ],
            },
        )

    assert response.status_code == 200
    restored = arrow_ipc_to_points(response.content)
    calc = [p for p in restored if p.channel_name == "sumAB"]
    assert len(calc) == 1
    assert calc[0].value == 3.0
