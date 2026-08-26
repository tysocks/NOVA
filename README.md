# NOVA

NOVA (Northern Operation Viewer and Analysis) is a desktop app for viewing and analyzing test telemetry. It runs a local backend and opens a native window. You add data files or catalogs, pick channels, and plot them.

Libraries (configurations, units, range definitions, ingestion rules, limits) live in your user folder by default, not next to the app. Set each folder in **Preferences → Directory**.

## Quickstart

From a fresh clone, this is enough to plot a file.

1. Install Python 3.11+ and clone the repo:

```bat
git clone https://github.com/tysocks/NOVA.git
cd NOVA
```

2. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

3. Start NOVA from the repository root:

```powershell
.\.venv\Scripts\python .\backend\desktop_app.py
```

4. In the window:

   1. Click **Sources** in the left rail, then **+** and choose a folder that contains CSV, TDMS, HDF5, Parquet, or Arrow files (or use the ingest button to pick files).
   2. Click **Channels**, then **+**, and move channels into the selected list. With multiple sources selected, switch **Source** in the picker to add channels from each.
   3. Click **Plot** (bottom of the rail, or `F5`).

A sample CSV is at `examples/example_rocket_test.csv`.

Closing the window stops the backend. Each launch starts a fresh backend on the first free port in **8000–8010** (any leftover process from a previous run is stopped first).

---

## Window layout

| Area | What it does |
|------|----------------|
| **File menu** (top of the rail) | Managers, export, project save/open, reset, preferences |
| **Toolbars** | Sources, Channels, Masks, Ranges, Objects, Options, Pages |
| **Plot** (bottom of the rail) | Load the active view |
| **Plot workspace** | One or more Plotly views; scroll to zoom |

Click, Ctrl/Cmd-click, and Shift-click work in the sidebar lists. An empty selection means “all”.

---

## File menu

**Configuration Manager** — Saved plot setups (channels, layout, options). Apply a configuration to the current session.

**Databases** — Local DuckDB catalogs and optional PostgreSQL connection profiles. Add, edit, test, import, and export profiles. Catalogs are how permanent ingest is stored.

**Units** — Unit categories, a preferred display unit, and conversion formulas (`x` is the value in the source unit). Example: `x + 273.15` for °C → K. Channels whose unit matches a translation are plotted in the preferred unit.

**Range Definitions** — Reusable rules for detecting ranges on a test (named windows on the time axis).

**Ingestion Rules** — Permanent ingest recipes: target catalog, channel include/exclude/rename, calculated channels, range definitions, time index, units parsing. Apply a rule to files from the ingest window or the CLI.

**Limits** — Named limit sets and thresholds used in summary views.

**Export** — PNG or PDF of the view or page, or a CSV / Parquet file of the plotted series.

**Save Project / Open Project** — `.nova` project files restore sources, channels, and view layout.

**Reset Scope** — Clear views, pages, channels, masks, and objects for a clean slate.

**Preferences** — Theme, font, keyboard shortcuts, plotting behavior, library directories, and About.

---

## Toolbars

### Sources

Add data to the session.

- **+** — Native folder picker. NOVA scans for supported files and adds them.
- **Ingest** — Pick files and optionally apply an ingestion rule into a catalog.
- **↻** — Refresh the selection. For an ingested file (or files under a selected folder), this clears the session channel cache and **re-ingests** so schema/unit/time-index changes take effect. Permanent catalogs are not wiped.

Supported files: CSV, TDMS, HDF5 (`.h5` / `.hdf5`), Parquet, Arrow/Feather. Files are ingested to Parquet for fast queries. Session ingest lives under `backend/.nova_sessions/` (safe to delete; re-add files afterward).

Right-click or double-click a source to rename or edit it. For CSV sources, **Edit → Properties** includes:

- **Parse Units from Header** — units in the header (`NAME (unit)`, `NAME[unit]`)
- **Parse Units from Row** — second row is a units row (e.g. GFSSP-style `SECONDS,kPa,...`); enable this, save, then refresh

