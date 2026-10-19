"""Exercise advisory policy with isolated real subprocess suites and failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPORTER = Path(__file__).resolve().parents[1] / "template/docker/coverage_report.py"


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Each real fixture owns its collector; do not merge synthetic sample.py into
    # the maintenance suite through coverage.py's subprocess auto-start environment.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    return subprocess.run(
        args,
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def suite(tmp_path: Path, *, failing: bool = False, branch: bool = True) -> Path:
    (tmp_path / "sample.py").write_text(
        "def choose(flag):\n    if flag:\n        return 1\n    return 2\n"
        "def unused():\n    return 3\n"
    )
    (tmp_path / "test_sample.py").write_text(
        "from sample import choose\n"
        f"def test_choose():\n    assert choose(True) == {2 if failing else 1}\n"
    )
    path = tmp_path / "coverage.json"
    result = run(
        tmp_path,
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--cov=sample",
        *(["--cov-branch"] if branch else []),
        f"--cov-report=json:{path}",
    )
    assert result.returncode == (1 if failing else 0), result.stdout + result.stderr
    return path


def invoke(tmp_path: Path, path: Path) -> subprocess.CompletedProcess[str]:
    return run(tmp_path, sys.executable, str(REPORTER), str(path))


def test_real_incomplete_suite_succeeds_with_advisory(tmp_path: Path) -> None:
    path = suite(tmp_path)
    data = json.loads(path.read_text())
    assert 60 <= data["totals"]["percent_covered"] < 100
    result = invoke(tmp_path, path)
    assert result.returncode == 0, result.stderr
    assert "WARNING: Coverage advisory:" in result.stdout
    assert "branch" in result.stdout


def test_failing_assertion_remains_failure(tmp_path: Path) -> None:
    suite(tmp_path, failing=True)
    (tmp_path / "Makefile").write_text(
        f"test:\n\t{sys.executable} -m pytest -q --cov=sample --cov-report=json\n"
        f"\t{sys.executable} {REPORTER} coverage.json\n"
    )
    result = run(tmp_path, "make", "test")
    assert result.returncode != 0
    assert "Coverage advisory" not in result.stdout


def test_build_failure_is_not_coverage_warning(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("build:\n\tfalse\n")
    assert run(tmp_path, "make", "build").returncode != 0


@pytest.mark.parametrize("text", ["{broken", "{}", '{"files":{}}'])
def test_corrupt_required_report_fails(tmp_path: Path, text: str) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(text)
    assert invoke(tmp_path, path).returncode != 0


def test_branch_not_measured_is_explicit(tmp_path: Path) -> None:
    result = invoke(tmp_path, suite(tmp_path, branch=False))
    assert result.returncode == 0
    assert "| branch | notMeasured |" in result.stdout


def test_missing_and_stale_reports_fail(tmp_path: Path) -> None:
    assert invoke(tmp_path, tmp_path / "missing.json").returncode != 0
    path = suite(tmp_path)
    started = tmp_path / "started"
    started.touch()
    os.utime(path, (1, 1))
    result = run(
        tmp_path, sys.executable, str(REPORTER), str(path), "--after", str(started)
    )
    assert result.returncode != 0
