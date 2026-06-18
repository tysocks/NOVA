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

## PostgreSQL database requirements

NOVA can query PostgreSQL directly (without file ingest) when a connection profile is saved in **Database Manager** and then used via **Sources `+` → Database**.

### Database Manager workflow

1. Open **File → Database Manager...**
2. Add a profile via the `⋯` menu (**Add database profile**), or edit an existing row (✎).
3. Set **Name**, **Host**, **Port**, **User**, **Password**, and **SSL** (`sslmode`, e.g. `disable` or `require`).
4. Click **⚡ Test connection** to verify the profile can reach PostgreSQL.
5. Click **Save** — profiles are written to `backend/.nova_database_library.json` on the backend host.
6. Open **Sources `+` → Database**, pick **Connection → Database → Tests** (each column has a search box), then **Add Selected Tests**.

On first run with no library file, NOVA auto-creates **RedScale** and **BlueScale** profiles from `NOVA_REDSCALE_*` and `NOVA_BLUESCALE_*` environment variables.

Use **⋯ → Import profiles...** / **Export profiles...** to move profile libraries between machines as JSON.

Each selected test becomes a source entry named `database_name/run_code` (e.g. `hfr_test_data/HFR-0010`).

**Profile JSON shape** (stored in the library file):

```json
{
  "databases": [
    {
      "id": "uuid",
      "type": "postgres",
      "name": "RedScale",
      "host": "localhost",
      "port": 5432,
      "user": "pipeline",
      "password": "secret",
      "sslmode": "disable"
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

When adding database sources, NOVA:

1. Lists saved profiles from **Database Manager** in the **Connection** column.
2. Calls `GET /api/databases` with the profile credentials to populate the **Database** column.
3. Discovers test tables via `GET /api/test-tables` (prefers `test_runs` when present).
4. Lists tests via `GET /api/tests` for the selected database and test table.

If the **Database** column is empty after selecting a connection, verify credentials in Database Manager (use **Test connection**), click **Save**, and confirm PostgreSQL is reachable from the NOVA backend.
