"""Native Python metadata and evidence serialization; this tool runs no commands."""

from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import io
import json
import platform
import tarfile
import tomllib
import zipfile
from datetime import UTC, datetime
from email.parser import BytesParser
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator

app = typer.Typer(pretty_exceptions_show_locals=False)
receipt_app = typer.Typer(pretty_exceptions_show_locals=False)
app.add_typer(receipt_app, name="receipt")
REPORTS = Path("coverage")


class Source(BaseModel):
    """A worktree identity does not imply a published or integrated commit."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    commit: str | None = Field(pattern=r"^[0-9a-f]{40,64}$")
    dirty: bool
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    name: str
    command: str
    status: Literal["passed", "failed", "blocked"]
    exit_code: int = Field(alias="exitCode", ge=0, le=255)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: str


class Plan(BaseModel):
    """The Make-owned ordered gate distinguishes full from explicit scoped runs."""

    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["full", "scoped"]
    checks: list[str] = Field(min_length=1)

    @field_validator("checks")
    @classmethod
    def unique_targets(cls, targets: list[str]) -> list[str]:
        if len(set(targets)) != len(targets) or any(
            not target
            or target == "offline-preflight"
            or any(
                not (char.isascii() and (char.isalnum() or char in "_.:-"))
                for char in target
            )
            for target in targets
        ):
            raise ValueError("Planned checks must be unique native Make target names")
        return targets


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    source: Source
    scope: Literal["full", "scoped"]
    planned_checks: list[str] = Field(alias="plannedChecks")
    status: Literal["running", "passed", "failed", "blocked"]
    started_at: str = Field(alias="startedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    checks: list[Check] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    tools: dict[str, str] = Field(default_factory=dict)
    versions: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, str] = Field(default_factory=dict)


class Status(StrEnum):
    passed = "passed"
    failed = "failed"
    blocked = "blocked"


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def source() -> Source:
    return Source.model_validate_json((REPORTS / "source.json").read_text())


def plan() -> Plan:
    return Plan.model_validate_json((REPORTS / "ci-plan.json").read_text())


def read_receipt() -> Receipt:
    return Receipt.model_validate_json((REPORTS / "ci-local.json").read_text())


def save_receipt(value: Receipt) -> None:
    write_json(REPORTS / "ci-local.json", value.model_dump(by_alias=True))


def tool_versions() -> dict[str, str]:
    tools = {"python": platform.python_version()}
    for name in ("uv", "typer", "pydantic", "packaging", "pytest", "ruff", "mypy"):
        try:
            tools[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    image = REPORTS / "quality-image-id.txt"
    if image.is_file():
        tools["qualityImageId"] = image.read_text().strip()
    return tools


@receipt_app.command("init")
def receipt_init() -> None:
    """Start a receipt for the current source; never reuse a previous PASS."""
    selected = plan()
    save_receipt(
        Receipt.model_validate(
            {
                "source": source(),
                "scope": selected.scope,
                "plannedChecks": selected.checks,
                "status": "running",
                "startedAt": now(),
                "tools": tool_versions(),
            }
        )
    )


@receipt_app.command("record")
def receipt_record(
    name: Annotated[str, typer.Option()],
    command: Annotated[str, typer.Option()],
    exit_code: Annotated[int, typer.Option()],
) -> None:
    """Record the status returned by Make, without executing the command."""
    value = read_receipt()
    if value.status != "running" or any(check.name == name for check in value.checks):
        raise typer.BadParameter("Receipt is finished or check already recorded")
    if not 0 <= exit_code <= 255:
        raise typer.BadParameter("Exit code must be between 0 and 255")
    recorded = [check.name for check in value.checks]
    if name == "offline-preflight":
        if recorded:
            raise typer.BadParameter("Offline preflight must be the first check")
    else:
        completed = [item for item in recorded if item != "offline-preflight"]
        if [*completed, name] != value.planned_checks[: len(completed) + 1]:
            raise typer.BadParameter("Check is not the next planned native target")
    value.checks.append(
        Check.model_validate(
            {
                "name": name,
                "command": command,
                "exitCode": exit_code,
                "status": "passed"
                if exit_code == 0
                else ("blocked" if exit_code == 3 else "failed"),
            }
        )
    )
    save_receipt(value)


def checked_artifacts() -> list[Artifact]:
    manifest = REPORTS / "artifacts.json"
    if not manifest.is_file():
        return []
    items = json.loads(manifest.read_text())
    if not isinstance(items, list):
        raise ValueError("Artifact inventory must be a list")
    artifacts = [Artifact.model_validate(item) for item in items]
    for artifact in artifacts:
        path = Path(artifact.path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.is_symlink()
            or not path.resolve().is_relative_to(Path.cwd().resolve())
            or not path.is_file()
            or digest(path) != artifact.sha256
        ):
            raise ValueError("Artifact path or checksum does not match this checkout")
    return artifacts


@receipt_app.command("finish")
def receipt_finish(status: Annotated[Status, typer.Option()]) -> None:
    """Recheck inputs and artifact bytes before accepting a terminal result."""
    value = read_receipt()
    failure: str | None = None
    try:
        if value.status != "running" or value.source != source():
            raise ValueError("Source changed during ci-local or receipt already ended")
        selected = plan()
        if selected.scope != value.scope or selected.checks != value.planned_checks:
            raise ValueError("CI plan changed during execution")
        completed = [
            check.name for check in value.checks if check.name != "offline-preflight"
        ]
        if status == Status.passed and (
            completed != value.planned_checks
            or any(
                check.exit_code != 0 or check.status != "passed"
                for check in value.checks
            )
        ):
            raise ValueError(
                "A failed, skipped or missing planned check cannot become PASS"
            )
        release_verified = any(
            check.name == "release-check" and check.exit_code == 0
            for check in value.checks
        )
        value.artifacts = checked_artifacts() if release_verified else []
        if (
            status == Status.passed
            and "release-check" in value.planned_checks
            and not value.artifacts
        ):
            raise ValueError(
                "Release verification requires a nonempty current artifact inventory"
            )
        versions = REPORTS / "versions.json"
        if release_verified and versions.is_file():
            metadata = json.loads(versions.read_text())
            if not isinstance(metadata, dict):
                raise ValueError("Version inventory must be an object")
            value.versions = metadata
        for name in ("coverage.json", "coverage.xml", "junit.xml", "summary.md"):
            path = REPORTS / name
            if path.is_file():
                value.coverage[name] = digest(path)
        value.status = status.value
    except (ValueError, OSError) as error:
        value.status = "failed"
        failure = str(error)
    value.finished_at = now()
    save_receipt(value)
    summary = [
        "# Local CI",
        "",
        f"Status: **{value.status}**; scope: **{value.scope}**.",
        "Planned checks: "
        + ", ".join(f"`{name}`" for name in value.planned_checks)
        + ".",
        f"Source commit: `{value.source.commit}`; dirty: `{value.source.dirty}`.",
        f"Content SHA-256: `{value.source.content_sha256}`.",
        "",
    ]
    summary.extend(
        f"- `{check.command}`: {check.status} (exit {check.exit_code})"
        for check in value.checks
    )
    if failure:
        summary.extend(["", failure])
    (REPORTS / "ci-local-summary.md").write_text("\n".join(summary) + "\n")
    if failure:
        raise typer.BadParameter(failure)


def project_metadata(bundle_name: str | None = None) -> tuple[str, str, str]:
    """PEP 621 metadata owns Python/template versions; VERSION owns doc bundles."""
    metadata = Path("pyproject.toml")
    if not metadata.is_file():
        version = str(Version(Path("VERSION").read_text().strip()))
        return (
            canonicalize_name(bundle_name or Path.cwd().name, validate=True),
            version,
            "bundle",
        )
    data = tomllib.loads(metadata.read_text())
    project = data["project"]
    if "version" not in project:
        raise ValueError("Dynamic versions require the repository's native callback")
    version = str(Version(project["version"]))
    kind = "package"
    if data.get("tool", {}).get("uv", {}).get("package") is False:
        kind = "template" if Path("copier.yml").is_file() else "tooling"
    return project["name"], version, kind


@app.command("version")
def version_command(name: Annotated[str | None, typer.Option()] = None) -> None:
    name, version, kind = project_metadata(name)
    typer.echo(json.dumps({"name": name, "version": version, "kind": kind}))


def record_artifacts(paths: list[Path], version: str) -> None:
    artifacts = [
        Artifact(path=path.as_posix(), sha256=digest(path), version=version)
        for path in paths
    ]
    write_json(REPORTS / "artifacts.json", [item.model_dump() for item in artifacts])


def bundle_path(name: str, version: str) -> Path:
    return Path("dist") / f"{canonicalize_name(name)}-{version}.tar.gz"


def bundle_files() -> list[Path]:
    """Select source payload once for both build and verification."""
    excluded = {
        ".git",
        ".aws",
        ".venv",
        "coverage",
        "dist",
        ".local",
        ".serena",
        ".agents",
        ".codex",
        ".vscode",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "backups",
    }
    inventory = REPORTS / "source-files.nul"
    if not inventory.is_file():
        raise ValueError("Missing source inventory: run make build")
    files: list[Path] = []
    for raw in inventory.read_bytes().split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode())
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.resolve().is_relative_to(Path.cwd().resolve())
        ):
            raise ValueError("Source inventory escapes checkout")
        if any(part in excluded for part in path.parts):
            continue
        if (
            path.name == ".env"
            or (path.name.startswith(".env.") and path.name != ".env.example")
            or path.name.startswith((".coverage", "core."))
        ):
            continue
        if path.is_symlink():
            raise ValueError("Source bundles reject symlinks")
        if path.is_file() and path.suffix != ".pyc":
            files.append(path)
    return sorted(files)


@app.command("bundle-build")
def bundle_build(name: Annotated[str | None, typer.Option()] = None) -> None:
    """Build a deterministic source bundle for templates or documentation."""
    name, version, kind = project_metadata(name)
    if kind not in {"template", "bundle", "tooling"}:
        raise typer.BadParameter("Python packages use their native build backend")
    target = bundle_path(name, version)
    target.parent.mkdir(exist_ok=True)
    with (
        target.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as zipped,
    ):
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for path in bundle_files():
                info = tarfile.TarInfo(path.as_posix())
                data = path.read_bytes()
                info.size = len(data)
                info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                archive.addfile(info, io.BytesIO(data))
    record_artifacts([target], version)


@app.command("bundle-verify")
def bundle_verify(name: Annotated[str | None, typer.Option()] = None) -> None:
    """Inspect the built source artifact without extracting or rerunning tests."""
    name, version, _ = project_metadata(name)
    with tarfile.open(bundle_path(name, version), "r:gz") as archive:
        names = archive.getnames()
        if sorted(names) != [path.as_posix() for path in bundle_files()]:
            raise ValueError("Built source bundle inventory is stale")
        required = "pyproject.toml" if Path("pyproject.toml").exists() else "VERSION"
        if required not in names or "README.md" not in names:
            raise ValueError("Source bundle lacks its version source or README")
        for member in archive.getmembers():
            if (
                not member.isfile()
                or Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
            ):
                raise ValueError("Invalid member in source bundle")
            current = Path(member.name)
            extracted = archive.extractfile(member)
            if extracted is None or extracted.read() != current.read_bytes():
                raise ValueError("Built source bundle is stale")
    typer.echo(f"Verified {len(names)} source files")


class BuildEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Source
    artifacts: list[Artifact]


@app.command("capture-build")
def capture_build(
    artifact_dir: Annotated[Path, typer.Option()] = Path("dist"),
    name: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Bind a successful native build to unchanged source and artifact bytes."""
    initial = Source.model_validate_json((REPORTS / "build-source.json").read_text())
    if initial != source():
        raise ValueError("Source changed during build")
    project_name, version, kind = project_metadata(name)
    if kind == "package":
        paths = sorted(artifact_dir.glob("*.whl")) + sorted(
            artifact_dir.glob("*.tar.gz")
        )
        if len(paths) != 2 or not any(path.suffix == ".whl" for path in paths):
            raise ValueError("Build must produce one wheel and one sdist")
    else:
        paths = [bundle_path(project_name, version)]
    artifacts = [
        Artifact(path=path.as_posix(), sha256=digest(path), version=version)
        for path in paths
    ]
    write_json(
        REPORTS / "build.json",
        BuildEvidence(source=initial, artifacts=artifacts).model_dump(by_alias=True),
    )


