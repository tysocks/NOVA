from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engine.export_series import build_series_csv, build_series_parquet, iter_export_rows
from app.main import app

client = TestClient(app)

SAMPLE = [
    {
        "name": "Thrust",
        "unit": "N",
        "source": "HFR-0001",
        "x": [1_704_067_200_000, 1_704_067_200_100],
        "y": [10.5, 11.0],
    },
    {
        "name": "Chamber_P",
        "unit": "bar",
        "source": "HFR-0001",
        "x_ms": [1_704_067_200_000],
        "y": [12.25],
    },
]


def test_iter_export_rows_long_format_and_iso_time():
    rows = iter_export_rows(SAMPLE)
    assert len(rows) == 3
    assert rows[0]["channel"] == "Thrust"
    assert rows[0]["unit"] == "N"
    assert rows[0]["source"] == "HFR-0001"
    assert rows[0]["time_utc"].startswith("2024-01-01T")
    assert rows[0]["time_utc"].endswith("Z")
    assert rows[2]["channel"] == "Chamber_P"
    assert rows[2]["value"] == 12.25


def test_build_series_csv_has_header_and_values():
    text = build_series_csv(SAMPLE).decode("utf-8")
    lines = [line for line in text.strip().splitlines() if line]
    assert lines[0] == "time_utc,source,channel,value,unit"
    assert "Thrust" in text
    assert "11.0" in text


def test_build_series_parquet_round_trip():
    import pyarrow.parquet as pq
    import io

    raw = build_series_parquet(SAMPLE)
    table = pq.read_table(io.BytesIO(raw))
    assert table.num_rows == 3
    assert table.column_names == ["time_utc", "source", "channel", "value", "unit"]
    channels = table.column("channel").to_pylist()
    assert channels.count("Thrust") == 2


def test_series_export_csv_endpoint():
    response = client.post(
        "/api/v3/series/export",
        json={"format": "csv", "filename": "burn view", "series": SAMPLE},
    )
    assert response.status_code == 200, response.text
    assert "text/csv" in response.headers["content-type"]
    assert "burn_view.csv" in response.headers.get("content-disposition", "")
    assert response.text.splitlines()[0] == "time_utc,source,channel,value,unit"


def test_series_export_parquet_endpoint():
    response = client.post(
        "/api/v3/series/export",
        json={"format": "parquet", "series": SAMPLE},
    )
    assert response.status_code == 200, response.text
    assert "parquet" in response.headers["content-type"]
    assert response.content[:4] == b"PAR1"


def test_series_export_requires_series():
    response = client.post("/api/v3/series/export", json={"format": "csv", "series": []})
    assert response.status_code == 422


def test_desktop_save_file_writes_chosen_path(tmp_path):
    dest = tmp_path / "page.png"
    payload = {
        "suggested_name": "page.png",
        "content_base64": "aGVsbG8=",
        "filetypes": [["PNG", "*.png"]],
    }
    with patch("tkinter.filedialog.asksaveasfilename", return_value=str(dest)):
        with patch("tkinter.Tk") as mock_tk:
            mock_tk.return_value.withdraw = lambda: None
            mock_tk.return_value.attributes = lambda *a, **k: None
            mock_tk.return_value.destroy = lambda: None
            response = client.post("/api/desktop/save-file", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert dest.read_bytes() == b"hello"


def test_desktop_save_file_cancelled():
    payload = {"suggested_name": "page.png", "content_base64": "aGVsbG8="}
    with patch("tkinter.filedialog.asksaveasfilename", return_value=""):
        with patch("tkinter.Tk") as mock_tk:
            mock_tk.return_value.withdraw = lambda: None
            mock_tk.return_value.attributes = lambda *a, **k: None
            mock_tk.return_value.destroy = lambda: None
            response = client.post("/api/desktop/save-file", json=payload)
    assert response.status_code == 200
    assert response.json()["cancelled"] is True


def test_desktop_save_file_appends_png_extension(tmp_path):
    dest = tmp_path / "page"
    payload = {
        "suggested_name": "page.png",
        "content_base64": "aGVsbG8=",
        "filetypes": [["PNG image", "*.png"]],
    }
    with patch("tkinter.filedialog.asksaveasfilename", return_value=str(dest)):
        with patch("tkinter.Tk") as mock_tk:
            mock_tk.return_value.withdraw = lambda: None
            mock_tk.return_value.attributes = lambda *a, **k: None
            mock_tk.return_value.destroy = lambda: None
            response = client.post("/api/desktop/save-file", json=payload)
    assert response.status_code == 200, response.text
    written = tmp_path / "page.png"
    assert written.read_bytes() == b"hello"
    assert response.json()["path"].endswith("page.png")


def test_desktop_clipboard_image_sets_png():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4
    import base64

    with patch("app.services.clipboard_image.set_clipboard_png") as mock_set:
        response = client.post(
            "/api/desktop/clipboard-image",
            json={"content_base64": base64.b64encode(png).decode("ascii")},
        )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    mock_set.assert_called_once_with(png)


def test_desktop_clipboard_image_rejects_non_png():
    import base64

    response = client.post(
        "/api/desktop/clipboard-image",
        json={"content_base64": base64.b64encode(b"not-a-png").decode("ascii")},
    )
    assert response.status_code == 400
