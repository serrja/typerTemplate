"""Build an allocated H02 Python version in an isolated, exact Git export."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

import typer
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

app = typer.Typer(pretty_exceptions_show_locals=False)
MAX_CONTROL = 4 * 1024 * 1024
MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_MEMBER = 256 * 1024 * 1024
MAX_MEMBERS = 20000
SHA = r"^[a-f0-9]{40}$"
DIGEST = r"^[a-f0-9]{64}$"
DECIMAL = r"^[1-9][0-9]{0,18}$"
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


class Boundary(BaseModel):
    """Closed schemas reject coercion, unknown fields and raw-input diagnostics."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Allocation(Boundary):
    schema_version: Literal[2] = Field(alias="schemaVersion")
    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    adapter: Literal["python"]
    bundle_name: Annotated[
        str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    ] = Field(alias="bundleName")
    workflow: Annotated[
        str, Field(pattern=r"^\.github/workflows/[A-Za-z0-9_-]+\.ya?ml$")
    ]
    gate: Annotated[str, Field(pattern=r"^[A-Za-z0-9_ /()-]{1,100}$")]
    run_id: Annotated[str, Field(pattern=DECIMAL)] = Field(alias="runId")
    branch: Literal["dev", "main"]
    sha: Annotated[str, Field(pattern=SHA)]
    event_created_at: str = Field(alias="eventCreatedAt")
    version: str
    provider_ref: Annotated[str, Field(pattern=SHA)] = Field(alias="providerRef")
    contract_schema: Literal["h02-python/v1"] = Field(alias="contractSchema")
    builder_job: Annotated[str, Field(pattern=r"^[A-Za-z0-9_ /()-]{1,100}$")] = Field(
        alias="builderJob"
    )
    version_target: str = Field(alias="versionTarget")
    wheel_tags: list[str] = Field(alias="wheelTags", min_length=1, max_length=32)
    allocation_attempt: Annotated[str, Field(pattern=DECIMAL)] = Field(
        alias="allocationAttempt"
    )
    python_version: str = Field(alias="pythonVersion")

    @model_validator(mode="after")
    def identities(self) -> Self:
        require(self.gate != self.builder_job, "Builder must follow a separate gate")
        require(
            bool(re.fullmatch(VERSION, self.version_target)), "Invalid version target"
        )
        match = re.fullmatch(VERSION + r"(?:-dev\.(0|[1-9][0-9]*))?", self.version)
        require(match is not None, "Invalid allocated release version")
        assert match is not None
        dev = match.group(4)
        require((dev is not None) == (self.branch == "dev"), "Branch/version conflict")
        expected = ".".join(match.group(i) for i in (1, 2, 3))
        if dev is not None:
            expected += ".dev" + dev
        require(self.python_version == expected, "Python/release version conflict")
        require(
            self.wheel_tags == sorted(set(self.wheel_tags)),
            "Unsorted or duplicate wheel tags",
        )
        require(
            all(
                re.fullmatch(r"[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+", tag)
                for tag in self.wheel_tags
            ),
            "Invalid wheel tag",
        )
        stamp = datetime.fromisoformat(self.event_created_at.replace("Z", "+00:00"))
        require(stamp.tzinfo is not None, "Event timestamp requires a timezone")
        return self


class BuildTool(Boundary):
    backend: str
    backend_version: str = Field(alias="backendVersion")
    frontend: Literal["uv"]
    frontend_version: str = Field(alias="frontendVersion")
    version_injection: Literal["uv version --frozen --offline"] = Field(
        alias="versionInjection"
    )

    @model_validator(mode="after")
    def printable(self) -> Self:
        require(
            all(
                re.fullmatch(r"[A-Za-z0-9_.+ /:=-]{1,200}", value)
                for value in self.model_dump().values()
            ),
            "Invalid build tool identity",
        )
        return self


class Artifact(Boundary):
    name: str
    sha256: Annotated[str, Field(pattern=DIGEST)]


