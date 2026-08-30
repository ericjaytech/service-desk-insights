"""Tests for canonical CSV ingestion and validation (T2).

Covers valid ingestion, timestamp normalisation, duplicate detection,
malformed timestamps, empty values, limit enforcement, BOM handling and
the complete-run validation contract.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from service_desk_insights.ingest import CANONICAL_HEADERS, ingest_canonical_csv
from service_desk_insights.models import IngestLimits

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CANONICAL_PATH = FIXTURES / "canonical_tickets.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(name: str, *lines: str) -> pathlib.Path:
    """Write a temporary CSV and return its path."""
    p = pathlib.Path(tempfile.mkdtemp()) / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _assert_no_errors(result):
    assert len(result.errors) == 0, f"Unexpected errors: {[e.message for e in result.errors]}"


# ---------------------------------------------------------------------------
# Valid ingestion
# ---------------------------------------------------------------------------


def test_ingests_canonical_fixture():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    _assert_no_errors(result)
    assert len(result.tickets) == 5
    assert result.input_rows == 5


def test_returns_frozen_tickets():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    for ticket in result.tickets:
        assert ticket.created_at.tzinfo is not None
        assert ticket.created_at.utcoffset().total_seconds() == 0


def test_empty_reopen_count_becomes_zero():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    t4 = [t for t in result.tickets if t.ticket_id == "T-004"][0]
    assert t4.reopen_count == 0


def test_null_timestamps_become_none():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    t4 = [t for t in result.tickets if t.ticket_id == "T-004"][0]
    assert t4.first_response_at is None
    assert t4.resolved_at is None

    t2 = [t for t in result.tickets if t.ticket_id == "T-002"][0]
    assert t2.first_response_at is not None
    assert t2.resolved_at is None


def test_timestamps_normalised_to_utc():
    """Non-UTC offsets are normalised to UTC during parsing."""
    csv_path = _write_csv(
        "offset.csv",
        ",".join(CANONICAL_HEADERS),
        "T-OFF,2026-08-01T10:00:00+02:00,2026-08-01T12:30:00+02:00,2026-08-02T16:00:00+02:00,resolved,P2,network,0",
    )
    result = ingest_canonical_csv(str(csv_path))
    _assert_no_errors(result)
    t = result.tickets[0]
    # 10:00 +02:00 → 08:00 UTC
    assert t.created_at.hour == 8
    assert t.created_at.utcoffset().total_seconds() == 0


def test_source_basename_preserved():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    assert result.source_basename == "canonical_tickets.csv"


def test_input_sha256_is_hex():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    assert len(result.input_sha256) == 64
    int(result.input_sha256, 16)  # raises if not hex


def test_deterministic_hash():
    a = ingest_canonical_csv(str(CANONICAL_PATH)).input_sha256
    b = ingest_canonical_csv(str(CANONICAL_PATH)).input_sha256
    assert a == b


def test_is_open_and_is_resolved():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    open_ids = {t.ticket_id for t in result.tickets if t.is_open}
    resolved_ids = {t.ticket_id for t in result.tickets if t.is_resolved}
    assert open_ids == {"T-002", "T-004"}
    assert resolved_ids == {"T-001", "T-003", "T-005"}


def test_is_reopened():
    result = ingest_canonical_csv(str(CANONICAL_PATH))
    reopened = {t.ticket_id for t in result.tickets if t.is_reopened}
    assert reopened == {"T-003", "T-005"}


# ---------------------------------------------------------------------------
# BOM handling
# ---------------------------------------------------------------------------


def test_utf8_bom_accepted():
    raw = b"\xef\xbb\xbf" + CANONICAL_PATH.read_bytes()
    p = _write_csv("bom.csv", "")
    p.write_bytes(raw)
    result = ingest_canonical_csv(str(p))
    _assert_no_errors(result)
    assert len(result.tickets) == 5


# ---------------------------------------------------------------------------
# Header validation
# ---------------------------------------------------------------------------


def test_wrong_header_order_fails():
    lines = [
        "ticket_id,status,created_at,first_response_at,resolved_at,priority,category,reopen_count",
        "T-001,open,2026-08-01T10:00:00Z,,,P2,network,0",
    ]
    p = _write_csv("wrong_order.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("Canonical headers expected" in e.message for e in result.errors)


def test_extra_column_fails():
    lines = [
        "ticket_id,created_at,first_response_at,resolved_at,status,priority,category,reopen_count,extra_col",
        "T-001,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0,x",
    ]
    p = _write_csv("extra_col.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0


def test_missing_column_fails():
    lines = [
        "ticket_id,created_at,first_response_at,resolved_at,status,priority,category",
        "T-001,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network",
    ]
    p = _write_csv("missing_col.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Duplicate ticket IDs
# ---------------------------------------------------------------------------


def test_duplicate_id_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-001,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0",
        "T-001,2026-08-03T10:00:00Z,,,open,P3,email,0",
    ]
    p = _write_csv("dup_id.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("duplicate" in e.message.lower() for e in result.errors)


def test_empty_ticket_id_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        ",2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0",
    ]
    p = _write_csv("empty_id.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("ticket_id is empty" in e.message for e in result.errors)


# ---------------------------------------------------------------------------
# Timestamp validation
# ---------------------------------------------------------------------------


def test_malformed_timestamp_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-BAD,not-a-date,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0",
    ]
    p = _write_csv("bad_ts.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("created_at" in e.message for e in result.errors)


def test_naive_timestamp_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-NAIVE,2026-08-01T10:00:00,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0",
    ]
    p = _write_csv("naive_ts.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("naive" in e.message.lower() for e in result.errors)


def test_empty_category_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-CAT,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,,0",
    ]
    p = _write_csv("empty_cat.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("category is empty" in e.message for e in result.errors)


def test_negative_reopen_count_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-NEG,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,-1",
    ]
    p = _write_csv("neg_reopen.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("reopen_count" in e.message for e in result.errors)


def test_non_integer_reopen_count_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-STR,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,abc",
    ]
    p = _write_csv("str_reopen.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("reopen_count must be an integer" in e.message for e in result.errors)


def test_missing_created_at_fails():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-MC,,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0",
    ]
    p = _write_csv("missing_created.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) == 1
    assert "created_at is required" in result.errors[0].message


# ---------------------------------------------------------------------------
# Limits enforcement
# ---------------------------------------------------------------------------


def test_exceeds_max_rows_raises():
    limits = IngestLimits(max_rows=2)
    with pytest.raises(ValueError, match="exceeds"):
        ingest_canonical_csv(str(CANONICAL_PATH), limits=limits)


def test_wrong_column_count_per_row():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-SHORT,2026-08-01T10:00:00Z",  # too few fields
    ]
    p = _write_csv("short_row.csv", *lines)
    result = ingest_canonical_csv(str(p))
    assert len(result.errors) > 0
    assert any("Expected 8 fields" in e.message for e in result.errors)


def test_error_suppression_cap():
    """When errors exceed max_validation_errors_reported, the excess is suppressed."""
    limits = IngestLimits(max_validation_errors_reported=2)
    lines = [",".join(CANONICAL_HEADERS)]
    # Create multiple rows with duplicate ID to generate errors.
    lines.append(
        "T-DUP,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0"
    )
    lines.append("T-DUP,2026-08-03T10:00:00Z,,,open,P3,email,0")
    lines.append("T-DUP,2026-08-04T10:00:00Z,,,open,P3,email,0")
    lines.append("T-DUP,2026-08-05T10:00:00Z,,,open,P3,email,0")
    p = _write_csv("suppress.csv", *lines)
    result = ingest_canonical_csv(str(p), limits=limits)
    assert len(result.errors) == 2
    assert result.errors_suppressed > 0


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_csv_header_only():
    lines = [",".join(CANONICAL_HEADERS)]
    p = _write_csv("header_only.csv", *lines)
    result = ingest_canonical_csv(str(p))
    _assert_no_errors(result)
    assert len(result.tickets) == 0
    assert result.input_rows == 0


# ---------------------------------------------------------------------------
# Error messages do not expose raw values
# ---------------------------------------------------------------------------


def test_errors_do_not_expose_field_values_except_id():
    """Validation error messages must not include arbitrary field values,
    only the ticket_id (which is an opaque identifier)."""
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-VAL,not-a-date,,,resolved,P2,network,0",
    ]
    p = _write_csv("msg_safety.csv", *lines)
    result = ingest_canonical_csv(str(p))
    for err in result.errors:
        # "not-a-date" should NOT appear — only field name references.
        assert "not-a-date" not in err.message, f"Error message exposed raw value: {err.message}"


def test_dup_error_includes_ticket_id():
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-X,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,network,0",
        "T-X,2026-08-03T10:00:00Z,,,open,P3,email,0",
    ]
    p = _write_csv("dup_x.csv", *lines)
    result = ingest_canonical_csv(str(p))
    dup_errors = [e for e in result.errors if "duplicate" in e.message.lower()]
    assert len(dup_errors) > 0
    assert "T-X" in dup_errors[0].message


# ---------------------------------------------------------------------------
# Field size limit
# ---------------------------------------------------------------------------


def test_oversized_field_fails():
    limits = IngestLimits(max_field_bytes=4)
    lines = [
        ",".join(CANONICAL_HEADERS),
        "T-BIG,2026-08-01T10:00:00Z,2026-08-01T10:30:00Z,2026-08-02T14:00:00Z,resolved,P2,BIGCAT,0",
    ]
    p = _write_csv("big_field.csv", *lines)
    result = ingest_canonical_csv(str(p), limits=limits)
    assert len(result.errors) > 0
    assert any("exceeds" in e.message for e in result.errors)
