# Managed Python specialization of the portable Docker recipe.
COVERAGE_REPORT ?= coverage/coverage.json
COVERAGE_FORMAT ?= coveragepy
COVERAGE_COMMAND ?= python docker/coverage_report.py $(COVERAGE_REPORT) --format $(COVERAGE_FORMAT) --after coverage/.started
VERSION_COMMAND ?= $(QUALITY_RUN) python docker/release.py version
RELEASE_CHECK_COMMAND ?= $(QUALITY_RUN) python docker/release.py release-check
BUILD_RECEIPT_COMMAND ?= $(QUALITY_RUN) python docker/release.py capture-build
RECEIPT_COMMAND ?= $(QUALITY_RUN) python docker/release.py receipt
QUALITY_EXTRA_TARGETS ?= docs-check compile lock-check format-check
QUALITY_RUN_ARGS += --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/app \
	--env UV_NO_SYNC=1 --env UV_OFFLINE=$(if $(filter 1,$(OFFLINE)),true,false) --env COVERAGE_FILE=/app/coverage/.coverage \
	--env PYTEST_ADDOPTS=-o\ cache_dir=/tmp/pytest-cache --env UV_CACHE_DIR=/tmp/uv \
	--env RUFF_CACHE_DIR=/tmp/ruff --env MYPY_CACHE_DIR=/tmp/mypy \
	--env PYTHONPYCACHEPREFIX=/tmp/pycache
# Offline execution reuses verified artifacts/images, then reruns acceptance.
QUALITY_RUN_ARGS += $(if $(filter 1,$(OFFLINE)),--pull=never,)
INSTALLED_TEST_IMAGE ?= $(QUALITY_IMAGE)-installed
OFFLINE_PREFLIGHT_COMMAND ?= $(MAKE) python-offline-preflight
ifeq ($(OFFLINE),1)
BUILD_COMMAND ?= $(RELEASE_CHECK_COMMAND)
endif
RENDER_OUTPUT ?= $(CURDIR)/.local/rendered
RENDER_ARGS ?=
include docker/quality.mk

.PHONY: render verify-installed python-offline-preflight
python-offline-preflight:
	@test -s "$(REPORT_DIR)/installed/test-image-id.txt" -a -s "$(REPORT_DIR)/installed/source.json" || { echo "BLOCKED: prepare installed acceptance online first." >&2; exit 2; }
	@test "$$(cat $(REPORT_DIR)/installed/test-image-id.txt)" = "$$($(DOCKER) image inspect $(INSTALLED_TEST_IMAGE) --format '{{.Id}}')" || { echo "BLOCKED: prepared installed image missing or replaced." >&2; exit 2; }
	@test "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/source.json)" = "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/installed/source.json)" || { echo "BLOCKED: installed acceptance source changed." >&2; exit 2; }
	$(RELEASE_CHECK_COMMAND)
render: QUALITY_RUN_ARGS += --mount "type=bind,source=$(RENDER_OUTPUT),target=/render"
render: quality-image
	@mkdir -p "$(RENDER_OUTPUT)"
	$(QUALITY_RUN) uv run --locked copier copy --defaults /app /render $(RENDER_ARGS)
verify-installed: ## Fresh installed-package tests in a separate container context.
	@mkdir -p "$(REPORT_DIR)/installed"
ifneq ($(OFFLINE),1)
	$(DOCKER) buildx build --file $(BUILD_DOCKERFILE) $(DOCKER_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) --target installed-test --load --tag $(INSTALLED_TEST_IMAGE) .
else
	$(MAKE) python-offline-preflight
endif
	@touch "$(REPORT_DIR)/installed/.started"
	$(DOCKER) run --pull=never --rm --init --read-only --network none --tmpfs /tmp:rw,mode=1777,size=512m --user "$$(id -u):$$(id -g)" --mount "type=bind,source=$(CURDIR)/$(REPORT_DIR),target=/app/coverage" $(INSTALLED_TEST_IMAGE)
	@cp "$(REPORT_DIR)/source.json" "$(REPORT_DIR)/installed/source.json"
	@$(DOCKER) image inspect $(INSTALLED_TEST_IMAGE) --format '{{.Id}}' > "$(REPORT_DIR)/installed/test-image-id.txt"
	$(DOCKER) run --pull=never --rm --read-only --network none --user "$$(id -u):$$(id -g)" --mount "type=bind,source=$(CURDIR)/$(REPORT_DIR),target=/app/coverage" --entrypoint python $(INSTALLED_TEST_IMAGE) docker/coverage_report.py /app/coverage/installed/coverage.json --after /app/coverage/installed/.started --output /app/coverage/installed/summary.md
