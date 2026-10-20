# typerTemplate

Private Copier specialization for Python CLI applications, composed from the
pinned pythonTemplate source through the existing composition tool. It owns CLI
wiring, lazy Pydantic Settings and pytest/CliRunner examples. It adds no HTTP or
Pokerlost contracts and no runtime dependency on another checkout.

## 🚀 Create and verify

Python 3.12+, uv 0.12.15 and Copier 9.17.2 are required. Select a reviewed version
tag or full commit. `make version` reads the canonical template version;
`composition.lock.json` records the selected parent. Answers schema and generated
application version are independent of both template versions.

```bash
uvx --from 'copier==9.17.2' copier copy --defaults --vcs-ref=<reviewed-sha> git@github.com:serrja/typerTemplate.git notes-cli -d projectName=notesCli
cd notes-cli
uv lock
make check build verify
install_dir=$(mktemp -d)
uv venv "$install_dir"
uv pip install --python "$install_dir/bin/python" dist/*.whl
cd "$install_dir"
"$install_dir/bin/notesCli" --help
"$install_dir/bin/python" -m notescli --version
```

The main checks, package build and installed-wheel tests use the shared Docker
recipe and locked toolchain. Coverage below 100% warns. Source snapshots and
provenance permit clean generation without sibling checkouts.

## 🧭 Add a command

Put an application function in a project-owned module. Add an explicit Typer name:

```python
@app.command(name="hello")
def hello(name: Annotated[str, typer.Argument()]) -> None:
    typer.echo(f"Hello, {name}")
```

Keep its CLI contract test in a project-owned test module:

```python
from typer.testing import CliRunner
from notescli.cli import app

def test_hello():
    result = CliRunner().invoke(app, ["hello", "Ada"])
    assert result.exit_code == 0
    assert result.stdout == "Hello, Ada\n"
    assert not result.stderr
```

Do not expose secrets through errors, model repr or traceback locals. Selected
dotenv files are optional; CLI > env > selected file > defaults. Typer is the
only parser; Pydantic Settings CLI parsing is disabled.

## 🔄 Update

From a clean generated Git repository, run `uvx --from 'copier==9.17.2' copier
update --defaults --vcs-ref=<new-reviewed-sha>`. Inspect managed patches, skipped
project-owned settings, conflicts and tests before accepting. Never edit answers
or use copy --force as an update. Historical Python-base CLI refs remain available;
the [migration guide](docs/migration.md) explains the explicit source handoff.

## 🛠 Maintenance

Read [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) and
[composition.toml](composition.toml). Edit overlay/, refresh the parent explicitly,
assemble and run `make check`. The [shared policy](https://github.com/serrja/pythonTemplate/blob/main/docs/engineering.md)
owns engineering choices; this repository owns only the CLI specialization.

## 🔒 Access and use

This source repository is private and has no public release. The maintained
commands and test fixtures are available to authorized readers; the source license
is described below.

## 📄 License

Original additions use [MIT](LICENSE); inherited source and dependencies retain
their [notices](THIRD_PARTY_NOTICES.md). Generated projects choose `licensePolicy`
and their own holder/year through the common Python foundation.

For this template source, use `make version` and `make ci-local`; see
[development](docs/development.md), [version boundaries](docs/versioning.md) and the
[shared release guide](https://github.com/serrja/baseRules/blob/main/docs/releases.md).
