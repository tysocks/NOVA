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
