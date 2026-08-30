"""Tests for column mapping, SLA policy parsing and mapped CSV ingest (T3)."""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from service_desk_insights.ingest import ingest_mapped_csv
from service_desk_insights.policy import parse_column_map, parse_sla_policy

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
MAP_PATH = FIXTURES / "column-map.json"
SLA_PATH = FIXTURES / "sla-policy.json"
MAPPED_CSV_PATH = FIXTURES / "mapped_tickets.csv"


def _write_json(name: str, content: str) -> pathlib.Path:
    p = pathlib.Path(tempfile.mkdtemp()) / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Column map — valid
# ---------------------------------------------------------------------------


def test_parse_valid_column_map():
    cm = parse_column_map(str(MAP_PATH))
    assert cm.schema_version == 1
    assert cm.mapping["ticket_id"] == "Case Number"
    assert cm.mapping["status"] == "State"


def test_column_map_inverse():
    cm = parse_column_map(str(MAP_PATH))
    inv = cm.source_to_canonical()
    assert inv["Case Number"] == "ticket_id"


def test_column_map_canonical_names():
    cm = parse_column_map(str(MAP_PATH))
    assert "ticket_id" in cm.canonical_names
    assert len(cm.canonical_names) == 8


# ---------------------------------------------------------------------------
# Column map — invalid
# ---------------------------------------------------------------------------


def test_column_map_rejects_wrong_schema_version():
    content = '{"schema_version": 2, "columns": {}}'
    p = _write_json("bad_ver.json", content)
    with pytest.raises(ValueError, match="schema version"):
        parse_column_map(str(p))


def test_column_map_rejects_missing_keys():
    content = '{"schema_version": 1, "columns": {"ticket_id": "ID"}}'
    p = _write_json("missing.json", content)
    with pytest.raises(ValueError, match="missing canonical keys"):
        parse_column_map(str(p))


def test_column_map_rejects_unknown_keys():
    content = """{
        "schema_version": 1,
        "columns": {
            "ticket_id": "ID",
            "created_at": "Opened",
            "first_response_at": "First",
            "resolved_at": "Resolved",
            "status": "State",
            "priority": "P",
            "category": "Cat",
            "reopen_count": "R",
            "extra_field": "Extra"
        }
    }"""
    p = _write_json("unknown.json", content)
    with pytest.raises(ValueError, match="unknown canonical keys"):
        parse_column_map(str(p))


def test_column_map_rejects_duplicate_source_headings():
    content = """{
        "schema_version": 1,
        "columns": {
            "ticket_id": "ID",
            "created_at": "ID",
            "first_response_at": "First",
            "resolved_at": "Resolved",
            "status": "State",
            "priority": "P",
            "category": "Cat",
            "reopen_count": "R"
        }
    }"""
    p = _write_json("dup_src.json", content)
    with pytest.raises(ValueError, match="Duplicate source heading"):
        parse_column_map(str(p))


def test_column_map_rejects_non_dict():
    content = "[1, 2, 3]"
    p = _write_json("array.json", content)
    with pytest.raises(ValueError, match="JSON object"):
        parse_column_map(str(p))


def test_column_map_rejects_missing_columns_key():
    content = '{"schema_version": 1}'
    p = _write_json("no_cols.json", content)
    with pytest.raises(ValueError, match="columns"):
        parse_column_map(str(p))


# ---------------------------------------------------------------------------
# SLA policy — valid
# ---------------------------------------------------------------------------


def test_parse_valid_sla_policy():
    sla = parse_sla_policy(str(SLA_PATH))
    assert sla.schema_version == 1
    assert sla.clock == "elapsed"
    assert sla.priority_order == ("P1", "P2", "P3", "P4")
    assert sla.open_statuses == ("open", "pending")
    assert sla.closed_statuses == ("resolved", "closed")
    assert sla.targets["P1"].first_response_hours == 1
    assert sla.targets["P1"].resolution_hours == 4


# ---------------------------------------------------------------------------
# SLA policy — invalid
# ---------------------------------------------------------------------------


def test_sla_rejects_bad_schema_version():
    content = '{"schema_version": 99, "clock": "elapsed"}'
    p = _write_json("bad_sla_ver.json", content)
    with pytest.raises(ValueError, match="schema version"):
        parse_sla_policy(str(p))


def test_sla_rejects_unsupported_clock():
    content = """{
        "schema_version": 1,
        "clock": "business-hours",
        "priority_order": ["P1"],
        "open_statuses": ["open"],
        "closed_statuses": ["closed"],
        "targets": {"P1": {"first_response_hours": 1, "resolution_hours": 4}}
    }"""
    p = _write_json("bad_clock.json", content)
    with pytest.raises(ValueError, match="clock"):
        parse_sla_policy(str(p))


def test_sla_rejects_overlapping_statuses():
    content = """{
        "schema_version": 1,
        "clock": "elapsed",
        "priority_order": ["P1"],
        "open_statuses": ["open", "resolved"],
        "closed_statuses": ["resolved", "closed"],
        "targets": {"P1": {"first_response_hours": 1, "resolution_hours": 4}}
    }"""
    p = _write_json("overlap.json", content)
    with pytest.raises(ValueError, match="overlap"):
        parse_sla_policy(str(p))


