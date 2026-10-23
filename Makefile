ifeq ($(CONTAINERIZED),1)
RUFF ?= ruff
else
RUFF ?= uvx --from ruff==0.16.4 ruff
endif
# Quick dev commands never prepare a product/build image.
ifneq ($(filter lint format format-check check,$(MAKECMDGOALS)),)
override CONTAINERIZED := 1
endif
ifneq ($(CONTAINERIZED),1)
QUALITY_IMAGE := typertemplate:quality
QUALITY_NETWORK := bridge
QUALITY_EXTRA_TARGETS := test-integration
IMAGE_COMMAND = printf "%s\n" "notApplicable: this repository owns template source"
VERIFY_COMMAND = $(QUALITY_RUN) python docker/release.py bundle-verify
BUILD_COMMAND = $(QUALITY_RUN) python docker/release.py bundle-build
OFFLINE_PREFLIGHT_COMMAND = true
QUALITY_EXTRA_TARGETS += acceptance
CI_LOCAL_TARGETS ?= acceptance coverage build verify release-check
include docker/quality-python.mk
else
.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install format lint typecheck test test-unit test-integration check

help: ## Show available maintenance commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the locked maintenance environment.
	$(UV) sync --locked

format: ## Format owned Python maintenance sources.
	$(RUFF) check --fix tools tests
	$(RUFF) format tools tests

lint: ## Check owned source formatting and lint.
	$(RUFF) check tools tests
	$(RUFF) format --check tools tests

typecheck: ## Check maintenance sources with strict mypy.
	$(UV) run --locked mypy

test: ## Run composition and real Copier regressions with full coverage.
	$(UV) run --locked pytest --junitxml=coverage/junit.xml --cov --cov-report=term-missing --cov-report=json --cov-report=xml

test-unit: ## Run composition behavior tests.
	$(UV) run --locked pytest tools/test_composition.py

test-integration: ## Run real Copier render and update tests.
	$(UV) run --locked pytest tests/test_template.py

check: lint ## Fast lint-only gate; no tests/build.

acceptance: lint typecheck test ## Run every mandatory maintenance gate.
	$(UV) lock --check

endif

.PHONY: dev-policy-help
help: dev-policy-help
dev-policy-help:
	@printf '%s\n' 'Daily: make lint; explicit git push origin dev.' \
	  'Manual only: test, typecheck, coverage, build, acceptance, ci-local.' \
	  'Native lint tools must be installed; lint never syncs/builds the application.'
