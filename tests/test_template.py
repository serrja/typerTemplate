"""Exercise real Copier renders and a Git-ref update of this source."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COPIER = [sys.executable, "-m", "copier"]


@pytest.mark.parametrize("profile", ["cli"])
def test_no_git_sdist_omits_private_runtime_files_and_retains_legitimate_core(
    tmp_path: Path, profile: str
) -> None:
    source, revision = _source(tmp_path)
    output = tmp_path / "output"
    _run(
        *COPIER,
        "copy",
        "--quiet",
        "--defaults",
        f"--vcs-ref={revision}",
        str(source),
        str(output),
        "-d",
        "projectName=privacyProject",
        "-d",
        f"profile={profile}",
        "-d",
        "withCi=false",
    )
    markers = [
        ".aws/credentials",
        ".agents/private.txt",
        ".codex/private.txt",
        ".serena/private.txt",
        ".local/private.txt",
        "backups/private.txt",
        ".env.private",
        "core.1234",
        ".coverage",
        ".coverage.worker",
    ]
    for name in markers:
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SYNTHETIC-PRIVATE-MARKER")
        path.chmod(0)
    core = output / "src/privacyproject/core/kept.py"
    core.parent.mkdir()
    core.write_text('"""Legitimate package module."""\n')
    (output / ".coveragerc").write_text("[run]\nbranch = true\n")
    try:
        _run(
            "uv",
            "build",
            "--sdist",
            "--offline",
            "--no-build-isolation",
            "--python",
            sys.executable,
            "--cache-dir",
            str(tmp_path / "empty-build-cache"),
            cwd=output,
        )
        archive = next((output / "dist").glob("*.tar.gz"))
        with tarfile.open(archive) as bundle:
            names = {name.split("/", 1)[-1] for name in bundle.getnames()}
        assert not set(markers) & names
        assert "src/privacyproject/core/kept.py" in names
        assert ".coveragerc" in names
    finally:
        for name in markers:
            (output / name).chmod(0o600)


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, check=True, timeout=60
    )
    return result.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    return _run("git", *args, cwd=repo)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "copier.yml", "template")
    _git(
        repo,
        "-c",
        "user.name=Template Test",
        "-c",
        "user.email=template-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(ROOT / "copier.yml", source / "copier.yml")
    shutil.copytree(ROOT / "template", source / "template")
    _git(source, "init", "--quiet")
    for directory in [".vscode", ".serena"]:
        marker = source / "template" / directory / "synthetic.txt"
        marker.parent.mkdir()
        marker.write_text("synthetic editor state")
        _git(source, "add", "--force", str(marker.relative_to(source)))
    return source, _commit(source, "base template")


@pytest.mark.parametrize(
    ("profile", "persistence"),
    [("cli", False)],
)
def test_render_profiles(tmp_path: Path, profile: str, persistence: bool) -> None:
    source, revision = _source(tmp_path)
    output = tmp_path / "output"
    _run(
        *COPIER,
        "copy",
        "--quiet",
        "--defaults",
        f"--vcs-ref={revision}",
        str(source),
        str(output),
        "-d",
        "projectName=sampleProject",
        "-d",
        f"profile={profile}",
        "-d",
        f"withPersistence={str(persistence).lower()}",
        "-d",
        "withCi=false",
    )
    package = output / "src/sampleproject"
    assert package.is_dir()
    assert not (output / ".vscode").exists()
    assert not (output / ".serena").exists()
    assert (package / "__init__.py").is_file()
    assert "N" in (output / "pyproject.toml").read_text()
    assert (package / "app.py").exists() is (profile == "service")
    assert (package / "persistence.py").exists() is persistence
    assert (output / "migrations/env.py").exists() is persistence
    assert (output / "native/core.cpp").exists() is (profile == "native")


def test_update_preserves_owner_files_and_detects_overlap(tmp_path: Path) -> None:
    source, first = _source(tmp_path)
    output = tmp_path / "output"
    _run(
        *COPIER,
        "copy",
        "--quiet",
        "--defaults",
        f"--vcs-ref={first}",
        str(source),
        str(output),
        "-d",
        "projectName=sampleProject",
        "-d",
        "profile=cli",
        "-d",
        "withCi=false",
    )
    _git(output, "init", "--quiet")
    _git(output, "add", ".")
    _git(
        output,
        "-c",
        "user.name=Template Test",
        "-c",
        "user.email=template-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "initial render",
    )
    owner = output / "PROJECT.md"
    owner.write_text(owner.read_text() + "\nOwner note is retained.\n")
    local_make = output / "Makefile.local"
    local_make.write_text("owner-check:\n\t@printf '%s\\n' owner-command\n")
    _git(output, "add", "PROJECT.md", "Makefile.local")
    _git(
        output,
        "-c",
        "user.name=Template Test",
        "-c",
        "user.email=template-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "owner note",
    )

    readme = source / "template/README.md.jinja"
    readme.write_text(readme.read_text() + "\nUpdated source marker.\n")
    second = _commit(source, "update managed README")
    _run(*COPIER, "update", "--quiet", "--defaults", f"--vcs-ref={second}", cwd=output)
    assert "Owner note is retained." in owner.read_text()
    assert not (output / ".vscode").exists()
    assert not (output / ".serena").exists()
    assert "Updated source marker." in (output / "README.md").read_text()
    assert (
        _run("make", "--no-print-directory", "owner-check", cwd=output)
        == "owner-command"
    )
    assert not _git(output, "ls-files", "-u")
    assert not list(output.rglob("*.rej"))

    assert os.path.isfile(output / ".copier-answers.yml")

    # Commit the accepted update, then change the same README line on both sides.
    _git(output, "add", ".")
    _git(
        output,
        "-c",
        "user.name=Template Test",
        "-c",
        "user.email=template-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "accept update",
    )
    rendered = output / "README.md"
    rendered.write_text(
        rendered.read_text().replace(
            "A typed Python command-line starting point.", "User-owned CLI description."
        )
    )
    _git(output, "add", "README.md")
    _git(
        output,
        "-c",
        "user.name=Template Test",
        "-c",
        "user.email=template-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "edit README locally",
    )
    readme.write_text(
        readme.read_text().replace(
            "A typed Python command-line starting point.",
            "New template CLI description.",
        )
    )
    third = _commit(source, "change README wording")
    result = subprocess.run(
        [*COPIER, "update", "--quiet", "--defaults", f"--vcs-ref={third}"],
        cwd=output,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    conflicts = bool(_git(output, "ls-files", "-u"))
    conflicts = conflicts or "<<<<<<<" in rendered.read_text()
    conflicts = conflicts or bool(list(output.rglob("*.rej")))
    assert conflicts, (
        f"Copier silently resolved an overlapping edit: {result.returncode}"
    )


@pytest.mark.parametrize("name", ["x", "examplecli"])
def test_short_cli_name_keeps_formatter_stable(tmp_path: Path, name: str) -> None:
    source, revision = _source(tmp_path)
    output = tmp_path / "output"
    _run(
        *COPIER,
        "copy",
        "--quiet",
        "--defaults",
        f"--vcs-ref={revision}",
        str(source),
        str(output),
        "-d",
        f"projectName={name}",
        "-d",
        "profile=cli",
        "-d",
        "withCi=false",
    )
    _run(sys.executable, "-m", "ruff", "check", str(output / "src"))
    _run(sys.executable, "-m", "ruff", "format", "--check", str(output / "src"))


def test_project_and_foundation_test_names_do_not_collide(tmp_path: Path) -> None:
    source, revision = _source(tmp_path)
    output = tmp_path / "output"
    _run(
        *COPIER,
        "copy",
        "--quiet",
        "--defaults",
        f"--vcs-ref={revision}",
        str(source),
        str(output),
        "-d",
        "projectName=sampleLibrary",
        "-d",
        "profile=cli",
        "-d",
        "withCi=false",
    )
    for directory in ("tests", "tests/foundation"):
        file = output / directory / "test_collision.py"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            '"""Test the collected module identity."""\n'
            "from __future__ import annotations\n"
            "from pathlib import Path\n"
            "def test_module_identity() -> None:\n"
            f'    assert Path(__file__).parent.name == "{file.parent.name}"\n'
        )
    _run(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_collision.py",
        "tests/foundation/test_collision.py",
        cwd=output,
    )
