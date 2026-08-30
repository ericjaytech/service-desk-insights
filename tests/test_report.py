"""Tests for terminal and JSON report output (T4)."""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import pathlib
import tempfile

from service_desk_insights import __version__
from service_desk_insights.ingest import ingest_canonical_csv
from service_desk_insights.metrics import calculate_metrics
from service_desk_insights.policy import parse_sla_policy
from service_desk_insights.report import json_summary, terminal_summary

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _policy():
    return parse_sla_policy(str(FIXTURES / "sla-policy.json"))


def _metrics():
    result = ingest_canonical_csv(str(FIXTURES / "canonical_tickets.csv"))
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    return calculate_metrics(
        tickets=result.tickets,
        policy=_policy(),
        as_of=as_of,
        input_hash=result.input_sha256,
        input_basename=result.source_basename,
        policy_hash=hashlib.sha256((FIXTURES / "sla-policy.json").read_bytes()).hexdigest(),
    )


def test_terminal_summary_contains_key_sections():
    buf = io.StringIO()
    terminal_summary(_metrics(), file=buf)
    text = buf.getvalue()
    assert "Service Desk Insights Report" in text
    assert "SLA Performance" in text
    assert "Open Tickets" in text
    assert "Resolution Times" in text
    assert "Reopened" in text
    assert "Recurring Categories" in text
    assert "Weekly Trends" in text


def test_terminal_summary_includes_ticket_count():
    buf = io.StringIO()
    terminal_summary(_metrics(), file=buf)
    assert "Tickets:        5" in buf.getvalue()


def test_terminal_summary_includes_source():
    buf = io.StringIO()
    terminal_summary(_metrics(), file=buf)
    assert "canonical_tickets.csv" in buf.getvalue()


def test_terminal_empty_metrics():
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    empty = calculate_metrics(tickets=(), policy=_policy(), as_of=as_of)
    buf = io.StringIO()
    terminal_summary(empty, file=buf)
    assert "Tickets:        0" in buf.getvalue()


def test_json_is_valid_and_has_schema_version():
    data = json.loads(json_summary(_metrics()))
    assert data["schema_version"] == 1
    assert data["tool_version"] == __version__


def test_json_contains_all_sections():
    data = json.loads(json_summary(_metrics()))
    for key in (
        "sla_by_priority",
        "open_ageing",
        "backlog",
        "resolution_times",
        "reopened",
        "recurring_categories",
        "weekly_trends",
    ):
        assert key in data, f"Missing key: {key}"


def test_json_ticket_count_matches():
    data = json.loads(json_summary(_metrics()))
    assert data["ticket_count"] == 5


def test_json_no_ticket_ids():
    data = json.loads(json_summary(_metrics()))
    text = json.dumps(data)
    assert "T-001" not in text
    assert "T-002" not in text


def test_json_deterministic():
    a = json_summary(_metrics())
    b = json_summary(_metrics())
    assert a == b


def test_json_null_values_for_empty_populations():
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    empty = calculate_metrics(tickets=(), policy=_policy(), as_of=as_of)
    data = json.loads(json_summary(empty))
    assert data["resolution_times"]["median_hours"] is None
    assert data["resolution_times"]["p90_hours"] is None
    assert data["open_ageing"]["oldest_age_hours"] is None
    assert data["reopened"]["pct"] is None


def test_json_contains_warnings():
    data = json.loads(json_summary(_metrics()))
    assert isinstance(data["warnings"], list)
    assert any("treat as indicative" in w.lower() for w in data["warnings"])


