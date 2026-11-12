# Contributing to typerTemplate

Use the [shared engineering policy](https://github.com/serrja/pythonTemplate/blob/main/docs/engineering.md)
and [contribution guide](https://github.com/serrja/pythonTemplate/blob/main/docs/contributing.md).

## 🧪 Source checks

From the repository root, install the locked maintenance environment and run:

```bash
uv sync --locked --all-groups
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest -q
uv run --locked python tools/check_documentation.py --documents README.md CONTRIBUTING.md AGENTS.md
```

`make check` currently provides lint only. The explicit native commands above also
check formatting, typing, real render/update behavior and documentation. The full
Docker acceptance remains `make ci-local`; source checks do not replace it.

Render a disposable CLI with `make render
RENDER_OUTPUT=../review-cli RENDER_ARGS='-d projectName=reviewCli'`.
Resolve its first lock in the maintenance Docker image, then run `make check build
verify` inside the render. Preserve project-owned modules, models and tests in
real Copier updates; inspect conflicts before committing. Do not publish tags,
images or releases as part of a test.

## 🔀 Delivery

Start one lowerCamelCase working branch from fresh `origin/test`. Submit its PR
to `test`, then promote `test` → `dev` → `main` with ancestry-preserving merges.
Keep permanent branches and review the full diff, compatibility and current checks.
Every `dev`/`main` push requires an exact-SHA GitHub Release through the reviewed
shared provider. The Python native release adapter remains a separate dependency;
passing source checks does not establish release readiness.
