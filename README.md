# NOVA - Northern Operation Viewer and Analysis

NOVA is a desktop-first data viewer for test telemetry. It runs a local FastAPI backend and opens a native desktop window that renders the NOVA web UI.

## Current Implementation

NOVA supports multiple data source types in one session:

- **PostgreSQL** — save connection profiles in **Database Manager**, then use **Sources `+` → Database** to pick **Connection → Database → Tests** in a 3-column picker. Each selected test appears in **Sources** as `database_name/run_code` (e.g. `hfr_test_data/HFR-0010`).
- **Data Files** (CSV, H5, Parquet, Arrow) — pick a file, set name/path/units-in-headers (CSV), then add channels from the file catalog.
- **TDMS** — file picker adds the source immediately (rename via double-click or right-click).

The UI lets you:

- Manage PostgreSQL connections via **File → Database Manager...** (searchable list, inline edit, add via `⋯` menu).
- Define unit preferences and conversions via **File → Unit Manager...** (preferred units per category, translation formulas).
- Add database-backed sources from **Sources `+` → Database** (requires at least one saved profile).
- Edit or rename sources (right-click or double-click on Sources).
- Add channels via a two-column transfer dialog (available → selected).
- Use explorer-style selection in Sources/Channels (click, Ctrl/Cmd toggle, Shift range; empty selection = all).
- Use the `+` button in Channels with a dropdown:
  - `Add channels from source`
  - `Rolling channel calculations`
  - `Channel wise calculations`
- Plot in Plotly (toolbar hidden; scroll zoom on plot and axes).
- **v3 data engine** (default): columnar queries, file ingest to Parquet, server-side calculated channels.
- Use either `time` or a selected channel on the X axis.
- Switch time reference between `Raw Time` and `t0 = First Point (per test)`.
- Apply optional start/end time filtering for database-backed timeseries.
- Downsample channels using per-channel frequency overrides.
- Override or set channel units via right-click **Set Unit...** (applies to all selected channels in the Channels list).
- Show/hide an in-app data preview drawer.
- Use a simple ruler tool (`R` key + right-click after hover) for delta and slope checks.

UI state is persisted in browser storage and restored on restart.

## Database Manager

PostgreSQL connection profiles are stored locally on the backend host in `backend/.nova_database_library.json` (not committed; contains passwords).

### Workflow

1. **File → Database Manager...** — add or edit profiles (name, host, port, user, password, sslmode).
2. Use **⚡ Test connection** on a profile row to verify credentials before saving.
3. Use the `⋯` menu to **Add**, **Import**, or **Export** profiles (JSON).
4. Click **Save** to persist profiles to disk.
5. **Sources `+` → Database** — select a saved **Connection**, then a **Database**, then one or more **Tests** (use the Tests search box for long lists).
6. Click **Add Selected Tests** — each test becomes a leaf source (`database_name/run_code`) in the Sources sidebar.

The Add Source picker auto-discovers the test table (`test_runs` preferred) and lists tests from the selected database. See [docs/data-file-formats.md](docs/data-file-formats.md) for required PostgreSQL schema.

On first run, if no library file exists, NOVA seeds **RedScale** and **BlueScale** profiles from `NOVA_REDSCALE_*` and `NOVA_BLUESCALE_*` environment variables.

### API

- `GET /api/database-library` — list saved profiles (seeds from env when empty)
- `POST /api/database-library` — replace saved profiles (`{ "databases": [ ... ] }`)
- `POST /api/database-library/test` — verify a connection (`{ "host", "port", "user", "password", "sslmode" }`)

### Features

- Persistent profile library (CRUD via Database Manager UI)
- Configuration Manager–style UI (search toolbar, list rows, inline edit, row actions)
- Per-profile **Test connection** (⚡)
- JSON **Import** / **Export** via `⋯` menu
- 3-column Add Source flow wired to saved profiles
- **Search** filter in the Tests column of the Add Source picker
- Auto-seed from `NOVA_REDSCALE_*` / `NOVA_BLUESCALE_*` when the library file is empty
- API tests in `backend/tests/test_database_library.py`

### Future work

| Item | Notes |
|------|--------|
| **Credential storage** | Passwords are plaintext in `.nova_database_library.json`; OS keychain integration is a future improvement |

