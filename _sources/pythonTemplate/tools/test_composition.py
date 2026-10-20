"""Verify composition, provenance, ownership, and failure cleanup at real boundaries."""

from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import compose_template as composer

TOOL = Path(composer.__file__)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    write(root / "template/base.txt", "Parent content\n")
    write(root / "template/nested/value.txt", "Nested content\n")
    write(root / "copier.yml", "_subdirectory: template\n")
    write(root / "pyproject.toml", '[project]\nname="base"\nversion="0.3.0.dev1"\n')
    write(root / "tools/compose_template.py", TOOL.read_text())
    write(root / "tools/test_composition.py", Path(__file__).read_text())
    return root


@pytest.fixture
def derivative(tmp_path: Path, base: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "derivative"
    root.mkdir()
    write(root / "pyproject.toml", '[project]\nname="derived"\nversion="0.2.0.dev0"\n')
    write(root / "overlay/base.txt", "Derivative content\n")
    write(root / "overlay/owned.txt", "Overlay content\n")
    write(
        root / "composition.toml",
        "answersSchema = 1\n"
        'snapshot = "_sources/base"\noverlay = "overlay"\noutput = "template"\n'
        'include = ["base.txt", "nested/value.txt"]\nallowOverride = ["base.txt"]\n'
        '[retainBase]\n"base.txt" = "_base/base.txt"\n'
        '[smokeAnswers]\nenabled = true\nname = "example"\n',
    )
    write(
        root / "copier.yml",
        "_subdirectory: template\nenabled:\n  type: bool\n  default: false\n"
        'name:\n  type: str\n  default: "sample"\n',
    )
    composer.refresh_snapshot(root, base, root / "_sources/base")
    return root, composer.load_config(root)


def test_source_filter_and_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing source"):
        composer.source_files(tmp_path / "missing")
    first = write(tmp_path / "nested/input.txt", "one")
    write(tmp_path / ".git/private", "ignored")
    write(tmp_path / ".vscode/settings.json", "ignored")
    write(tmp_path / ".serena/project.yml", "ignored")
    write(tmp_path / "__pycache__/input.pyc", "ignored")
    write(tmp_path / "input.pyc", "ignored")
    files = composer.source_files(tmp_path)
    assert files == {"nested/input.txt": first}
    identity = composer.content_identity(files)
    assert composer.content_identity(dict(reversed(list(files.items())))) == identity
    first.write_text("two")
    assert composer.content_identity(files) != identity
    assert composer.content_identity(
        {"renamed.txt": first}
    ) != composer.content_identity(files)
    (tmp_path / "link").symlink_to(first)
    with pytest.raises(ValueError, match="Symlink"):
        composer.source_files(tmp_path)


def test_stamp_is_stable_and_covers_configuration_and_tool(base: Path) -> None:
    first = composer.stamp_base(base)
    assert composer.stamp_base(base) == first
    stamp = json.loads((base / "template/.template-source.json").read_text())
    assert stamp["sourceContentSha256"] == first
    assert stamp["answersSchema"] == composer.BASE_ANSWERS_SCHEMA
    assert stamp["templateVersion"] == composer.template_version(base)
    write(base / "copier.yml", "_subdirectory: template\n_answers_file: answers.yml\n")
    assert composer.stamp_base(base) != first


def test_refresh_records_git_and_parent_composition(tmp_path: Path, base: Path) -> None:
    root = tmp_path / "derivative"
    root.mkdir()
    snapshot = root / "_sources/base"
    write(base / "composition.lock.json", '{"schema": 1}\n')
    git(base, "init", "--quiet")
    git(
        base,
        "add",
        "copier.yml",
        "pyproject.toml",
        "template",
        "tools",
        "composition.lock.json",
    )
    git(
        base,
        "-c",
        "user.name=Template Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "Initial source",
    )
    composer.refresh_snapshot(root, base, snapshot)
    record = json.loads((snapshot / "sourceIdentity.json").read_text())
    assert record["head"] == git(base, "rev-parse", "HEAD")
    assert record["dirtyTracked"] is False
    assert (snapshot / "composition.lock.json").read_text() == '{"schema": 1}\n'
    write(base / "template/base.txt", "New parent content\n")
    composer.refresh_snapshot(root, base, snapshot)
    assert json.loads((snapshot / "sourceIdentity.json").read_text())["dirtyTracked"]
    assert (snapshot / "template/base.txt").read_text() == "New parent content\n"


def test_refresh_without_tool_or_git(tmp_path: Path, base: Path) -> None:
    (base / "tools/compose_template.py").unlink()
    (base / "tools/test_composition.py").unlink()
    root = tmp_path / "derivative"
    root.mkdir()
    snapshot = root / "_sources/base"
    composer.refresh_snapshot(root, base, snapshot)
    record = json.loads((snapshot / "sourceIdentity.json").read_text())
    assert record["head"] is None
    assert record["dirtyTracked"] is None
    assert not (snapshot / "tools").exists()


@pytest.mark.parametrize(
    "tool_name", ["composeTemplate.py", "compose_template.py", "test_composition.py"]
)
def test_refresh_rejects_unreviewed_local_tool(
    tmp_path: Path, base: Path, tool_name: str
) -> None:
    root = tmp_path / "derivative"
    write(root / "tools" / tool_name, "Local implementation\n")
    with pytest.raises(ValueError, match="local edits"):
        composer.refresh_snapshot(root, base, root / "_sources/base")
    assert (root / "tools" / tool_name).read_text() == "Local implementation\n"


def test_refresh_retains_snapshot_on_missing_config(
    derivative: tuple[Path, dict[str, Any]], base: Path
) -> None:
    root, _ = derivative
    snapshot = root / "_sources/base"
    before = composer.content_identity(composer.source_files(snapshot))
    (base / "copier.yml").unlink()
    with pytest.raises(ValueError, match="Missing Copier configuration"):
        composer.refresh_snapshot(root, base, snapshot)
    assert composer.content_identity(composer.source_files(snapshot)) == before


def test_refresh_rejects_edited_snapshot(
    derivative: tuple[Path, dict[str, Any]], base: Path
) -> None:
    root, _ = derivative
    snapshot = root / "_sources/base"
    write(snapshot / "template/base.txt", "Local snapshot edit\n")
    with pytest.raises(ValueError, match="snapshot has local edits"):
        composer.refresh_snapshot(root, base, snapshot)
    assert (snapshot / "template/base.txt").read_text() == "Local snapshot edit\n"


def test_refresh_rejects_tool_changed_since_snapshot(
    derivative: tuple[Path, dict[str, Any]], base: Path
) -> None:
    root, _ = derivative
    write(root / "tools/compose_template.py", "Local tool edit\n")
    with pytest.raises(ValueError, match="local edits"):
        composer.refresh_snapshot(root, base, root / "_sources/base")


def test_assemble_has_deterministic_provenance_and_ownership(
    derivative: tuple[Path, dict[str, Any]],
) -> None:
    root, config = derivative
    first = composer.assemble(root, config)
    assert composer.assemble(root, config) == first
    assert (root / "template/base.txt").read_text() == "Derivative content\n"
    assert (root / "template/_base/base.txt").read_text() == "Parent content\n"
    assert first["owners"]["base.txt"] == "overlay"
    assert first["owners"]["nested/value.txt"] == "parent"
    assert first["owners"]["_base/base.txt"] == "retainedParent"
    assert first["owners"][".template-source.json"] == "compositor"
    for name, expected in first["files"].items():
        assert (
            hashlib.sha256((root / "template" / name).read_bytes()).hexdigest()
            == expected
        )
    stamp = json.loads((root / "template/.template-source.json").read_text())
    assert stamp["sourceContentSha256"] == first["sourceContentSha256"]
    (root / "template/owned.txt").unlink()
    assert composer.assemble(root, config) == first
    write(root / "overlay/owned.txt", "Reviewed overlay update\n")
    assert (
        composer.assemble(root, config)["sourceContentSha256"]
        != first["sourceContentSha256"]
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("templateVersion", "0.9", "templateVersion"),
        ("answersSchema", "1", "answersSchema"),
        ("answersSchema", 0, "answersSchema"),
        ("answersSchema", True, "answersSchema"),
        ("include", ["missing.txt"], "Missing parent source"),
        ("allowOverride", [], "Override contract mismatch"),
        ("retainBase", {"missing.txt": "old.txt"}, "Missing retained base"),
    ],
)
def test_assemble_rejects_invalid_contract_without_output(
    derivative: tuple[Path, dict[str, Any]], key: str, value: Any, message: str
) -> None:
    root, config = derivative
    config[key] = value
    with pytest.raises(ValueError, match=message):
        composer.assemble(root, config)
    assert not (root / "template").exists()