Folder sources can be expanded like a tree. See [docs/data-file-formats.md](docs/data-file-formats.md) for column names, units, and format rules.

### Channels

Channels from selected sources, plus calculated channels and aliases.

- **+** — Add channels from source (two-column picker). Switch the **Source** dropdown to pick from each selected source in one session; channels keep a per-source identity when names overlap.
- **Calculated channel** — A formula over other channels. Examples: `A + B`, `C/SQRT(2*810*(A-B))`, `RISE(A)`, `ROLLING_MEAN(A, 50)`, `BAND_PASS_FILTER(A, low, high)`. Other functions include `ABS`, `SQRT`, `SMOOTH`, `RMS`, `PEAK`, `TRAPZ`, `FALL`, and `SETTLING`.

If a calculated channel produces no finite values (for example `SQRT` of a negative input), Plot shows a status error instead of failing silently.

Right-click a channel for edit, unit/frequency overrides, or alias actions. Multi-select applies Set Unit to all selected channels.

### Masks

Hide or keep data.

- **Channel mask** — Keep/drop samples by channel value.
- **Range mask** — Keep/drop by time window.

### Ranges

Time windows on a test: add with **+**, detect from a range definition, or draw **Define Range** on a time-based plot (right-click). **Stitch** places selected ranges tip-to-tail (`Ctrl+Shift+S`). **Align Sources** and **Toggle Link X** (`Ctrl+Shift+L`) keep views aligned.

### Objects

Plot overlays. Right-click a plot → **Add** to place them; this list is for selecting and deleting.

- **Ruler** — Distance / delta between two points
- **Vertical / horizontal line** — Drag to make a band
- **Polyline** and **Shape**
- **Datanote** — Label a datapoint
- **Text** and **Arrow**

### Options

Properties of the focused plot: axes, legend, colors, line width, time reference (`Raw Time` vs `t0 = First Point`), X axis (time or a channel), and zoom-detail refetch.

### Pages

A page is a named layout of one or more views. **+** adds a page. Each view can be:

| View | Role |
|------|------|
| **Time Scope** | Standard time series |
| **Stacked Scope** | One strip per channel |
| **Grid Scope** | Channel tiles in a grid |
| **Parametric** | Channel vs channel |
| **Spectral** | Frequency-domain |
| **Cursor** | A/B cursor table (values and Δ) |
| **Summary** | Stats against limit sets |
| **Legend** | Standalone legend tile |

---

## Plotting

- **Plot** / `F5` loads the active view from the current sources, channels, masks, and ranges.
- **Shift+F5** plots every visible tile on the page.
- Scroll-zoom on the plot and axes. Double-click the plot to reset zoom (including unit-converted scales).
- Time reference can be raw timestamps or per-test `t0`.
- Right-click a plot for overlays, **Define Range**, **Align Sources**, **Stitch Ranges**, and **Copy**.
- Calculated channels run on the server when **Preferences → Features → Server-side calculated channels** is on (default).
- **Zoom-in detail refetch** (same Features tab) reloads higher-resolution data after you zoom into a downsampled overview.

Copy the active view image with **Ctrl+Shift+C**. Copy/paste channels with **Ctrl+C** / **Ctrl+V**.

---

## Preferences → Directory

Each library is a JSON file inside a folder you choose:

| Library | Typical file name |
|---------|-------------------|
| Configurations | `.nova_config_library.json` |
| Units | `.nova_unit_library.json` |
| Range Definitions | `.nova_range_definition_library.json` |
| Ingestion Rules | `.nova_ingestion_library.json` |
| Limits | `.nova_limit_library.json` |

**Default folder (Windows):** `%LOCALAPPDATA%\NOVA\libraries`  
Example: `C:\Users\<you>\AppData\Local\NOVA\libraries`

On Linux the default is `~/.local/share/NOVA/libraries` (or `$XDG_DATA_HOME/NOVA/libraries`).

That location stays writable after an installer puts NOVA in Program Files. Click the folder icon on a row to pick a directory. If the JSON file is missing in the new folder, NOVA copies the current library there. Older files under `backend/` are migrated into the user folder on first use.

