"""Generate example Parquet and Arrow files for NOVA ingest testing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq

OUT_DIR = Path(__file__).resolve().parent
N = 601  # 60 s @ 10 Hz


def _sample_table() -> tuple[pa.Table, dict[str, str]]:
    time_s = np.linspace(0.0, 60.0, N)
    chamber_p = 12.5 + 0.04 * time_s + 0.15 * np.sin(time_s / 4.0)
    thrust = 1500.0 + 2.0 * time_s + 30.0 * np.sin(time_s / 6.0)
    fuel_mdot = 0.95 + 0.001 * time_s
    lox_mdot = 2.10 + 0.002 * time_s

    schema = pa.schema(
        [
            pa.field("time_s", pa.float64(), metadata={b"description": b"Elapsed seconds from test start"}),
            pa.field("chamber_pressure_bar", pa.float64(), metadata={b"unit": b"bar"}),
            pa.field("thrust_n", pa.float64(), metadata={b"unit": b"N"}),
            pa.field("fuel_mass_flow_kg_s", pa.float64(), metadata={b"unit": b"kg/s"}),
            pa.field("lox_mass_flow_kg_s", pa.float64(), metadata={b"unit": b"kg/s"}),
        ]
    )
    table = pa.table(
        {
            "time_s": time_s,
            "chamber_pressure_bar": chamber_p,
            "thrust_n": thrust,
            "fuel_mass_flow_kg_s": fuel_mdot,
            "lox_mass_flow_kg_s": lox_mdot,
        },
        schema=schema,
    )
    channel_units = {
        "chamber_pressure_bar": "bar",
        "thrust_n": "N",
        "fuel_mass_flow_kg_s": "kg/s",
        "lox_mass_flow_kg_s": "kg/s",
    }
    return table, channel_units


def main() -> None:
    table, channel_units = _sample_table()
    parquet_path = OUT_DIR / "example_rocket_test.parquet"
    arrow_path = OUT_DIR / "example_rocket_test.arrow"
    csv_path = OUT_DIR / "example_rocket_test.csv"

    pq.write_table(table, parquet_path)
    feather.write_feather(table, arrow_path)

    df = table.to_pandas()
    df.to_csv(csv_path, index=False)

    print(f"Wrote {parquet_path} ({parquet_path.stat().st_size} bytes)")
    print(f"Wrote {arrow_path} ({arrow_path.stat().st_size} bytes)")
    print(f"Wrote {csv_path} ({csv_path.stat().st_size} bytes)")
    print(f"Rows: {table.num_rows}, columns: {table.column_names}")


if __name__ == "__main__":
    main()
