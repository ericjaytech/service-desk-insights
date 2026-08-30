# Metric definitions

All metrics are calculated from the anonymised ticket CSV at a fixed `as_of`
timestamp. Tickets created after `as_of` are excluded.

## SLA compliance

For each priority level (P1-P4), response and resolution compliance is
calculated as:

- **eligible**: tickets where the SLA clock has started (the ticket has been
  created and the relevant timestamp can be measured).
- **met**: eligible tickets where the elapsed time from creation to first
  response (or resolution) is less than or equal to the SLA target.
- **breached**: eligible tickets where the elapsed time exceeds the SLA target.
- **pending**: tickets that are still open and within the SLA target window.
- **compliance %**: `met / (met + breached) × 100`. Pending tickets are excluded
  from the percentage.

SLA targets are defined in the policy file. Boundaries are exact: a response at
exactly the target hour is met.

## Open ticket ageing

Calculated for all tickets with `resolved_at` empty at `as_of`:

- **oldest age**: maximum age in hours and days.
- **buckets**: count and percentage in six buckets: <1 day, 1-2 days, 3-7 days,
  8-14 days, 15-30 days, >30 days.

## Resolution times

Calculated for all tickets with a non-empty `resolved_at` at or before `as_of`:

- **sample size**: number of resolved tickets.
- **median**: 50th percentile of resolution duration in hours.
- **P90**: 90th percentile using nearest-rank method.
- **small sample warning**: emitted when sample size is less than 10.

## Reopened tickets

- **count**: number of tickets with `reopen_count > 0`.
- **rate**: `count / total_tickets × 100`.
- **total reopen events**: sum of all `reopen_count` values.

## Recurring categories

Categories with a count at or above `--recurring-min-count` (default 3):

- **count**: total tickets in the category.
- **share**: `count / total_tickets × 100`.
- **open**: tickets in the category that are still open.

## Weekly trends

For each ISO week from the earliest ticket creation to `as_of`:

- **opened**: tickets created in that week.
- **resolved**: tickets resolved in that week.
- **net change**: `opened - resolved`.

Weeks with zero activity are included (zero-filled).

## Backlog

- **by priority**: open tickets grouped by priority, with count and percentage.
- **by category**: open tickets grouped by category, with count and percentage.

## Limitations

- Metrics are point-in-time at `as_of`. They do not show SLA compliance trends
  over time.
- Resolution time percentiles use nearest-rank, not linear interpolation.
- Recurring categories use exact string matching on the `category` field.
- The tool does not perform deduplication, merging or linking of related tickets.