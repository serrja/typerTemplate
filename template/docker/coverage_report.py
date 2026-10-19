"""Validate coverage evidence and report advisory line and branch measurements."""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    help=__doc__,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


class ReportFormat(StrEnum):
    COVERAGE_PY = "coveragepy"
    COBERTURA = "cobertura"


def counter(hit: Any, total: Any) -> tuple[int, int]:
    """Reject invalid counters rather than turning broken reports into warnings."""
    if type(hit) is not int or type(total) is not int or not 0 <= hit <= total:
        raise ValueError("Invalid coverage counters")
    return hit, total


def measurements(path: Path, report_format: str) -> dict[str, tuple[int, int] | None]:
    """Read independent metrics without mixing languages or inferring branches."""
    if report_format == "coveragepy":
        data = json.loads(path.read_text())
        if not data["files"]:
            raise ValueError("Empty coverage source inventory")
        totals = data["totals"]
        lines = counter(totals["covered_lines"], totals["num_statements"])
        branches = (
            counter(totals["covered_branches"], totals["num_branches"])
            if data["meta"]["branch_coverage"]
            else None
        )
    else:
        root = ET.parse(path).getroot()
        if root.tag != "coverage" or not root.findall(".//class"):
            raise ValueError("Empty or invalid Cobertura report")
        lines = counter(
            int(root.attrib["lines-covered"]),
            int(root.attrib["lines-valid"]),
        )
        branches = counter(
            int(root.attrib["branches-covered"]),
            int(root.attrib["branches-valid"]),
        )
    if lines[1] == 0:
        raise ValueError("Runtime coverage has no executable lines")
    return {"statement/line": lines, "branch": branches}


def annotation(message: str) -> str:
    """Escape workflow command data, including untrusted report labels."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def report(path: Path, report_format: str, output: Path, after: Path | None) -> str:
    if after is not None and path.stat().st_mtime_ns < after.stat().st_mtime_ns:
        raise ValueError("Coverage report predates this test execution")
    rows = [
        "| Metric | Covered / total | Missing | Coverage |",
        "| --- | --- | --- | --- |",
    ]
    for name, value in measurements(path, report_format).items():
        if value is None:
            rows.append(f"| {name} | notMeasured | notMeasured | notMeasured |")
            continue
        hit, total = value
        percentage = "notApplicable (no branches)"
        if total:
            percentage = f"{100 * hit / total:.2f}%"
        rows.append(f"| {name} | {hit} / {total} | {total - hit} | {percentage} |")
        if hit < total:
            message = f"Coverage advisory: {name} {hit}/{total} ({percentage}); "
            message += f"{total - hit} missing."
            sys.stdout.write(f"WARNING: {message}\n")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                sys.stdout.write(f"::warning::{annotation(message)}\n")
    text = "\n".join(rows)
    text += "\n\nCoverage percentages are advisory; test and report errors fail.\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    sys.stdout.write(text + "\n")
    return text


@app.command(name="report")
def main(
    path: Annotated[Path, typer.Argument()],
    report_format: Annotated[
        ReportFormat,
        typer.Option("--format"),
    ] = ReportFormat.COVERAGE_PY,
    output: Annotated[Path, typer.Option("--output")] = Path("coverage/summary.md"),
    after: Annotated[Path | None, typer.Option("--after")] = None,
) -> None:
    report(path, report_format.value, output, after)


if __name__ == "__main__":
    app()