### Troubleshooting (database sources)

- **“No saved databases” when adding a source** — open Database Manager, confirm profiles exist, and click **Save**.
- **Connection column shows profiles but Database column is empty** — check host, port, password, and that PostgreSQL is reachable; status bar shows discovery errors.
- **No tests listed** — confirm the database has a `public` test table with `id`, `run_code`, and `start_time` (default `test_runs`). See [docs/data-file-formats.md](docs/data-file-formats.md).

## Unit Manager

Unit categories and conversion formulas are stored locally on the backend host in `backend/.nova_unit_library.json` (not committed).

### Workflow

1. **File → Unit Manager...** — add or edit categories (name, preferred unit, translations).
2. For each translation, set the **source unit symbol** and a formula with `x` as the value in that unit, e.g. `x + 273` (C → K) or `x / 1000` (N → kN).
3. Use the `⋯` menu to **Add**, **Import**, or **Export** categories (JSON).
4. Click **Save** to persist the library to disk.

When a channel’s unit (from file metadata or a per-channel override) matches a translation symbol, NOVA plots and previews values in the **preferred unit** and labels the Y axis accordingly. Double-click the plot area to reset zoom to the converted scale.

On first run, if no library file exists, NOVA seeds a default **Temperature** category (preferred **K**, with **C** and **F** translations).

### Channel unit overrides

- Right-click a channel in the Channels list → **Set Unit...**
- Multi-select with Ctrl/Cmd+click or Shift+click, then right-click → **Set Unit...** to apply the same override to all selected channels.
- Overrides set the channel’s **source** unit; Unit Manager controls display unit and value conversion when a matching translation exists.

### API

- `GET /api/unit-library` — list saved categories (seeds defaults when empty)
- `POST /api/unit-library` — replace saved categories (`{ "categories": [ ... ] }`)

Formulas are validated on save (numbers, `x`, `+ - * / ( )` only). API tests in `backend/tests/test_unit_library.py`.

## Calculated Channels

Calculated channels are available in the same channels list as regular channels. With **Preferences → Compute calculated channels on server** (default on), rolling and formula channels are evaluated in the v3 query engine. Turn that off to use the legacy client-side path (`computeCalculatedRows`).

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
- `SMOOTH(A, N)`, `ROLLING_MEAN(A, N)`, `ROLLING_SUM(A, N)`, `ROLLING_MIN(A, N)`, `ROLLING_MAX(A, N)`, `ROLLING_STD(A, N)`
- `RMS(A, N)`, `PEAK(A, N)` — trailing-window RMS and peak magnitude
- `TRAPZ(A)` — cumulative trapezoidal integral (output in source-unit · seconds)
- `RISE(A)` / `RISE(A, lo, hi)` — rise time in seconds, 10%→90% of the series span by default. Values in `[0, 1]` with `lo < hi` are span fractions; otherwise absolute levels (e.g. `RISE(A, 10, 90)`). Holds the last completed rise.
- `FALL(A)` / `FALL(A, lo, hi)` — fall time in seconds, 90%→10% of the series span by default (same `lo`/`hi` rules as `RISE`)
- `SETTLING(A)` / `SETTLING(A, band, hold_s)` — settling time in seconds from leaving the start value until staying within `band` of the final value. `band` in `(0, 1]` is a fraction of the start-to-final span (default `0.02`); otherwise an absolute band. `hold_s` is extra time the signal must remain in-band (default `0`)

You can configure:

- calculated channel name
- output units
- channel selection order
- formula expression

### Editing calculated channels

Right-click a calculated channel in the Channels list and choose:

- `Edit calculated channel...`

You can edit name, units, input channels, and formula/rolling settings. The existing right-click actions for frequency and delete still apply.

## v3 migration (from v1/v2 row APIs)

| Concern | Legacy | v3 (default) |
|--------|--------|----------------|
| Postgres Chooch | `GET /api/v2/timeseries` | `POST /api/v3/series/query` with SQL aggregation |
| File Chooch | `GET /api/file/timeseries` (full file) | Ingest + `POST /api/v3/series/query` on Parquet |
| Calculated channels | Browser main thread | Server (`calculated_channels` on query) |
| Zoom detail | Postgres only | Postgres + indexed files when overview is downsampled (`aggregate` / `raw_lttb`) |

