# Managed portable recipe: pythonTemplate/template/docker/quality.mk.
# Project Makefiles select images and native commands; no sibling checkout is used.
DOCKER ?= docker
REPORT_DIR ?= coverage
QUALITY_DOCKERFILE ?= Dockerfile.quality
QUALITY_TARGET ?= quality
QUALITY_IMAGE ?= project:quality
QUALITY_NETWORK ?= none
QUALITY_RUN_ARGS ?=
COMMA := ,
QUALITY_GIT_MOUNT = $(if $(wildcard $(CURDIR)/.git/HEAD),--mount "type=bind$(COMMA)source=$(CURDIR)/.git$(COMMA)target=/app/.git$(COMMA)readonly",$(if $(wildcard $(CURDIR)/.git),--mount "type=bind$(COMMA)source=$(shell git rev-parse --absolute-git-dir)$(COMMA)target=/app/.git$(COMMA)readonly" --mount "type=bind$(COMMA)source=$(shell git rev-parse --path-format=absolute --git-common-dir)$(COMMA)target=/app/.git-common$(COMMA)readonly" --env GIT_COMMON_DIR=/app/.git-common --env GIT_WORK_TREE=/app,))
QUALITY_DIST_MOUNT ?= --mount "type=bind,source=$(CURDIR)/dist,target=/app/dist"
QUALITY_BUILD_ARGS ?= $(DOCKER_BUILD_ARGS)
QUALITY_TEST ?= test
QUALITY_TEST_COMMAND ?= $(QUALITY_RUN) make $(QUALITY_TEST)
ARTIFACT_TARGET ?= artifacts
BUILD_DOCKERFILE ?= Dockerfile
BUILD_COMMAND ?= $(DOCKER) buildx build --file $(BUILD_DOCKERFILE) $(DOCKER_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) --target $(ARTIFACT_TARGET) --output type=local,dest=dist .
RUNTIME_IMAGE ?= project:candidate
IMAGE_COMMAND ?= $(DOCKER) buildx build $(DOCKER_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) --target runtime --load --tag $(RUNTIME_IMAGE) .
VERIFY_COMMAND ?= $(MAKE) verify-installed
QUALITY_EXTRA_TARGETS ?=
CI_LOCAL_TARGETS ?= check coverage build verify release-check
# An explicit environment/command-line subset is evidence for that scope only.
CI_LOCAL_SCOPE = $(if $(filter command line environment override,$(origin CI_LOCAL_TARGETS)),scoped,full)
OFFLINE_PREFLIGHT_COMMAND ?= sh -ec 'echo "BLOCKED: declare the repository-specific cached artifact/acceptance inputs before offline CI." >&2; exit 2'
OFFLINE ?= 0
SKIP_QUALITY_BUILD ?= 0
ifeq ($(OFFLINE),1)
LOCAL_BUILD_NETWORK := --network=none
QUALITY_NETWORK := none
endif

COVERAGE_COMMAND ?= sh -ec 'echo "Configure a language-native coverage reporter" >&2; exit 2'
VERSION_COMMAND ?= sh -ec 'echo "Configure a native version command" >&2; exit 2'
RELEASE_CHECK_COMMAND ?= sh -ec 'echo "Configure a native release checker" >&2; exit 2'
BUILD_RECEIPT_COMMAND ?= true
RECEIPT_COMMAND ?= sh -ec 'echo "Configure a native receipt serializer" >&2; exit 2'

QUALITY_RUN ?= $(DOCKER) run --init --rm --read-only --network $(QUALITY_NETWORK) \
	--tmpfs /tmp:rw,exec,mode=1777,size=2g --user "$$(id -u):$$(id -g)" \
	--mount "type=bind,source=$(CURDIR)/$(REPORT_DIR),target=/app/coverage" \
	$(QUALITY_DIST_MOUNT) \
	$(QUALITY_RUN_ARGS) $(QUALITY_GIT_MOUNT) --env GIT_OPTIONAL_LOCKS=0 --env CONTAINERIZED=1 \
	--env HTTP_PROXY --env HTTPS_PROXY --env NO_PROXY \
	$(QUALITY_IMAGE)

