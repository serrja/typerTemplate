"""Materialize a Copier source from a pinned source snapshot and explicit overlay."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

app = typer.Typer(
    help=__doc__,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
BASE_TEMPLATE_VERSION = "0.3.0.dev0"
BASE_ANSWERS_SCHEMA = 1


class CompositionConfig(BaseModel):
    """Closed, versioned input schema; filesystem authorization remains separate."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    template_version: str = Field(alias="templateVersion", min_length=1)
    answers_schema: Annotated[int, Field(strict=True, gt=0)] = Field(
        alias="answersSchema"
    )
    snapshot: str
    overlay: str
    output: str
    include: list[str]
    include_repository: list[str] = Field(
        default_factory=list, alias="includeRepository"
    )
    allow_override: list[str] = Field(default_factory=list, alias="allowOverride")
    retain_base: dict[str, str] = Field(default_factory=dict, alias="retainBase")
    smoke_answers: dict[str, str | bool | int] = Field(alias="smokeAnswers")


def source_files(root: Path) -> dict[str, Path]:
    """List regular source files, without caches or symlinks escaping the root."""
    found: dict[str, Path] = {}
    if not root.is_dir():
        raise ValueError(f"Missing source directory: {root}")
    for path in root.rglob("*"):
        if any(part in IGNORED_PARTS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink in template source: {path}")
        if path.is_file() and path.suffix != ".pyc":
            found[path.relative_to(root).as_posix()] = path
    return found


def content_identity(files: dict[str, Path]) -> str:
    """Hash paths and bytes so renames also change the source identity."""
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def copy_files(files: dict[str, Path], destination: Path) -> None:
    for name, path in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def load_config(repo: Path) -> dict[str, Any]:
    with (repo / "composition.toml").open("rb") as file:
        data = tomllib.load(file)
    try:
        return CompositionConfig.model_validate(data).model_dump(by_alias=True)
    except ValidationError:
        raise ValueError(
            "Invalid composition contract; check field types and schema"
        ) from None


def stamp_base(repo: Path) -> str:
    """Stamp the direct Python Copier source without hashing the stamp itself."""
    files = {
        f"template/{name}": path
        for name, path in source_files(repo / "template").items()
        if name != ".template-source.json"
    }
    files["copier.yml"] = repo / "copier.yml"
    files["tools/compose_template.py"] = repo / "tools/compose_template.py"
    shared_tests = repo / "tools/test_composition.py"
    if shared_tests.is_file():
        files["tools/test_composition.py"] = shared_tests
    identity = content_identity(files)
    (repo / "template/.template-source.json").write_text(
        json.dumps(
            {
                "sourceContentSha256": identity,
                "templateVersion": BASE_TEMPLATE_VERSION,
                "answersSchema": BASE_ANSWERS_SCHEMA,
                "publication": (
                    "local worktree; release status must be checked separately"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return identity


def refresh_snapshot(repo: Path, base: Path, snapshot: Path) -> None:
    """Refresh only the declared parent Copier tree, never its Git metadata."""
    source = base / "template"
    files = source_files(source)
    parent_config = base / "copier.yml"
    parent_tool = base / "tools/compose_template.py"
    parent_tests = base / "tools/test_composition.py"
    parent_record = base / "composition.lock.json"
    for name in ("composeTemplate.py", "compose_template.py", "test_composition.py"):
        derived_tool = repo / "tools" / name
        previous_tool = snapshot / "tools" / name
        if derived_tool.is_file() and (
            not previous_tool.is_file()
            or derived_tool.read_bytes() != previous_tool.read_bytes()
        ):
            raise ValueError(
                "Derivative composition tool has local edits; preserve and review them"
            )
    if not parent_config.is_file():
        raise ValueError(f"Missing Copier configuration: {parent_config}")
    staging = Path(
        tempfile.mkdtemp(prefix="templateSource-", dir=os.environ.get("TMPDIR"))
    )
    try:
        copy_files(files, staging / "template")
        shutil.copy2(parent_config, staging / "copier.yml")
        source_set = {f"template/{key}": value for key, value in files.items()} | {
            "copier.yml": parent_config
        }
        if parent_tool.is_file():
            (staging / "tools").mkdir()
            shutil.copy2(parent_tool, staging / "tools/compose_template.py")
            source_set["tools/compose_template.py"] = parent_tool
        if parent_tests.is_file():
            (staging / "tools").mkdir(exist_ok=True)
            shutil.copy2(parent_tests, staging / "tools/test_composition.py")
            source_set["tools/test_composition.py"] = parent_tests
        if parent_record.is_file():
            shutil.copy2(parent_record, staging / "composition.lock.json")
            source_set["composition.lock.json"] = parent_record
        identity = content_identity(source_set)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=base,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=no"],
            cwd=base,
            capture_output=True,
            text=True,
            check=False,
        )
        provenance = {
            "contentSha256": identity,
            "head": head.stdout.strip() if head.returncode == 0 else None,
            "dirtyTracked": bool(dirty.stdout.strip())
            if dirty.returncode == 0
            else None,
            "note": (
                "Content identity is authoritative; "
                "HEAD does not identify dirty content."
            ),
        }
        (staging / "sourceIdentity.json").write_text(
            json.dumps(provenance, indent=2) + "\n"
        )
        if snapshot.exists():
            old_files = source_files(snapshot)
            old_identity = content_identity(
                {
                    key: value
                    for key, value in old_files.items()
                    if key != "sourceIdentity.json"
                }
            )
            old_record = json.loads((snapshot / "sourceIdentity.json").read_text())
            if old_identity != old_record["contentSha256"]:
                raise ValueError(
                    "Pinned parent snapshot has local edits; "
                    "preserve and review them first"
                )
            shutil.rmtree(snapshot)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(snapshot))
        legacy_tool = repo / "tools/composeTemplate.py"
        legacy_tool.unlink(missing_ok=True)
        if parent_tool.is_file():
            (repo / "tools").mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                snapshot / "tools/compose_template.py",
                repo / "tools/compose_template.py",
            )
        if parent_tests.is_file():
            (repo / "tools").mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                snapshot / "tools/test_composition.py",
                repo / "tools/test_composition.py",
            )
        else:
            (repo / "tools/test_composition.py").unlink(missing_ok=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def assemble(repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    validated = CompositionConfig.model_validate(config)
    template_version = validated.template_version
    answers_schema = validated.answers_schema
    snapshot = repo / config["snapshot"]
    for name in ("compose_template.py", "test_composition.py"):
        derived_tool = repo / "tools" / name
        if derived_tool.is_file():
            pinned_tool = snapshot / "tools" / name
            if (
                not pinned_tool.is_file()
                or derived_tool.read_bytes() != pinned_tool.read_bytes()
            ):
                raise ValueError(
                    "Derivative composition file differs from the pinned parent"
                )
    overlay = repo / config["overlay"]
    output = repo / config["output"]
    identity_record = json.loads((snapshot / "sourceIdentity.json").read_text())
    snapshot_files = source_files(snapshot)
    snapshot_identity = content_identity(
        {
            key: value
            for key, value in snapshot_files.items()
            if key != "sourceIdentity.json"
        }
    )
    if snapshot_identity != identity_record["contentSha256"]:
        raise ValueError("Pinned parent source identity does not match its snapshot")
    base_files = source_files(snapshot / "template")
    overlay_files = source_files(overlay)
    repository_files: dict[str, Path] = {}
    for name in config.get("includeRepository", []):
        path = repo / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or any(part in IGNORED_PARTS for part in Path(name).parts)
            or path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(repo.resolve())
        ):
            raise ValueError("Invalid repository-owned composition source")
        repository_files[name] = path
    if repository_files.keys() & (base_files.keys() | overlay_files.keys()):
        raise ValueError("Repository-owned source collides with parent or overlay")
    include = set(config["include"])
    missing = include - base_files.keys()
    if missing:
        raise ValueError(f"Missing parent source paths: {sorted(missing)}")
    overrides = set(config.get("allowOverride", []))
    conflicts = include & overlay_files.keys()
    if conflicts != overrides:
        raise ValueError(
            "Override contract mismatch: "
            f"actual={sorted(conflicts)}, declared={sorted(overrides)}"
        )
    staging = Path(
        tempfile.mkdtemp(prefix="composedTemplate-", dir=os.environ.get("TMPDIR"))
    )
    try:
        copy_files({key: base_files[key] for key in include}, staging)
        for old_name, new_name in config.get("retainBase", {}).items():
            if old_name not in base_files:
                raise ValueError(f"Missing retained base file: {old_name}")
            target = staging / new_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base_files[old_name], target)
        copy_files(overlay_files, staging)
        copy_files(repository_files, staging)
        overlay_identity = content_identity(overlay_files | repository_files)
        config_identity = hashlib.sha256(
            (repo / "composition.toml").read_bytes()
        ).hexdigest()
        copier_config_identity = hashlib.sha256(
            (repo / "copier.yml").read_bytes()
        ).hexdigest()
        source_identity = hashlib.sha256(
            f"{snapshot_identity}:{overlay_identity}:{config_identity}:{copier_config_identity}".encode()
        ).hexdigest()
        (staging / ".template-source.json").write_text(
            json.dumps(
                {
                    "sourceContentSha256": source_identity,
                    "templateVersion": template_version,
                    "answersSchema": answers_schema,
                    "parentContentSha256": snapshot_identity,
                    "overlayContentSha256": overlay_identity,
                    "configurationSha256": config_identity,
                    "copierConfigurationSha256": copier_config_identity,
                    "publication": (
                        "local composition; release status must be checked separately"
                    ),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        new_files = source_files(staging)
        previous_record_path = repo / "composition.lock.json"
        if output.exists() and previous_record_path.exists():
            previous_record = json.loads(previous_record_path.read_text())
            unexpected = source_files(output).keys() - previous_record["files"].keys()
            if unexpected:
                raise ValueError(
                    f"Unowned files in derivative output: {sorted(unexpected)}"
                )
            for name, expected in previous_record["files"].items():
                path = output / name
                if (
                    path.exists()
                    and hashlib.sha256(path.read_bytes()).hexdigest() != expected
                ):
                    raise ValueError(
                        f"Managed derivative source edited locally: {name}"
                    )
        elif output.exists() and any(output.iterdir()):
            raise ValueError(f"Output has no ownership record: {output}")
        owners = {name: "parent" for name in include}
        owners.update({name: "overlay" for name in overlay_files})
        owners.update({name: "repository" for name in repository_files})
        owners.update(
            {new: "retainedParent" for new in config.get("retainBase", {}).values()}
        )
        owners[".template-source.json"] = "compositor"
        record = {
            "templateVersion": template_version,
            "answersSchema": answers_schema,
            "parentContentSha256": snapshot_identity,
            "overlayContentSha256": overlay_identity,
            "sourceContentSha256": source_identity,
            "configurationSha256": config_identity,
            "copierConfigurationSha256": copier_config_identity,
            "files": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in sorted(new_files.items())
            },
            "owners": owners,
            "schema": 1,
        }
        if output.exists():
            shutil.rmtree(output)
        shutil.move(str(staging), str(output))
        previous_record_path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )
        return record
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def smoke_copier(repo: Path, answers: dict[str, Any]) -> None:
    """Use actual Copier to verify the assembled repository is renderable."""
    with tempfile.TemporaryDirectory(
        prefix="copierSmoke-", dir=os.environ.get("TMPDIR")
    ) as temporary:
        destination = Path(temporary) / "render"
        command = [sys.executable, "-m", "copier", "copy", "--defaults"]
        for name, value in answers.items():
            command.extend(
                [
                    "--data",
                    f"{name}="
                    f"{str(value).lower() if isinstance(value, bool) else value}",
                ]
            )
        subprocess.run([*command, str(repo), str(destination)], check=True)


@app.command(name="compose")
def compose(
    repo: Annotated[Path, typer.Option("--repo")] = Path("."),
    refresh_base: Annotated[Path | None, typer.Option("--refresh-base")] = None,
    smoke: Annotated[bool, typer.Option("--smoke")] = False,
    stamp: Annotated[bool, typer.Option("--stamp-base")] = False,
) -> None:
    """Materialize or explicitly refresh the declared source composition."""
    repo = repo.resolve()
    if stamp:
        print(f"pythonTemplate={stamp_base(repo)}")
        return
    config = load_config(repo)
    snapshot = repo / config["snapshot"]
    if refresh_base is not None:
        running_tool = Path(__file__).resolve()
        running_identity = hashlib.sha256(running_tool.read_bytes()).digest()
        refresh_snapshot(repo, refresh_base.resolve(), snapshot)
        if hashlib.sha256(running_tool.read_bytes()).digest() != running_identity:
            command = [sys.executable, str(running_tool), "--repo", str(repo)]
            if smoke:
                command.append("--smoke")
            subprocess.run(command, check=True)
            return
    record = assemble(repo, config)
    print(f"parent={record['parentContentSha256']}")
    print(f"overlay={record['overlayContentSha256']}")
    if smoke:
        smoke_copier(repo, config["smokeAnswers"])


def main() -> None:
    """Retain the maintenance callable while Typer owns argument parsing."""
    app(standalone_mode=False)


if __name__ == "__main__":
    app()
