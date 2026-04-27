# NOVA - Northern Operation Viewer and Analysis

NOVA is a desktop data viewer for rocket test data stored in TimescaleDB.

## What NOVA Does

- Loads test data from TimescaleDB databases (for example `hfr_test_data`, `ptf_test_data`).
- Lets you select one or more tests and channels.
- Plots selected channels interactively (pan/zoom/hover).
- Groups traces by **unit** and gives each unit an **independent y-axis scale**.
- Supports time reference mode:
  - `Raw Time`
  - `t0 = First Point (per test)` for comparison alignment.

## CSV Input Format Example

- Example file: `examples/example_input_template.csv`
- Required:
  - A UTC time column named one of: `timestamp_utc`, `time`, `timestamp`, `datetime`
  - One or more numeric channel columns
- Notes:
  - Rows should be ordered by time (NOVA will sort if needed)
  - ISO timestamps with `Z` suffix are recommended

## First-Time Setup (One Time Only)

From project root (`dataviewer`):

1. Create virtual environment:
   - `python -m venv .venv`
2. Install dependencies:
   - `.\.venv\Scripts\python -m pip install -r backend/requirements.txt`
3. Configure NOVA DB connection:
   - `copy backend\.env.example backend\.env`
   - Edit `backend\.env` values to match your TimescaleDB credentials.

Example expected values for your current NORDSTROM stack:
- `NOVA_DB_HOST=localhost`
- `NOVA_DB_PORT=5432`
- `NOVA_DB_NAME=hfr_test_data`
- `NOVA_DB_USER=pipeline`
- `NOVA_DB_PASSWORD=test`

## Create Desktop Icon (No Terminal Window)

Run:

- `powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1`

This creates:

- `C:\Users\tyler\Desktop\NOVA.lnk`

The shortcut launches `Launch_NOVA.vbs`, which starts NOVA hidden (no terminal window).

## Daily Use

### Start NOVA

- Double-click the Desktop icon: `NOVA`
- or run `Launch_NOVA.vbs` from project root.
- Startup is optimized:
  - shows a lightweight splash while loading
  - reuses backend if already running
  - restores your last selected database/tests/channels/settings.

### Load and plot data

1. Choose a database from `Database`.
2. Multi-select tests in `Tests`.
3. Multi-select channels in `Channels`.
4. Choose `X Axis`:
   - `time` (default)
   - or another selected channel.
5. Choose `Time Reference`:
   - `Raw Time`, or
   - `t0 = First Point (per test)` for per-test alignment.
6. Optional: set `Start Time` / `End Time`.
7. Click `Chooch`.

### Plot behavior

- Traces are grouped by channel unit.
- Each unit gets its own y-axis scale automatically.
- Data preview is hidden by default.
- Use `Show Data Preview` if you want to inspect rows.

## If Something Fails

### App opens but no data appears

1. Verify TimescaleDB is running.
2. Verify `backend\.env` credentials and DB name.
3. Confirm selected DB actually contains `test_runs`, `channels`, `sensor_readings`.

### Desktop icon does nothing

1. Verify `.venv` exists and dependencies are installed.
2. Recreate shortcut:
   - `powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1`

### Launch from terminal for debugging

If needed, run visible mode:

- `.\.venv\Scripts\python .\backend\desktop_app.py`

## API Endpoints (for future integrations)

- `GET /health`
- `GET /api/databases`
- `GET /api/tests`
- `GET /api/channels`
- `GET /api/timeseries`
