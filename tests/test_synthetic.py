"""Tests for the deterministic synthetic ticket generator (T7)."""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

from service_desk_insights.ingest import ingest_canonical_csv
from service_desk_insights.synthetic import generate


def _tmp_csv(name: str = "syn.csv") -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp()) / name


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_bytes():
    p1 = _tmp_csv("a.csv")
    p2 = _tmp_csv("b.csv")
    generate(rows=100, seed=42, start_date="2026-01-05", weeks=4, output=str(p1))
    generate(rows=100, seed=42, start_date="2026-01-05", weeks=4, output=str(p2))
    assert (
        hashlib.sha256(p1.read_bytes()).hexdigest() == hashlib.sha256(p2.read_bytes()).hexdigest()
    )


def test_different_seed_produces_different_bytes():
    p1 = _tmp_csv("a.csv")
    p2 = _tmp_csv("b.csv")
    generate(rows=100, seed=42, start_date="2026-01-05", weeks=4, output=str(p1))
    generate(rows=100, seed=99, start_date="2026-01-05", weeks=4, output=str(p2))
    assert (
        hashlib.sha256(p1.read_bytes()).hexdigest() != hashlib.sha256(p2.read_bytes()).hexdigest()
    )


# ---------------------------------------------------------------------------
# Output passes the production validator
# ---------------------------------------------------------------------------


def test_generated_csv_passes_validator():
    p = _tmp_csv()
    generate(rows=200, seed=1, start_date="2026-01-05", weeks=8, output=str(p))
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) == 0, [e.message for e in result.errors]
    assert len(result.tickets) == 200


# ---------------------------------------------------------------------------
# Contract invariants
# ---------------------------------------------------------------------------


def test_generated_ids_are_syn_prefixed():
    p = _tmp_csv()
    generate(rows=50, seed=1, start_date="2026-01-05", weeks=4, output=str(p))
    result = ingest_canonical_csv(str(p))
    for t in result.tickets:
        assert t.ticket_id.startswith("SYN-")


def test_generated_has_open_and_closed_tickets():
    p = _tmp_csv()
    generate(rows=200, seed=1, start_date="2026-01-05", weeks=8, output=str(p))
    result = ingest_canonical_csv(str(p))
    open_count = sum(1 for t in result.tickets if t.resolved_at is None)
    closed_count = sum(1 for t in result.tickets if t.resolved_at is not None)
    assert open_count > 0, "Expected some open tickets"
    assert closed_count > 0, "Expected some closed tickets"


def test_generated_has_multiple_priorities():
    p = _tmp_csv()
    generate(rows=200, seed=1, start_date="2026-01-05", weeks=8, output=str(p))
    result = ingest_canonical_csv(str(p))
    priorities = {t.priority for t in result.tickets}
    assert len(priorities) >= 3


def test_generated_has_multiple_categories():
    p = _tmp_csv()
    generate(rows=200, seed=1, start_date="2026-01-05", weeks=8, output=str(p))
    result = ingest_canonical_csv(str(p))
    categories = {t.category for t in result.tickets}
    assert len(categories) >= 5


def test_generated_has_reopened_tickets():
    p = _tmp_csv()
    generate(rows=200, seed=1, start_date="2026-01-05", weeks=8, output=str(p))
    result = ingest_canonical_csv(str(p))
    reopened = [t for t in result.tickets if t.reopen_count > 0]
    assert len(reopened) > 0


def test_generated_spans_multiple_weeks():
    p = _tmp_csv()
    generate(rows=200, seed=1, start_date="2026-01-05", weeks=8, output=str(p))
    result = ingest_canonical_csv(str(p))
    weeks = {t.created_at.isocalendar().week for t in result.tickets}
    assert len(weeks) >= 3


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_refuses_existing_output():
    p = _tmp_csv()
    p.write_text("existing")
    with __import__("pytest").raises(FileExistsError, match="already exists"):
        generate(rows=10, seed=1, start_date="2026-01-05", weeks=1, output=str(p))


def test_refuses_zero_rows():
    p = _tmp_csv()
    with __import__("pytest").raises(ValueError, match="rows"):
        generate(rows=0, seed=1, start_date="2026-01-05", weeks=1, output=str(p))


def test_refuses_zero_weeks():
    p = _tmp_csv()
    with __import__("pytest").raises(ValueError, match="weeks"):
        generate(rows=10, seed=1, start_date="2026-01-05", weeks=0, output=str(p))


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


def test_cli_generate_synthetic_produces_valid_csv():
    tmp = tempfile.mkdtemp()
    out = pathlib.Path(tmp) / "syn.csv"

    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "service_desk_insights",
            "generate-synthetic",
            "--rows",
            "100",
            "--seed",
            "42",
            "--start-date",
            "2026-01-05",
            "--weeks",
            "4",
            "--output",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, f"stderr: {cp.stderr}"
    assert out.is_file()
    assert "Generated 100 tickets" in cp.stdout

    # Verify it passes the validator.
    result = ingest_canonical_csv(str(out))
    assert len(result.errors) == 0
    assert len(result.tickets) == 100


def test_cli_generate_synthetic_refuses_existing():
    tmp = tempfile.mkdtemp()
    out = pathlib.Path(tmp) / "syn.csv"
    out.write_text("existing")

    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "service_desk_insights",
            "generate-synthetic",
            "--rows",
            "10",
            "--seed",
            "1",
            "--start-date",
            "2026-01-05",
            "--weeks",
            "1",
            "--output",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 4
    assert "already exists" in cp.stderr


def test_cli_generate_synthetic_missing_required_args():
    cp = subprocess.run(
        [sys.executable, "-m", "service_desk_insights", "generate-synthetic"],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 2
