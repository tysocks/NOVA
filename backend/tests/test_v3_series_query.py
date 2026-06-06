from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engine.arrow_codec import arrow_ipc_to_points
from app.main import app
from app.models import TimeSeriesPoint

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)

client = TestClient(app)


def test_v3_series_query_returns_arrow_and_meta_header():
    mock_points = [
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="HFR-0001",
            channel_name="Thrust",
            unit="lbf",
            time=BASE,
            value=100.0,
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
                        "channel_names": ["Thrust"],
                        "db_name": "test_db",
                    }
                ],
                "mode": "overview",
                "resolution_px": 500,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.apache.arrow.stream")
    meta_header = response.headers.get("x-nova-series-meta")
    assert meta_header is not None
    assert '"row_count":1' in meta_header.replace(" ", "")
    assert "aggregate" in meta_header

    restored = arrow_ipc_to_points(response.content)
    assert len(restored) == 1
    assert restored[0].channel_name == "Thrust"
    assert restored[0].value == 100.0


def test_v3_series_query_requires_at_least_one_source():
    response = client.post(
        "/api/v3/series/query",
        json={"sources": []},
    )
    assert response.status_code == 422


def test_legacy_v2_timeseries_deprecation_headers():
    with patch("app.main.get_timeseries_envelope") as mock_env:
        from app.models import TimeSeriesEnvelope

        mock_env.return_value = TimeSeriesEnvelope(overview=[], series_meta=[], detail_hint=None)
        response = client.get(
            "/api/v2/timeseries",
            params={
                "test_run_ids": 1,
                "channel_names": "Thrust",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("deprecation") == "true"
    assert "v3/series/query" in response.headers.get("x-nova-deprecated", "").lower()
