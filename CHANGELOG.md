# Changelog

## 0.1.0 (2026-08-30)

Initial alpha release.

### Features

- **analyse**: validate anonymised ticket CSV and produce a report directory with
  terminal summary, JSON, self-contained HTML and formula-safe CSV tables.
- **generate-synthetic**: deterministic synthetic ticket generator for
  demonstrations and testing.
- **SLA compliance**: response and resolution compliance by priority, with
  met/breached/pending classification at exact boundary timestamps.
- **Open ticket ageing**: oldest age, bucket distribution and per-ticket detail.
- **Resolution times**: median and nearest-rank P90 with small-sample warnings.
- **Reopen tracking**: reopen count, rate and total reopen events.
- **Recurring categories**: categories above a configurable minimum count
  threshold.
- **Weekly trends**: opened, resolved and net change per ISO week.
- **Column mapping**: support for non-canonical CSV headings via JSON map.
- **Aggregate-only SQLite history**: optional, idempotent, versioned schema with
  no raw ticket IDs.
- **Formula-safe CSV**: all spreadsheet outputs protected against formula
  injection.
- **Self-contained HTML**: no external resources, Jinja2 autoescape.
- **Atomic output**: temp directory + rename; no partial reports on failure.
- **Privacy**: no raw ticket IDs in aggregate outputs, errors or history.