.DEFAULT_GOAL := help
.PHONY: help install quality-image lint typecheck test test-unit coverage check build image verify version release-check ci-local source-identity $(QUALITY_EXTRA_TARGETS)
help: ## Show commands; does not build, write files or use the network.
	@printf '%s\n' 'lint typecheck test coverage check build image verify version release-check ci-local' \
	  'Prerequisites: Docker Buildx, Make, bash, git and coreutils. First dependency fetch needs network.' \
	  'OFFLINE=1 requires prepared images and build inputs; unavailable inputs are blocked.' \
	  'Docker runs native tools. $(REPORT_DIR)/ holds reports; coverage below 100% is advisory.'
install: quality-image ## Prepare the cached tool/dependency image.
source-identity:
	@bash docker/source_identity.sh "$(REPORT_DIR)/source.json"
quality-image: source-identity
	@mkdir -p "$(REPORT_DIR)" dist
ifeq ($(SKIP_QUALITY_BUILD),0)
ifeq ($(OFFLINE),1)
	@test -f $(REPORT_DIR)/quality-source.json -a -f $(REPORT_DIR)/quality-image-id.txt || { echo 'BLOCKED: prepare exact quality inputs online first.' >&2; exit 2; }
	@test "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/source.json)" = "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/quality-source.json)" || { echo 'BLOCKED: offline quality inputs changed.' >&2; exit 2; }
	@test "$$(cat $(REPORT_DIR)/quality-image-id.txt)" = "$$($(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}')" || { echo 'BLOCKED: prepared quality image missing or replaced.' >&2; exit 2; }
else
	$(DOCKER) buildx build --file $(QUALITY_DOCKERFILE) --target $(QUALITY_TARGET) --load --tag $(QUALITY_IMAGE) $(QUALITY_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) .
	@cp $(REPORT_DIR)/source.json $(REPORT_DIR)/quality-source.json
	@$(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}' > $(REPORT_DIR)/quality-image-id.txt
endif
endif
lint typecheck $(QUALITY_EXTRA_TARGETS): quality-image
	$(QUALITY_RUN) make $@
test-unit: test ## Compatibility name; runs the canonical suite once.
test: quality-image source-identity ## Fresh execution after cached image restoration.
	@rm -f $(REPORT_DIR)/test-image-id.txt $(REPORT_DIR)/test-source.json
	@touch $(REPORT_DIR)/.started
	$(QUALITY_TEST_COMMAND)
	$(QUALITY_RUN) $(COVERAGE_COMMAND)
	@$(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}' > $(REPORT_DIR)/test-image-id.txt
	@cp $(REPORT_DIR)/source.json $(REPORT_DIR)/test-source.json
coverage: quality-image source-identity ## Report a fresh result; never rerun tests.
	@test "$$(cat $(REPORT_DIR)/test-image-id.txt)" = "$$($(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}')" || { echo 'Missing/stale coverage image: run make test.' >&2; exit 1; }
	@test "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/source.json)" = "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/test-source.json)" || { echo 'Missing/stale coverage source: run make test.' >&2; exit 1; }
	$(QUALITY_RUN) $(COVERAGE_COMMAND)
check: QUALITY_TEST := check
check: test ## Run the native mandatory checks and one fresh suite.
build: quality-image source-identity ## Export distributions from the canonical cached build graph.
	@mkdir -p "$(REPORT_DIR)" dist
	@cp $(REPORT_DIR)/source.json $(REPORT_DIR)/build-source.json
	$(BUILD_COMMAND)
	@bash docker/source_identity.sh "$(REPORT_DIR)/source.json"
	$(BUILD_RECEIPT_COMMAND)
image: ## Build a local runtime candidate; never publish it.
	$(IMAGE_COMMAND)
verify: quality-image source-identity ## Run the repository's installed/runtime acceptance command.
	$(VERIFY_COMMAND)
version: quality-image ## Read the canonical native artifact version.
	$(VERSION_COMMAND)
release-check: quality-image ## Validate versions and built artifacts; never publish.
	$(RELEASE_CHECK_COMMAND)