@pytest.mark.parametrize("with_pinned_tool", [False, True])
def test_assemble_rejects_tool_drift(
    derivative: tuple[Path, dict[str, Any]], with_pinned_tool: bool
) -> None:
    root, config = derivative
    if with_pinned_tool:
        write(root / "tools/compose_template.py", "Edited tool\n")
    else:
        (root / "_sources/base/tools/compose_template.py").unlink()
    with pytest.raises(ValueError, match="differs from the pinned parent"):
        composer.assemble(root, config)


def test_assemble_without_local_tool(derivative: tuple[Path, dict[str, Any]]) -> None:
    root, config = derivative
    (root / "tools/compose_template.py").unlink()
    assert composer.assemble(root, config)["owners"]["owned.txt"] == "overlay"


def test_shared_cases_are_managed_and_removable(
    derivative: tuple[Path, dict[str, Any]], base: Path
) -> None:
    root, config = derivative
    cases = root / "tools/test_composition.py"
    assert cases.read_bytes() == (base / "tools/test_composition.py").read_bytes()
    cases.write_text("Local suite edit\n")
    with pytest.raises(ValueError, match="differs from the pinned parent"):
        composer.assemble(root, config)
    cases.write_bytes((base / "tools/test_composition.py").read_bytes())
    (base / "tools/test_composition.py").unlink()
    composer.refresh_snapshot(root, base, root / "_sources/base")
    assert not cases.exists()
    assert composer.assemble(root, config)["schema"] == 1


