"""Tests for the CLI skeleton (T1).

Covers --help, --version, missing required arguments on both subcommands and exit
codes from the placeholder handlers. These tests are intentionally narrow: they
prove the parser and entry point without depending on any implementation slice.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from service_desk_insights import __version__
from service_desk_insights.cli import main


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "service_desk_insights", *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_flag_stdout():
    """``--version`` writes the version to stdout and exits 0."""
    cp = _run("--version")
    assert cp.returncode == 0
    assert __version__ in cp.stdout


def test_version_output_format():
    """``--version`` includes the program name."""
    cp = _run("--version")
    assert "service-desk-insights" in cp.stdout


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_flag_stdout():
    """``--help`` prints usage and exits 0."""
    cp = _run("--help")
    assert cp.returncode == 0
    assert "usage:" in cp.stdout.lower() or "usage:" in cp.stdout


def test_help_includes_subcommands():
    """Top-level help lists both subcommands."""
    cp = _run("--help")
    assert "analyse" in cp.stdout
    assert "generate-synthetic" in cp.stdout


def test_analyse_help():
    """``analyse --help`` shows the subcommand help."""
    cp = _run("analyse", "--help")
    assert cp.returncode == 0
    assert "sla-policy" in cp.stdout


def test_generate_synthetic_help():
    """``generate-synthetic --help`` shows the subcommand help."""
    cp = _run("generate-synthetic", "--help")
    assert cp.returncode == 0
    assert "seed" in cp.stdout


# ---------------------------------------------------------------------------
# Missing subcommand
# ---------------------------------------------------------------------------


def test_no_subcommand_exits_2():
    """Invoking with no subcommand exits 2 (invalid arguments)."""
    cp = _run()
    assert cp.returncode == 2


# ---------------------------------------------------------------------------
# analyse placeholder
# ---------------------------------------------------------------------------


def test_analyse_with_dummy_files_fails_on_missing_input():
    """``analyse`` with non-existent input files exits 1 (uncaught FileNotFoundError)."""
    cp = _run(
        "analyse",
        "dummy.csv",
        "--sla-policy",
        "dummy.json",
        "--output-dir",
        "dummy-out",
    )
    assert cp.returncode in (1, 3)


def test_analyse_missing_sla_policy():
    """``analyse`` without --sla-policy exits 2 (argparse error)."""
    cp = _run("analyse", "dummy.csv", "--output-dir", "dummy-out")
    assert cp.returncode == 2


def test_analyse_missing_output_dir():
    """``analyse`` without --output-dir exits 2 (argparse error)."""
    cp = _run("analyse", "dummy.csv", "--sla-policy", "dummy.json")
    assert cp.returncode == 2


def test_analyse_missing_csv_positional():
    """``analyse`` without the CSV positional exits 2."""
    cp = _run("analyse", "--sla-policy", "dummy.json", "--output-dir", "dummy-out")
    assert cp.returncode == 2


def test_analyse_accepts_all_optional_args():
    """``analyse`` parses all optional arguments; fails on missing files."""
    cp = _run(
        "analyse",
        "dummy.csv",
        "--sla-policy",
        "dummy.json",
        "--column-map",
        "dummy-map.json",
        "--as-of",
        "2026-08-30T12:00:00Z",
        "--output-dir",
        "dummy-out",
        "--history-db",
        "dummy.sqlite",
        "--recurring-min-count",
        "5",
    )
    assert cp.returncode in (1, 3)


# ---------------------------------------------------------------------------
# generate-synthetic placeholder
# ---------------------------------------------------------------------------


def test_generate_synthetic_succeeds():
    """``generate-synthetic`` with required args produces a valid CSV."""
    import tempfile

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "syn.csv")
    cp = _run(
        "generate-synthetic",
        "--rows",
        "100",
        "--start-date",
        "2026-01-05",
        "--weeks",
        "4",
        "--output",
        out,
    )
    assert cp.returncode == 0, f"stderr: {cp.stderr}"
    assert "Generated 100 tickets" in cp.stdout
    assert os.path.isfile(out)


def test_generate_synthetic_missing_rows():
    """``generate-synthetic`` without --rows exits 2."""
    cp = _run(
        "generate-synthetic",
        "--start-date",
        "2026-01-05",
        "--weeks",
        "4",
        "--output",
        "dummy.csv",
    )
    assert cp.returncode == 2


def test_generate_synthetic_missing_start_date():
    """``generate-synthetic`` without --start-date exits 2."""
    cp = _run(
        "generate-synthetic",
        "--rows",
        "100",
        "--weeks",
        "4",
        "--output",
        "dummy.csv",
    )
    assert cp.returncode == 2


def test_generate_synthetic_missing_weeks():
    """``generate-synthetic`` without --weeks exits 2."""
    cp = _run(
        "generate-synthetic",
        "--rows",
        "100",
        "--start-date",
        "2026-01-05",
        "--output",
        "dummy.csv",
    )
    assert cp.returncode == 2


def test_generate_synthetic_missing_output():
    """``generate-synthetic`` without --output exits 2."""
    cp = _run(
        "generate-synthetic",
        "--rows",
        "100",
        "--start-date",
        "2026-01-05",
        "--weeks",
        "4",
    )
    assert cp.returncode == 2


# ---------------------------------------------------------------------------
# Programmatic main() invocation
# ---------------------------------------------------------------------------


def test_main_importable_and_callable():
    """``main()`` can be called programmatically with argv."""
    with pytest.raises((SystemExit, FileNotFoundError)):
        main(
            [
                "analyse",
                "dummy.csv",
                "--sla-policy",
                "p.json",
                "--output-dir",
                "out",
            ]
        )


# ---------------------------------------------------------------------------
# Atomic output edge cases (T9)
# ---------------------------------------------------------------------------


def test_analyse_refuses_existing_output_dir():
    """Output directory that already exists is rejected."""
    import tempfile

    tmp = tempfile.mkdtemp()
    existing = os.path.join(tmp, "existing")
    os.makedirs(existing)

    cp = _run(
        "analyse",
        "tests/fixtures/canonical_tickets.csv",
        "--sla-policy",
        "examples/sla-policy.json",
        "--as-of",
        "2026-08-30T12:00:00Z",
        "--output-dir",
        existing,
    )
    assert cp.returncode == 4
    assert "already exists" in cp.stderr


def test_analyse_refuses_symlink_output_dir():
    """Output directory that is a symlink to an existing directory is rejected."""
    import tempfile

    tmp = tempfile.mkdtemp()
    real_dir = os.path.join(tmp, "real")
    os.makedirs(real_dir)
    link = os.path.join(tmp, "link")
    os.symlink(real_dir, link)

    cp = _run(
        "analyse",
        "tests/fixtures/canonical_tickets.csv",
        "--sla-policy",
        "examples/sla-policy.json",
        "--as-of",
        "2026-08-30T12:00:00Z",
        "--output-dir",
        link,
    )
    assert cp.returncode == 4
    assert "already exists" in cp.stderr


def test_analyse_no_partial_output_on_failure():
    """When validation fails, no .tmp directory or report is left behind."""
    import tempfile

    tmp = tempfile.mkdtemp()
    output_dir = os.path.join(tmp, "should-not-exist")

    cp = _run(
        "analyse",
        "tests/fixtures/canonical_tickets.csv",
        "--sla-policy",
        "tests/fixtures/sla-policy.json",
        "--as-of",
        "invalid-timestamp",
        "--output-dir",
        output_dir,
    )
    assert cp.returncode != 0
    assert not os.path.exists(output_dir)
    assert not os.path.exists(output_dir + ".tmp")


def test_analyse_dry_run_calculates_metrics_without_writing(tmp_path):
    """Dry-run validates and calculates metrics without publishing output."""
    output_dir = tmp_path / "report"
    history_db = tmp_path / "history.sqlite"

    cp = _run(
        "analyse",
        "tests/fixtures/canonical_tickets.csv",
        "--sla-policy",
        "examples/sla-policy.json",
        "--as-of",
        "2026-08-30T12:00:00Z",
        "--output-dir",
        str(output_dir),
        "--history-db",
        str(history_db),
        "--dry-run",
    )
    assert cp.returncode == 0, cp.stderr
    assert "Dry run complete" in cp.stdout
    assert "Report written" not in cp.stdout
    assert not output_dir.exists()
    assert not history_db.exists()