class BuildEvidence(Boundary):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    allocation_sha256: Annotated[str, Field(pattern=DIGEST)] = Field(
        alias="allocationSha256"
    )
    provider_ref: Annotated[str, Field(pattern=SHA)] = Field(alias="providerRef")
    source_sha: Annotated[str, Field(pattern=SHA)] = Field(alias="sourceSha")
    source_pyproject_sha256: Annotated[str, Field(pattern=DIGEST)] = Field(
        alias="sourcePyprojectSha256"
    )
    source_lock_sha256: str | None = Field(alias="sourceLockSha256")
    run_id: Annotated[str, Field(pattern=DECIMAL)] = Field(alias="runId")
    run_attempt: Annotated[str, Field(pattern=DECIMAL)] = Field(alias="runAttempt")
    builder_job: str = Field(alias="builderJob")
    output: str
    build_tool: BuildTool = Field(alias="buildTool")
    files: list[Artifact]


class NativeMetadata(Artifact):
    package_name: str = Field(alias="packageName")
    version: str
    requires_python: str | None = Field(default=None, alias="requiresPython")
    tags: list[str] | None = None
    build_backend: str | None = Field(default=None, alias="buildBackend")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def control(path: Path) -> bytes:
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_CONTROL,
        "Invalid control file",
    )
    return path.read_bytes()


def load_allocation(path: Path, checksum: str) -> Allocation:
    raw = control(path)
    require(
        bool(re.fullmatch(DIGEST, checksum)) and digest(raw) == checksum,
        "Allocation digest mismatch",
    )
    data = json.loads(raw)
    require(
        raw == json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(),
        "Preserve exact compact allocation bytes",
    )
    require(
        isinstance(data, dict) and type(data.get("schemaVersion")) is int,
        "Invalid allocation schema type",
    )
    return Allocation.model_validate(data)


def github_identity(a: Allocation, attempt: str | None = None) -> str:
    expected = {
        "GITHUB_REPOSITORY": a.repository,
        "GITHUB_SHA": a.sha,
        "GITHUB_RUN_ID": a.run_id,
        "GITHUB_REF_NAME": a.branch,
        "GITHUB_REF": "refs/heads/" + a.branch,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_WORKFLOW_REF": f"{a.repository}/{a.workflow}@refs/heads/{a.branch}",
    }
    for key, value in expected.items():
        require(
            key not in os.environ or os.environ[key] == value,
            "GitHub identity mismatch: " + key,
        )
    actual = os.environ.get("GITHUB_RUN_ATTEMPT", attempt or a.allocation_attempt)
    require(
        bool(re.fullmatch(DECIMAL, actual))
        and int(actual) >= int(a.allocation_attempt),
        "Invalid build run attempt",
    )
    require(attempt is None or actual == attempt, "Build run attempt mismatch")
    return actual


def backend_environment(original: dict[str, str]) -> dict[str, str]:
    """The unprivileged backend receives no GitHub/OIDC or credential variables."""
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "UV_CACHE_DIR",
        "PYTHONDONTWRITEBYTECODE",
        "CC",
        "CXX",
        "AR",
        "LD",
        "RANLIB",
        "CFLAGS",
        "CXXFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
        "CMAKE_GENERATOR",
        "CMAKE_BUILD_PARALLEL_LEVEL",
        "MAKEFLAGS",
        "SDKROOT",
        "MACOSX_DEPLOYMENT_TARGET",
        "ARCHFLAGS",
        "SOURCE_DATE_EPOCH",
    }
    return {key: value for key, value in original.items() if key in allowed}


def git(*args: str) -> bytes:
    return subprocess.check_output(
        ["git", "--no-optional-locks", *args], stderr=subprocess.DEVNULL
    )


def clean_source(a: Allocation) -> Path:
    root = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    require(Path.cwd().resolve() == root, "Run from the exact source Git root")
    require(git("rev-parse", "HEAD").decode().strip() == a.sha, "Source HEAD mismatch")
    require(
        not git("status", "--porcelain", "--untracked-files=all"),
        "Source checkout must be clean",
    )
    return root


def outside(path: Path, root: Path) -> Path:
    require(not path.is_symlink(), "Symlink destination is unsupported")
    result = path.resolve()
    require(
        not result.is_relative_to(root), "Build paths must be outside source checkout"
    )
    return result


