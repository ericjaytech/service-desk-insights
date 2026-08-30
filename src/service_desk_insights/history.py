"""Aggregate-only SQLite history (T8).

Writes completed analysis results into a versioned SQLite database.
Contains no raw ticket IDs or source values.  Idempotent: repeating
the same analysis does not create a duplicate run.  Failure rolls
back the complete transaction.
"""

from __future__ import annotations

import sqlite3
import time

from service_desk_insights import __version__
from service_desk_insights.metrics import MetricResult

_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_version  TEXT    NOT NULL,
    input_basename TEXT   NOT NULL,
    input_sha256  TEXT    NOT NULL,
    policy_sha256 TEXT    NOT NULL,
    as_of         TEXT    NOT NULL,
    ticket_count  INTEGER NOT NULL,
    created_at    TEXT    NOT NULL,
    UNIQUE(input_sha256, policy_sha256, as_of)
);

CREATE TABLE IF NOT EXISTS sla_by_priority (
    run_id                   INTEGER NOT NULL REFERENCES runs(run_id),
    priority                 TEXT    NOT NULL,
    response_met             INTEGER NOT NULL,
    response_breached        INTEGER NOT NULL,
    response_pending         INTEGER NOT NULL,
    response_eligible        INTEGER NOT NULL,
    response_compliance_pct  REAL,
    resolution_met           INTEGER NOT NULL,
    resolution_breached      INTEGER NOT NULL,
    resolution_pending       INTEGER NOT NULL,
    resolution_eligible      INTEGER NOT NULL,
    resolution_compliance_pct REAL,
    PRIMARY KEY (run_id, priority)
);

CREATE TABLE IF NOT EXISTS ageing_buckets (
    run_id  INTEGER NOT NULL REFERENCES runs(run_id),
    label   TEXT    NOT NULL,
    count   INTEGER NOT NULL,
    pct     REAL,
    PRIMARY KEY (run_id, label)
);

CREATE TABLE IF NOT EXISTS backlog_by_priority (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    priority TEXT    NOT NULL,
    count    INTEGER NOT NULL,
    pct      REAL,
    PRIMARY KEY (run_id, priority)
);

CREATE TABLE IF NOT EXISTS backlog_by_category (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    category TEXT    NOT NULL,
    count    INTEGER NOT NULL,
    pct      REAL,
    PRIMARY KEY (run_id, category)
);

CREATE TABLE IF NOT EXISTS resolution_times (
    run_id               INTEGER PRIMARY KEY REFERENCES runs(run_id),
    sample_size          INTEGER NOT NULL,
    median_hours         REAL,
    p90_hours            REAL,
    small_sample_warning TEXT
);

CREATE TABLE IF NOT EXISTS reopened_summary (
    run_id              INTEGER PRIMARY KEY REFERENCES runs(run_id),
    count               INTEGER NOT NULL,
    pct                 REAL,
    total_reopen_events INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS recurring_categories (
    run_id    INTEGER NOT NULL REFERENCES runs(run_id),
    category  TEXT    NOT NULL,
    count     INTEGER NOT NULL,
    share_pct REAL,
    open_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, category)
);

CREATE TABLE IF NOT EXISTS weekly_trends (
    run_id     INTEGER NOT NULL REFERENCES runs(run_id),
    week_start TEXT    NOT NULL,
    opened     INTEGER NOT NULL,
    resolved   INTEGER NOT NULL,
    net_change INTEGER NOT NULL,
    PRIMARY KEY (run_id, week_start)
);
"""


def append_run(db_path: str, result: MetricResult) -> bool:
    """Append *result* to the history database.

    Returns ``True`` if a new run was inserted, ``False`` if an
    identical run already existed (idempotent).

    Raises ``sqlite3.Error`` on schema or I/O failures; the caller
    should treat these as non-fatal (the report was already published).
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        _ensure_schema(conn)

        existing = conn.execute(
            "SELECT run_id FROM runs WHERE input_sha256 = ? AND policy_sha256 = ? AND as_of = ?",
            (result.input_hash, result.policy_hash, result.as_of),
        ).fetchone()
        if existing is not None:
            return False

        created_at = _utcnow_iso()

        conn.execute(
            """INSERT INTO runs (tool_version, input_basename, input_sha256,
               policy_sha256, as_of, ticket_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                __version__,
                result.input_basename,
                result.input_hash,
                result.policy_hash,
                result.as_of,
                result.ticket_count,
                created_at,
            ),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for r in result.sla_by_priority:
            conn.execute(
                """INSERT INTO sla_by_priority
                   (run_id, priority, response_met, response_breached,
                    response_pending, response_eligible, response_compliance_pct,
                    resolution_met, resolution_breached, resolution_pending,
                    resolution_eligible, resolution_compliance_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    r.priority,
                    r.response.met,
                    r.response.breached,
                    r.response.pending,
                    r.response.eligible,
                    r.response.compliance_pct,
                    r.resolution.met,
                    r.resolution.breached,
                    r.resolution.pending,
                    r.resolution.eligible,
                    r.resolution.compliance_pct,
                ),
            )

        for b in result.ageing.buckets:
            conn.execute(
                "INSERT INTO ageing_buckets (run_id, label, count, pct) VALUES (?, ?, ?, ?)",
                (run_id, b.label, b.count, b.pct),
            )

        for r in result.backlog.by_priority:
            conn.execute(
                "INSERT INTO backlog_by_priority"
                " (run_id, priority, count, pct) VALUES (?, ?, ?, ?)",
                (run_id, r.key, r.count, r.pct),
            )

        for r in result.backlog.by_category:
            conn.execute(
                "INSERT INTO backlog_by_category"
                " (run_id, category, count, pct) VALUES (?, ?, ?, ?)",
                (run_id, r.key, r.count, r.pct),
            )

        conn.execute(
            """INSERT INTO resolution_times
               (run_id, sample_size, median_hours, p90_hours, small_sample_warning)
               VALUES (?, ?, ?, ?, ?)""",
            (
                run_id,
                result.resolution.sample_size,
                result.resolution.median_hours,
                result.resolution.p90_hours,
                result.resolution.small_sample_warning,
            ),
        )

        conn.execute(
            """INSERT INTO reopened_summary
               (run_id, count, pct, total_reopen_events)
               VALUES (?, ?, ?, ?)""",
            (
                run_id,
                result.reopened.count,
                result.reopened.pct,
                result.reopened.total_reopen_events,
            ),
        )

        for rc in result.recurring:
            conn.execute(
                """INSERT INTO recurring_categories
                   (run_id, category, count, share_pct, open_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, rc.category, rc.count, rc.share_pct, rc.open_count),
            )

        for wr in result.weekly:
            conn.execute(
                """INSERT INTO weekly_trends
                   (run_id, week_start, opened, resolved, net_change)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, wr.week_start, wr.opened, wr.resolved, wr.net_change),
            )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)

    row = conn.execute("SELECT version FROM _schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO _schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))
    elif row[0] != _SCHEMA_VERSION:
        raise sqlite3.OperationalError(
            f"Unsupported history schema version {row[0]} (expected {_SCHEMA_VERSION}). "
            "The database was created by a different version of service-desk-insights."
        )


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