**Zoom-in detail refetch** only runs when the overview response used downsampling (`fetch_strategy` is not plain `raw`). Turn off **Preferences → Compute calculated channels on server** to evaluate formulas in the browser (legacy path).

Session artifacts live under `backend/.nova_sessions/` (safe to delete to reclaim disk; re-ingest files afterward).

## Project Structure

- `backend/app/main.py`: FastAPI app and HTTP endpoints.
- `backend/app/engine/`: v3 query planner, Postgres/Parquet sources, Arrow codec, calc engine.
- `backend/app/services/timeseries.py`: PostgreSQL-backed query logic (v1/v2).
- `backend/app/services/file_sources.py`: CSV/TDMS parsing; uses Parquet artifacts when indexed.
- `backend/app/services/unit_library.py`: unit category validation and conversion formulas.
- `backend/app/static/index.html`: single-page UI; `static/js/nova-v3.js` v3 query client.
- `backend/desktop_app.py`: PySide6 desktop launcher with splash screen and backend lifecycle.

Local runtime data (gitignored): `backend/.nova_database_library.json` (PostgreSQL profiles), `backend/.nova_unit_library.json` (unit categories), `backend/.nova_config_library.json`, `backend/.nova_sessions/`.

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
   - optional PostgreSQL profile envs (seed Database Manager on first run when the library file is empty):
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

PostgreSQL connection profiles are managed via **File → Database Manager...** and saved to `backend/.nova_database_library.json`.

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
- `GET /api/timeseries` — **legacy**; prefer v3
- `GET /api/v2/timeseries` — **legacy** envelope adapter (uses v3 postgres engine internally); prefer v3
- `POST /api/v3/series/query` — **primary** (Arrow IPC default, or `?format=json`; optional `calculated_channels`)
- `POST /api/v3/ingest/file` — index CSV/H5/TDMS to `.nova_sessions/` Parquet
- `GET /api/v3/ingest/{artifact_id}/status` (and `/tests`, `/channels`)
- Legacy v1/v2 postgres queries use the v3 engine by default. Set `NOVA_LEGACY_ROW_ENGINE=1` to restore the old row-oriented SQL path.
- File sources: ingest on add; Chooch uses v3 query API
- `GET /api/v3/ingest/by-path` — resolve existing artifact for a file path (restored sessions)
- `GET /api/database-library` — saved PostgreSQL connection profiles
- `POST /api/database-library`
- `POST /api/database-library/test` — verify connection credentials
- `GET /api/unit-library` — saved unit categories and conversion formulas
- `POST /api/unit-library`
- `GET /api/config-library`
- `POST /api/config-library`
- `GET /api/file/tests`
- `GET /api/file/channels`
- `GET /api/file/timeseries` — **legacy**; uses Parquet when indexed, otherwise slow full-file parse
- `POST /api/desktop/pick-folder` — native folder dialog (absolute path, no copy)
- `POST /api/desktop/pick-files` — native multi-file dialog (absolute paths, no copy)
- `GET /api/file/scan-folder` — list supported data files under a folder path

## Troubleshooting

- If desktop launch fails, run `backend/desktop_app.py` in terminal to see immediate errors.
- If backend data is empty, verify DB credentials and that required tables exist.
- If database sources fail to list databases, open **Database Manager**, verify the profile password, click **Save**, then retry **Sources `+` → Database**.
- If file sources fail, confirm the original file path still exists and that CSV/TDMS format is compatible.
- If Chooch is slow on large TDMS/CSV, confirm ingest completed (status bar) and that `.nova_sessions/` contains the artifact.
- If zoom feels sluggish on fully loaded file data, overview is already at full resolution — detail refetch is skipped automatically; disable **Zoom-in detail refetch** to force-off LOD refetch.
- To reclaim disk space, delete `backend/.nova_sessions/` (or individual artifact folders). Re-add file sources afterward so NOVA can re-ingest them.
- Legacy endpoints (`/api/timeseries`, `/api/v2/timeseries`, `/api/file/timeseries`) return `Deprecation: true` headers; migrate integrations to `POST /api/v3/series/query`.
