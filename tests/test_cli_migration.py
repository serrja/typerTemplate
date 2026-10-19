"""Verify an explicit source handoff, real conflicts and a subsequent Copier update."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(root: Path, *args: str) -> str:
    result = subprocess.run(
        args, cwd=root, text=True, capture_output=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def git(root: Path, *args: str) -> str:
    return run(root, "git", *args)


def commit(root: Path, message: str) -> str:
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Migration Test",
        "-c",
        "user.email=migration@example.invalid",
        "commit",
        "-qm",
        message,
    )
    return git(root, "rev-parse", "HEAD")


def test_historical_cli_handoff_and_subsequent_real_update(tmp_path: Path) -> None:
    fixture = ROOT / "tests/fixtures/python-cli-9a4b58e.tar.gz"
    provenance = json.loads((fixture.parent / "provenance.json").read_text())
    assert (
        hashlib.sha256(fixture.read_bytes()).hexdigest() == provenance["archiveSha256"]
    )
    old = tmp_path / "historical source"
    old.mkdir()
    with tarfile.open(fixture) as bundle:
        bundle.extractall(old, filter="data")
    git(old, "init", "-q")
    old_revision = commit(old, "frozen historical Python CLI input")
    new = tmp_path / "new source"
    new.mkdir()
    shutil.copy2(ROOT / "copier.yml", new / "copier.yml")
    shutil.copytree(ROOT / "template", new / "template")
    git(new, "init", "-q")
    new_revision = commit(new, "reviewed Typer specialization")
    app = tmp_path / "application"
    app.mkdir()
    copier = [sys.executable, "-m", "copier"]
    data = [
        "-d",
        "projectName=sampleProject",
        "-d",
        "profile=cli",
        "-d",
        "withCi=false",
    ]
    run(
        tmp_path,
        *copier,
        "copy",
        "--defaults",
        "--quiet",
        f"--vcs-ref={old_revision}",
        str(old),
        str(app),
        *data,
    )
    git(app, "init", "-q")
    baseline = commit(app, "original generated application")
    patch = tmp_path / "migration patch"
    git(tmp_path, "clone", "--quiet", "--no-hardlinks", str(app), str(patch))
    conflict = tmp_path / "conflicting application"
    git(tmp_path, "clone", "--quiet", "--no-hardlinks", str(app), str(conflict))
    owned = app / "src/sampleproject/own_models.py"
    owned.write_text('"""Project-owned models remain application-owned."""\n')
    owner_doc = app / "PROJECT.md"
    owner_doc.write_text(owner_doc.read_text() + "\nOwner project decisions.\n")
    commit(app, "project-owned evolution")
    rendered = tmp_path / "new render"
    run(
        tmp_path,
        *copier,
        "copy",
        "--defaults",
        "--quiet",
        f"--vcs-ref={new_revision}",
        str(new),
        str(rendered),
        *data,
    )
    for name in git(patch, "ls-files").splitlines():
        (patch / name).unlink()
    shutil.copytree(rendered, patch, dirs_exist_ok=True)
    migration = commit(patch, "Copier-generated source migration patch")
    assert git(patch, "rev-parse", "HEAD^") == baseline
    git(app, "fetch", "--quiet", str(patch), migration)
    git(
        app,
        "-c",
        "user.name=Migration Test",
        "-c",
        "user.email=migration@example.invalid",
        "cherry-pick",
        migration,
    )
    assert owned.is_file() and "Owner project decisions." in owner_doc.read_text()
    settings = app / "src/sampleproject/settings.py"
    settings.write_text(settings.read_text() + "\n# Project-owned settings note.\n")
    before = settings.read_bytes()
    commit(app, "customize application-owned settings")
    readme = new / "template/README.md.jinja"
    readme.write_text(readme.read_text() + "\nManaged update after source handoff.\n")
    future = commit(new, "reviewed managed update")
    run(app, *copier, "update", "--defaults", "--quiet", f"--vcs-ref={future}")
    assert settings.read_bytes() == before
    assert owned.is_file() and "Owner project decisions." in owner_doc.read_text()
    assert "Managed update after source handoff." in (app / "README.md").read_text()
    assert not git(app, "ls-files", "-u") and not list(app.rglob("*.rej"))

    # Existing commands require three-way review; never clobber owner changes.
    cli = conflict / "src/sampleproject/cli.py"
    cli.write_text(
        cli.read_text().replace(
            "Run the command-line application.", "Owner-maintained command behavior."
        )
    )
    commit(conflict, "owner command change")
    git(conflict, "fetch", "--quiet", str(patch), migration)
    failed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Migration Test",
            "-c",
            "user.email=migration@example.invalid",
            "cherry-pick",
            migration,
        ],
        cwd=conflict,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert failed.returncode != 0
    assert git(conflict, "ls-files", "-u")
    assert "Owner-maintained command behavior." in cli.read_text()