def test_sla_rejects_missing_target_for_priority():
    content = """{
        "schema_version": 1,
        "clock": "elapsed",
        "priority_order": ["P1", "P2"],
        "open_statuses": ["open"],
        "closed_statuses": ["closed"],
        "targets": {"P1": {"first_response_hours": 1, "resolution_hours": 4}}
    }"""
    p = _write_json("missing_target.json", content)
    with pytest.raises(ValueError, match="missing from targets"):
        parse_sla_policy(str(p))


def test_sla_rejects_zero_target_hours():
    content = """{
        "schema_version": 1,
        "clock": "elapsed",
        "priority_order": ["P1"],
        "open_statuses": ["open"],
        "closed_statuses": ["closed"],
        "targets": {"P1": {"first_response_hours": 0, "resolution_hours": 4}}
    }"""
    p = _write_json("zero_target.json", content)
    with pytest.raises(ValueError, match="positive"):
        parse_sla_policy(str(p))


def test_sla_rejects_empty_open_statuses():
    content = """{
        "schema_version": 1,
        "clock": "elapsed",
        "priority_order": ["P1"],
        "open_statuses": [],
        "closed_statuses": ["closed"],
        "targets": {"P1": {"first_response_hours": 1, "resolution_hours": 4}}
    }"""
    p = _write_json("empty_open.json", content)
    with pytest.raises(ValueError, match="must not be empty"):
        parse_sla_policy(str(p))


def test_sla_rejects_non_dict_target():
    content = """{
        "schema_version": 1,
        "clock": "elapsed",
        "priority_order": ["P1"],
        "open_statuses": ["open"],
        "closed_statuses": ["closed"],
        "targets": "not-an-object"
    }"""
    p = _write_json("non_dict_target.json", content)
    with pytest.raises(ValueError, match="targets"):
        parse_sla_policy(str(p))


def test_sla_rejects_extra_targets():
    content = """{
        "schema_version": 1,
        "clock": "elapsed",
        "priority_order": ["P1"],
        "open_statuses": ["open"],
        "closed_statuses": ["closed"],
        "targets": {
            "P1": {"first_response_hours": 1, "resolution_hours": 4},
            "P2": {"first_response_hours": 4, "resolution_hours": 12}
        }
    }"""
    p = _write_json("extra_target.json", content)
    with pytest.raises(ValueError, match="not in priority_order"):
        parse_sla_policy(str(p))


# ---------------------------------------------------------------------------
# Mapped CSV ingest
# ---------------------------------------------------------------------------


def test_mapped_ingest_produces_same_tickets_as_canonical():
    from service_desk_insights.ingest import ingest_canonical_csv

    cm = parse_column_map(str(MAP_PATH))
    mapped = ingest_mapped_csv(str(MAPPED_CSV_PATH), cm)
    assert len(mapped.errors) == 0, [e.message for e in mapped.errors]
    assert len(mapped.tickets) == 3

    # Compare ticket IDs and resolved status with canonical fixture first 3 rows.
    canonical = ingest_canonical_csv(str(FIXTURES / "canonical_tickets.csv"))
    c_first3 = canonical.tickets[:3]
    for ct, mt in zip(c_first3, mapped.tickets, strict=False):
        assert ct.ticket_id == mt.ticket_id
        assert ct.resolved_at == mt.resolved_at


def test_mapped_ingest_ignores_unmapped_columns():
    cm = parse_column_map(str(MAP_PATH))
    result = ingest_mapped_csv(str(MAPPED_CSV_PATH), cm)
    assert "Notes" in result.ignored_columns


def test_mapped_ingest_no_unmapped_values_leak():
    """The Notes column contains 'some note' on row 1 — it must not appear
    in any ticket field."""
    cm = parse_column_map(str(MAP_PATH))
    result = ingest_mapped_csv(str(MAPPED_CSV_PATH), cm)
    ticket = result.tickets[0]
    assert ticket.category == "network"
    assert "some note" not in ticket.category
    assert "some note" not in ticket.status
    assert "some note" not in ticket.priority


def test_mapped_ingest_missing_source_heading_errors():
    """When a mapped source heading is absent from the CSV, ingest produces an error."""
    # Column map references a heading the CSV doesn't have — but the fixture
    # has all mapped columns.  Test a column map with a heading not in the CSV.
    content = """{
        "schema_version": 1,
        "columns": {
            "ticket_id": "Case Number",
            "created_at": "Opened UTC",
            "first_response_at": "First Reply UTC",
            "resolved_at": "Resolved UTC",
            "status": "State",
            "priority": "NO_SUCH_COLUMN",
            "category": "Issue Type",
            "reopen_count": "Reopens"
        }
    }"""
    p = _write_json("bad_map.json", content)
    cm = parse_column_map(str(p))
    result = ingest_mapped_csv(str(MAPPED_CSV_PATH), cm)
    assert len(result.errors) > 0
    assert any("missing source headings" in e.message for e in result.errors)