ci-local: ## Run ordered native targets once and preserve a receipt even on failure.
	@mkdir -p "$(REPORT_DIR)" dist
	@rm -f $(REPORT_DIR)/ci-local.json $(REPORT_DIR)/artifacts.json $(REPORT_DIR)/versions.json
	@set -eu; plan_checks=''; separator=''; \
	  for target in $(CI_LOCAL_TARGETS); do \
	    case "$$target" in ''|*[!a-zA-Z0-9_.:-]*) echo 'Invalid CI target name' >&2; exit 2;; esac; \
	    plan_checks="$$plan_checks$$separator\"$$target\""; separator=,; \
	  done; \
	  printf '{"scope":"%s","checks":[%s]}\n' '$(CI_LOCAL_SCOPE)' "$$plan_checks" > $(REPORT_DIR)/ci-plan.json
	@printf '%s\n' '{"schemaVersion":1,"source":null,"scope":"$(CI_LOCAL_SCOPE)","status":"blocked","checks":[],"artifacts":[],"tools":{},"reason":"Source identity or native receipt unavailable"}' > $(REPORT_DIR)/ci-local.json
	@set -eu; \
	  bash docker/source_identity.sh "$(REPORT_DIR)/source.json"; \
	  printf '{"schemaVersion":1,"source":%s,"scope":"$(CI_LOCAL_SCOPE)","status":"blocked","checks":[],"artifacts":[],"tools":{},"reason":"Native preparation or receipt unavailable"}\n' "$$(cat $(REPORT_DIR)/source.json)" > $(REPORT_DIR)/ci-local.json; \
	  command -v $(firstword $(DOCKER)) >/dev/null && $(DOCKER) info >/dev/null 2>&1 || { printf '%s\n' 'BLOCKED: Docker/daemon unavailable. Scope: $(CI_LOCAL_SCOPE).' > $(REPORT_DIR)/ci-local-summary.md; exit 2; }; \
	  code=0; $(MAKE) --no-print-directory quality-image || code=$$?; \
	  if test "$$code" -ne 0; then \
	    state=failed; if test "$(OFFLINE)" = 1; then state=blocked; fi; \
	    printf '{"schemaVersion":1,"source":%s,"scope":"$(CI_LOCAL_SCOPE)","status":"%s","checks":[{"name":"prepare","command":"make quality-image","status":"%s","exitCode":%s}],"artifacts":[],"tools":{}}\n' "$$(cat $(REPORT_DIR)/source.json)" "$$state" "$$state" "$$code" > $(REPORT_DIR)/ci-local.json; \
	    printf 'Quality image preparation: %s (exit %s). Scope: $(CI_LOCAL_SCOPE).\n' "$$state" "$$code" > $(REPORT_DIR)/ci-local-summary.md; exit "$$code"; \
	  fi; \
	  $(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}' > $(REPORT_DIR)/quality-image-id.txt; \
	  $(RECEIPT_COMMAND) init; \
	  if test "$(OFFLINE)" = 1; then \
	    code=0; $(OFFLINE_PREFLIGHT_COMMAND) || code=$$?; \
	    $(RECEIPT_COMMAND) record --name offline-preflight --command "offline input preflight" --exit-code "$$code"; \
	    if test "$$code" -ne 0; then $(RECEIPT_COMMAND) finish --status blocked; exit 2; fi; \
	  fi; \
	  result=0; \
	  for target in $(CI_LOCAL_TARGETS); do \
	    code=0; $(MAKE) --no-print-directory SKIP_QUALITY_BUILD=1 "$$target" || code=$$?; \
	    $(RECEIPT_COMMAND) record --name "$$target" --command "make $$target" --exit-code "$$code"; \
	    if test "$$code" -ne 0; then result=$$code; break; fi; \
	  done; \
	  bash docker/source_identity.sh "$(REPORT_DIR)/source.json"; \
	  status=passed; if test "$$result" -ne 0; then status=failed; fi; \
	  $(RECEIPT_COMMAND) finish --status "$$status" || result=1; \
	  exit "$$result"
-include Makefile.local