def validate_build(paths: list[Path]) -> None:
    evidence_path = REPORTS / "build.json"
    if not evidence_path.is_file():
        raise ValueError("Missing build evidence: run make build for current inputs")
    evidence = BuildEvidence.model_validate_json(evidence_path.read_text())
    if evidence.source.content_sha256 != source().content_sha256:
        raise ValueError("Build source is stale: run make build")
    expected = {item.path: item.sha256 for item in evidence.artifacts}
    if expected != {path.as_posix(): digest(path) for path in paths}:
        raise ValueError("Built artifact inventory or checksum changed")


@app.command("release-check")
def release_check(
    artifact_dir: Annotated[Path, typer.Option()] = Path("dist"),
    name: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Compare wheel/sdist metadata or source bundle with its canonical version."""
    name, version, kind = project_metadata(name)
    paths: list[Path]
    if kind == "package":
        wheels = sorted(artifact_dir.glob("*.whl"))
        sdists = sorted(artifact_dir.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError(
                "Expected exactly one current wheel and one sdist in dist/"
            )
        paths = wheels + sdists
        validate_build(paths)
        with zipfile.ZipFile(wheels[0]) as wheel:
            metadata = [
                item
                for item in wheel.namelist()
                if item.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1:
                raise ValueError("Invalid wheel metadata inventory")
            wheel_metadata = wheel.read(metadata[0])
        with tarfile.open(sdists[0], "r:gz") as archive:
            sdist_members = [
                member
                for member in archive.getmembers()
                if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
            ]
            if len(sdist_members) != 1:
                raise ValueError("Invalid sdist metadata inventory")
            extracted = archive.extractfile(sdist_members[0])
            if extracted is None:
                raise ValueError("Missing sdist metadata")
            sdist_metadata = extracted.read()
        for raw in (wheel_metadata, sdist_metadata):
            parsed = BytesParser().parsebytes(raw)
            if canonicalize_name(str(parsed["Name"])) != canonicalize_name(
                name
            ) or Version(str(parsed["Version"])) != Version(version):
                raise ValueError(
                    "Built artifact name/version differs from pyproject.toml"
                )
    else:
        bundle_verify(name)
        paths = [bundle_path(name, version)]
        validate_build(paths)
    record_artifacts(paths, version)
    write_json(REPORTS / "versions.json", {name: version, "kind": kind})
    typer.echo(f"Release metadata verified: {name} {version}; no publication")


if __name__ == "__main__":
    app()
