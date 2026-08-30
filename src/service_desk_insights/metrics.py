"""Metric calculations from validated tickets.

Every function takes an immutable ticket collection plus policy context and
returns frozen result objects.  No I/O, no rendering and no state mutation.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
import statistics
from collections import Counter

from service_desk_insights.models import Ticket
from service_desk_insights.policy import SlaPolicy

# ---------------------------------------------------------------------------
# SLA classification helpers
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SlaBands:
    met: int
    breached: int
    pending: int

    @property
    def eligible(self) -> int:
        return self.met + self.breached

    @property
    def compliance_pct(self) -> float | None:
        if self.eligible == 0:
            return None
        return round(self.met / self.eligible * 100, 1)


# ---------------------------------------------------------------------------
# Metric result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SlaByPriority:
    priority: str
    response: SlaBands
    resolution: SlaBands


@dataclasses.dataclass(frozen=True)
class AgeingBucket:
    label: str
    count: int
    pct: float


@dataclasses.dataclass(frozen=True)
class OpenAgeing:
    total_open: int
    buckets: tuple[AgeingBucket, ...]
    oldest_age_hours: float | None


@dataclasses.dataclass(frozen=True)
class BacklogRow:
    key: str
    count: int
    pct: float


@dataclasses.dataclass(frozen=True)
class Backlog:
    by_priority: tuple[BacklogRow, ...]
    by_category: tuple[BacklogRow, ...]


@dataclasses.dataclass(frozen=True)
class ResolutionTimes:
    median_hours: float | None
    p90_hours: float | None
    sample_size: int
    small_sample_warning: bool  # True when sample < 10


@dataclasses.dataclass(frozen=True)
class ReopenedSummary:
    count: int
    pct: float | None
    total_reopen_events: int


@dataclasses.dataclass(frozen=True)
class RecurringCategory:
    category: str
    count: int
    share_pct: float
    open_count: int


@dataclasses.dataclass(frozen=True)
class WeeklyRow:
    week_start: str  # ISO date YYYY-MM-DD
    opened: int
    resolved: int
    net_change: int


@dataclasses.dataclass(frozen=True)
class MetricResult:
    """Complete, immutable metric calculation result.

    Every renderer (terminal, JSON, CSV, HTML) reads from this object.
    """

    # Provenance
    ticket_count: int
    as_of: str  # ISO-8601 UTC
    policy_hash: str
    input_hash: str
    input_basename: str
    recurring_min_count: int
    warnings: tuple[str, ...]

    # SLA
    sla_by_priority: tuple[SlaByPriority, ...]

    # Open ageing
    ageing: OpenAgeing

    # Backlog
    backlog: Backlog

    # Resolution times
    resolution: ResolutionTimes

    # Reopened
    reopened: ReopenedSummary

    # Recurring categories
    recurring: tuple[RecurringCategory, ...]

    # Weekly trends
    weekly: tuple[WeeklyRow, ...]


# ---------------------------------------------------------------------------
# Main calculation entry point
# ---------------------------------------------------------------------------


def calculate_metrics(
    tickets: tuple[Ticket, ...],
    policy: SlaPolicy,
    as_of: datetime.datetime,
    recurring_min_count: int = 3,
    policy_hash: str = "",
    input_hash: str = "",
    input_basename: str = "",
) -> MetricResult:
    """Calculate all metrics from validated tickets and policy.

    *as_of* must be an aware UTC datetime.
    """
    _assert_utc(as_of)
    warnings: list[str] = []

    # --- SLA classification ------------------------------------------------
    sla_rows: list[SlaByPriority] = []
    for priority in policy.priority_order:
        target = policy.targets.get(priority)
        if target is None:
            continue
        response_bands = _classify_sla(
            tickets, priority, policy, as_of, "response", target.first_response_hours
        )
        resolution_bands = _classify_sla(
            tickets, priority, policy, as_of, "resolution", target.resolution_hours
        )
        sla_rows.append(
            SlaByPriority(priority=priority, response=response_bands, resolution=resolution_bands)
        )

    # --- Open ageing -------------------------------------------------------
    open_tickets = [t for t in tickets if _is_open(t, policy)]
    ageing = _calculate_ageing(open_tickets, as_of)

    # --- Backlog -----------------------------------------------------------
    backlog = _calculate_backlog(open_tickets, policy, tickets)

    # --- Resolution times --------------------------------------------------
    resolution = _calculate_resolution_times(tickets, warnings)

    # --- Reopened ----------------------------------------------------------
    reopened = _calculate_reopened(tickets)

    # --- Recurring categories ----------------------------------------------
    recurring = _calculate_recurring(tickets, recurring_min_count)

    # --- Weekly trends -----------------------------------------------------
    weekly = _calculate_weekly(tickets, as_of)

    return MetricResult(
        ticket_count=len(tickets),
        as_of=as_of.isoformat().replace("+00:00", "Z"),
        policy_hash=policy_hash,
        input_hash=input_hash,
        input_basename=input_basename,
        recurring_min_count=recurring_min_count,
        warnings=tuple(warnings),
        sla_by_priority=tuple(sla_rows),
        ageing=ageing,
        backlog=backlog,
        resolution=resolution,
        reopened=reopened,
        recurring=tuple(recurring),
        weekly=tuple(weekly),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_utc(dt: datetime.datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() != datetime.timedelta(0):
        msg = "as_of must be an aware UTC datetime."
        raise ValueError(msg)


def _is_open(ticket: Ticket, policy: SlaPolicy) -> bool:
    return ticket.status in policy.open_statuses and ticket.resolved_at is None


def _deadline(created: datetime.datetime, hours: float) -> datetime.datetime:
    return created + datetime.timedelta(hours=hours)


def _classify_sla(
    tickets: tuple[Ticket, ...],
    priority: str,
    policy: SlaPolicy,
    as_of: datetime.datetime,
    kind: str,  # 'response' or 'resolution'
    target_hours: float,
) -> SlaBands:
    met = breached = pending = 0
    for t in tickets:
        if t.priority != priority:
            continue
        deadline_dt = _deadline(t.created_at, target_hours)

        if kind == "response":
            if t.first_response_at is not None:
                if t.first_response_at <= deadline_dt:
                    met += 1
                else:
                    breached += 1
            else:
                if as_of > deadline_dt:
                    breached += 1
                else:
                    pending += 1
        else:  # resolution
            if t.resolved_at is not None:
                if t.resolved_at <= deadline_dt:
                    met += 1
                else:
                    breached += 1
            else:
                if as_of > deadline_dt:
                    breached += 1
                else:
                    pending += 1
    return SlaBands(met=met, breached=breached, pending=pending)


_AGEING_BUCKETS: tuple[tuple[float, str], ...] = (
    (1, "<1 day"),
    (2, "1-2 days"),
    (7, "3-7 days"),
    (14, "8-14 days"),
    (30, "15-30 days"),
    (float("inf"), ">30 days"),
)


def _calculate_ageing(open_tickets: list[Ticket], as_of: datetime.datetime) -> OpenAgeing:
    buckets: dict[str, int] = {label: 0 for _, label in _AGEING_BUCKETS}
    oldest: float | None = None
    for t in open_tickets:
        age_hours = (as_of - t.created_at).total_seconds() / 3600.0
        age_days = age_hours / 24.0
        if oldest is None or age_hours > oldest:
            oldest = age_hours
        for threshold, label in _AGEING_BUCKETS:
            if age_days < threshold:
                buckets[label] += 1
                break

    total = sum(buckets.values())
    bucket_rows = tuple(
        AgeingBucket(
            label=label,
            count=buckets[label],
            pct=round(buckets[label] / total * 100, 1) if total else 0.0,
        )
        for _, label in _AGEING_BUCKETS
    )
    return OpenAgeing(total_open=total, buckets=bucket_rows, oldest_age_hours=oldest)


def _calculate_backlog(
    open_tickets: list[Ticket], policy: SlaPolicy, all_tickets: tuple[Ticket, ...]
) -> Backlog:
    total_open = len(open_tickets)

    by_priority: list[BacklogRow] = []
    for p in policy.priority_order:
        count = sum(1 for t in open_tickets if t.priority == p)
        pct = round(count / total_open * 100, 1) if total_open else 0.0
        by_priority.append(BacklogRow(key=p, count=count, pct=pct))

    cat_counts = Counter(t.category for t in open_tickets)
    by_cat = sorted(cat_counts.items(), key=lambda x: (-x[1], x[0]))
    by_category = tuple(
        BacklogRow(key=cat, count=cnt, pct=round(cnt / total_open * 100, 1) if total_open else 0.0)
        for cat, cnt in by_cat
    )

    return Backlog(by_priority=tuple(by_priority), by_category=by_category)


def _calculate_resolution_times(
    tickets: tuple[Ticket, ...], warnings: list[str]
) -> ResolutionTimes:
    durations = [
        (t.resolved_at - t.created_at).total_seconds() / 3600.0
        for t in tickets
        if t.resolved_at is not None
    ]
    durations.sort()
    n = len(durations)
    if n == 0:
        return ResolutionTimes(
            median_hours=None, p90_hours=None, sample_size=0, small_sample_warning=True
        )

    median = statistics.median(durations)
    # Nearest-rank p90: ceil(0.90 * n) in 1-indexed sorted list
    rank = math.ceil(0.90 * n)
    p90 = durations[rank - 1]  # 0-indexed

    small = n < 10
    if small:
        warnings.append(f"Resolution-time p90 based on {n} tickets (<10); treat as indicative.")
    return ResolutionTimes(
        median_hours=median, p90_hours=p90, sample_size=n, small_sample_warning=small
    )


def _calculate_reopened(tickets: tuple[Ticket, ...]) -> ReopenedSummary:
    reopened_tickets = [t for t in tickets if t.reopen_count > 0]
    total = len(tickets)
    pct = round(len(reopened_tickets) / total * 100, 1) if total else None
    return ReopenedSummary(
        count=len(reopened_tickets),
        pct=pct,
        total_reopen_events=sum(t.reopen_count for t in reopened_tickets),
    )


def _calculate_recurring(
    tickets: tuple[Ticket, ...], min_count: int
) -> tuple[RecurringCategory, ...]:
    cat_counter = Counter(t.category for t in tickets)
    open_per_cat = Counter(t.category for t in tickets if t.resolved_at is None)
    total = len(tickets)

    result: list[RecurringCategory] = []
    for cat, count in cat_counter.most_common():
        if count < min_count:
            break
        result.append(
            RecurringCategory(
                category=cat,
                count=count,
                share_pct=round(count / total * 100, 1) if total else 0.0,
                open_count=open_per_cat.get(cat, 0),
            )
        )
    return tuple(result)


def _calculate_weekly(
    tickets: tuple[Ticket, ...], as_of: datetime.datetime
) -> tuple[WeeklyRow, ...]:
    """UTC Monday-based weekly demand and resolution trends."""
    if not tickets:
        return ()

    def monday(dt: datetime.datetime) -> datetime.date:
        return (dt - datetime.timedelta(days=dt.weekday())).date()

    # Find the date range: earliest created_at to as_of.
    earliest = min(t.created_at for t in tickets)
    start_monday = monday(earliest)
    end_monday = monday(as_of)

    # Build zero-filled week map.
    weeks: dict[datetime.date, dict[str, int]] = {}
    current = start_monday
    while current <= end_monday:
        weeks[current] = {"opened": 0, "resolved": 0}
        current += datetime.timedelta(days=7)

    for t in tickets:
        w = monday(t.created_at)
        if w in weeks:
            weeks[w]["opened"] += 1
        if t.resolved_at is not None:
            rw = monday(t.resolved_at)
            if rw in weeks:
                weeks[rw]["resolved"] += 1

    return tuple(
        WeeklyRow(
            week_start=w.isoformat(),
            opened=v["opened"],
            resolved=v["resolved"],
            net_change=v["opened"] - v["resolved"],
        )
        for w, v in sorted(weeks.items())
    )
