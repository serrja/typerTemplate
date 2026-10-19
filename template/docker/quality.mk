# Managed portable recipe: pythonTemplate/template/docker/quality.mk.
# Project Makefiles select images and native commands; no sibling checkout is used.
DOCKER ?= docker
QUALITY_DOCKERFILE ?= Dockerfile.quality
QUALITY_TARGET ?= quality
QUALITY_IMAGE ?= project:quality
QUALITY_NETWORK ?= none
QUALITY_RUN_ARGS ?=
COMMA := ,
QUALITY_GIT_MOUNT = $(if $(wildcard $(CURDIR)/.git/HEAD),--mount "type=bind$(COMMA)source=$(CURDIR)/.git$(COMMA)target=/app/.git$(COMMA)readonly",)
QUALITY_BUILD_ARGS ?= $(DOCKER_BUILD_ARGS)
QUALITY_TEST ?= test
ARTIFACT_TARGET ?= artifacts
BUILD_DOCKERFILE ?= Dockerfile
BUILD_COMMAND ?= $(DOCKER) buildx build --file $(BUILD_DOCKERFILE) $(DOCKER_BUILD_ARGS) --target $(ARTIFACT_TARGET) --output type=local,dest=dist .
RUNTIME_IMAGE ?= project:candidate
IMAGE_COMMAND ?= $(DOCKER) buildx build $(DOCKER_BUILD_ARGS) --target runtime --load --tag $(RUNTIME_IMAGE) .
VERIFY_COMMAND ?= $(MAKE) verify-installed
QUALITY_EXTRA_TARGETS ?=

COVERAGE_COMMAND ?= sh -ec 'echo "Configure a language-native coverage reporter" >&2; exit 2'

QUALITY_RUN ?= $(DOCKER) run --init --rm --read-only --network $(QUALITY_NETWORK) \
	--tmpfs /tmp:rw,exec,mode=1777,size=2g --user "$$(id -u):$$(id -g)" \
	--mount "type=bind,source=$(CURDIR)/coverage,target=/app/coverage" \
	$(QUALITY_RUN_ARGS) $(QUALITY_GIT_MOUNT) --env GIT_OPTIONAL_LOCKS=0 --env GITHUB_ACTIONS --env CONTAINERIZED=1 \
	--env HTTP_PROXY --env HTTPS_PROXY --env NO_PROXY \
	$(QUALITY_IMAGE)

.DEFAULT_GOAL := help
.PHONY: help install quality-image lint typecheck test test-unit coverage check build image verify $(QUALITY_EXTRA_TARGETS)
help: ## Show commands; does not build, write files or use the network.
	@printf '%s\n' 'lint typecheck test coverage check build image verify' \
	  'Docker runs native tools. Coverage is advisory; missing/stale reports fail.'
install: quality-image ## Prepare the cached tool/dependency image.
quality-image:
	$(DOCKER) buildx build --file $(QUALITY_DOCKERFILE) --target $(QUALITY_TARGET) --load --tag $(QUALITY_IMAGE) $(QUALITY_BUILD_ARGS) .
	@mkdir -p coverage
lint typecheck $(QUALITY_EXTRA_TARGETS): quality-image
	$(QUALITY_RUN) make $@
test-unit: test ## Compatibility name; runs the canonical suite once.
test: quality-image ## Fresh execution after cached image restoration.
	@rm -f coverage/test-image-id.txt
	@touch coverage/.started
	$(QUALITY_RUN) make $(QUALITY_TEST)
	$(QUALITY_RUN) $(COVERAGE_COMMAND)
	@$(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}' > coverage/test-image-id.txt
coverage: quality-image ## Report a fresh result; never rerun tests.
	@test "$$(cat coverage/test-image-id.txt)" = "$$($(DOCKER) image inspect $(QUALITY_IMAGE) --format '{{.Id}}')" || { echo 'Missing/stale coverage: run make test for the current inputs.' >&2; exit 1; }
	$(QUALITY_RUN) $(COVERAGE_COMMAND)
check: QUALITY_TEST := check
check: test ## Run the native mandatory checks and one fresh suite.
build: ## Export distributions from the canonical cached build graph.
	$(BUILD_COMMAND)
image: ## Build a local runtime candidate; never publish it.
	$(IMAGE_COMMAND)
verify: ## Run the repository's installed/runtime acceptance command.
	$(VERIFY_COMMAND)
-include Makefile.local
