"""Keep existing maintenance arguments and exit/status streams under Typer."""

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools import check_documentation, compose_template


@pytest.mark.parametrize("width", [88, 99, 100])
def test_portable_reporter_has_one_native_format_across_consumers(width: int) -> None:
    source = Path(__file__).resolve().parents[1] / "template/docker/coverage_report.py"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--line-length",
            str(width),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_compose_help_and_invalid_option_do_not_write(tmp_path: Path) -> None:
    runner = CliRunner()
    before = list(tmp_path.iterdir())
    for arguments, code in [(["--help"], 0), (["--unknown"], 2)]:
        result = runner.invoke(compose_template.app, arguments)
        assert result.exit_code == code
    assert list(tmp_path.iterdir()) == before


def test_document_option_retains_variadic_paths_and_failure_status(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    first = tmp_path / "первая страница.md"
    second = tmp_path / "second page.md"
    for path in [first, second]:
        path.write_text("# Guide\n\nAn opening paragraph.\n")
    result = runner.invoke(
        check_documentation.app, ["--documents", str(first), str(second)]
    )
    assert result.exit_code == 0, result.output
    assert "2 page(s)" in result.stdout
    assert not result.stderr
    missing = runner.invoke(
        check_documentation.app, ["--documents", str(tmp_path / "absent.md")]
    )
    assert missing.exit_code == 1
    assert "document is missing" in missing.stderr
    assert runner.invoke(check_documentation.app, ["--documents"]).exit_code == 2
    assert runner.invoke(check_documentation.app, [str(first)]).exit_code == 2


def test_composition_schema_uses_explicit_aliases_and_hides_invalid_input() -> None:
    from pydantic import ValidationError

    secret = "SYNTHETIC_SECRET_DO_NOT_LOG"
    try:
        compose_template.CompositionConfig.model_validate({"templateVersion": secret})
    except ValidationError as error:
        assert secret not in str(error)
    else:
        raise AssertionError("Incomplete contract accepted")
    schema = compose_template.CompositionConfig.model_json_schema()
    assert "templateVersion" not in schema["properties"]
    assert "answersSchema" in schema["properties"]
    assert "template_version" not in schema["properties"]