def test_stamp_without_shared_cases(base: Path) -> None:
    first = composer.stamp_base(base)
    (base / "tools/test_composition.py").unlink()
    assert composer.stamp_base(base) != first


def test_assemble_rejects_parent_drift(derivative: tuple[Path, dict[str, Any]]) -> None:
    root, config = derivative
    write(root / "_sources/base/template/base.txt", "Edited parent\n")
    with pytest.raises(ValueError, match="identity does not match"):
        composer.assemble(root, config)


@pytest.mark.parametrize("with_record", [False, True])
def test_assemble_preserves_unowned_output(
    derivative: tuple[Path, dict[str, Any]], with_record: bool
) -> None:
    root, config = derivative
    if with_record:
        composer.assemble(root, config)
    owner = write(root / "template/user.txt", "Must survive\n")
    with pytest.raises(ValueError, match=r"Unowned files|no ownership record"):
        composer.assemble(root, config)
    assert owner.read_text() == "Must survive\n"


def test_assemble_preserves_edited_managed_output(
    derivative: tuple[Path, dict[str, Any]],
) -> None:
    root, config = derivative
    composer.assemble(root, config)
    owner = write(root / "template/owned.txt", "Must survive\n")
    with pytest.raises(ValueError, match="edited locally"):
        composer.assemble(root, config)
    assert owner.read_text() == "Must survive\n"


def test_assemble_accepts_empty_output(derivative: tuple[Path, dict[str, Any]]) -> None:
    root, config = derivative
    (root / "template").mkdir()
    assert composer.assemble(root, config)["schema"] == 1