def project_backend(raw: bytes, a: Allocation) -> tuple[str, str]:
    data = tomllib.loads(raw.decode())
    project, build = data.get("project", {}), data.get("build-system", {})
    require(
        isinstance(project, dict) and isinstance(build, dict),
        "Invalid source project/build-system tables",
    )
    require(
        canonicalize_name(project.get("name", "")) == a.bundle_name,
        "Source package mismatch",
    )
    version = project.get("version")
    require(
        isinstance(version, str)
        and "version" not in project.get("dynamic", [])
        and bool(re.fullmatch(VERSION + r"(?:\.dev[0-9]+)?", version)),
        "Only static canonical Python versions are supported",
    )
    require(
        str(Version(version)) == version
        and Version(version).base_version == a.version_target,
        "Source version target mismatch",
    )
    backend = build.get("build-backend")
    require(
        isinstance(backend, str)
        and bool(
            re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*)?", backend)
        ),
        "Missing declared build backend",
    )
    require(not build.get("backend-path"), "In-tree build backends are unsupported")
    requirements = build.get("requires")
    require(
        isinstance(requirements, list)
        and bool(requirements)
        and all(isinstance(item, str) for item in requirements),
        "Missing backend requirements",
    )
    installed = []
    for text in requirements:
        requirement = Requirement(text)
        require(not requirement.url, "Direct backend requirements are unsupported")
        if requirement.marker and not requirement.marker.evaluate():
            continue
        actual = importlib.metadata.version(requirement.name)
        require(
            actual in requirement.specifier, "Installed backend requirement mismatch"
        )
        installed.append((canonicalize_name(requirement.name), actual))
    distributions = importlib.metadata.packages_distributions().get(
        backend.split(".")[0].split(":")[0], []
    )
    matching = [
        (name, version)
        for name, version in installed
        if name in {canonicalize_name(item) for item in distributions}
    ]
    require(
        len(matching) == 1, "Cannot identify declared installed backend distribution"
    )
    return backend, matching[0][1]