Settings themselves are `%LOCALAPPDATA%\NOVA\.nova_user_settings.json` (or the Linux equivalent). Override the data root with `NOVA_USER_DATA_DIR` if needed. Native folder pickers require the desktop app, not a browser tab.

Other Preferences tabs: **Theme**, **Font & Text**, **Keyboard Shortcuts**, **Features** (server-side calcs, zoom detail refetch), **About**.

---

## Keyboard shortcuts (defaults)

| Action | Shortcut |
|--------|----------|
| Plot | `F5` |
| Plot all visible views | `Shift+F5` |
| Delete | `Delete` |
| Preferences | `Ctrl+,` |
| Export | `Ctrl+Shift+E` |
| Copy view | `Ctrl+Shift+C` |
| Copy / paste channels | `Ctrl+C` / `Ctrl+V` |
| Reset scope | `Home` |
| Stitch ranges | `Ctrl+Shift+S` |
| Link X axes | `Ctrl+Shift+L` |

Rebind these under **Preferences → Keyboard Shortcuts**.

---

## Optional: PostgreSQL

File sources work with no database. PostgreSQL is off by default.

To use it, set `NOVA_ENABLE_POSTGRES=1` and put connection values in `backend/.env` (`NOVA_DB_*`). **File → Databases** still holds profiles. Empty libraries can seed RedScale / BlueScale from `NOVA_REDSCALE_*` and `NOVA_BLUESCALE_*`.

---

## Headless CLI

From `backend/` with the venv active (or use `..\.venv\Scripts\python`):

```powershell
python -m app.cli ingest-rule --rule-name "My rule" path\to\a.csv path\to\b.csv
python -m app.cli export-series --series series.json --format csv --out out.csv
```

Pass `--rule-id`, `--rule-name`, or `--rule-json`. `ingest-rule` reads the ingestion-rule library from **Preferences → Directory** unless you pass `--library`.

---

## Backend-only (browser)

```powershell
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000). Prefer the desktop app: it owns the backend, uses native folder pickers, and starts on the first free port in **8000–8010**.

Primary query API: `POST /api/v3/series/query`. Health check: `GET /health`.

---

## Project layout

- `backend/desktop_app.py` — Desktop window, splash, backend start/stop
- `backend/app/main.py` — HTTP API
- `backend/app/engine/` — v3 query, ingest, Parquet, calculated channels
- `backend/app/services/` — libraries, file sources, settings, native dialogs
- `backend/app/static/` — UI (`index.html`, `js/nova-v3.js`)
- `examples/` — Sample CSV / Parquet / Arrow
- `docs/data-file-formats.md` — File format rules

Runtime data (not committed): `%LOCALAPPDATA%\NOVA\`, `backend/.nova_sessions/`, `backend/.nova_profile/` (desktop cache).

---

## Troubleshooting

- **Launch fails** — Run `.\.venv\Scripts\python .\backend\desktop_app.py` in a terminal and read the traceback. Also see `nova_backend.log`.
- **UI changes do not appear** — Fully quit NOVA and start again from the command above. Desktop always starts a new backend; do not leave an old `uvicorn` running on port 8000.
- **Folder picker does nothing** — Use the desktop app, not the browser. Native dialogs only exist in desktop mode.
- **File plot is empty or slow** — Confirm ingest finished (status bar) and the original file path still exists. CSV needs a time column (`timestamp_utc`, `time`, `timestamp`, `datetime`, `time_s`, `TIME`, `SECONDS`, …) and numeric channels. After changing CSV unit/time options, use **↻** to re-ingest.
- **CSV units wrong / missing** — Enable **Parse Units from Header** and/or **Parse Units from Row** on the source, save, refresh.
- **Calculated channel missing from plot** — Check the status toast after Plot. Domain errors (e.g. `SQRT` of a negative) produce no finite points and now raise a visible alarm.
- **Disk use** — Delete `backend/.nova_sessions/` and re-add files.
- **No databases when adding a source** — Open **File → Databases**, add a catalog or profile, save, retry.
