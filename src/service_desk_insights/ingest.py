"""CSV reading, validation and normalisation for the canonical ticket schema.

Exposes ``ingest_canonical_csv`` as the primary entry point.  Only recognised
columns enter the internal model; extra columns fail in canonical mode.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import os

from service_desk_insights.models import (
    IngestLimits,
    IngestResult,
    Ticket,
    ValidationError,
)
from service_desk_insights.policy import ColumnMap

# Canonical headers and their CSV column indices (used for DictReader-like
# behaviour without relying on fieldnames that may contain BOM ghosts).
CANONICAL_HEADERS: tuple[str, ...] = (
    "ticket_id",
    "created_at",
    "first_response_at",
    "resolved_at",
    "status",
    "priority",
    "category",
    "reopen_count",
)


def ingest_canonical_csv(path: str, limits: IngestLimits | None = None) -> IngestResult:
    """Read and validate a canonical ticket CSV.

    Returns ``IngestResult`` regardless of success or failure.  Callers must
    check ``result.errors`` before computing metrics.

    Raises ``FileNotFoundError``, ``PermissionError`` and early-limit
    ``ValueError`` for issues that prevent reading entirely.
    """
    limits = limits if limits is not None else IngestLimits()

    # Check file size before opening.
    stat = os.stat(path)
    if stat.st_size > limits.max_input_bytes:
        msg = f"Input file exceeds {limits.max_input_bytes} bytes (got {stat.st_size})."
        raise ValueError(msg)

    # Read raw bytes for hashing and BOM detection, then rewrap as text.
    raw = _read_bytes(path, limits.max_input_bytes)
    source_basename = os.path.basename(path)
    input_sha256 = hashlib.sha256(raw).hexdigest()
    text = _decode_with_bom_handling(raw)

    # Parse CSV with bounds enforcement.
    errors: list[ValidationError] = []
    tickets: list[Ticket] = []
    seen_ids: set[str] = set()
    seen_columns: tuple[str, ...] | None = None

    reader = csv.reader(text.splitlines(), strict=True)
    for row_num, raw_row in enumerate(reader, start=1):
        if row_num == 1:
            seen_columns = _validate_header(tuple(raw_row), errors)
            continue
        if seen_columns is None:
            continue  # header already failed

        if len(raw_row) > limits.max_columns:
            errors.append(
                ValidationError(
                    row_num, f"Row has {len(raw_row)} columns; max {limits.max_columns}"
                )
            )
            continue
        if len(tickets) >= limits.max_rows:
            msg = f"Input exceeds {limits.max_rows} data rows."
            raise ValueError(msg)

        _parse_and_validate_row(row_num, seen_columns, raw_row, tickets, seen_ids, errors, limits)

    errors_suppressed = 0
    if len(errors) > limits.max_validation_errors_reported:
        errors_suppressed = len(errors) - limits.max_validation_errors_reported
        errors = errors[: limits.max_validation_errors_reported]

    return IngestResult(
        tickets=tuple(tickets),
        limits=limits,
        errors=tuple(errors),
        errors_suppressed=errors_suppressed,
        ignored_columns=(),
        input_rows=row_num - 1 if seen_columns else 0,
        source_basename=source_basename,
        input_sha256=input_sha256,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_bytes(path: str, max_bytes: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(max_bytes + 1)  # +1 so stat vs read mismatch is caught


def _decode_with_bom_handling(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    return raw.decode("utf-8")


def _validate_header(
    headings: tuple[str, ...], errors: list[ValidationError]
) -> tuple[str, ...] | None:
    trimmed = tuple(h.strip() for h in headings)

    if trimmed != CANONICAL_HEADERS:
        errors.append(
            ValidationError(
                0,
                f"Canonical headers expected {list(CANONICAL_HEADERS)}, "
                f"got {list(trimmed)}. Use --column-map for different headings.",
            )
        )
        return None

    return trimmed


def _parse_and_validate_row(
    row_num: int,
    headers: tuple[str, ...],
    raw: list[str],
    tickets: list[Ticket],
    seen_ids: set[str],
    errors: list[ValidationError],
    limits: IngestLimits,
) -> None:
    if len(raw) != len(headers):
        errors.append(
            ValidationError(
                row_num,
                f"Expected {len(headers)} fields, got {len(raw)}.",
            )
        )
        return

    # Enforce per-field size limits.
    for idx, val in enumerate(raw):
        if len(val.encode("utf-8")) > limits.max_field_bytes:
            errors.append(
                ValidationError(
                    row_num,
                    f"Field {idx + 1} exceeds {limits.max_field_bytes} bytes.",
                )
            )
            return  # one limit violation per row is enough

    fields = dict(zip(headers, raw, strict=True))

    ticket_id = fields["ticket_id"].strip()
    if not ticket_id:
        errors.append(ValidationError(row_num, "ticket_id is empty."))
        return
    if ticket_id in seen_ids:
        errors.append(ValidationError(row_num, f"{ticket_id!r} is a duplicate ticket_id."))
        return

    # Timestamps.  created_at is required; empty field is an error, not a skip.
    created_raw = fields["created_at"].strip()
    if not created_raw:
        errors.append(ValidationError(row_num, "created_at is required."))
        return
    created_at = _parse_timestamp(created_raw, "created_at", row_num, errors)
    if created_at is None:
        return

    first_response_at = _parse_timestamp(
        fields["first_response_at"], "first_response_at", row_num, errors
    )
    resolved_at = _parse_timestamp(fields["resolved_at"], "resolved_at", row_num, errors)

    status = fields["status"].strip()
    priority = fields["priority"].strip()
    category = fields["category"].strip()
    if not category:
        errors.append(ValidationError(row_num, "category is empty."))
        return

    reopen_raw = fields["reopen_count"].strip()
    reopen_count = 0
    if reopen_raw:
        try:
            reopen_count = int(reopen_raw)
        except ValueError:
            errors.append(
                ValidationError(row_num, f"reopen_count must be an integer, got {reopen_raw!r}.")
            )
            return
        if reopen_count < 0:
            errors.append(
                ValidationError(row_num, f"reopen_count must be >= 0, got {reopen_count}.")
            )
            return

    # Convert to Ticket (post_init validation for UTC assurance).
    try:
        ticket = Ticket(
            ticket_id=ticket_id,
            created_at=created_at,
            first_response_at=first_response_at,
            resolved_at=resolved_at,
            status=status,
            priority=priority,
            category=category,
            reopen_count=reopen_count,
        )
    except ValueError as exc:
        errors.append(ValidationError(row_num, str(exc)))
        return

    seen_ids.add(ticket_id)
    tickets.append(ticket)


def _parse_timestamp(
    value: str, field_name: str, row_num: int, errors: list[ValidationError]
) -> datetime.datetime | None:
    """Parse an ISO-8601 timestamp.  Returns None on failure; appends to errors."""
    stripped = value.strip()
    if stripped == "":
        return None

    # Python strptime treats a trailing Z as literal, leaving tzinfo=None.
    # We strip Z and parse, then attach UTC explicitly.
    ends_z = stripped.endswith("Z")
    body = stripped[:-1] if ends_z else stripped

    # ISO-8601 with optional fractional seconds.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.datetime.strptime(body, fmt)
        except ValueError:
            continue
        if ends_z:
            dt = dt.replace(tzinfo=datetime.UTC)
        elif dt.tzinfo is None:
            # No Z and no offset → naive timestamp.
            errors.append(
                ValidationError(
                    row_num,
                    f"{field_name} is a naive timestamp (missing timezone).",
                )
            )
            return None
        else:
            dt = dt.astimezone(datetime.UTC)
        return dt

    # Also try formats with explicit numeric offset (%z).
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.datetime.strptime(stripped, fmt)
        except ValueError:
            continue
        return dt.astimezone(datetime.UTC)

    errors.append(
        ValidationError(
            row_num,
            f"{field_name} is not a recognised ISO-8601 timestamp.",
        )
    )
    return None


# ---------------------------------------------------------------------------
# Mapped CSV ingest
# ---------------------------------------------------------------------------


def ingest_mapped_csv(
    path: str, column_map: ColumnMap, limits: IngestLimits | None = None
) -> IngestResult:
    """Read and validate a CSV with non-canonical headings using a ColumnMap.

    Unmapped columns are ignored (only their headings appear in
    ``ignored_columns``).  No unmapped values enter the internal model.
    """
    limits = limits if limits is not None else IngestLimits()

    stat = os.stat(path)
    if stat.st_size > limits.max_input_bytes:
        msg = f"Input file exceeds {limits.max_input_bytes} bytes (got {stat.st_size})."
        raise ValueError(msg)

    raw = _read_bytes(path, limits.max_input_bytes)
    source_basename = os.path.basename(path)
    input_sha256 = hashlib.sha256(raw).hexdigest()
    text = _decode_with_bom_handling(raw)

    errors: list[ValidationError] = []
    tickets: list[Ticket] = []
    seen_ids: set[str] = set()
    ignored: list[str] = []

    inverse = column_map.source_to_canonical()
    canonical_order: list[str] = list(column_map.canonical_names)

    reader = csv.reader(text.splitlines(), strict=True)
    source_headings: tuple[str, ...] | None = None
    heading_map: dict[int, str | None] = {}  # col_index → canonical_name or None

    for row_num, raw_row in enumerate(reader, start=1):
        if row_num == 1:
            source_headings = tuple(h.strip() for h in raw_row)
            for idx, sh in enumerate(source_headings):
                canonical = inverse.get(sh)
                if canonical is not None:
                    heading_map[idx] = canonical
                else:
                    heading_map[idx] = None
                    if sh:  # non-empty headings only
                        ignored.append(sh)

            # Check all canonical names are covered.
            missing = set(canonical_order) - {heading_map[i] for i in heading_map}
            if missing:
                errors.append(
                    ValidationError(
                        0,
                        f"Column map is missing source headings for: {sorted(missing)}",
                    )
                )
            continue

        if source_headings is None:
            continue

        # Reorder row fields to canonical order.
        reordered: list[str] = []
        for canonical_name in canonical_order:
            col_idx = None
            for idx, cn in heading_map.items():
                if cn == canonical_name:
                    col_idx = idx
                    break
            if col_idx is not None and col_idx < len(raw_row):
                reordered.append(raw_row[col_idx])
            else:
                reordered.append("")

        _parse_and_validate_row(
            row_num,
            tuple(canonical_order),
            reordered,
            tickets,
            seen_ids,
            errors,
            limits,
        )

        if len(tickets) >= limits.max_rows:
            msg = f"Input exceeds {limits.max_rows} data rows."
            raise ValueError(msg)

    errors_suppressed = 0
    if len(errors) > limits.max_validation_errors_reported:
        errors_suppressed = len(errors) - limits.max_validation_errors_reported
        errors = errors[: limits.max_validation_errors_reported]

    return IngestResult(
        tickets=tuple(tickets),
        limits=limits,
        errors=tuple(errors),
        errors_suppressed=errors_suppressed,
        ignored_columns=tuple(ignored),
        input_rows=row_num - 1 if source_headings else 0,
        source_basename=source_basename,
        input_sha256=input_sha256,
    )
