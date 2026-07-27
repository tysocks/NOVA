# NOVA Data File Formats

NOVA ingests telemetry from CSV, Parquet, Arrow/Feather, HDF5, and TDMS files. Each format must expose a time axis and one or more numeric channels. Files are validated at probe and ingest time.

## Shared rules

- **Time index** — One column or dataset defines sample timestamps. It is used for plotting and alignment but does **not** appear in the channel list.
- **Channels** — Numeric columns/datasets with the same row count as the time index (except TDMS; see below).
- **Units** — Optional. CSV can encode units in headers; Parquet/Arrow can store `unit` in field metadata; HDF5/TDMS use dataset/channel properties.

Recognized time column names (first match wins): `timestamp_utc`, `time`, `timestamp`, `datetime`, `time_s`, `x_ms`, `TIME`. You can also pick any column via **Time Index Channel** in the configure modal.

---

## CSV

**Layout:** Header row + data rows. Wide table (one column per channel).

| Column | Required | Notes |
|--------|----------|-------|
| Time column | Yes | Numeric seconds (`time_s`), epoch ms (`x_ms`), or parseable datetime strings (`timestamp_utc`) |
| Channel columns | ≥1 | Numeric values aligned with time |

**Example** (`examples/example_rocket_test.csv`):

```csv
time_s,chamber_pressure_bar,thrust_n,fuel_mass_flow_kg_s,lox_mass_flow_kg_s
0.0,12.5,1500.0,0.95,2.10
0.1,12.504,1500.2,0.9501,2.1002
```

**Optional header units** (enable *Parse Units from Header*):

```csv
time_s,THRUST (N),P[bar]
0.0,1500.0,12.5
```

Supported patterns: `NAME (unit)`, `NAME[unit]`, `NAME [unit]`.

---

## Parquet

**Layout:** Apache Parquet table with the same wide layout as CSV.

| Field | Required | Notes |
|-------|----------|-------|
| Time field | Yes | `float64`/`int64` seconds, `x_ms` milliseconds, or datetime type |
| Channel fields | ≥1 | Numeric types with valid (non-all-null) data |

**Units:** Attach metadata on each channel field:

```python
pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"})
```

**Example:** `examples/example_rocket_test.parquet` (regenerate with `python examples/generate_tabular_examples.py`).

---

## Arrow / Feather

Same requirements as Parquet. Feather (`.arrow`, `.feather`) is written/read as an Arrow IPC file.

**Example:** `examples/example_rocket_test.arrow`

---

## HDF5

**Layout:** 1-D numeric datasets sharing length with a TIME dataset.

| Dataset | Required | Notes |
|---------|----------|-------|
| TIME | Yes | Default path `telemetry/TIME`; or any dataset flagged as time-like |
| Channels | ≥1 | 1-D numeric datasets aligned to TIME length |

Optional: `unit` attribute on datasets; start-time attributes for absolute-time alignment.

---

## TDMS

**Layout:** NI TDMS groups and channels. Each channel carries its own time track.

| Property | Required | Notes |
|----------|----------|-------|
| `wf_start_time` | Yes | Channel start timestamp |
| Time track / `wf_increment` | Yes | Sample spacing or explicit time array |
| Channel data | ≥1 readable | Numeric waveform per channel |

No shared time index column is configured in NOVA — the time index dropdown is hidden for TDMS sources.

---

## Schema validation

`POST /api/v3/file/probe` returns `schema_validation`:

- `valid` — whether ingest is allowed
- `errors` — blocking issues (missing time column, no numeric channels, unreadable file)
- `warnings` — non-blocking (e.g. fewer than 2 rows)
- `format_requirements` — short format summary for the detected type

`POST /api/v3/ingest/file` runs the same checks and rejects invalid files before indexing.

---

## DuckDB catalog and Parquet lake

NOVA’s primary analytical store is **DuckDB + Parquet**, not PostgreSQL.

- **Catalog** — a DuckDB file (`.duckdb`) holds `tests`, `test_parameters`, `channels`, `ranges`, `range_rules`, and `results`.
- **Hidden local catalog** — system cache at `backend/.nova_catalog.duckdb` used only for temporary file-open ingest. It is **not** listed in Sources or **Databases > Manager**. Cleared (with `.nova_sessions`) on each backend start.
- **Project catalogs** — configure durable catalogs in **File → Databases** (`+` to create). Each profile has:
  - `catalog_path` — path to the `.duckdb` file (Filepath in the create/edit form)
  - `parquet_root` — permanent lake root (derived next to the catalog file when omitted)
- **Active catalog** — first/default project DuckDB profile is used as the permanent-ingest target.
- **Samples** — one Parquet file per channel with columns `x_ms`, `y`.
- **Temporary ingest** (file open / plot) — always writes under `.nova_sessions/{artifact_id}/data/` and registers `durability=temporary` in the **local** catalog. Re-opening the same file reuses the cached artifact within the same app instance.
- **Permanent ingest** — requires a project `catalog_id`; writes under `{profile.parquet_root}/{run_code}/data/` with `durability=permanent`.

