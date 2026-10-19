# Contributing to typerTemplate

Use the [shared engineering policy](https://github.com/serrja/pythonTemplate/blob/main/docs/engineering.md)
and [contribution guide](https://github.com/serrja/pythonTemplate/blob/main/docs/contributing.md).

Run `make check` and render a disposable CLI with `make render
RENDER_OUTPUT=/tmp/review-cli RENDER_ARGS='-d projectName=reviewCli'`.
Resolve its first lock in the maintenance Docker image, then run `make check build
verify` inside the render. Preserve project-owned modules, models and tests in
real Copier updates; inspect conflicts before committing. Do not publish tags,
images or releases as part of a test.
