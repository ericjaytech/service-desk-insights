"""Tests for aggregate-only SQLite history (T8)."""

from __future__ import annotations

import datetime
import hashlib
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from service_desk_insights.history import append_run
from service_desk_insights.ingest import ingest_canonical_csv
from service_desk_insights.metrics import calculate_metrics
from service_desk_insights.policy import parse_sla_policy

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _metrics():
    result = ingest_canonical_csv(str(FIXTURES / "canonical_tickets.csv"))
    policy = parse_sla_policy(str(FIXTURES / "sla-policy.json"))
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    policy_bytes = (FIXTURES / "sla-policy.json").read_bytes()
    return calculate_metrics(
        tickets=result.tickets,
        policy=policy,
        as_of=as_of,
        policy_hash=hashlib.sha256(policy_bytes).hexdigest(),
        input_hash=result.input_sha256,
        input_basename=result.source_basename,
    )


def _tmp_db() -> str:
    return os.path.join(tempfile.mkdtemp(), "history.sqlite")


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


def test_append_run_creates_database():
    db = _tmp_db()
    m = _metrics()
    assert append_run(db, m) is True
    assert os.path.isfile(db)


def test_append_run_creates_all_tables():
    db = _tmp_db()
    m = _metrics()
    append_run(db, m)

    conn = sqlite3.connect(db)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    }
    conn.close()

    expected = {
        "_schema_version",
        "runs",
        "sla_by_priority",
        "ageing_buckets",
        "backlog_by_priority",
        "backlog_by_category",
        "resolution_times",
        "reopened_summary",
        "recurring_categories",
        "weekly_trends",
    }
    assert expected <= tables, f"Missing tables: {expected - tables}"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_append_run_is_idempotent():
    db = _tmp_db()
    m = _metrics()
    assert append_run(db, m) is True
    assert append_run(db, m) is False  # same input/policy/as_of

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# Aggregate-only (no raw ticket IDs or source values)
# ---------------------------------------------------------------------------


def test_history_contains_no_ticket_ids():
    db = _tmp_db()
    m = _metrics()
    append_run(db, m)

    conn = sqlite3.connect(db)
    # Check every text column in every table for SYN- or T- patterns.
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != '_schema_version'"
        ).fetchall()
    ]
    for table in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for col in cols:
            rows = conn.execute(f"SELECT {col} FROM {table}").fetchall()
            for (val,) in rows:
                if isinstance(val, str):
                    assert "T-" not in val, f"{table}.{col} contains T-: {val}"
                    assert "SYN-" not in val, f"{table}.{col} contains SYN-: {val}"
    conn.close()


# ---------------------------------------------------------------------------
# Schema version rejection
# ---------------------------------------------------------------------------


def test_rejects_unknown_schema_version():
    db = _tmp_db()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE _schema_version (version INTEGER)")
    conn.execute("INSERT INTO _schema_version (version) VALUES (99)")
    conn.commit()
    conn.close()

    m = _metrics()
    with pytest.raises(sqlite3.OperationalError, match="Unsupported history schema"):
        append_run(db, m)


# ---------------------------------------------------------------------------
# Transaction rollback on failure
# ---------------------------------------------------------------------------


def test_failure_rolls_back():
    """A mid-transaction failure leaves no partial data."""
    db = _tmp_db()
    m = _metrics()

    # Use a read-only database to trigger a real OperationalError mid-write.
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE _schema_version (version INTEGER)")
    conn.execute("INSERT INTO _schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    os.chmod(db, 0o444)  # read-only

    try:
        with pytest.raises(sqlite3.OperationalError):
            append_run(db, m)
    finally:
        os.chmod(db, 0o644)

    # Database should have no runs table (schema init failed before any writes).
    conn = sqlite3.connect(db)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert "runs" not in tables


# ---------------------------------------------------------------------------
# CLI end-to-end with --history-db
# ---------------------------------------------------------------------------


def test_cli_analyse_with_history_db():
    tmp = tempfile.mkdtemp()
    output_dir = os.path.join(tmp, "report")
    history_db = os.path.join(tmp, "history.sqlite")

    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            output_dir,
            "--history-db",
            history_db,
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, f"stderr: {cp.stderr}"
    assert "Report written to:" in cp.stdout
    assert "History appended to:" in cp.stdout
    assert os.path.isfile(history_db)

    # Verify the database has content.
    conn = sqlite3.connect(history_db)
    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    conn.close()
    assert count == 1


def test_cli_analyse_history_idempotent():
    tmp = tempfile.mkdtemp()
    output_dir1 = os.path.join(tmp, "report1")
    output_dir2 = os.path.join(tmp, "report2")
    history_db = os.path.join(tmp, "history.sqlite")

    # First run.
    cp1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            output_dir1,
            "--history-db",
            history_db,
        ],
        capture_output=True,
        text=True,
    )
    assert cp1.returncode == 0
    assert "History appended to:" in cp1.stdout

    # Second run with same inputs.
    cp2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            output_dir2,
            "--history-db",
            history_db,
        ],
        capture_output=True,
        text=True,
    )
    assert cp2.returncode == 0
    assert "already contains this run" in cp2.stdout

    conn = sqlite3.connect(history_db)
    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    conn.close()
    assert count == 1
