# Managed Python specialization of the portable Docker recipe.
COVERAGE_REPORT ?= coverage/coverage.json
COVERAGE_FORMAT ?= coveragepy
COVERAGE_COMMAND ?= python docker/coverage_report.py $(COVERAGE_REPORT) --format $(COVERAGE_FORMAT) --after coverage/.started
QUALITY_EXTRA_TARGETS ?= docs-check compile lock-check format-check
QUALITY_RUN_ARGS += --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/app \
	--env UV_NO_SYNC=1 --env COVERAGE_FILE=/app/coverage/.coverage \
	--env PYTEST_ADDOPTS=-o\ cache_dir=/tmp/pytest-cache --env UV_CACHE_DIR=/tmp/uv \
	--env RUFF_CACHE_DIR=/tmp/ruff --env MYPY_CACHE_DIR=/tmp/mypy \
	--env PYTHONPYCACHEPREFIX=/tmp/pycache
RENDER_OUTPUT ?= $(CURDIR)/.local/rendered
RENDER_ARGS ?=
include docker/quality.mk

.PHONY: render verify-installed
render: QUALITY_RUN_ARGS += --mount "type=bind,source=$(RENDER_OUTPUT),target=/render"
render: quality-image
	@mkdir -p "$(RENDER_OUTPUT)"
	$(QUALITY_RUN) uv run --locked copier copy --defaults /app /render $(RENDER_ARGS)
verify-installed: ## Fresh installed-package tests in a separate container context.
	@mkdir -p coverage/installed
	$(DOCKER) buildx build --file $(BUILD_DOCKERFILE) $(DOCKER_BUILD_ARGS) --target installed-test --load --tag $(QUALITY_IMAGE)-installed .
	@touch coverage/installed/.started
	$(DOCKER) run --rm --init --read-only --network none --tmpfs /tmp:rw,mode=1777,size=512m --user "$$(id -u):$$(id -g)" --mount "type=bind,source=$(CURDIR)/coverage,target=/app/coverage" $(QUALITY_IMAGE)-installed
	@$(DOCKER) image inspect $(QUALITY_IMAGE)-installed --format '{{.Id}}' > coverage/installed/test-image-id.txt
	$(DOCKER) run --rm --read-only --network none --user "$$(id -u):$$(id -g)" --mount "type=bind,source=$(CURDIR)/coverage,target=/app/coverage" --entrypoint python $(QUALITY_IMAGE)-installed docker/coverage_report.py /app/coverage/installed/coverage.json --after /app/coverage/installed/.started --output /app/coverage/installed/summary.md