def test_real_copier_smoke_and_main(
    derivative: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config = derivative
    monkeypatch.setattr(sys, "argv", [str(TOOL), "--repo", str(root), "--smoke"])
    composer.main()
    record = json.loads((root / "composition.lock.json").read_text())
    output = capsys.readouterr().out
    assert f"parent={record['parentContentSha256']}" in output
    assert f"overlay={record['overlayContentSha256']}" in output
    assert record["answersSchema"] == config["answersSchema"]


def test_main_stamp_entrypoint(
    base: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", [str(TOOL), "--repo", str(base), "--stamp-base"])
    with pytest.raises(SystemExit) as status:
        runpy.run_path(str(TOOL), run_name="__main__")
    assert status.value.code == 0
    stamp = json.loads((base / "template/.template-source.json").read_text())
    assert f"pythonTemplate={stamp['sourceContentSha256']}" in capsys.readouterr().out


@pytest.mark.parametrize("changed_tool", [False, True])
@pytest.mark.parametrize("smoke", [False, True])
def test_main_refresh_restarts_only_when_tool_changes(
    derivative: tuple[Path, dict[str, Any]],
    base: Path,
    changed_tool: bool,
    smoke: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = derivative
    local_tool = root / "tools/compose_template.py"
    monkeypatch.setattr(composer, "__file__", str(local_tool))
    if changed_tool:
        write(base / "tools/compose_template.py", TOOL.read_text() + "\n")
    args = [str(local_tool), "--repo", str(root), "--refresh-base", str(base)]
    if smoke:
        args.append("--smoke")
    monkeypatch.setattr(sys, "argv", args)
    composer.main()
    assert (root / "composition.lock.json").is_file()
    assert local_tool.read_bytes() == (base / "tools/compose_template.py").read_bytes()


def test_refresh_removes_only_the_owned_legacy_tool(
    derivative: tuple[Path, dict[str, Any]], base: Path
) -> None:
    root, _ = derivative
    legacy = root / "tools/composeTemplate.py"
    pinned = root / "_sources/base/tools/composeTemplate.py"
    write(legacy, TOOL.read_text())
    write(pinned, TOOL.read_text())
    snapshot = root / "_sources/base"
    record = json.loads((snapshot / "sourceIdentity.json").read_text())
    record["contentSha256"] = composer.content_identity(
        {
            k: v
            for k, v in composer.source_files(snapshot).items()
            if k != "sourceIdentity.json"
        }
    )
    (snapshot / "sourceIdentity.json").write_text(json.dumps(record))
    composer.refresh_snapshot(root, base, snapshot)
    assert not legacy.exists()
    assert (root / "tools/compose_template.py").is_file()


def test_copier_failure_is_not_suppressed(
    derivative: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = derivative
    composer.assemble(root, composer.load_config(root))
    shutil.rmtree(root / "template")
    with pytest.raises(subprocess.CalledProcessError):
        composer.smoke_copier(root, {})


def test_canonical_repository_source_is_hashed_and_published(
    derivative: tuple[Path, dict[str, Any]],
) -> None:
    root, config = derivative
    source = write(root / ".github/scripts/admission.py", "print('first')\n")
    config["includeRepository"] = [".github/scripts/admission.py"]
    first = composer.assemble(root, config)
    assert first["owners"][".github/scripts/admission.py"] == "repository"
    assert (
        root / "template/.github/scripts/admission.py"
    ).read_bytes() == source.read_bytes()
    source.write_text("print('second')\n")
    second = composer.assemble(root, config)
    assert first["sourceContentSha256"] != second["sourceContentSha256"]


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "missing", "overlay/base.txt"]
)
def test_repository_source_cannot_escape_or_replace_other_owner(
    derivative: tuple[Path, dict[str, Any]], name: str
) -> None:
    root, config = derivative
    config["includeRepository"] = [name]
    if name == "overlay/base.txt":
        # A symlink is rejected even when it targets a regular source file.
        (root / "overlay/base.txt").unlink()
        (root / "overlay/base.txt").symlink_to(root / "copier.yml")
    with pytest.raises(ValueError):
        composer.assemble(root, config)


def test_repository_source_collision_is_rejected(
    derivative: tuple[Path, dict[str, Any]],
) -> None:
    root, config = derivative
    write(root / "base.txt", "cannot replace the parent/overlay\n")
    config["includeRepository"] = ["base.txt"]
    with pytest.raises(ValueError, match="collides"):
        composer.assemble(root, config)


def test_engineering_subset_adoption_and_local_edit_protection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owner"
    write(root / "template/docker/quality.mk", "first\n")
    write(root / "template/docker/source_identity.sh", "source\n")
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "source",
    )
    target = tmp_path / "consumer"
    target.mkdir()
    composer.sync_engineering(root, target, ["docker/quality.mk"])
    record = json.loads((target / ".github/engineering-source.json").read_text())
    assert record["files"]["docker/quality.mk"]["revision"] == git(
        root, "rev-parse", "HEAD"
    )
    write(target / "docker/quality.mk", "owner edit\n")
    with pytest.raises(ValueError, match="unreviewed local edits"):
        composer.sync_engineering(
            root, target, ["docker/source_identity.sh", "docker/quality.mk"]
        )
    assert not (target / "docker/source_identity.sh").exists()
    assert (target / "docker/quality.mk").read_text() == "owner edit\n"
    with pytest.raises(ValueError, match="explicit portable"):
        composer.sync_engineering(root, target, ["../outside"])
    write(root / "template/docker/quality.mk", "uncommitted\n")
    with pytest.raises(ValueError, match="clean source commit"):
        composer.sync_engineering(root, target, ["docker/source_identity.sh"])
