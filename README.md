# NOVA - Northern Operation Viewer and Analysis

NOVA is a desktop-first data viewer for test telemetry. It runs a local FastAPI backend and opens a native desktop window that renders the NOVA web UI.

## Current Implementation

NOVA supports multiple data source types in one session:

- **RedScale / BlueScale / PostgreSQL** — connect once, then pick database, test table, and tests in a 3-column picker. Each selected test appears in **Sources** as `database_name/run_code` (e.g. `hfr_test_data/HFR-0010`).
- **Data Files** (CSV or H5) — pick a file, set name/path/units-in-headers (CSV), then add channels from the file catalog.
- **TDMS** — file picker adds the source immediately (rename via double-click or right-click).
- Configurable default PostgreSQL sources (`File -> Default Sources...`).

The UI lets you:

- Add sources from the **Sources `+`** modal (no dropdown).
- Edit or rename sources (right-click or double-click on Sources).
- Add channels via a two-column transfer dialog (available → selected).
- Use explorer-style selection in Sources/Channels (click, Ctrl/Cmd toggle, Shift range; empty selection = all).
- Use the `+` button in Channels with a dropdown:
  - `Add channels from source`
  - `Rolling channel calculations`
  - `Channel wise calculations`
- Plot in Plotly (toolbar hidden; zoom still refetches detail for Postgres).
- Use either `time` or a selected channel on the X axis.
- Switch time reference between `Raw Time` and `t0 = First Point (per test)`.
- Apply optional start/end time filtering for database-backed timeseries.
- Add RedScale / BlueScale from the Sources `+` menu using configured defaults.
- Downsample channels using per-channel frequency overrides.
- Show/hide an in-app data preview drawer.
- Use a simple ruler tool (`R` key + right-click after hover) for delta and slope checks.

UI state is persisted in browser storage and restored on restart.

## Calculated Channels

Calculated channels are created client-side from selected source channels and are available in the same channels list as regular channels.

### Rolling channel calculations

Create a new derived channel from one source channel using:

- `mean`
- `sum`
- `min`
- `max`
- `std`

You can configure:

- calculated channel name
- output units
- source channel
- rolling window size (samples)

### Channel wise calculations

Create a new derived channel from multiple source channels using a formula with letter variables:

- selected channels are mapped by order to `A`, `B`, `C`, ...
- formula examples: `A + B`, `A / B`, `(A - B) * 10`
- band-pass syntax: `band_pass_filter(A, low_freq, high_freq)`

Supported formula functions:

- `ABS(x)`
- `SQRT(x)`
- `POW(x, y)`
- `EXP(x)`
- `LOG(x)`
- `LOG10(x)`
- `SIN(x)`, `COS(x)`, `TAN(x)`
- `ASIN(x)`, `ACOS(x)`, `ATAN(x)`
- `ROUND(x)`, `FLOOR(x)`, `CEIL(x)`
- `MIN(...)`, `MAX(...)`
- `CLAMP(x, lo, hi)`
- `BAND_PASS_FILTER(A, low_freq, high_freq)`

You can configure:

- calculated channel name
- output units
- channel selection order
- formula expression

### Editing calculated channels

Right-click a calculated channel in the Channels list and choose:

- `Edit calculated channel...`

You can edit name, units, input channels, and formula/rolling settings. The existing right-click actions for frequency and delete still apply.

## Project Structure

- `backend/app/main.py`: FastAPI app and HTTP endpoints.
- `backend/app/services/timeseries.py`: PostgreSQL-backed query logic.
- `backend/app/services/file_sources.py`: CSV/TDMS parsing and in-memory shaping.
- `backend/app/static/index.html`: full single-page UI (HTML/CSS/JS + Plotly).
- `backend/desktop_app.py`: PySide6 desktop launcher with splash screen and backend lifecycle.

## Install (Fresh Clone)

From a new machine/user account:

1. Clone the repo:
   - `git clone https://github.com/tysocks/NOVA.git`
   - `cd NOVA`
2. Create and activate a virtual environment:
   - `python -m venv .venv`
   - `.\.venv\Scripts\Activate.ps1`
3. Install dependencies:
   - `python -m pip install -r backend/requirements.txt`
4. Configure database environment (optional if only using file sources):
   - copy your env file to `backend\.env`
   - set:
     - `NOVA_DB_HOST`
     - `NOVA_DB_PORT`
     - `NOVA_DB_NAME`
     - `NOVA_DB_USER`
     - `NOVA_DB_PASSWORD`
     - `NOVA_DB_SSLMODE`
   - optional default source envs for one-click source creation:
     - `NOVA_REDSCALE_HOST`
     - `NOVA_REDSCALE_PORT`
     - `NOVA_REDSCALE_USER`
     - `NOVA_REDSCALE_PASSWORD`
     - `NOVA_REDSCALE_SSLMODE`
     - `NOVA_BLUESCALE_HOST`
     - `NOVA_BLUESCALE_PORT`
     - `NOVA_BLUESCALE_USER`
     - `NOVA_BLUESCALE_PASSWORD`
     - `NOVA_BLUESCALE_SSLMODE`
     - `NOVA_DB_NAME_REDSCALE` (optional, API abstraction use)
     - `NOVA_DB_NAME_BLUESCALE` (optional, API abstraction use)

Default source entries are persisted to `backend/.nova_source_defaults.json` once edited via `File -> Default Sources...`.

## Run NOVA

### Desktop mode (recommended)

From repository root:

- `.\.venv\Scripts\python .\backend\desktop_app.py`

Or use the launcher scripts:

- `Launch_NOVA.vbs` (silent launch)
- `Launch_NOVA.bat` (terminal launch)

### Desktop/Start shortcuts

To create/update shortcuts with the NOVA icon:

- `powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1`

This creates:

- Desktop shortcut: `NOVA.lnk`
- Start Menu shortcut: `NOVA.lnk`

### Backend-only mode

- `.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (run from `backend`)

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The desktop launcher (`Launch_NOVA.bat` / `Launch_NOVA.vbs`) automatically uses the first free port in **8000–8010** if another app is already bound to 8000, and reuses an existing NOVA backend when `/health` reports `app: NOVA`.

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
- `GET /api/v2/timeseries`
- `GET /api/source-defaults`
- `POST /api/source-defaults`
- `GET /api/file/tests`
- `GET /api/file/channels`
- `GET /api/file/timeseries`
- `POST /api/file/upload`

## Troubleshooting

- If desktop launch fails, run `backend/desktop_app.py` in terminal to see immediate errors.
- If backend data is empty, verify DB credentials and that required tables exist.
- If file sources fail, re-upload the file and confirm CSV/TDMS format compatibility.
