"""Immutable internal ticket model.

Every record entering the metric pipeline must pass through these types.
Dictionaries and raw CSV rows stay in the ingest boundary; everything downstream
uses ``Ticket`` and its companions.
"""

from __future__ import annotations

import dataclasses
import datetime


@dataclasses.dataclass(frozen=True)
class Ticket:
    """A single validated and UTC-normalised ticket.

    All timestamps are aware UTC.  ``reopen_count`` is always a non-negative
    integer (empty in the source means zero).
    """

    ticket_id: str
    created_at: datetime.datetime
    first_response_at: datetime.datetime | None
    resolved_at: datetime.datetime | None
    status: str
    priority: str
    category: str
    reopen_count: int = 0

    def __post_init__(self) -> None:
        if not self.ticket_id.strip():
            msg = "ticket_id must be non-empty."
            raise ValueError(msg)
        if self.reopen_count < 0:
            msg = f"reopen_count must be >= 0, got {self.reopen_count}"
            raise ValueError(msg)
        _assert_utc(self.created_at, "created_at")
        if self.first_response_at is not None:
            _assert_utc(self.first_response_at, "first_response_at")
        if self.resolved_at is not None:
            _assert_utc(self.resolved_at, "resolved_at")

    @property
    def is_open(self) -> bool:
        """Whether the ticket has no resolved timestamp."""
        return self.resolved_at is None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def is_reopened(self) -> bool:
        return self.reopen_count > 0

    @property
    def age_hours(self) -> float | None:
        """Age in elapsed hours since *created_at*.  Caller must pass an
        ``as_of`` to the metric functions for a deterministic answer."""
        return None  # computed externally so as_of is always explicit


def _assert_utc(dt: datetime.datetime, field_name: str) -> None:
    if dt.tzinfo is None:
        msg = f"{field_name} must be timezone-aware (UTC)."
        raise ValueError(msg)
    if dt.utcoffset() != datetime.timedelta(0):
        msg = f"{field_name} must be UTC, got offset {dt.utcoffset()}"
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Ingest limits (safety bounds, not performance targets)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class IngestLimits:
    """Safety bounds enforced during CSV reading."""

    max_input_bytes: int = 100 * 1024 * 1024  # 100 MiB
    max_rows: int = 1_000_000
    max_columns: int = 128
    max_field_bytes: int = 64 * 1024  # 64 KiB
    max_validation_errors_reported: int = 50


# ---------------------------------------------------------------------------
# Ingest error model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ValidationError:
    """A single row-level validation failure with row number and message.

    Messages must never include field values that could contain personal data.
    """

    row: int
    message: str


@dataclasses.dataclass(frozen=True)
class IngestResult:
    """Complete result of CSV ingestion and validation."""

    tickets: tuple[Ticket, ...]
    limits: IngestLimits
    errors: tuple[ValidationError, ...]
    errors_suppressed: int  # errors beyond the reported cap
    ignored_columns: tuple[str, ...]  # empty in canonical mode
    input_rows: int  # total CSV data rows (excluding header)
    source_basename: str
    input_sha256: str
