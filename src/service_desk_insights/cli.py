"""Command-line interface for service-desk-insights.

Exposes ``analyse``, ``generate-synthetic``, ``--help`` and ``--version`` through
``argparse`` subcommands.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import sys

from service_desk_insights import __version__
from service_desk_insights.history import append_run
from service_desk_insights.ingest import ingest_canonical_csv, ingest_mapped_csv
from service_desk_insights.metrics import calculate_metrics
from service_desk_insights.policy import parse_column_map, parse_sla_policy
from service_desk_insights.report import csv_tables, html_report, json_summary, terminal_summary
from service_desk_insights.synthetic import generate as generate_synthetic


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="service-desk-insights",
        description="Local, reproducible service-desk reporting from anonymised ticket exports.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- analyse -----------------------------------------------------------
    analyse = sub.add_parser(
        "analyse",
        help="Validate ticket CSV and produce a report directory.",
    )
    analyse.add_argument(
        "csv_path",
        help="Path to the anonymised ticket CSV export.",
    )
    analyse.add_argument(
        "--sla-policy",
        required=True,
        help="Path to the JSON SLA policy file.",
    )
    analyse.add_argument(
        "--column-map",
        default=None,
        help="Path to a JSON column mapping for non-canonical headings.",
    )
    analyse.add_argument(
        "--as-of",
        default=None,
        help="ISO-8601 timestamp for deterministic analysis (default: now UTC).",
    )
    analyse.add_argument(
        "--output-dir",
        required=True,
        help="Directory for report output (must not already exist).",
    )
    analyse.add_argument(
        "--history-db",
        default=None,
        help="Optional path to an aggregate-only SQLite history database.",
    )
    analyse.add_argument(
        "--recurring-min-count",
        type=int,
        default=3,
        help="Minimum ticket count for a category to be considered recurring (default: 3).",
    )

    # ---- generate-synthetic ------------------------------------------------
    gen = sub.add_parser(
        "generate-synthetic",
        help="Generate a deterministic synthetic ticket CSV for demonstrations and tests.",
    )
    gen.add_argument("--rows", type=int, required=True, help="Number of ticket rows to generate.")
    gen.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic output (default: 42).",
    )
    gen.add_argument(
        "--start-date",
        required=True,
        help="ISO-8601 start date for the ticket window (e.g. 2026-01-05).",
    )
    gen.add_argument(
        "--weeks",
        type=int,
        required=True,
        help="Number of weeks to spread tickets across.",
    )
    gen.add_argument(
        "--output",
        required=True,
        help="Path for the generated CSV file (must not already exist).",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyse":
        _cmd_analyse(args)
    elif args.command == "generate-synthetic":
        _cmd_generate_synthetic(args)
    else:
        parser.print_help()
        sys.exit(2)


def _cmd_analyse(args: argparse.Namespace) -> None:
    # --- Resolve as_of -----------------------------------------------------
    if args.as_of is not None:
        as_of = _parse_as_of(args.as_of)
    else:
        as_of = datetime.datetime.now(datetime.UTC)

    # --- Load policy -------------------------------------------------------
    try:
        policy = parse_sla_policy(args.sla_policy)
    except ValueError as exc:
        sys.stderr.write(f"Invalid SLA policy: {exc}\n")
        sys.exit(3)

    with open(args.sla_policy, "rb") as fh:
        policy_bytes = fh.read()
    policy_hash = hashlib.sha256(policy_bytes).hexdigest()

    # --- Ingest ------------------------------------------------------------
    if args.column_map is not None:
        try:
            column_map = parse_column_map(args.column_map)
        except ValueError as exc:
            sys.stderr.write(f"Invalid column map: {exc}\n")
            sys.exit(3)
        result = ingest_mapped_csv(args.csv_path, column_map)
    else:
        result = ingest_canonical_csv(args.csv_path)

    if result.errors:
        for err in result.errors:
            prefix = f"Row {err.row}: " if err.row > 0 else ""
            sys.stderr.write(f"{prefix}{err.message}\n")
        if result.errors_suppressed:
            sys.stderr.write(f"... and {result.errors_suppressed} more errors suppressed.\n")
        sys.stderr.write("Validation failed. No report was published.\n")
        sys.exit(3)

    # --- Calculate metrics -------------------------------------------------
    metrics = calculate_metrics(
        tickets=result.tickets,
        policy=policy,
        as_of=as_of,
        recurring_min_count=args.recurring_min_count,
        policy_hash=policy_hash,
        input_hash=result.input_sha256,
        input_basename=result.source_basename,
    )

    # --- Output directory (atomic via temp + rename) -----------------------
    output_dir = args.output_dir
    if os.path.exists(output_dir):
        sys.stderr.write(f"Output directory already exists: {output_dir}\n")
        sys.exit(4)

    tmp_dir = output_dir + ".tmp"
    try:
        os.makedirs(os.path.join(tmp_dir, "tables"), exist_ok=False)

        # Terminal summary to stdout.
        terminal_summary(metrics)

        # JSON summary.
        json_path = os.path.join(tmp_dir, "summary.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            fh.write(json_summary(metrics))

        # HTML report.
        html_path = os.path.join(tmp_dir, "report.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(html_report(metrics))

        # CSV tables.
        open_tickets_list = [t for t in result.tickets if t.resolved_at is None]
        csv_tables(metrics, result.tickets, open_tickets_list, os.path.join(tmp_dir, "tables"))

        # Atomic rename.
        os.rename(tmp_dir, output_dir)

        sys.stdout.write(f"\nReport written to: {output_dir}\n")

        # --- Optional history (non-fatal) -----------------------------------
        if args.history_db is not None:
            try:
                inserted = append_run(args.history_db, metrics)
                if inserted:
                    sys.stdout.write(f"History appended to: {args.history_db}\n")
                else:
                    sys.stdout.write("History already contains this run (skipped).\n")
            except Exception as exc:
                sys.stderr.write(f"History write failed (report was published): {exc}\n")

    except BaseException:
        # Clean up partial output on any failure.
        if os.path.isdir(tmp_dir):
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _cmd_generate_synthetic(args: argparse.Namespace) -> None:
    try:
        generate_synthetic(
            rows=args.rows,
            seed=args.seed,
            start_date=args.start_date,
            weeks=args.weeks,
            output=args.output,
        )
    except FileExistsError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(4)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(3)


def _parse_as_of(value: str) -> datetime.datetime:
    """Parse --as-of value to a UTC datetime."""
    # Reuse ingest timestamp parser logic inline.
    stripped = value.strip()
    ends_z = stripped.endswith("Z")
    body = stripped[:-1] if ends_z else stripped
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.datetime.strptime(body, fmt)
        except ValueError:
            continue
        if ends_z:
            return dt.replace(tzinfo=datetime.UTC)
        elif dt.tzinfo is not None:
            return dt.astimezone(datetime.UTC)
    # Also try %z formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.datetime.strptime(stripped, fmt).astimezone(datetime.UTC)
        except ValueError:
            continue
    msg = f"Invalid --as-of timestamp: {value!r}"
    raise ValueError(msg)
