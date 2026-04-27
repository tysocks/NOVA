# NOVA - Northern Operation Viewer and Analysis

NOVA is a desktop-first data viewer for test telemetry. It runs a local FastAPI backend and opens a native desktop window that renders the NOVA web UI.

## Current Implementation

NOVA supports multiple data source types in one session:

- PostgreSQL/TimescaleDB sources (browse databases, tests, channels, and timeseries).
- Local CSV sources (uploaded via the app).
- Local TDMS sources (uploaded via the app).

The UI lets you:

- Add multiple sources, then add databases/tests/channels from those sources.
- Multi-select tests/channels and plot in Plotly.
- Use either `time` or a selected channel on the X axis.
- Switch time reference between `Raw Time` and `t0 = First Point (per test)`.
- Apply optional start/end time filtering for database-backed timeseries.
- Downsample channels using per-channel frequency overrides.
- Show/hide an in-app data preview drawer.
- Use a simple ruler tool (`R` key + right-click after hover) for delta and slope checks.

UI state is persisted in browser storage and restored on restart.

## Project Structure

- `backend/app/main.py`: FastAPI app and HTTP endpoints.
- `backend/app/services/timeseries.py`: PostgreSQL-backed query logic.
- `backend/app/services/file_sources.py`: CSV/TDMS parsing and in-memory shaping.
- `backend/app/static/index.html`: full single-page UI (HTML/CSS/JS + Plotly).
- `backend/desktop_app.py`: PySide6 desktop launcher with splash screen and backend lifecycle.

## Setup

From repository root:

1. Create a virtual environment:
   - `python -m venv .venv`
2. Install dependencies:
   - `.\.venv\Scripts\python -m pip install -r backend/requirements.txt`
3. Create local environment file:
   - `copy backend\.env.example backend\.env`
4. Edit `backend\.env` for your database connection (when using PostgreSQL sources).

Common keys:

- `NOVA_DB_HOST`
- `NOVA_DB_PORT`
- `NOVA_DB_NAME`
- `NOVA_DB_USER`
- `NOVA_DB_PASSWORD`
- `NOVA_DB_SSLMODE`

## Running NOVA

### Desktop mode (recommended)

- `.\.venv\Scripts\python .\backend\desktop_app.py`

or use the provided launchers:

- `Launch_NOVA.vbs`
- `Launch_NOVA.bat`

### Backend-only mode

- `.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (run from `backend`)

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## CSV Expectations

CSV files must include:

- A time column named one of: `timestamp_utc`, `time`, `timestamp`, `datetime`
- One or more numeric columns to be treated as channels

NOVA parses timestamps as UTC, drops invalid time rows, and sorts by time.

Example template:

- `examples/example_input_template.csv`

## API Endpoints

- `GET /health`
- `GET /api/databases`
- `GET /api/tests`
- `GET /api/channels`
- `GET /api/available-channels`
- `GET /api/timeseries`
- `GET /api/file/tests`
- `GET /api/file/channels`
- `GET /api/file/timeseries`
- `POST /api/file/upload`

## Troubleshooting

- If desktop launch fails, run `backend/desktop_app.py` in terminal to see immediate errors.
- If backend data is empty, verify DB credentials and that required tables exist.
- If file sources fail, re-upload the file and confirm CSV/TDMS format compatibility.
