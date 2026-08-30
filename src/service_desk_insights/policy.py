"""SLA policy and column-map parsing and validation.

Both are versioned JSON documents.  This module validates structure, converts
them into typed internal objects and refuses implicit defaults.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

# ---------------------------------------------------------------------------
# Column map
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ColumnMap:
    """A validated rename-only mapping from canonical to source headings."""

    schema_version: int
    mapping: dict[str, str]  # canonical_name → source_heading

    @property
    def canonical_names(self) -> tuple[str, ...]:
        return tuple(self.mapping.keys())

    def source_to_canonical(self) -> dict[str, str]:
        """Inverse mapping: source heading → canonical name."""
        return {v: k for k, v in self.mapping.items()}


_CANONICAL_NAMES = frozenset(
    (
        "ticket_id",
        "created_at",
        "first_response_at",
        "resolved_at",
        "status",
        "priority",
        "category",
        "reopen_count",
    )
)


def parse_column_map(path: str) -> ColumnMap:
    """Load and validate a JSON column map.

    Returns ``ColumnMap`` on success.

    Raises ``ValueError`` with a human-readable message for missing keys,
    duplicate mappings, unknown schema versions or invalid structure.
    """
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        msg = "Column map must be a JSON object."
        raise ValueError(msg)

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        msg = f"Unsupported column-map schema version: {schema_version!r}. Expected 1."
        raise ValueError(msg)

    columns = raw.get("columns")
    if not isinstance(columns, dict):
        msg = "Column map must contain a 'columns' object."
        raise ValueError(msg)

    missing = _CANONICAL_NAMES - set(columns.keys())
    if missing:
        msg = f"Column map is missing canonical keys: {sorted(missing)}"
        raise ValueError(msg)

    extra = set(columns.keys()) - _CANONICAL_NAMES
    if extra:
        msg = f"Column map contains unknown canonical keys: {sorted(extra)}"
        raise ValueError(msg)

    source_headings = list(columns.values())
    seen: set[str] = set()
    for sh in source_headings:
        if not isinstance(sh, str) or not sh.strip():
            msg = "All source column headings must be non-empty strings."
            raise ValueError(msg)
        if sh in seen:
            msg = f"Duplicate source heading in column map: {sh!r}"
            raise ValueError(msg)
        seen.add(sh)

    return ColumnMap(schema_version=schema_version, mapping=dict(columns))


# ---------------------------------------------------------------------------
# SLA policy
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PriorityTarget:
    first_response_hours: float
    resolution_hours: float

    def __post_init__(self) -> None:
        if self.first_response_hours <= 0:
            msg = f"first_response_hours must be positive, got {self.first_response_hours}"
            raise ValueError(msg)
        if self.resolution_hours <= 0:
            msg = f"resolution_hours must be positive, got {self.resolution_hours}"
            raise ValueError(msg)


@dataclasses.dataclass(frozen=True)
class SlaPolicy:
    """A validated, explicit SLA policy."""

    schema_version: int
    clock: str  # always 'elapsed' in v0.1.0
    priority_order: tuple[str, ...]
    open_statuses: tuple[str, ...]
    closed_statuses: tuple[str, ...]
    targets: dict[str, PriorityTarget]


def parse_sla_policy(path: str) -> SlaPolicy:
    """Load and validate a JSON SLA policy.

    Returns ``SlaPolicy`` on success.

    Raises ``ValueError`` for unknown schema versions, missing targets,
    overlapping status sets, non-positive targets or other structural errors.
    """
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        msg = "SLA policy must be a JSON object."
        raise ValueError(msg)

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        msg = f"Unsupported SLA policy schema version: {schema_version!r}. Expected 1."
        raise ValueError(msg)

    clock = raw.get("clock", "elapsed")
    if clock != "elapsed":
        msg = f"Unsupported SLA clock: {clock!r}. Only 'elapsed' is supported in v0.1.0."
        raise ValueError(msg)

    priority_order = raw.get("priority_order", [])
    if not isinstance(priority_order, list) or not priority_order:
        msg = "'priority_order' must be a non-empty list."
        raise ValueError(msg)
    priority_order = tuple(priority_order)

    open_statuses = _parse_string_list(raw, "open_statuses")
    closed_statuses = _parse_string_list(raw, "closed_statuses")

    overlap = set(open_statuses) & set(closed_statuses)
    if overlap:
        msg = f"open_statuses and closed_statuses must not overlap; found: {sorted(overlap)}"
        raise ValueError(msg)

    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, dict):
        msg = "'targets' must be a JSON object mapping priority to SLA targets."
        raise ValueError(msg)

    targets: dict[str, PriorityTarget] = {}
    for priority in priority_order:
        if priority not in targets_raw:
            msg = f"Priority {priority!r} is in priority_order but missing from targets."
            raise ValueError(msg)
        pt_raw = targets_raw[priority]
        if not isinstance(pt_raw, dict):
            msg = f"Target for priority {priority!r} must be a JSON object."
            raise ValueError(msg)
        try:
            targets[priority] = PriorityTarget(
                first_response_hours=float(pt_raw["first_response_hours"]),
                resolution_hours=float(pt_raw["resolution_hours"]),
            )
        except KeyError as exc:
            msg = f"Target for priority {priority!r} missing key {exc.args[0]!r}."
            raise ValueError(msg) from exc
        except (TypeError, ValueError) as exc:
            msg = f"Invalid target value for priority {priority!r}: {exc}"
            raise ValueError(msg) from exc

    extra_targets = set(targets_raw) - set(priority_order)
    if extra_targets:
        msg = f"targets contain priorities not in priority_order: {sorted(extra_targets)}"
        raise ValueError(msg)

    return SlaPolicy(
        schema_version=schema_version,
        clock=clock,
        priority_order=priority_order,
        open_statuses=open_statuses,
        closed_statuses=closed_statuses,
        targets=targets,
    )


def _parse_string_list(raw: dict, key: str) -> tuple[str, ...]:
    values = raw.get(key)
    if not isinstance(values, list):
        msg = f"'{key}' must be a JSON array."
        raise ValueError(msg)
    result: list[str] = []
    for v in values:
        if not isinstance(v, str) or not v.strip():
            msg = f"All entries in '{key}' must be non-empty strings."
            raise ValueError(msg)
        result.append(v)
    if not result:
        msg = f"'{key}' must not be empty."
        raise ValueError(msg)
    return tuple(result)