def test_cli_analyse_writes_output_directory():
    import subprocess
    import sys as _sys

    tmp = tempfile.mkdtemp()
    output_dir = pathlib.Path(tmp) / "report-out"

    cp = subprocess.run(
        [
            _sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, f"stderr: {cp.stderr}"
    assert output_dir.is_dir()
    assert (output_dir / "summary.json").is_file()
    assert (output_dir / "tables").is_dir()
    assert "Report written to" in cp.stdout


def test_cli_analyse_rejects_invalid_csv():
    import subprocess
    import sys as _sys

    cp = subprocess.run(
        [
            _sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "column-map.json"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--output-dir",
            "/tmp/sdi-nonexistent-dir-xyz",
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode in (2, 3), f"Got {cp.returncode}: {cp.stderr}"
    assert not pathlib.Path("/tmp/sdi-nonexistent-dir-xyz").exists()


def test_cli_analyse_existing_output_dir_fails():
    import subprocess
    import sys as _sys

    tmp = tempfile.mkdtemp()
    existing = pathlib.Path(tmp) / "exists"
    existing.mkdir()

    cp = subprocess.run(
        [
            _sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            str(existing),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 4
    assert "already exists" in cp.stderr


# ---------------------------------------------------------------------------
# CSV tables (T5)
# ---------------------------------------------------------------------------


def _csv_metrics_and_tickets():
    """Return (MetricResult, tickets, open_tickets) for CSV tests."""

    result = __import__(
        "service_desk_insights.ingest", fromlist=["ingest_canonical_csv"]
    ).ingest_canonical_csv(str(FIXTURES / "canonical_tickets.csv"))
    tickets = result.tickets
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    m = calculate_metrics(
        tickets=tickets,
        policy=_policy(),
        as_of=as_of,
        input_hash=result.input_sha256,
        input_basename=result.source_basename,
        policy_hash=hashlib.sha256((FIXTURES / "sla-policy.json").read_bytes()).hexdigest(),
    )
    open_tickets_list = [t for t in tickets if t.resolved_at is None]
    return m, tickets, open_tickets_list


def test_csv_all_nine_files_produced():
    from service_desk_insights.report import csv_tables as _csv_tables

    m, tickets, open_list = _csv_metrics_and_tickets()
    tmp = tempfile.mkdtemp()
    tables_dir = pathlib.Path(tmp) / "tables"
    _csv_tables(m, tickets, open_list, str(tables_dir))

    expected = {
        "sla_by_priority.csv",
        "ageing_buckets.csv",
        "backlog_by_priority.csv",
        "backlog_by_category.csv",
        "resolution_times.csv",
        "reopened_summary.csv",
        "recurring_categories.csv",
        "weekly_trends.csv",
        "open_tickets.csv",
    }
    actual = {p.name for p in tables_dir.iterdir()}
    assert actual == expected


def test_csv_aggregate_files_no_ticket_ids():
    from service_desk_insights.report import csv_tables as _csv_tables

    m, tickets, open_list = _csv_metrics_and_tickets()
    tmp = tempfile.mkdtemp()
    tables_dir = pathlib.Path(tmp) / "tables"
    _csv_tables(m, tickets, open_list, str(tables_dir))

    for csv_file in tables_dir.iterdir():
        if csv_file.name == "open_tickets.csv":
            continue
        content = csv_file.read_text()
        assert "T-001" not in content
        assert "T-002" not in content


def test_csv_open_tickets_has_documented_fields():
    from service_desk_insights.report import csv_tables as _csv_tables

    m, tickets, open_list = _csv_metrics_and_tickets()
    tmp = tempfile.mkdtemp()
    tables_dir = pathlib.Path(tmp) / "tables"
    _csv_tables(m, tickets, open_list, str(tables_dir))

    content = (tables_dir / "open_tickets.csv").read_text()
    lines = content.strip().split("\n")
    header = lines[0]
    assert "ticket_id" in header
    assert "status" in header
    assert "priority" in header
    assert "category" in header
    assert "created_at" in header
    assert "age_hours" in header
    assert "age_days" in header


def test_csv_formula_protection():
    """Category names starting with =, +, -, @ are prefixed with single quote."""

    from service_desk_insights.report import _escape_formula

    # Verify the escape function works.
    assert _escape_formula("=cmd|'calc'!A1") == "'=cmd|'calc'!A1"
    assert _escape_formula("+SUM(A1:A10)") == "'+SUM(A1:A10)"
    assert _escape_formula("-DDE") == "'-DDE"
    assert _escape_formula("@SUM") == "'@SUM"
    assert _escape_formula("normal") == "normal"
    assert _escape_formula("") == ""


def test_cli_analyse_produces_csv_files():
    import subprocess
    import sys as _sys

    tmp = tempfile.mkdtemp()
    output_dir = pathlib.Path(tmp) / "csv-report"

    cp = subprocess.run(
        [
            _sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, f"stderr: {cp.stderr}"

    tables = output_dir / "tables"
    assert tables.is_dir()
    assert (tables / "sla_by_priority.csv").is_file()
    assert (tables / "open_tickets.csv").is_file()
    assert (tables / "weekly_trends.csv").is_file()


# ---------------------------------------------------------------------------
# HTML report (T6)
# ---------------------------------------------------------------------------


def test_html_report_is_valid_html5():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_metrics())
    assert html.startswith("<!DOCTYPE html>")
    assert '<html lang="en">' in html
    assert "</html>" in html


def test_html_report_contains_key_sections():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_metrics())
    assert "Service Desk Insights Report" in html
    assert "SLA Performance" in html
    assert "Open Ticket Ageing" in html
    assert "Resolution Times" in html
    assert "Reopened Tickets" in html
    assert "Recurring Categories" in html
    assert "Weekly Trends" in html


def test_html_report_no_ticket_ids():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_metrics())
    assert "T-001" not in html
    assert "T-002" not in html


def test_html_report_self_contained():
    """HTML must not reference external resources (no http://, no //cdn)."""
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_metrics())
    assert "http://" not in html
    assert "https://" not in html
    assert "//cdn" not in html


def test_html_report_contains_provenance():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_metrics())
    assert "canonical_tickets.csv" in html
    assert "2026-08-30T12:00:00Z" in html


def test_html_report_contains_warnings():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_metrics())
    assert "treat as indicative" in html.lower()


def test_html_report_empty_tickets():
    from service_desk_insights.report import html_report as _html_report

    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    empty = calculate_metrics(tickets=(), policy=_policy(), as_of=as_of)
    html = _html_report(empty)
    assert "<!DOCTYPE html>" in html
    assert "0" in html  # ticket count


def test_cli_analyse_produces_html():
    import subprocess
    import sys as _sys

    tmp = tempfile.mkdtemp()
    output_dir = pathlib.Path(tmp) / "html-report"

    cp = subprocess.run(
        [
            _sys.executable,
            "-m",
            "service_desk_insights",
            "analyse",
            str(FIXTURES / "canonical_tickets.csv"),
            "--sla-policy",
            str(FIXTURES / "sla-policy.json"),
            "--as-of",
            "2026-08-30T12:00:00Z",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, f"stderr: {cp.stderr}"
    assert (output_dir / "report.html").is_file()

    html = (output_dir / "report.html").read_text()
    assert "<!DOCTYPE html>" in html
    assert "Service Desk Insights Report" in html


# ---------------------------------------------------------------------------
# Adversarial / leakage tests (T9)
# ---------------------------------------------------------------------------

_HOSTILE_FIXTURE = FIXTURES / "hostile_labels.csv"


def _hostile_metrics():
    """Return MetricResult from the hostile_labels fixture."""
    result = __import__(
        "service_desk_insights.ingest", fromlist=["ingest_canonical_csv"]
    ).ingest_canonical_csv(str(_HOSTILE_FIXTURE))
    tickets = result.tickets
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    return calculate_metrics(
        tickets=tickets,
        policy=_policy(),
        as_of=as_of,
        recurring_min_count=1,
        input_hash=result.input_sha256,
        input_basename=result.source_basename,
        policy_hash=hashlib.sha256((FIXTURES / "sla-policy.json").read_bytes()).hexdigest(),
    )


def test_html_escapes_script_tags():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_hostile_metrics())
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_escapes_img_tag():
    from service_desk_insights.report import html_report as _html_report

    html = _html_report(_hostile_metrics())
    # Jinja2 autoescape converts <img to <img.
    assert "<img " not in html
    assert "&lt;img" in html


def test_csv_formula_protection_applied_to_hostile():
    from service_desk_insights.report import csv_tables as _csv_tables

    m = _hostile_metrics()
    result = __import__(
        "service_desk_insights.ingest", fromlist=["ingest_canonical_csv"]
    ).ingest_canonical_csv(str(_HOSTILE_FIXTURE))
    tickets = result.tickets
    open_list = [t for t in tickets if t.resolved_at is None]

    tmp = tempfile.mkdtemp()
    tables_dir = pathlib.Path(tmp) / "tables"
    _csv_tables(m, tickets, open_list, str(tables_dir))

    # Check recurring_categories.csv for formula-protected values.
    recurring = (tables_dir / "recurring_categories.csv").read_text()
    assert "'=cmd" in recurring
    assert "'+SUM" in recurring
    assert "'-DDE" in recurring
    assert "'@SUM" in recurring

    # Check open_tickets.csv for formula-protected categories.
    open_csv = (tables_dir / "open_tickets.csv").read_text()
    assert "'+SUM" in open_csv
    assert "'@SUM" in open_csv


def test_json_does_not_escape_formula_prefixes():
    """JSON output should contain raw category values (not formula-escaped)."""
    from service_desk_insights.report import json_summary as _json_summary

    j = _json_summary(_hostile_metrics())
    assert "=cmd|'calc'!A1" in j
    assert "+SUM(A1:A10)" in j


def test_terminal_output_contains_hostile_categories():
    """Terminal output shows categories as-is (no injection risk in terminal)."""
    import io

    from service_desk_insights.report import terminal_summary as _terminal_summary

    buf = io.StringIO()
    _terminal_summary(_hostile_metrics(), file=buf)
    output = buf.getvalue()
    assert "=cmd|'calc'!A1" in output


def test_no_ticket_ids_in_any_output():
    """Hostile ticket IDs (H-001 etc.) must not appear in aggregate outputs."""
    from service_desk_insights.report import (
        csv_tables as _csv_tables,
    )
    from service_desk_insights.report import (
        html_report as _html_report,
    )
    from service_desk_insights.report import (
        json_summary as _json_summary,
    )

    m = _hostile_metrics()
    result = __import__(
        "service_desk_insights.ingest", fromlist=["ingest_canonical_csv"]
    ).ingest_canonical_csv(str(_HOSTILE_FIXTURE))
    tickets = result.tickets
    open_list = [t for t in tickets if t.resolved_at is None]

    # HTML
    html = _html_report(m)
    assert "H-001" not in html
    assert "H-002" not in html

    # JSON
    j = _json_summary(m)
    assert "H-001" not in j
    assert "H-002" not in j

    # Aggregate CSVs
    tmp = tempfile.mkdtemp()
    tables_dir = pathlib.Path(tmp) / "tables"
    _csv_tables(m, tickets, open_list, str(tables_dir))
    for csv_file in tables_dir.iterdir():
        if csv_file.name == "open_tickets.csv":
            continue
        content = csv_file.read_text()
        assert "H-001" not in content
        assert "H-002" not in content


def test_sql_injection_category_not_in_sqlite():
    """Category containing SQL injection attempt must not break history."""
    import sqlite3

    from service_desk_insights.history import append_run as _append_run

    m = _hostile_metrics()
    db = os.path.join(tempfile.mkdtemp(), "hostile.sqlite")
    _append_run(db, m)

    conn = sqlite3.connect(db)
    # Verify the hostile category was stored safely.
    rows = conn.execute(
        "SELECT category FROM recurring_categories WHERE category LIKE '%DROP%'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert "DROP TABLE" in rows[0][0]
