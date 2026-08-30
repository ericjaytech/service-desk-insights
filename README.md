# service-desk-insights

Local, reproducible service-desk reporting from anonymised ticket exports.

Produces a terminal summary, JSON, self-contained HTML and formula-safe CSV tables
from a single CSV file. No network access, no external services, no raw ticket
IDs in aggregate outputs.

## Quick start

```bash
# Install from source
pip install .

# Generate synthetic tickets for a demo
service-desk-insights generate-synthetic \
  --rows 1000 --seed 42 \
  --start-date 2026-01-05 --weeks 32 \
  --output synthetic.csv

# Analyse and produce a report
service-desk-insights analyse synthetic.csv \
  --sla-policy examples/sla-policy.json \
  --as-of 2026-08-30T12:00:00Z \
  --output-dir report

# With optional aggregate-only history
service-desk-insights analyse synthetic.csv \
  --sla-policy examples/sla-policy.json \
  --as-of 2026-08-30T12:00:00Z \
  --output-dir report \
  --history-db history.sqlite
```

## Requirements

Python 3.11 or later. The only runtime dependency is Jinja2 (for HTML rendering).

## Input

A CSV file with these columns (canonical format):

| Column | Description |
|--------|-------------|
| `ticket_id` | Unique ticket identifier |
| `created_at` | ISO-8601 creation timestamp |
| `first_response_at` | ISO-8601 first response timestamp (empty if not yet responded) |
| `resolved_at` | ISO-8601 resolution timestamp (empty if open) |
| `status` | `open`, `resolved`, `closed`, `pending`, `on-hold` |
| `priority` | `P1`, `P2`, `P3`, `P4` |
| `category` | Free-text category label |
| `reopen_count` | Integer count of reopen events |

Non-canonical column headings are supported via `--column-map` (see
`examples/column-map.json`).

## Output

Every `analyse` run produces a directory containing:

| File | Description |
|------|-------------|
| `summary.json` | All metrics in structured JSON |
| `report.html` | Self-contained HTML report (no external resources) |
| `tables/sla_by_priority.csv` | SLA compliance by priority |
| `tables/ageing_buckets.csv` | Open ticket age distribution |
| `tables/backlog_by_priority.csv` | Open tickets by priority |
| `tables/backlog_by_category.csv` | Open tickets by category |
| `tables/resolution_times.csv` | Median and P90 resolution times |
| `tables/reopened_summary.csv` | Reopen count and rate |
| `tables/recurring_categories.csv` | Categories above the minimum count threshold |
| `tables/weekly_trends.csv` | Weekly opened, resolved and net change |
| `tables/open_tickets.csv` | Individual open tickets with age |

All CSV files are formula-safe: values starting with `=`, `+`, `-` or `@` are
prefixed with a single quote.

## SLA policy

SLA targets are defined in a JSON file. See `examples/sla-policy.json` for the
schema. Each priority level defines response and resolution targets in hours.

## History database

Pass `--history-db` to append aggregate metrics to a versioned SQLite database.
The database contains no raw ticket IDs or source values. Repeating the same
analysis is idempotent (no duplicate rows).

## Synthetic data

`generate-synthetic` produces deterministic, reproducible ticket CSVs for
demonstrations and testing. The same seed always produces the same output.

## Development

```bash
pip install -e ".[dev]"
python -m ruff format --check .
python -m ruff check .
python -m pytest
python -m build
```

## License

MIT