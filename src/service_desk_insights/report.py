"""Output renderers: terminal summary, versioned JSON and CSV tables.

Every renderer consumes ``MetricResult`` and writes to a text stream.
No business logic lives here.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from typing import TextIO

from service_desk_insights import __version__
from service_desk_insights.metrics import MetricResult
from service_desk_insights.models import Ticket


def terminal_summary(result: MetricResult, *, file: TextIO | None = None) -> None:
    """Write a concise human-readable report to *file* (default stdout)."""
    f = file if file is not None else sys.stdout

    def _w(line: str = "") -> None:
        f.write(line + "\n")

    _w("Service Desk Insights Report")
    _w("=" * 60)
    _w(f"  Source:         {result.input_basename}")
    _w(f"  As-of:          {result.as_of}")
    _w(f"  Tickets:        {result.ticket_count}")
    _w()

    _w("SLA Performance")
    _w("-" * 40)
    for row in result.sla_by_priority:
        _w(f"  {row.priority}:")
        resp = row.response
        if resp.eligible > 0:
            _w(
                f"    Response:   {resp.compliance_pct}%  "
                f"({resp.met}/{resp.eligible} met, {resp.pending} pending)"
            )
        else:
            _w("    Response:   no eligible tickets")
        res = row.resolution
        if res.eligible > 0:
            _w(
                f"    Resolution: {res.compliance_pct}%  "
                f"({res.met}/{res.eligible} met, {res.pending} pending)"
            )
        else:
            _w("    Resolution: no eligible tickets")
    _w()

    a = result.ageing
    _w(f"Open Tickets: {a.total_open}")
    if a.total_open > 0:
        _w(f"  Oldest age:  {_format_hours(a.oldest_age_hours)}")
        for b in a.buckets:
            _w(f"  {b.label:>10s}: {b.count:>3d}  ({b.pct}%)")
    _w()

    r = result.resolution
    if r.sample_size > 0:
        _w(f"Resolution Times (n={r.sample_size})")
        _w(f"  Median: {_format_hours(r.median_hours)}")
        _w(f"  P90:    {_format_hours(r.p90_hours)}")
        if r.small_sample_warning:
            _w("  (small sample — treat as indicative)")
    else:
        _w("Resolution Times: no resolved tickets")
    _w()

    ro = result.reopened
    _w(f"Reopened: {ro.count} tickets ({ro.pct}%), {ro.total_reopen_events} events")
    _w()

    if result.recurring:
        _w(f"Recurring Categories (min-count={result.recurring_min_count}):")
        for rc in result.recurring:
            _w(f"  {rc.category}: {rc.count} ({rc.share_pct}%), {rc.open_count} open")
    else:
        _w("Recurring Categories: none")
    _w()

    if result.weekly:
        _w("Weekly Trends (opened / resolved / net)")
        for wr in result.weekly:
            _w(f"  {wr.week_start}: {wr.opened:>3d} / {wr.resolved:>3d} / {wr.net_change:>+4d}")

    if result.warnings:
        _w()
        _w("Warnings:")
        for w in result.warnings:
            _w(f"  - {w}")


def json_summary(result: MetricResult) -> str:
    """Return the versioned, machine-readable JSON representation."""
    return json.dumps(_to_json_dict(result), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def html_report(result: MetricResult) -> str:
    """Render the self-contained, static HTML report."""
    import pathlib

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    template_dir = pathlib.Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("report.html.j2")

    ctx = {
        "tool_version": __version__,
        "input_basename": result.input_basename,
        "as_of": result.as_of,
        "ticket_count": result.ticket_count,
        "input_sha256": result.input_hash,
        "policy_sha256": result.policy_hash,
        "recurring_min_count": result.recurring_min_count,
        "warnings": result.warnings,
        "oldest_age_display": _format_hours(result.ageing.oldest_age_hours),
        "sla_by_priority": [
            {
                "priority": r.priority,
                "response": {
                    "met": r.response.met,
                    "breached": r.response.breached,
                    "pending": r.response.pending,
                    "compliance_pct": r.response.compliance_pct,
                },
                "resolution": {
                    "met": r.resolution.met,
                    "breached": r.resolution.breached,
                    "pending": r.resolution.pending,
                    "compliance_pct": r.resolution.compliance_pct,
                },
            }
            for r in result.sla_by_priority
        ],
        "ageing": {
            "total_open": result.ageing.total_open,
            "buckets": [
                {"label": b.label, "count": b.count, "pct": b.pct} for b in result.ageing.buckets
            ],
        },
        "backlog": {
            "by_priority": [
                {"key": r.key, "count": r.count, "pct": r.pct} for r in result.backlog.by_priority
            ],
            "by_category": [
                {"key": r.key, "count": r.count, "pct": r.pct} for r in result.backlog.by_category
            ],
        },
        "resolution": {
            "sample_size": result.resolution.sample_size,
            "median_hours": result.resolution.median_hours,
            "p90_hours": result.resolution.p90_hours,
            "small_sample_warning": result.resolution.small_sample_warning,
        },
        "reopened": {
            "count": result.reopened.count,
            "pct": result.reopened.pct,
            "total_reopen_events": result.reopened.total_reopen_events,
        },
        "recurring": [
            {
                "category": rc.category,
                "count": rc.count,
                "share_pct": rc.share_pct,
                "open_count": rc.open_count,
            }
            for rc in result.recurring
        ],
        "weekly": [
            {
                "week_start": wr.week_start,
                "opened": wr.opened,
                "resolved": wr.resolved,
                "net_change": wr.net_change,
            }
            for wr in result.weekly
        ],
    }
    return template.render(**ctx)


# ---------------------------------------------------------------------------
# CSV tables
# ---------------------------------------------------------------------------

_CSV_FORMULA_RISK_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_formula(value: str) -> str:
    """Prefix values that could be interpreted as spreadsheet formulae."""
    if value and value[0] in _CSV_FORMULA_RISK_PREFIXES:
        return "'" + value
    return value


def _format_pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.1f}"


def csv_tables(
    result: MetricResult,
    tickets: tuple[Ticket, ...],
    open_tickets: list[Ticket],
    directory: str,
) -> None:
    """Write all aggregate CSV tables plus the operational open_tickets.csv."""
    os.makedirs(directory, exist_ok=True)

    # sla_by_priority.csv
    with open(
        os.path.join(directory, "sla_by_priority.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "priority",
                "response_met",
                "response_breached",
                "response_pending",
                "response_eligible",
                "response_compliance_pct",
                "resolution_met",
                "resolution_breached",
                "resolution_pending",
                "resolution_eligible",
                "resolution_compliance_pct",
            ]
        )
        for r in result.sla_by_priority:
            w.writerow(
                [
                    _escape_formula(r.priority),
                    r.response.met,
                    r.response.breached,
                    r.response.pending,
                    r.response.eligible,
                    _format_pct(r.response.compliance_pct),
                    r.resolution.met,
                    r.resolution.breached,
                    r.resolution.pending,
                    r.resolution.eligible,
                    _format_pct(r.resolution.compliance_pct),
                ]
            )

    # ageing_buckets.csv
    with open(
        os.path.join(directory, "ageing_buckets.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["label", "count", "pct"])
        for b in result.ageing.buckets:
            w.writerow([_escape_formula(b.label), b.count, _format_pct(b.pct)])

    # backlog_by_priority.csv
    with open(
        os.path.join(directory, "backlog_by_priority.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["priority", "count", "pct"])
        for r in result.backlog.by_priority:
            w.writerow([_escape_formula(r.key), r.count, _format_pct(r.pct)])

    # backlog_by_category.csv
    with open(
        os.path.join(directory, "backlog_by_category.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["category", "count", "pct"])
        for r in result.backlog.by_category:
            w.writerow([_escape_formula(r.key), r.count, _format_pct(r.pct)])

    # resolution_times.csv
    with open(
        os.path.join(directory, "resolution_times.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        r = result.resolution
        w.writerow(["sample_size", "median_hours", "p90_hours", "small_sample_warning"])
        w.writerow(
            [
                r.sample_size,
                f"{r.median_hours:.2f}" if r.median_hours is not None else "",
                f"{r.p90_hours:.2f}" if r.p90_hours is not None else "",
                r.small_sample_warning,
            ]
        )

    # reopened_summary.csv
    with open(
        os.path.join(directory, "reopened_summary.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["count", "pct", "total_reopen_events"])
        w.writerow(
            [
                result.reopened.count,
                _format_pct(result.reopened.pct),
                result.reopened.total_reopen_events,
            ]
        )

    # recurring_categories.csv
    with open(
        os.path.join(directory, "recurring_categories.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["category", "count", "share_pct", "open_count"])
        for rc in result.recurring:
            w.writerow(
                [
                    _escape_formula(rc.category),
                    rc.count,
                    _format_pct(rc.share_pct),
                    rc.open_count,
                ]
            )

    # weekly_trends.csv
    with open(
        os.path.join(directory, "weekly_trends.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["week_start", "opened", "resolved", "net_change"])
        for wr in result.weekly:
            w.writerow([wr.week_start, wr.opened, wr.resolved, wr.net_change])

    # open_tickets.csv
    with open(os.path.join(directory, "open_tickets.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["ticket_id", "status", "priority", "category", "created_at", "age_hours", "age_days"]
        )
        for t in open_tickets:
            age_hours = None
            age_days = None
            try:
                as_of_str = result.as_of.replace("Z", "+00:00")
                from datetime import datetime as _dt

                as_of_dt = _dt.fromisoformat(as_of_str)
                age_hours = round((as_of_dt - t.created_at).total_seconds() / 3600.0, 2)
                age_days = round(age_hours / 24.0, 2)
            except Exception:
                pass
            w.writerow(
                [
                    t.ticket_id,
                    _escape_formula(t.status),
                    _escape_formula(t.priority),
                    _escape_formula(t.category),
                    t.created_at.isoformat().replace("+00:00", "Z"),
                    f"{age_hours:.2f}" if age_hours is not None else "",
                    f"{age_days:.2f}" if age_days is not None else "",
                ]
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_hours(hours: float | None) -> str:
    if hours is None:
        return "N/A"
    if hours < 1:
        return f"{hours:.2f}h"
    if hours < 24:
        return f"{hours:.1f}h"
    days = hours / 24
    return f"{days:.1f}d ({hours:.1f}h)"


def _to_json_dict(result: MetricResult) -> dict:
    return {
        "schema_version": 1,
        "tool_version": __version__,
        "generated_at": result.as_of,
        "input_basename": result.input_basename,
        "input_sha256": result.input_hash,
        "ticket_count": result.ticket_count,
        "as_of": result.as_of,
        "policy_sha256": result.policy_hash,
        "recurring_min_count": result.recurring_min_count,
        "warnings": list(result.warnings),
        "sla_by_priority": [
            {
                "priority": r.priority,
                "response": {
                    "met": r.response.met,
                    "breached": r.response.breached,
                    "pending": r.response.pending,
                    "eligible": r.response.eligible,
                    "compliance_pct": r.response.compliance_pct,
                },
                "resolution": {
                    "met": r.resolution.met,
                    "breached": r.resolution.breached,
                    "pending": r.resolution.pending,
                    "eligible": r.resolution.eligible,
                    "compliance_pct": r.resolution.compliance_pct,
                },
            }
            for r in result.sla_by_priority
        ],
        "open_ageing": {
            "total_open": result.ageing.total_open,
            "oldest_age_hours": result.ageing.oldest_age_hours,
            "buckets": [
                {"label": b.label, "count": b.count, "pct": b.pct} for b in result.ageing.buckets
            ],
        },
        "backlog": {
            "by_priority": [
                {"priority": r.key, "count": r.count, "pct": r.pct}
                for r in result.backlog.by_priority
            ],
            "by_category": [
                {"category": r.key, "count": r.count, "pct": r.pct}
                for r in result.backlog.by_category
            ],
        },
        "resolution_times": {
            "median_hours": result.resolution.median_hours,
            "p90_hours": result.resolution.p90_hours,
            "sample_size": result.resolution.sample_size,
            "small_sample_warning": result.resolution.small_sample_warning,
        },
        "reopened": {
            "count": result.reopened.count,
            "pct": result.reopened.pct,
            "total_reopen_events": result.reopened.total_reopen_events,
        },
        "recurring_categories": [
            {
                "category": rc.category,
                "count": rc.count,
                "share_pct": rc.share_pct,
                "open_count": rc.open_count,
            }
            for rc in result.recurring
        ],
        "weekly_trends": [
            {
                "week_start": wr.week_start,
                "opened": wr.opened,
                "resolved": wr.resolved,
                "net_change": wr.net_change,
            }
            for wr in result.weekly
        ],
    }