def export_source(a: Allocation, destination: Path) -> None:
    """Validate the complete bounded archive before creating any exported file."""
    with tempfile.TemporaryFile() as raw:
        with subprocess.Popen(
            ["git", "archive", "--format=tar", a.sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            assert process.stdout is not None
            size = 0
            try:
                while chunk := process.stdout.read(1024 * 1024):
                    size += len(chunk)
                    require(size <= MAX_ARCHIVE, "Source archive exceeds size limit")
                    raw.write(chunk)
            except BaseException:
                process.kill()
                raise
            require(process.wait(timeout=30) == 0, "Git archive failed")
        raw.seek(0)
        with tarfile.open(fileobj=raw) as archive:
            members: list[tarfile.TarInfo] = []
            seen: set[str] = set()
            total = 0
            for member in archive:
                name = member.name.rstrip("/")
                archive_path = PurePosixPath(name)
                require(
                    name
                    and not archive_path.is_absolute()
                    and "\\" not in name
                    and all(
                        part not in {"", ".", "..", ".git"} for part in name.split("/")
                    )
                    and (member.isfile() or member.isdir()),
                    "Unsafe source archive member",
                )
                require(name.casefold() not in seen, "Source archive path collision")
                seen.add(name.casefold())
                total += member.size
                require(
                    member.size <= MAX_MEMBER
                    and total <= MAX_ARCHIVE
                    and len(members) < MAX_MEMBERS,
                    "Source archive exceeds limits",
                )
                members.append(member)
            types = {
                member.name.rstrip("/").casefold(): member.isdir() for member in members
            }
            for member in members:
                for parent in PurePosixPath(member.name).parents:
                    require(
                        types.get(str(parent).casefold(), True),
                        "Source archive file/directory collision",
                    )
            destination.mkdir(parents=False)
            for member in members:
                path = destination / member.name
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    content = archive.extractfile(member)
                    require(content is not None, "Missing source archive content")
                    assert content is not None
                    with path.open("xb") as target:
                        while chunk := content.read(1024 * 1024):
                            target.write(chunk)
                    path.chmod(member.mode & 0o777)


def inventory(directory: Path, a: Allocation) -> list[dict[str, str]]:
    entries = sorted(directory.iterdir())
    require(
        len(entries) == len(a.wheel_tags) + 1, "Expected only wheel(s) and one sdist"
    )
    wheels: list[str] = []
    sdists = 0
    result = []
    for path in entries:
        require(
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size <= MAX_MEMBER,
            "Invalid native build artifact",
        )
        if path.suffix == ".whl":
            name, version, build, tags = parse_wheel_filename(path.name)
            require(
                name == a.bundle_name
                and str(version) == a.python_version
                and not build
                and len(tags) == 1,
                "Wheel identity mismatch",
            )
            tag = str(next(iter(tags)))
            require(
                path.name
                == f"{a.bundle_name.replace('-', '_')}-{a.python_version}-{tag}.whl",
                "Noncanonical wheel filename",
            )
            wheels.append(tag)
        else:
            require(
                path.name
                == f"{a.bundle_name.replace('-', '_')}-{a.python_version}.tar.gz",
                "Sdist identity mismatch",
            )
            sdists += 1
        result.append({"name": path.name, "sha256": digest(path.read_bytes())})
    require(
        sdists == 1 and sorted(wheels) == a.wheel_tags,
        "Native wheel tag inventory mismatch",
    )
    return result


def write_json(path: Path, value: object) -> None:
    require(not path.exists() and not path.is_symlink(), "Output already exists")
    with path.open("x") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


@app.command()
def build(
    allocation: Annotated[Path, typer.Option()],
    allocation_sha256: Annotated[str, typer.Option()],
    provider_ref: Annotated[str, typer.Option()],
    destination: Annotated[Path, typer.Option()],
    output: Annotated[Path, typer.Option()],
    evidence: Annotated[Path, typer.Option()],
    source_pyproject: Annotated[Path, typer.Option()],
) -> None:
    """Export clean HEAD, inject the allocation with uv, and build offline."""
    a = load_allocation(allocation, allocation_sha256)
    require(a.provider_ref == provider_ref, "Provider pin mismatch")
    attempt = github_identity(a)
    root = clean_source(a)
    original = control(source_pyproject)
    require(
        original == git("show", a.sha + ":pyproject.toml"),
        "Original pyproject mismatch",
    )
    backend, backend_version = project_backend(original, a)
    destination, output, evidence = (
        outside(path, root) for path in (destination, output, evidence)
    )
    paths = (destination, output, evidence)
    require(
        all(
            not left.is_relative_to(right) and not right.is_relative_to(left)
            for i, left in enumerate(paths)
            for right in paths[i + 1 :]
        ),
        "Build output paths must not overlap",
    )
    require(all(not path.exists() for path in paths), "Build destinations must be new")
    lock = root / "uv.lock"
    lock_bytes = lock.read_bytes() if lock.is_file() else None
    env = backend_environment(dict(os.environ))
    frontend = subprocess.check_output(["uv", "--version"], text=True, env=env).split()[
        1
    ]
    export_source(a, destination)
    require(
        (destination / "pyproject.toml").read_bytes() == original,
        "Git export changed original pyproject",
    )
    subprocess.run(
        [
            "uv",
            "version",
            a.python_version,
            "--frozen",
            "--offline",
            "--directory",
            str(destination),
        ],
        check=True,
        env=env,
        timeout=120,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    staged = tomllib.loads((destination / "pyproject.toml").read_text())
    require(
        staged["project"]["version"] == a.python_version,
        "Native version injection failed",
    )
    require(
        (destination / "uv.lock").read_bytes() == lock_bytes
        if lock_bytes is not None
        else not (destination / "uv.lock").exists(),
        "Native injection changed source lock",
    )
    subprocess.run(
        [
            "uv",
            "build",
            "--no-build-isolation",
            "--offline",
            "--no-create-gitignore",
            "--python",
            sys.executable,
            "--sdist",
            "--wheel",
            "--out-dir",
            str(output),
            str(destination),
        ],
        check=True,
        env=env,
        timeout=120,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    files = inventory(output, a)
    require(
        control(source_pyproject) == original and clean_source(a) == root,
        "Native build changed source checkout",
    )
    require(
        (lock.read_bytes() if lock.is_file() else None) == lock_bytes,
        "Native build changed source lock",
    )
    record = BuildEvidence(
        schemaVersion=1,
        allocationSha256=allocation_sha256,
        providerRef=a.provider_ref,
        sourceSha=a.sha,
        sourcePyprojectSha256=digest(original),
        sourceLockSha256=digest(lock_bytes) if lock_bytes is not None else None,
        runId=a.run_id,
        runAttempt=attempt,
        builderJob=a.builder_job,
        output=str(output),
        buildTool=BuildTool(
            backend=backend,
            backendVersion=backend_version,
            frontend="uv",
            frontendVersion=frontend,
            versionInjection="uv version --frozen --offline",
        ),
        files=[Artifact.model_validate(file) for file in files],
    )
    write_json(evidence, record.model_dump(by_alias=True))


@app.command()
def receipt(
    allocation: Annotated[Path, typer.Option()],
    allocation_sha256: Annotated[str, typer.Option()],
    metadata: Annotated[Path, typer.Option()],
    build_evidence: Annotated[Path, typer.Option()],
    job_id: Annotated[str, typer.Option()],
    run_attempt: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
) -> None:
    """Bind provider-verified ordered metadata and current bytes to the build job."""
    a = load_allocation(allocation, allocation_sha256)
    require(
        bool(re.fullmatch(DECIMAL, job_id))
        and bool(re.fullmatch(DECIMAL, run_attempt)),
        "Invalid builder job/attempt identity",
    )
    github_identity(a, run_attempt)
    evidence = BuildEvidence.model_validate_json(control(build_evidence))
    require(
        evidence.allocation_sha256 == allocation_sha256
        and evidence.provider_ref == a.provider_ref
        and evidence.source_sha == a.sha
        and evidence.run_id == a.run_id
        and evidence.run_attempt == run_attempt
        and evidence.builder_job == a.builder_job,
        "Build evidence identity mismatch",
    )
    root = clean_source(a)
    require(
        evidence.source_pyproject_sha256
        == digest(git("show", a.sha + ":pyproject.toml"))
        and evidence.source_lock_sha256
        == (
            digest((root / "uv.lock").read_bytes())
            if (root / "uv.lock").is_file()
            else None
        ),
        "Build evidence/source metadata mismatch",
    )
    require(
        output.name == "python-build-receipt.json"
        and output.parent.resolve() == Path(evidence.output),
        "Receipt must accompany the verified build artifacts",
    )
    files = json.loads(control(metadata))
    require(isinstance(files, list), "Expected ordered provider metadata array")
    validated = [NativeMetadata.model_validate(file) for file in files]
    current = inventory(output.parent, a)
    require(
        [file.model_dump() for file in evidence.files] == current,
        "Build artifacts changed after native build",
    )
    require(
        [{"name": file.name, "sha256": file.sha256} for file in validated] == current,
        "Provider metadata/current artifact mismatch",
    )
    for file in validated:
        require(
            file.package_name == a.bundle_name and file.version == a.python_version,
            "Provider metadata package/version mismatch",
        )
        if file.name.endswith(".whl"):
            require(
                file.tags is not None
                and len(file.tags) == 1
                and file.tags[0] in a.wheel_tags
                and file.build_backend is None,
                "Provider wheel metadata mismatch",
            )
        else:
            require(
                file.tags is None and file.build_backend == evidence.build_tool.backend,
                "Provider sdist backend mismatch",
            )
    write_json(
        output,
        {
            "schemaVersion": 1,
            "allocationSha256": allocation_sha256,
            "runId": a.run_id,
            "runAttempt": run_attempt,
            "sourceSha": a.sha,
            "builderJob": a.builder_job,
            "builderJobId": job_id,
            "buildTool": evidence.build_tool.model_dump(by_alias=True),
            "files": files,
        },
    )


if __name__ == "__main__":
    try:
        app()
    except (ValueError, ValidationError, OSError, subprocess.SubprocessError):
        typer.echo(
            "Python release validation/build failed; no receipt was issued.", err=True
        )
        raise SystemExit(1) from None