Profiles are stored in `backend/.nova_database_library.json` (also supports optional PostgreSQL profiles when `NOVA_ENABLE_POSTGRES=1`).

### APIs

- `GET /api/catalog/tests` — list registered tests (also the default for `GET /api/tests`)
- `GET /api/catalog/tests/{id}/channels` / `.../parameters`
- `POST /api/v3/ingest/file` — temporary by default (local catalog); permanent requires `catalog_id` of a project profile. Accepts `parameters`, `apply_range_rule_ids`
- `POST /api/v3/series/query?format=series` — columnar JSON (`series[].x_ms` / `series[].y`); `format=arrow` IPC; `format=json` row compat
- Ranges: `POST /api/catalog/ranges`, `POST /api/catalog/range-rules`, `POST /api/catalog/range-rules/apply`
- Results: `GET|POST /api/catalog/results`

### UI

- **Sources** — file folders/files plus every database from **Databases > Manager**. Local temporary cache is invisible. Sources `+` only adds folders/files.
- **Ranges** panel — create interactive ranges or apply threshold/edge rules; overlays draw on the Plotly timeline.

### Config (`NOVA_` env prefix)

| Variable | Default | Meaning |
|----------|---------|---------|
| `NOVA_PARQUET_ROOT` | `backend/.nova_parquet` | Fallback permanent lake root |
| `NOVA_DEFAULT_INGEST_MODE` | `temporary` | Legacy default (file open is always temporary into local) |
| `NOVA_ENABLE_POSTGRES` | `false` | Re-enable legacy PostgreSQL sources/APIs |

---

## PostgreSQL database requirements (legacy, opt-in)

PostgreSQL is **disabled by default**. Set `NOVA_ENABLE_POSTGRES=1` to restore Postgres profiles in **File → Databases**.

When enabled, NOVA can query PostgreSQL directly (without file ingest) if a connection profile is present in the database library.

### Databases workflow (DuckDB)

1. Open **File → Databases...**
2. Click **+** to open **Databases > Manager > Create**
3. Enter **Name** and **Filepath** (`.duckdb`), then save
4. The database appears in Sources automatically; expand it to browse tests
5. Right-click a row in the manager for **Edit** / **Delete**

On first run with Postgres enabled and no library file, NOVA may seed **RedScale** / **BlueScale** from `NOVA_REDSCALE_*` / `NOVA_BLUESCALE_*`.

Each selected test becomes a source entry named `database_name/run_code` (e.g. `hfr_test_data/HFR-0010`).

**Profile JSON shape** (stored in the library file):

```json
{
  "databases": [
    {
      "id": "uuid",
      "type": "duckdb",
      "name": "Project A",
      "catalog_path": "D:/projects/a/catalog.duckdb",
      "parquet_root": "D:/projects/a/parquet",
      "default_ingest_mode": "permanent"
    }
  ]
}
```

Passwords are stored in plaintext in the local library file. Do not commit `.nova_database_library.json`.

### Connection requirements

- Reachable PostgreSQL instance (`host`, `port`, `user`, `password`, `sslmode`)
- Login must be able to:
  - connect to the chosen database
  - read `public` schema metadata (`information_schema.columns`, `pg_database`)
  - `SELECT` from telemetry tables used by NOVA

### Required tables and columns

NOVA expects this logical schema:

- `sensor_readings`
  - `test_run_id` (FK-like reference to test table `id`)
  - `channel_id` (FK-like reference to `channels.id`)
  - `time` (`timestamptz` recommended)
  - `value` (numeric)
- `channels`
  - `id` (primary key)
  - `channel_name` (text, unique-ish channel identifier)
  - optional but supported: `display_name`, `unit`, `sample_rate_hz`, `valid_min`, `valid_max`
- test table (default `test_runs`, configurable)
  - `id` (primary key)
  - `run_code` (text)
  - `start_time` (`timestamptz`)
  - optional but used when present: `end_time`, `duration_s`

### Test table discovery rule

When NOVA lists available test tables, it looks for `public` tables containing at least:

- `id`
- `run_code`
- `start_time`

If your table has these columns, it should be selectable as a test table in NOVA.

### Add Source picker behavior

Postgres profiles from **File → Databases** appear in the Sources tree automatically. Expanding a profile:

1. Calls `GET /api/databases` with the profile credentials
2. Discovers test tables via `GET /api/test-tables` (prefers `test_runs` when present)
3. Lists tests via `GET /api/tests` for each database

If a profile shows as unavailable, verify credentials via **Edit** in Databases > Manager and confirm PostgreSQL is reachable from the NOVA backend.
