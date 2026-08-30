"""Deterministic synthetic ticket generator (T7).

Produces safe demonstration data using only generated identifiers and
controlled categories.  Same seed + arguments → identical CSV bytes.
"""

from __future__ import annotations

import csv
import datetime
import io
import os
import random

# Fixed pools — no real company data, names, email addresses or free text.
_PRIORITIES = ("P1", "P2", "P3", "P4")
_CATEGORIES = (
    "network",
    "email",
    "printer",
    "server-down",
    "vpn",
    "password-reset",
    "software-install",
    "hardware-fault",
    "account-lockout",
    "file-restore",
)
_STATUSES = ("open", "pending", "resolved", "closed")
_OPEN_STATUSES = ("open", "pending")
_CLOSED_STATUSES = ("resolved", "closed")

# Priority weights for realistic distribution (P4 most common).
_PRIORITY_WEIGHTS = (0.05, 0.15, 0.30, 0.50)

# Category weights.
_CATEGORY_WEIGHTS = (0.12, 0.10, 0.08, 0.05, 0.10, 0.15, 0.12, 0.08, 0.10, 0.10)

# Reopen probability per ticket.
_REOPEN_PROB = 0.08

# Probability a ticket is still open (no resolved_at).
_OPEN_PROB = 0.25

# SLA target hours by priority (matching the illustrative policy).
_SLA_RESPONSE = {"P1": 1, "P2": 4, "P3": 8, "P4": 24}
_SLA_RESOLUTION = {"P1": 4, "P2": 12, "P3": 48, "P4": 120}

# Deliberate breach rates.
_RESPONSE_BREACH_RATE = 0.12
_RESOLUTION_BREACH_RATE = 0.18


def generate(
    *,
    rows: int,
    seed: int,
    start_date: str,
    weeks: int,
    output: str,
) -> None:
    """Generate a deterministic synthetic ticket CSV.

    Raises ``FileExistsError`` if *output* already exists.
    """
    if os.path.exists(output):
        raise FileExistsError(f"Output file already exists: {output}")

    if rows < 1:
        raise ValueError("rows must be >= 1")
    if weeks < 1:
        raise ValueError("weeks must be >= 1")

    rng = random.Random(seed)

    start_dt = datetime.date.fromisoformat(start_date)
    end_dt = start_dt + datetime.timedelta(weeks=weeks)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "ticket_id",
            "created_at",
            "first_response_at",
            "resolved_at",
            "status",
            "priority",
            "category",
            "reopen_count",
        ]
    )

    for i in range(1, rows + 1):
        ticket_id = f"SYN-{i:06d}"

        # Random creation timestamp within the window.
        created = _random_datetime(rng, start_dt, end_dt)
        priority = rng.choices(_PRIORITIES, weights=_PRIORITY_WEIGHTS, k=1)[0]
        category = rng.choices(_CATEGORIES, weights=_CATEGORY_WEIGHTS, k=1)[0]

        # Response time: mostly within SLA, some breaches.
        resp_target = _SLA_RESPONSE[priority]
        if rng.random() < _RESPONSE_BREACH_RATE:
            resp_delay_h = resp_target + rng.uniform(0.5, resp_target * 3)
        else:
            resp_delay_h = rng.uniform(0.05, resp_target * 0.95)
        first_response_at = created + datetime.timedelta(hours=resp_delay_h)

        # Resolution: some tickets stay open.
        is_open = rng.random() < _OPEN_PROB
        if is_open:
            resolved_at = None
            status = rng.choice(_OPEN_STATUSES)
        else:
            res_target = _SLA_RESOLUTION[priority]
            if rng.random() < _RESOLUTION_BREACH_RATE:
                res_delay_h = res_target + rng.uniform(0.5, res_target * 2)
            else:
                res_delay_h = rng.uniform(0.1, res_target * 0.95)
            resolved_at = created + datetime.timedelta(hours=res_delay_h)
            status = rng.choice(_CLOSED_STATUSES)

        # Reopen count.
        reopen_count = 0
        if rng.random() < _REOPEN_PROB:
            reopen_count = rng.choices([1, 2, 3], weights=[0.7, 0.2, 0.1], k=1)[0]

        w.writerow(
            [
                ticket_id,
                _fmt_ts(created),
                _fmt_ts(first_response_at),
                _fmt_ts(resolved_at) if resolved_at is not None else "",
                status,
                priority,
                category,
                str(reopen_count),
            ]
        )

    with open(output, "w", newline="", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    print(f"Generated {rows} tickets → {output}")  # noqa: T201
    print(f"  Window: {start_date} to {end_dt} ({weeks} weeks)")  # noqa: T201
    print(f"  Seed:   {seed}")  # noqa: T201


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _random_datetime(
    rng: random.Random, start: datetime.date, end: datetime.date
) -> datetime.datetime:
    """Return a random UTC datetime between start (inclusive) and end (exclusive)."""
    delta_days = (end - start).days
    if delta_days <= 0:
        delta_days = 1
    day_offset = rng.randint(0, delta_days - 1)
    second_offset = rng.randint(0, 86399)  # seconds in a day
    dt = datetime.datetime.combine(
        start + datetime.timedelta(days=day_offset),
        datetime.time.min,
        tzinfo=datetime.UTC,
    )
    return dt + datetime.timedelta(seconds=second_offset)


def _fmt_ts(dt: datetime.datetime | None) -> str:
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")
