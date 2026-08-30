"""Tests for metric calculations (T4)."""

from __future__ import annotations

import datetime
import hashlib

import pytest

from service_desk_insights.ingest import ingest_canonical_csv
from service_desk_insights.metrics import calculate_metrics
from service_desk_insights.policy import parse_sla_policy

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def _policy():
    return parse_sla_policy(str(FIXTURES / "sla-policy.json"))


def _canonical_metrics():
    result = ingest_canonical_csv(str(FIXTURES / "canonical_tickets.csv"))
    assert len(result.errors) == 0
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    return calculate_metrics(
        tickets=result.tickets,
        policy=_policy(),
        as_of=as_of,
        input_hash=result.input_sha256,
        input_basename=result.source_basename,
        policy_hash=hashlib.sha256((FIXTURES / "sla-policy.json").read_bytes()).hexdigest(),
    )


def test_sla_bands_have_correct_totals():
    m = _canonical_metrics()
    # P2 tickets: T-001(28h>12h→breached), T-005(~116h>12h→breached)
    p2 = [r for r in m.sla_by_priority if r.priority == "P2"][0]
    assert p2.resolution.met == 0
    assert p2.resolution.breached == 2
    assert p2.resolution.pending == 0
    assert p2.resolution.compliance_pct == 0.0


def test_pending_tickets_excluded_from_denominator():
    m = _canonical_metrics()
    # T-002 P1: first_response at 08:45 (deadline 09:00) → met.
    # No resolution, as_of way past deadline → resolution breached.
    p1 = [r for r in m.sla_by_priority if r.priority == "P1"][0]
    assert p1.response.met == 1
    assert p1.response.breached == 0
    assert p1.resolution.breached == 1


def test_no_response_but_within_deadline_is_pending():
    from service_desk_insights.models import Ticket

    now = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    t = Ticket(
        ticket_id="T-FRESH",
        created_at=datetime.datetime(2026, 8, 30, 11, 30, 0, tzinfo=datetime.UTC),
        first_response_at=None,
        resolved_at=None,
        status="open",
        priority="P1",
        category="test",
        reopen_count=0,
    )
    m = calculate_metrics(tickets=(t,), policy=_policy(), as_of=now)
    p1 = [r for r in m.sla_by_priority if r.priority == "P1"][0]
    assert p1.response.pending == 1
    assert p1.response.eligible == 0
    assert p1.response.compliance_pct is None


def test_ageing_bucket_coverage():
    m = _canonical_metrics()
    assert m.ageing.total_open == 2
    bucket_labels = {b.label for b in m.ageing.buckets}
    assert bucket_labels == {
        "<1 day",
        "1-2 days",
        "3-7 days",
        "8-14 days",
        "15-30 days",
        ">30 days",
    }


def test_oldest_age():
    m = _canonical_metrics()
    assert m.ageing.oldest_age_hours is not None
    assert m.ageing.oldest_age_hours > 600


def test_resolution_median_and_p90():
    m = _canonical_metrics()
    assert m.resolution.sample_size == 3
    assert m.resolution.median_hours == 28.0
    # Nearest-rank p90: ceil(0.90*3)=3 → index 2 = 116
    assert m.resolution.p90_hours == pytest.approx(116.0, abs=0.5)
    assert m.resolution.small_sample_warning is True


def test_reopened_counts():
    m = _canonical_metrics()
    assert m.reopened.count == 2
    assert m.reopened.total_reopen_events == 3
    assert m.reopened.pct == 40.0


def test_recurring_categories_with_threshold():
    m = _canonical_metrics()
    assert len(m.recurring) == 0


def test_recurring_threshold_2():
    import datetime as _dt

    result = ingest_canonical_csv(str(FIXTURES / "canonical_tickets.csv"))
    as_of = _dt.datetime(2026, 8, 30, 12, 0, 0, tzinfo=_dt.UTC)
    m = calculate_metrics(
        tickets=result.tickets, policy=_policy(), as_of=as_of, recurring_min_count=2
    )
    assert len(m.recurring) >= 1
    net = [r for r in m.recurring if r.category == "network"][0]
    assert net.count == 2
    assert net.open_count == 0


def test_weekly_zero_filled():
    m = _canonical_metrics()
    assert len(m.weekly) >= 4
    for wr in m.weekly:
        assert wr.opened >= 0
        assert wr.resolved >= 0


def test_net_change_is_opened_minus_resolved():
    m = _canonical_metrics()
    for wr in m.weekly:
        assert wr.net_change == wr.opened - wr.resolved


def test_empty_tickets_returns_sane_result():
    as_of = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.UTC)
    m = calculate_metrics(tickets=(), policy=_policy(), as_of=as_of)
    assert m.ticket_count == 0
    assert m.ageing.total_open == 0
    assert m.ageing.oldest_age_hours is None
    assert m.resolution.median_hours is None
    assert m.resolution.sample_size == 0
    assert m.reopened.count == 0
    assert m.reopened.pct is None
