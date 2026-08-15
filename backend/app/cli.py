"""Headless NOVA CLI for batch ingest and series export.

Examples:
  python -m app.cli ingest-rule --rule-id <id> path/to/a.csv path/to/b.csv
  python -m app.cli export-series --format csv --out burn.csv --series series.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _repo_backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_ingestion_rules(library_path: Path | None = None) -> list[dict[str, Any]]:
    from .services.ingestion_rule_library import clean_ingestion_rule_rows

    path = library_path or (_repo_backend_root() / ".nova_ingestion_library.json")
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rules") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return clean_ingestion_rule_rows(rows)


def _resolve_rule(
    *,
    rule_id: str | None,
    rule_name: str | None,
    rule_json: Path | None,
    library_path: Path | None,
) -> dict[str, Any]:
    from .services.ingestion_rule_library import clean_ingestion_rule

    if rule_json is not None:
        payload = json.loads(Path(rule_json).read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "rules" in payload and isinstance(payload["rules"], list):
            rules = payload["rules"]
            if not rules:
                raise SystemExit(f"No rules in {rule_json}")
            return clean_ingestion_rule(rules[0])
        return clean_ingestion_rule(payload)

    rules = _load_ingestion_rules(library_path)
    if rule_id:
        match = next((r for r in rules if str(r.get("id")) == str(rule_id)), None)
        if match is None:
            raise SystemExit(f"Ingestion rule not found: {rule_id}")
        return match
    if rule_name:
        needle = str(rule_name).strip().lower()
        matches = [r for r in rules if str(r.get("name") or "").strip().lower() == needle]
        if not matches:
            raise SystemExit(f"Ingestion rule not found by name: {rule_name}")
        if len(matches) > 1:
            raise SystemExit(f"Multiple ingestion rules named '{rule_name}'. Use --rule-id.")
        return matches[0]
    raise SystemExit("Provide --rule-id, --rule-name, or --rule-json")


def cmd_ingest_rule(args: argparse.Namespace) -> int:
    from .engine.file_index import run_ingest
    from .services.file_sources import detect_source_type

    rule = _resolve_rule(
        rule_id=args.rule_id,
        rule_name=args.rule_name,
        rule_json=Path(args.rule_json) if args.rule_json else None,
        library_path=Path(args.library) if args.library else None,
    )
    paths = [Path(p) for p in (args.files or [])]
    if not paths:
        raise SystemExit("Provide one or more file paths to ingest.")

    ok = 0
    failed: list[tuple[str, str]] = []
    for path in paths:
        file_path = str(path.resolve())
        try:
            source_type = detect_source_type(file_path)
            if not source_type:
                raise ValueError(f"Could not detect source type for {file_path}")
            manifest = run_ingest(
                source_type,
                file_path,
                ingest_mode="permanent",
                catalog_id=str(rule.get("target_catalog_id")),
                ingestion_rule=rule,
            )
            status = manifest.get("status", "ready")
            run_code = manifest.get("run_code") or ""
            print(f"OK  {path.name}: status={status} run_code={run_code}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - CLI should keep going across files
            failed.append((file_path, str(exc)))
            print(f"ERR {path.name}: {exc}", file=sys.stderr)

    print(f"Ingested {ok}/{len(paths)} with rule '{rule.get('name') or rule.get('id')}'.")
    return 1 if failed and not ok else (1 if failed and args.fail_fast else 0)


def cmd_export_series(args: argparse.Namespace) -> int:
    from .engine.export_series import build_series_csv, build_series_parquet

    series_path = Path(args.series)
    payload = json.loads(series_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "series" in payload:
        series = payload["series"]
    else:
        series = payload
    if not isinstance(series, list):
        raise SystemExit("Series JSON must be a list or {\"series\": [...]}")

    fmt = str(args.format or "csv").lower()
    out = Path(args.out)
    if fmt == "parquet":
        data = build_series_parquet(series)
    elif fmt == "csv":
        data = build_series_csv(series)
    else:
        raise SystemExit("format must be csv or parquet")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data)} bytes, {len(series)} series)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="NOVA headless tools")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest-rule", help="Permanently ingest files with an ingestion rule")
    ingest.add_argument("files", nargs="+", help="Data file paths")
    ingest.add_argument("--rule-id", help="Ingestion rule id from the library")
    ingest.add_argument("--rule-name", help="Ingestion rule name from the library")
    ingest.add_argument("--rule-json", help="Path to a rule JSON object or library export")
    ingest.add_argument("--library", help="Override path to .nova_ingestion_library.json")
    ingest.add_argument(
        "--fail-fast",
        action="store_true",
        help="Exit non-zero if any file fails (default: non-zero only when all fail)",
    )
    ingest.set_defaults(func=cmd_ingest_rule)

    export = sub.add_parser("export-series", help="Write plotted-series JSON to CSV or Parquet")
    export.add_argument("--series", required=True, help="JSON file with series arrays")
    export.add_argument("--format", choices=("csv", "parquet"), default="csv")
    export.add_argument("--out", required=True, help="Output file path")
    export.set_defaults(func=cmd_export_series)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
