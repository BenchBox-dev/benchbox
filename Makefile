override BENCHBOX_MAKEFILE_ROOT := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))

PR_FANOUT_JOBS ?= 4
PR_REVIEW_BASE ?= develop
PR_REVIEW_PR_LIMIT ?= 1000
PR_REVIEW_MAX_COMMENTS ?= 0
PR_REVIEW_INCLUDE_RESOLVED ?= 0
PR_REVIEW_INCLUDE_POST_MERGE ?= 0
PR_REVIEW_FAIL_ON_PENDING ?= 0
PR_REVIEW_EXECUTOR_SANDBOX ?= workspace-write
PR_REVIEW_EXECUTOR_APPROVAL ?= never
PR_STATUS_LIMIT ?= 20
PR_STATUS_ALL_OPEN_LIMIT ?= 1000
DEV_LOOP_METRICS_DAYS ?= 30
DEV_LOOP_METRICS_ARGS ?=
AUDIT_SHA_TARGET_REF ?= origin/develop
AUDIT_SHA_REQUIRE_CURRENT ?=
JOINORDER_BUILD_DIR ?= $(HOME)/Developer/benchmark_runs/joinorder/build/joinorder-imdb-2013-v1
JOINORDER_POSTGRES_DB ?= imdb
JOINORDER_POSTGRES_USER ?= postgres
JOINORDER_QUERIES ?= _project/joinorder/build-inputs/queries
JOINORDER_REFERENCE ?= _project/joinorder/reference_cardinalities.json

DEVELOPMENT_TREE_ONLY_TARGETS := \
	correctness-gate-digests-regen ledger-seed-gates ledger-seed-recut tpch-subscale-row-counts-regen \
	cross-surface-baseline-autodetect \
	oracle-coverage-map oracle-coverage-map-check review-backoff-reset omp-candidate-check cross-surface-applicability-report \
	joinorder-verify-reference-results complexity-check complexity-report \
	quality-governance-typecheck uv-lock-revision-check sqlglot-repro-retirement-check audit-deps audit-raw audit-raw-check \
	audit-sha-check artifact-hygiene agent-instructions-check agent-identity-check security-audit \
	agent-commit-range-check skill-integrity-check ci-lint pr-preflight pr-arm-auto-merge shrink-rollup \
	pr-review-followups-list pr-review-followups dev-loop-metrics ci-usage-report platform-manifest \
	platform-manifest-check test-docker-parity blind-spots-list blind-spots-report \
	soundness-drain-report soundness-drain-self-test worktree-audit worktree-finish worktree-reap review-mutation-test corpus-stage-local

.PHONY: test
test: test-fast
	@echo "Default test run completed. Use 'make help' to see all test options."

$(DEVELOPMENT_TREE_ONLY_TARGETS): .development-tree-required

.development-tree-required: SHELL := /bin/sh
.PHONY: .development-tree-required
.development-tree-required:
	@if [ ! -d "$(BENCHBOX_MAKEFILE_ROOT)_project/decisions" ]; then \
		echo "Error: this target requires the BenchBox development tree (full development tree required); curated releases retain only the Results Explorer publication helpers under _project/scripts/." >&2; \
		exit 2; \
	fi

include $(BENCHBOX_MAKEFILE_ROOT)make/tests.mk
include $(BENCHBOX_MAKEFILE_ROOT)make/reports.mk
include $(BENCHBOX_MAKEFILE_ROOT)make/oracle.mk
include $(BENCHBOX_MAKEFILE_ROOT)make/review.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/platform-tests.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/quality.mk
include $(BENCHBOX_MAKEFILE_ROOT)make/build.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/documentation.mk
include $(BENCHBOX_MAKEFILE_ROOT)make/site-inputs.mk


MAKE_TIMING_SHELL := $(BENCHBOX_MAKEFILE_ROOT)scripts/make_timing_shell.sh
MAKE_TIMED_TARGETS := \
	agent-write-preflight comment-policy-check pr-arm pr-open pr-preflight pr-preflight-fast-tests \
	test-fast test-medium
$(MAKE_TIMED_TARGETS): SHELL := $(MAKE_TIMING_SHELL)
$(MAKE_TIMED_TARGETS): export BENCHBOX_MAKE_TIMING_TARGET = $@
$(MAKE_TIMED_TARGETS): export BENCHBOX_MAKE_TIMING_GOALS = $(MAKECMDGOALS)

MAKE_TIMINGS_DAYS ?= 30
MAKE_TIMINGS_SLOWEST ?= 5
.PHONY: make-timings-report
make-timings-report:
	@uv run -- python scripts/make_timing.py report --days "$(MAKE_TIMINGS_DAYS)" --slowest "$(MAKE_TIMINGS_SLOWEST)"

include $(BENCHBOX_MAKEFILE_ROOT)make/pr-landing.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/worktrees.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/worktree-maintenance.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/batch.mk

.PHONY: branch-prune-merged
branch-prune-merged:
	@command -v gh >/dev/null 2>&1 || { echo "gh CLI required for branch-prune-merged" >&2; exit 1; }
	@DRY_RUN='$(if $(filter 1,$(DRY_RUN)),1,$(if $(strip $(DRY_RUN)),invalid,))' \
		uv run -- python scripts/branch_prune_merged.py

include $(BENCHBOX_MAKEFILE_ROOT)make/uat.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/help.mk
