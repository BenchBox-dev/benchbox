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
DEV_LOOP_METRICS_LIMIT ?= 100
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
	oracle-coverage-map oracle-coverage-map-check oracle-backoff-reset omp-candidate-check cross-surface-applicability-report \
	joinorder-verify-reference-results complexity-check complexity-report \
	quality-governance-typecheck uv-lock-revision-check sqlglot-repro-retirement-check audit-deps audit-raw audit-raw-check \
	audit-sha-check artifact-hygiene agent-instructions-check agent-identity-check security-audit \
	agent-commit-range-check skill-integrity-check ci-lint pr-preflight pr-arm-auto-merge shrink-rollup \
	pr-review-followups-list pr-review-followups dev-loop-metrics ci-usage-report platform-manifest \
	platform-manifest-check test-docker-parity blind-spots-list blind-spots-report \
	soundness-drain-report soundness-drain-self-test worktree-audit worktree-finish worktree-reap oracle-mutation-test

.PHONY: coverage-check

.PHONY: test
test: test-fast
	@echo "Default test run completed. Use 'make help' to see all test options."

$(DEVELOPMENT_TREE_ONLY_TARGETS): .development-tree-required

.PHONY: .development-tree-required
.development-tree-required:
	@if [ ! -d "$(BENCHBOX_MAKEFILE_ROOT)_project/decisions" ]; then \
		echo "Error: this target requires the BenchBox development tree (full development tree required); curated releases retain only the Results Explorer publication helpers under _project/scripts/." >&2; \
		exit 2; \
	fi

.PHONY: publication-help
publication-help:
	@echo "Publication flow:"
	@echo "  benchbox.dev is built and deployed from BenchBox-dev/benchbox-site, not from this repository."
	@echo "  Data and pipeline changes reach it through the site-inputs bundle (site-inputs.yml)."
	@echo "  Site, Explorer, landing and blog sources live in that repository; see CONTRIBUTING.md."

.PHONY: test-all
test-all:
	@echo "Running non-resource-heavy tests in parallel..."
	BENCHBOX_TEST_TIER=t2 uv run -- python -m pytest -m "not (slow or stress or resource_heavy or live_integration)" --timeout=300
	@echo "Running slow and resource-heavy tests serially..."
	BENCHBOX_TEST_TIER=t3 uv run -- python -m pytest -m "(slow or resource_heavy) and not (stress or live_integration)" -n 0 --timeout=1200

.PHONY: test-unit
test-unit:
	BENCHBOX_TEST_TIER=t2 uv run -- python -m pytest -m "unit" --tb=short

.PHONY: test-integration
test-integration:
	BENCHBOX_TEST_TIER=t2 uv run -- python -m pytest -m "integration and not live_integration and not stress" --tb=short

.PHONY: test-tpch
test-tpch:
	BENCHBOX_TEST_TIER=t2 uv run -- python -m pytest -m "tpch" --tb=short

test-quick:
	BENCHBOX_TEST_TIER=t1 uv run -- python -m pytest -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short --maxfail=5 --timeout=120

test-verbose:
	BENCHBOX_TEST_TIER=t3 uv run -- python -m pytest -v

.PHONY: test-pytest
test-pytest:
	BENCHBOX_TEST_TIER=t2 uv run -- python -m pytest -m "not stress"

.PHONY: test-fast
test-fast:
	BENCHBOX_TEST_TIER=t1 uv run -- python -m pytest -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short -rs --timeout=120

.PHONY: test-unlock
test-unlock:
	@LOCK_DIR="$${BENCHBOX_TEST_LOCK_DIR:-$$HOME/.benchbox}"; \
	case "$$LOCK_DIR" in \
		"~") LOCK_DIR="$$HOME" ;; \
		"~/"*) LOCK_DIR="$$HOME/$${LOCK_DIR#\~/}" ;; \
	esac; \
	LOCK_PATH="$$LOCK_DIR/test.lock"; \
	python3 scripts/local_validation.py clear-test-lock "$$LOCK_PATH"

.PHONY: test-medium
test-medium:
	BENCHBOX_TEST_TIER=t2 uv run -- python -m pytest -m "medium and not (slow or stress or resource_heavy or live_integration)" --tb=short --timeout=60 -n 5

.PHONY: test-medium-selected
test-medium-selected:
	@set -eu; \
	OUTPUT=$$(mktemp); \
	trap 'rm -f "$$OUTPUT"' EXIT; \
	BENCHBOX_TEST_TIER=t2 uv run -- python scripts/canary_impact.py \
		--changed-json-env BENCHBOX_MEDIUM_CHANGED_PATHS_JSON \
		--marker-expression "medium and not (slow or stress or resource_heavy or live_integration)" --cant-affect-list empty \
		--product-code-only --run-selected --output "$$OUTPUT"

.PHONY: test-slow
test-slow:
	BENCHBOX_TEST_TIER=t3 uv run -- python -m pytest -m "slow and not (stress or live_integration)" -n 0 --tb=short -v --timeout=1200

.PHONY: test-stress
test-stress:
	BENCHBOX_TEST_TIER=t3 uv run -- python -m pytest -m "stress" -n 0 --tb=short -v --timeout=1800

test-dev:
	BENCHBOX_TEST_TIER=t1 uv run -- python -m pytest -m "fast and unit and not (slow or stress or resource_heavy or live_integration)" --tb=short --maxfail=3 --timeout=120

test-smoke: test-quick

CORRECTNESS_GATE_QUERY_IDS := 1,2,3,4,5,6,7,8,9,10,12,13,14,15,17,19,21,22

.PHONY: test-correctness-gate
test-correctness-gate:
	@REPORT="$$(mktemp)"; \
	BENCHBOX_STRICT_EXPECTED_RESULTS=1 BENCHBOX_EMIT_RESULT_DIGEST=1 BENCHBOX_CORRECTNESS_GATE_QUERY_IDS=$(CORRECTNESS_GATE_QUERY_IDS) uv run -- python -m pytest -m stress "tests/integration/test_local_platform_benchmark_matrix.py::test_local_platform_benchmark_matrix[tpch-duckdb]" -n 0 --tb=short --timeout=1200 -v --junitxml="$$REPORT"; \
	PYTEST_STATUS=$$?; \
	uv run -- python -c "import sys, xml.etree.ElementTree as ET; root = ET.parse(sys.argv[1]).getroot(); suites = [root] if root.tag == 'testsuite' else root.findall('testsuite'); tests = sum(int(s.get('tests') or 0) for s in suites); skipped = sum(int(s.get('skipped') or 0) for s in suites); errors = sum(int(s.get('errors') or 0) for s in suites); failures = sum(int(s.get('failures') or 0) for s in suites); print('correctness gate guard: ran=%d skipped=%d failures=%d errors=%d' % (tests, skipped, failures, errors)); sys.exit(0 if (tests == 1 and skipped == 0 and errors == 0 and failures == 0) else 'correctness gate: expected exactly 1 node to run with 0 skipped/failed/errored; a selected skip means duckdb/tpch dropped from the stable matrix or the node-id drifted')" "$$REPORT"; \
	GUARD_STATUS=$$?; \
	rm -f "$$REPORT"; \
	test $$PYTEST_STATUS -eq 0 && test $$GUARD_STATUS -eq 0

.PHONY: plan-capture-gate
plan-capture-gate:
	uv run -- python -m pytest tests/integration/test_plan_capture_gate.py -q -n 0 --tb=short

.PHONY: tuning-liveness-gate
tuning-liveness-gate:
	command -v zstd
	uv run -- python -m pytest tests/integration/tuning/test_tuning_liveness_e2e.py -m slow -q -n 0 --tb=short -p no:randomly

.PHONY: correctness-gate-digests-regen
correctness-gate-digests-regen:
	BENCHBOX_CORRECTNESS_GATE_QUERY_IDS=$(CORRECTNESS_GATE_QUERY_IDS) uv run -- python _project/scripts/regenerate_correctness_gate_digests.py

LEDGER_SEED_GATE_SUITES := tests/unit/scripts/publication/test_ledger_seed.py \
	tests/unit/scripts/publication/test_check_corpus_bijection.py \
	tests/unit/scripts/test_site_inputs.py

.PHONY: ledger-seed-gates
ledger-seed-gates:
	uv run -- pytest -n 0 -q $(LEDGER_SEED_GATE_SUITES)

.PHONY: ledger-seed-recut
ledger-seed-recut:
	git fetch --no-tags origin published-results:refs/remotes/origin/published-results
	uv run -- python scripts/publication/create_ledger_seed.py --accepted-ref origin/published-results
	$(MAKE) ledger-seed-gates

.PHONY: tpch-subscale-row-counts-regen
tpch-subscale-row-counts-regen:
	uv run -- python _project/scripts/generate_tpch_subscale_row_counts.py

.PHONY: tpchavoc-equivalence-report
tpchavoc-equivalence-report:
	uv run -- python -m benchbox.core.tpchavoc.equivalence

.PHONY: tpchavoc-equivalence-report-postgres
tpchavoc-equivalence-report-postgres:
	uv run -- python -m benchbox.core.tpchavoc.equivalence --engine postgres

.PHONY: tpchavoc-equivalence-report-datafusion
tpchavoc-equivalence-report-datafusion:
	uv run -- python -m benchbox.core.tpchavoc.equivalence --engine datafusion

.PHONY: tpchavoc-equivalence-report-clickhouse
tpchavoc-equivalence-report-clickhouse:
	uv run -- python -m benchbox.core.tpchavoc.equivalence --engine clickhouse

.PHONY: tpchavoc-dataframe-equivalence-report
tpchavoc-dataframe-equivalence-report:
	uv run -- python -m benchbox.core.tpchavoc.dataframe_equivalence

EXECUTION_ENGINE ?=
EXECUTION_ENGINE_OPTION = $(if $(strip $(EXECUTION_ENGINE)),--execution-engine $(EXECUTION_ENGINE),)

.PHONY: ssb-cross-surface-equivalence-report
ssb-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark ssb $(EXECUTION_ENGINE_OPTION)

.PHONY: amplab-cross-surface-equivalence-report
amplab-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark amplab $(EXECUTION_ENGINE_OPTION)

.PHONY: coffeeshop-cross-surface-equivalence-report
coffeeshop-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark coffeeshop $(EXECUTION_ENGINE_OPTION)

.PHONY: clickbench-cross-surface-equivalence-report
clickbench-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark clickbench $(EXECUTION_ENGINE_OPTION)

.PHONY: joinorder-synthetic-cross-surface-equivalence-report
joinorder-synthetic-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark joinorder_synthetic $(EXECUTION_ENGINE_OPTION)

.PHONY: h2odb-cross-surface-equivalence-report
h2odb-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark h2odb $(EXECUTION_ENGINE_OPTION)

.PHONY: read-primitives-cross-surface-equivalence-report
read-primitives-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark read_primitives $(EXECUTION_ENGINE_OPTION)

flightdata-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark flightdata $(EXECUTION_ENGINE_OPTION)

datavault-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark datavault $(EXECUTION_ENGINE_OPTION)

nyctaxi-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark nyctaxi $(EXECUTION_ENGINE_OPTION)

tsbs-devops-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark tsbs_devops $(EXECUTION_ENGINE_OPTION)

tpch-skew-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark tpch_skew $(EXECUTION_ENGINE_OPTION)

tpch-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark tpch $(EXECUTION_ENGINE_OPTION)

tpcds-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark tpcds --backend expression --backend datafusion $(EXECUTION_ENGINE_OPTION)

.PHONY: polars-df-compat-check
polars-df-compat-check:
	@set -eu; \
	DIR=$$(mktemp -d); \
	trap 'rm -rf "$$DIR"' EXIT; \
	uv run --no-sync -- python -c "import polars; print('polars', polars.__version__)"; \
	for benchmark in tpch tpcds ssb clickbench; do \
		BENCHBOX_OUTPUT_DIR="$$DIR" uv run --no-sync -- benchbox run --platform polars-df --benchmark "$$benchmark" \
			--scale 0.01 --non-interactive; \
	done; \
	uv run --no-sync -- python scripts/check_polars_df_smoke.py "$$DIR/results"; \
	for benchmark in tpch ssb clickbench; do \
		uv run --no-sync -- python -m benchbox.core.equivalence.cross_surface --benchmark "$$benchmark"; \
	done; \
	uv run --no-sync -- python -m benchbox.core.equivalence.cross_surface --benchmark tpcds --backend expression --backend datafusion

.PHONY: tpcds-pandas-cross-surface-equivalence-report
tpcds-pandas-cross-surface-equivalence-report:
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark tpcds --backend pandas

.PHONY: tpcds-cross-surface-draws-report
tpcds-cross-surface-draws-report:
	@status=0; \
	for draw in "--power-stream 1" "--seed 42" "--seed 42 --power-stream 1"; do \
		uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark tpcds \
			--backend expression --backend pandas --backend datafusion --repeats 2 $$draw $(EXECUTION_ENGINE_OPTION) || status=1; \
	done; \
	exit $$status

.PHONY: cross-surface-update-baseline
cross-surface-update-baseline:
	@test -n "$(BENCHMARK)" || { echo "Usage: make cross-surface-update-baseline BENCHMARK=<ssb|amplab|coffeeshop|clickbench|joinorder_synthetic|h2odb|read_primitives|flightdata|datavault>"; exit 1; }
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark $(BENCHMARK) --update-baseline

.PHONY: cross-surface-baseline-autodetect
cross-surface-baseline-autodetect:
	uv run -- python _project/scripts/cross_surface_baseline_autodetect.py \
		--json-out _project/cross-surface-baseline-autodetect/summary.json

.PHONY: oracle-coverage-map
oracle-coverage-map:
	uv run -- python _project/scripts/generate_oracle_coverage_map.py

.PHONY: oracle-coverage-map-check
oracle-coverage-map-check:
	uv run -- python _project/scripts/generate_oracle_coverage_map.py --check

.PHONY: oracle-backoff-reset
oracle-backoff-reset:
	@test -n "$(PR)" -a -n "$(HEAD)" -a -n "$(REASON)" || { echo "Usage: make oracle-backoff-reset PR=<number> HEAD=<40-character head SHA> REASON='<why>'" >&2; exit 1; }
	uv run -- python -m _project.scripts.oracle_reviewers.cli reset-backoff --pr "$$PR" --head "$$HEAD" --reason "$$REASON"

.PHONY: omp-candidate-check
omp-candidate-check:
	@test -n "$(VERSION)" || { echo "Usage: make omp-candidate-check VERSION=<omp version> [PIN=<file whose omp section names the candidate>] [BINARY=<local omp binary for this platform>]" >&2; exit 1; }
	uv run -- python -m _project.scripts.oracle_reviewers.omp_candidate check --version "$$VERSION" $${PIN:+--pin "$$PIN"} $${BINARY:+--binary "$$BINARY"}

.PHONY: cross-surface-applicability-report
cross-surface-applicability-report:
	uv run -- python _project/scripts/cross_surface_applicability_sweep.py

.PHONY: test-local-matrix
test-local-matrix:
	uv run -- python -m pytest tests/integration/test_local_platform_benchmark_matrix.py -m stress -n 0 --tb=short -v
	@echo "Tip: set BENCHBOX_SERVICE_LOCAL_MATRIX=1 to include Trino/Presto/Firebolt/PostgreSQL/TimescaleDB service-backed locals."

.PHONY: test-required-local-cases
test-required-local-cases:
	@set -eu; \
	DIR=$$(mktemp -d); \
	trap 'rm -rf "$$DIR"' EXIT; \
	SHA=$$(git rev-parse HEAD); \
	uv run -- python -c "from tests.required_local_cases import REQUIRED_LOCAL_CASES; print('\n'.join(REQUIRED_LOCAL_CASES))" > "$$DIR/required.txt"; \
	set -- $$(cat "$$DIR/required.txt"); \
	STATUS=0; \
	uv run -- python -m pytest -m "" -n 0 --tb=short --timeout=1200 -v -p scripts.pytest_shard_evidence \
		--basetemp "$$DIR/basetemp" --assigned-nodeids "$$DIR/required.txt" --shard-evidence "$$DIR/evidence.json" \
		--checked-sha "$$SHA" "$$@" || STATUS=$$?; \
	uv run -- python scripts/release_canary_sharding.py verify-required --evidence "$$DIR/evidence.json" \
		--nodeids "$$DIR/required.txt" --checked-sha "$$SHA" || STATUS=$$?; \
	exit $$STATUS

.PHONY: joinorder-verify-reference-results
joinorder-verify-reference-results:
	@[ -n "$(JOINORDER_POSTGRES_CONTAINER)" ] || { echo "JOINORDER_POSTGRES_CONTAINER is required"; exit 2; }
	uv run -- python _project/scripts/build_joinorder_data.py verify-reference-results \
		--work-dir "$(JOINORDER_BUILD_DIR)" \
		--container-name "$(JOINORDER_POSTGRES_CONTAINER)" \
		--database "$(JOINORDER_POSTGRES_DB)" \
		--user "$(JOINORDER_POSTGRES_USER)" \
		--queries "$(JOINORDER_QUERIES)" \
		--reference "$(JOINORDER_REFERENCE)"

.PHONY: test-duckdb
test-duckdb:
	uv run -- python -m pytest -m "duckdb" --tb=short

.PHONY: test-sqlite
test-sqlite:
	uv run -- python -m pytest -m "sqlite" --tb=short

.PHONY: test-pyspark
test-pyspark:
	./scripts/run_pyspark_tests.sh

.PHONY: test-read-primitives
test-read-primitives:
	uv run -- python -m pytest -m "primitives" --tb=short

.PHONY: test-benchmarks
test-benchmarks:
	uv run -- python -m pytest -m "tpch or tpcds or ssb or amplab or clickbench or h2odb or merge" --tb=short

test-tpcds:
	uv run -- python -m pytest -m "tpcds" --tb=short

TPCDS_OBT_EQUIVALENCE_SF ?= 0.01
.PHONY: tpcds-obt-equivalence
tpcds-obt-equivalence:
	BENCHBOX_TEST_TIER=t2 BENCHBOX_OBT_EQUIVALENCE_REQUIRED=1 BENCHBOX_OBT_EQUIVALENCE_SF=$(TPCDS_OBT_EQUIVALENCE_SF) uv run -- python -m pytest tests/integration/test_tpcds_obt_equivalence.py --tb=short -n 0 --timeout=3600 $(PYTEST_ARGS)

test-olap:
	uv run -- python -m pytest -m "olap" --tb=short

test-window:
	uv run -- python -m pytest -m "window_functions" --tb=short

.PHONY: test-ci
test-ci:
	uv run -- python -m pytest -c pytest-ci.ini -m "not (slow or stress or resource_heavy or live_integration)" --cov=benchbox --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml --cov-fail-under=0 --timeout=300

test-no-cloud:
	uv run -- python -m pytest -m "not (slow or cloud_import)" --ignore=tests/unit/platforms/databricks --ignore=tests/unit/platforms/snowflake --ignore=tests/unit/platforms/bigquery --ignore=tests/unit/platforms/redshift --tb=short

test-full:
	uv run -- python -m pytest -m "not stress" --tb=short -v

test-parallel:
	uv run -- python -m pytest -n auto --tb=short

test-parallel-fast:
	uv run -- python -m pytest -n auto -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short --timeout=120

include $(BENCHBOX_MAKEFILE_ROOT)make/platform-tests.mk

.PHONY: coverage-fast
coverage-fast:
	uv run -- python -m pytest -c pytest-ci.ini -m "fast and not (slow or stress or resource_heavy or live_integration or cloud_import)" --cov=benchbox --cov-report=term-missing:skip-covered --cov-fail-under=0 --timeout=120

.PHONY: coverage-all
coverage-all:
	uv run -- python -m pytest -c pytest-ci.ini -m "not (stress or resource_heavy or live_integration)" --cov=benchbox --cov-branch --cov-report=term-missing:skip-covered --cov-report=html:htmlcov --cov-report=xml:coverage.xml --cov-fail-under=0

.PHONY: coverage-opt-in-all
coverage-opt-in-all:
	uv run -- python -m pytest -c pytest-ci.ini --cov=benchbox --cov-branch --cov-report=term-missing:skip-covered --cov-report=html:htmlcov --cov-report=xml:coverage.xml --cov-fail-under=0

.PHONY: coverage
coverage: coverage-all

.PHONY: coverage-html
coverage-html:
	uv run -- python -m pytest -c pytest-ci.ini -m "not (stress or resource_heavy or live_integration)" --cov=benchbox --cov-report=html:htmlcov --cov-fail-under=0

.PHONY: coverage-report
coverage-report:
	uv run -- python -m pytest -c pytest-ci.ini -m "not (stress or resource_heavy or live_integration)" --cov=benchbox --cov-report=xml:coverage.xml --cov-report=term-missing --cov-fail-under=0


.PHONY: complexity-check
complexity-check:
	uv run -- python _project/scripts/check_complexity.py

.PHONY: complexity-report
complexity-report:
	uv run -- python _project/scripts/check_complexity.py --no-fail --top 30

.PHONY: quality-governance-typecheck
quality-governance-typecheck:
	uv run ty check --error all _project/scripts/check_complexity.py

.PHONY: install
install:
	uv sync

.PHONY: develop
develop:
	uv sync --group dev

.PHONY: clean
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf __pycache__/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete
	find . -name '*.pyo' -delete
	find . -name '.DS_Store' -delete

.PHONY: lint
lint:
	uv run ruff check .
	$(MAKE) comment-policy-check
	$(MAKE) windows-antipatterns-check

.PHONY: uv-lock-revision-check
uv-lock-revision-check:
	uv run -- python _project/scripts/check_uv_lock_revision.py $(if $(BASE_REF),--baseline-ref "$(BASE_REF)",)
	for project in _project/sql-translation-ml _project/scripts; do uv lock --check --project "$$project" || exit 1; done

.PHONY: sqlglot-upstream-freshness-check
sqlglot-upstream-freshness-check:
	uv run -- python scripts/check_sqlglot_upstream_freshness.py

.PHONY: sqlglot-adopt-wheel
sqlglot-adopt-wheel:
	@test -n "$(WHEEL)" || { echo "usage: make sqlglot-adopt-wheel WHEEL=path/to/benchbox_sqlglot-X.whl [SHA256=<digest>] [DRY_RUN=1]"; exit 2; }
	uv run -- python -m scripts.adopt_sqlglot_wheel --wheel "$(WHEEL)" $(if $(SHA256),--sha256 "$(SHA256)",) $(if $(DRY_RUN),--dry-run,)

.PHONY: sqlglot-advisory-check
sqlglot-advisory-check:
	uv run -- python scripts/check_sqlglot_advisories.py

.PHONY: sqlglot-fork-diff
sqlglot-fork-diff:
	uv run -- python -m scripts.sqlglot_fork_diff $(if $(OLD),--old "$(OLD)",) $(if $(NEW),--new "$(NEW)",) $(if $(BASE_REF),--base-ref "$(BASE_REF)",) $(if $(OUT),--output "$(OUT)",)

.PHONY: sql-execution-gate
sql-execution-gate:
	uv run -- python -m scripts.sql_execution_gate $(SQL_EXECUTION_GATE_ARGS)

.PHONY: sqlglot-repro-retirement-check
sqlglot-repro-retirement-check:
	uv run -- python -m scripts.check_sqlglot_repro_retirement $(if $(BASE_REF),--base-ref "$(BASE_REF)",) --check

audit-deps:
	uv run -- python _project/scripts/dependency_audit/check_deps.py

audit-raw:
	uv run -- python _project/scripts/dependency_audit/parse_deps.py

audit-raw-check:
	uv run -- python _project/scripts/dependency_audit/parse_deps.py --check

.PHONY: audit-sha-check
audit-sha-check:
	@test -n "$(FILE)" || { echo "Usage: make audit-sha-check FILE=<audit.md>"; exit 1; }
	uv run --no-project -- python _project/scripts/audit_sha_check.py \
		--target-ref "$(AUDIT_SHA_TARGET_REF)" \
		--ancestry-ref "$(or $(AUDIT_SHA_ANCESTRY_REF),HEAD)" \
		$(if $(AUDIT_SHA_REQUIRE_CURRENT),--require-current $(AUDIT_SHA_REQUIRE_CURRENT),) \
		"$(FILE)"

.PHONY: windows-antipatterns-check
windows-antipatterns-check:
	uv run -- python scripts/check_windows_antipatterns.py

.PHONY: site-linked-paths-check
site-linked-paths-check:
	uv run -- python scripts/check_site_linked_paths.py $(if $(INVENTORY_FILE),--inventory-file "$(INVENTORY_FILE)",)

.PHONY: comment-policy-check
comment-policy-check:
	uv run -- python scripts/run_comment_policy.py && uv run -- python scripts/check_comment_cleanup_scope.py --check-drift $(if $(BASE_REF),--base "$(BASE_REF)",)

.PHONY: check-comment-scope
check-comment-scope:
	uv run -- python scripts/check_comment_cleanup_scope.py --check-drift $(if $(BASE_REF),--base "$(BASE_REF)",)

.PHONY: comment-policy-strict
comment-policy-strict:
	uv run -- python scripts/check_comment_policy.py --mode strict

.PHONY: comment-policy-report
comment-policy-report:
	uv run -- python scripts/check_comment_policy.py --mode report

.PHONY: comment-policy-dead-exceptions
comment-policy-dead-exceptions:
	uv run -- python scripts/report_dead_comment_exceptions.py

.PHONY: lint-markers
lint-markers:
	uv run -- python -m pytest --collect-only -q -p no:warnings --assert=plain -p tests.utilities.marker_manifest $(if $(MARKER_MANIFEST),"--marker-manifest=$(MARKER_MANIFEST)")
	uv run -- python -m pytest tests/unit/test_marker_strategy.py -q

.PHONY: lint-imports
lint-imports:
	uv run -- lint-imports

.PHONY: artifact-hygiene
artifact-hygiene:
	uv run -- python _project/scripts/artifact_hygiene_check.py --all-tracked

.PHONY: agent-instructions-check
agent-instructions-check:
	uv run -- python _project/scripts/agent_instruction_audit.py

.PHONY: agent-identity-check
agent-identity-check:
	uv run -- python _project/scripts/agent_instruction_audit.py --check-git-identity

AGENT_IDENTITY_BASE_REF ?= origin/develop
.PHONY: agent-commit-range-check
agent-commit-range-check:
	@git fetch origin $(patsubst origin/%,%,$(AGENT_IDENTITY_BASE_REF)) --quiet 2>/dev/null || true
	uv run -- python _project/scripts/agent_instruction_audit.py --check-commit-range $(AGENT_IDENTITY_BASE_REF)

SKILL_SYNC ?= tools/skill-sync

.PHONY: skill-sync
skill-sync:
	@dry_run=""; \
	for flag_word in $(MAKEFLAGS); do \
		case "$$flag_word" in \
			n|-n|--dry-run|--just-print) dry_run=1; break ;; \
			-*) ;; \
			*n*) case "$$flag_word" in *[!a-zA-Z]*) ;; *) dry_run=1; break ;; esac ;; \
		esac; \
	done; \
	if [ -n "$$dry_run" ]; then echo "dry-run (-n): skipping skill-sync"; exit 0; fi; \
	if [ ! -x "$(SKILL_SYNC)" ]; then \
		echo "skill-sync wrapper not found or not executable at $(SKILL_SYNC); refusing to report success without syncing (override with SKILL_SYNC=path/to/skill-sync)" >&2; \
		exit 1; \
	fi; \
	$(MAKE) -s agent-write-preflight && \
	"$(SKILL_SYNC)" apply && \
	cp .claude/skills/skill-sync.config.yaml .agents/skills/skill-sync.config.yaml

.PHONY: skill-sync-check
skill-sync-check:
	@if [ ! -x "$(SKILL_SYNC)" ]; then \
		echo "skill-sync wrapper not found or not executable at $(SKILL_SYNC); cannot verify the mirror (override with SKILL_SYNC=path/to/skill-sync)" >&2; \
		exit 1; \
	fi
	@tmp=$$(mktemp); \
	"$(SKILL_SYNC)" preview >"$$tmp" || { rc=$$?; rm -f "$$tmp"; exit $$rc; }; \
	drift=""; \
	while IFS= read -r row; do \
		case "$$row" in "A "*|"M "*|"D "*|"R "*) ;; *) continue ;; esac; \
		path="$${row#? }"; \
		case "$$path" in ./*) path="$${path#./}";; esac; \
		case "$$path" in .agents/skills/*|.claude/skills/blog/*) continue;; esac; \
		drift="$$drift$$row\n"; \
	done <"$$tmp"; \
	rm -f "$$tmp"; \
	if [ -n "$$drift" ]; then \
		echo "skill-sync-check: tracked mirror drifts from skill-sync.conf:" >&2; \
		printf '%b' "$$drift" >&2; \
		exit 3; \
	fi; \
	echo "skill-sync-check: tracked mirror up to date."

.PHONY: skill-integrity-check
skill-integrity-check:
	@set -eu; \
	uv run -- python scripts/skill_sync_ci_policy.py validate --manifest skill-sync.conf; \
	"$(SKILL_SYNC)" verify; \
	sh scripts/check_untracked_skill_mirrors.sh; \
	$(MAKE) agent-instructions-check; \
	$(MAKE) agent-identity-check; \
	$(MAKE) agent-commit-range-check; \
	$(MAKE) artifact-hygiene

.PHONY: disk-check
disk-check:
	uv run -- python scripts/check_free_disk.py $(if $(DISK_PATH),--path "$(DISK_PATH)",) $(if $(MIN_FREE_PERCENT),--min-free-percent "$(MIN_FREE_PERCENT)",)

.PHONY: duplicate-check
duplicate-check:
	uv run -- python scripts/check_duplicate_code.py

.PHONY: duplicate-check-verbose
duplicate-check-verbose:
	uv run -- python scripts/check_duplicate_code.py --verbose --top-n 30

.PHONY: duplicate-check-json
duplicate-check-json:
	uv run -- python scripts/check_duplicate_code.py --json

.PHONY: duplicate-check-delta
duplicate-check-delta:
	uv run -- python scripts/check_duplicate_code.py --delta-vs "$(if $(BASE_REF),$(BASE_REF),origin/develop)"

.PHONY: makefile-inventory-check
makefile-inventory-check:
	uv run -- python make/check_makefile_inventory.py

.PHONY: mutation-test
mutation-test:
	@echo "Running mutation tests on critical modules..."
	uv run -- mutmut run
	@echo "--- Mutation test results ---"
	uv run -- mutmut results

.PHONY: oracle-mutation-test
oracle-mutation-test:
	@echo "Running mutation tests on the oracle decision modules against the oracle tests..."
	uv run -- python _project/scripts/oracle_mutation.py run
	@echo "--- Oracle mutation test results ---"
	uv run -- python _project/scripts/oracle_mutation.py report

.PHONY: guards-fix
guards-fix:
	@echo "== guards-fix: regenerating every mechanically-regenerable drift-guard artifact =="
	@$(MAKE) -s agent-write-preflight
	@echo "-- dependency inventory (audit-raw) --"
	@$(MAKE) -s audit-raw
	@echo "-- benchmark correctness-oracle coverage map --"
	@$(MAKE) -s oracle-coverage-map
	@echo "-- visualization parity fixtures --"
	@$(MAKE) -s parity-fixtures
	@echo "-- vendor-derived pricing tables (offline, from checked-in evidence) --"
	@$(MAKE) -s pricing-data
	@echo "-- skill-sync (fail-closed: a missing wrapper aborts instead of no-op-ing) --"
	@status=0; $(MAKE) -s skill-sync || status=$$?; \
	if [ "$$status" -ne 0 ]; then \
		echo "guards-fix: WARNING - the skill-sync step FAILED (see its output above); every other drift-guard artifact was still regenerated. Fix and re-run 'make skill-sync' separately."; \
	fi; \
	echo ""; \
	echo "No regen mode -- these are reviewed hand edits, guards-fix does not touch them:"; \
	echo "  - module-size guard: tests/system/test_module_size_thresholds.py (see its failure output for the ALLOWLIST entry to paste)"; \
	echo "  - DDL governance drift: benchbox/sql_compat/inventory.py --check-ddl-drift (register the transform, alias it, or exempt it)"; \
	echo "  - release curation list: scripts/check_release_curation.py (classify the path as main-only or release-cut curated)"; \
	echo ""; \
	echo "== guards-fix done. Review the diff below, then commit what you intend to keep. =="; \
	git status --porcelain; \
	exit "$$status"

.PHONY: ci-lint
ci-lint:
	@echo "Running CI lint checks..."
	@case " $(MAKEFLAGS) " in *" n "*|*" -n "*|*" --just-print "*) echo "Dry-run: ci-lint guards suppressed"; exit 0;; esac; \
	set +e; failed=""; \
	MARKER_MANIFEST=$$(mktemp "$${TMPDIR:-/tmp}/benchbox-marker-manifest.XXXXXX"); export MARKER_MANIFEST; \
	trap 'rm -f "$$MARKER_MANIFEST"' EXIT; \
	uv run ruff check .; \
	[ $$? -eq 0 ] || failed="$$failed ruff-check"; \
	uv run ruff format --check .; \
	[ $$? -eq 0 ] || failed="$$failed ruff-format"; \
	$(MAKE) typecheck; \
	[ $$? -eq 0 ] || failed="$$failed ty-check"; \
	$(MAKE) quality-governance-typecheck; \
	[ $$? -eq 0 ] || failed="$$failed quality-governance-typecheck"; \
	$(MAKE) uv-lock-revision-check; \
	[ $$? -eq 0 ] || failed="$$failed uv-lock-revision"; \
	$(MAKE) sqlglot-repro-retirement-check; \
	[ $$? -eq 0 ] || failed="$$failed sqlglot-repro-retirement"; \
	$(MAKE) lint-markers; \
	[ $$? -eq 0 ] || failed="$$failed lint-markers"; \
	$(MAKE) lint-imports; \
	[ $$? -eq 0 ] || failed="$$failed lint-imports"; \
	$(MAKE) windows-antipatterns-check; \
	[ $$? -eq 0 ] || failed="$$failed windows-antipatterns"; \
	$(MAKE) comment-policy-check; \
	[ $$? -eq 0 ] || failed="$$failed comment-policy"; \
	$(MAKE) artifact-hygiene; \
	[ $$? -eq 0 ] || failed="$$failed artifact-hygiene"; \
	$(MAKE) agent-instructions-check; \
	[ $$? -eq 0 ] || failed="$$failed agent-instructions"; \
	GATE=$$(uv run -- python _project/scripts/ci_lint_environment_gate.py agent-identity || true); \
	if [ "$$GATE" != "SKIP" ]; then \
		$(MAKE) agent-identity-check; \
		[ $$? -eq 0 ] || failed="$$failed agent-identity"; \
	fi; \
	$(MAKE) agent-commit-range-check; \
	[ $$? -eq 0 ] || failed="$$failed agent-commit-range"; \
	GATE=$$(uv run -- python _project/scripts/ci_lint_environment_gate.py skill-sync-check || true); \
	if [ "$$GATE" != "SKIP" ]; then \
		$(MAKE) skill-sync-check; \
		[ $$? -eq 0 ] || failed="$$failed skill-sync-check"; \
	fi; \
	uv run -- python _project/scripts/timing_policy_check.py --strict; \
	[ $$? -eq 0 ] || failed="$$failed timing-policy"; \
	uv run -- python _project/scripts/fast_lane_ceiling_check.py --strict --manifest-from-env; \
	[ $$? -eq 0 ] || failed="$$failed fast-lane-guards"; \
	uv run --project _project/scripts --no-sync -- python _project/scripts/uat_loc_table.py --check; \
	[ $$? -eq 0 ] || failed="$$failed uat-loc-table"; \
	$(MAKE) compat-inventory-check; \
	[ $$? -eq 0 ] || failed="$$failed compat-inventory-check"; \
	$(MAKE) platform-manifest-check; \
	[ $$? -eq 0 ] || failed="$$failed platform-manifest-check"; \
	$(MAKE) oracle-coverage-map-check; \
	[ $$? -eq 0 ] || failed="$$failed oracle-coverage-map-check"; \
	$(MAKE) pricing-data-check; \
	[ $$? -eq 0 ] || failed="$$failed pricing-data-check"; \
	$(MAKE) makefile-inventory-check; \
	[ $$? -eq 0 ] || failed="$$failed makefile-inventory-check"; \
	uv run -- python scripts/check_public_contract_drift.py; \
	[ $$? -eq 0 ] || failed="$$failed public-contract-drift"; \
	$(MAKE) doc-contracts-guard; \
	[ $$? -eq 0 ] || failed="$$failed doc-contracts"; \
	$(MAKE) audit-deps; \
	[ $$? -eq 0 ] || failed="$$failed audit-deps"; \
	$(MAKE) audit-raw-check; \
	[ $$? -eq 0 ] || failed="$$failed audit-raw-check"; \
	uv run -- python scripts/check_release_curation.py; \
	[ $$? -eq 0 ] || failed="$$failed release-curation"; \
	sh scripts/check_untracked_skill_mirrors.sh; \
	[ $$? -eq 0 ] || failed="$$failed skill-mirror-drift"; \
	sh scripts/check_untracked_handoffs.sh; \
	[ $$? -eq 0 ] || failed="$$failed handoff-tracking"; \
	$(MAKE) duplicate-check-delta; \
	[ $$? -eq 0 ] || failed="$$failed duplicate-delta"; \
	$(MAKE) complexity-check; \
	[ $$? -eq 0 ] || failed="$$failed complexity-check"; \
	uv run -- python scripts/check_patch_safety.py; \
	[ $$? -eq 0 ] || failed="$$failed patch-safety"; \
	$(MAKE) spellcheck; \
	[ $$? -eq 0 ] || failed="$$failed spellcheck"; \
	uv run -- python scripts/check_rerun_shard_retention.py; \
	[ $$? -eq 0 ] || failed="$$failed rerun-shard-retention"; \
	uv run -- python _project/scripts/check_project_references.py; \
	[ $$? -eq 0 ] || failed="$$failed project-references"; \
	if [ -n "$$failed" ]; then \
		echo ""; \
		echo "❌ FAILED guards:$$failed"; \
		exit 1; \
	fi; \
	echo "✅ CI lint checks passed"

.PHONY: ci-test
ci-test:
	@echo "Running CI test suite..."
	uv run -- python -m pytest tests -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short --timeout=120 -p pytest_cov --cov=benchbox --cov-report=xml:coverage.xml --cov-report=term-missing --cov-fail-under=70
	@echo "✅ CI test suite passed"

.PHONY: ci-docs
ci-docs:
	@echo "Running CI docs checks..."
	@$(MAKE) docs-validate docs-generate
	@echo "✅ CI docs checks passed"

.PHONY: security-audit
security-audit:
	@echo "Running security audit..."
	@if [ -n "$(PIP_AUDIT_IGNORE_VULNS)" ]; then \
		IGNORE_ARGS=$$(printf '%s' "$(PIP_AUDIT_IGNORE_VULNS)" | tr ',' '\n' | sed '/^$$/d;s/^/--ignore-vuln /' | tr '\n' ' '); \
		uvx pip-audit $$IGNORE_ARGS; \
	else \
		uvx pip-audit; \
	fi
	@echo "✅ Security audit passed"

SPELLCHECK_SKIP_GLOBS := *.pyc,*.json,*.lock,*.svg,*.min.js,*.min.css,*.tpl,*.dst,*.tbl,*.dat,*.pdf
SPELLCHECK_EXCLUDE_PATHS := \
	':(exclude,glob).*'             ':(exclude,glob).*/**' \
	':(exclude,glob)**/.*'          ':(exclude,glob)**/.*/**' \
	':(exclude,glob)**/_binaries/**' ':(exclude,glob)_binaries/**' \
	':(exclude,glob)**/_sources/**'  ':(exclude,glob)_sources/**' \
	':(exclude,glob)**/_project/**'  ':(exclude,glob)_project/**'

.PHONY: spellcheck
spellcheck:
	@echo "Running spellcheck..."
	git ls-files -z -- $(SPELLCHECK_EXCLUDE_PATHS) \
		| xargs -0 uvx codespell --ignore-words=.codespell-ignore.txt --skip="$(SPELLCHECK_SKIP_GLOBS)"
	@echo "✅ Spellcheck passed"

.PHONY: test-package
test-package:
	@echo "Building and testing package installation..."
	rm -rf dist/
	uv build
	uv run -- python -m scripts.verify_sqlglot_runtime_distribution dist/*.whl dist/*.tar.gz
	uvx twine check dist/*
	@echo "Testing package installation..."
	@wheel_count=$$(find "$$PWD/dist" -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d '[:space:]'); \
	if [ "$$wheel_count" != "1" ]; then \
		echo "Expected exactly one wheel, found $$wheel_count"; \
		find "$$PWD/dist" -maxdepth 1 -type f -print; \
		exit 1; \
	fi; \
	wheel=$$(find "$$PWD/dist" -maxdepth 1 -type f -name '*.whl' -print -quit); \
	tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	cd "$$tmpdir"; \
	uv run --isolated --no-project --with "$$wheel" -- python -c "import benchbox; print('Package installation successful')"; \
	uv run --isolated --no-project --with "$$wheel" -- benchbox --help > /dev/null
	@echo "✅ Package test passed"

.PHONY: test-integration-smoke
test-integration-smoke:
	@echo "Running integration smoke tests..."
	uv run -- python -m pytest tests/integration -m "platform_smoke or (integration and fast)" --tb=short
	@echo "✅ Integration smoke tests passed"

.PHONY: ci-local
ci-local:
	@echo "========================================"
	@echo "Running all CI checks locally..."
	@echo "========================================"
	@echo ""
	@echo "Step 1/5: Lint checks..."
	@$(MAKE) ci-lint
	@echo ""
	@echo "Step 2/5: Fast tests with coverage..."
	@$(MAKE) ci-test
	@echo ""
	@echo "Step 3/5: Integration smoke tests..."
	@$(MAKE) test-integration-smoke
	@echo ""
	@echo "Step 4/5: Documentation checks..."
	@$(MAKE) ci-docs
	@echo ""
	@echo "Step 5/5: Package build..."
	@$(MAKE) test-package
	@echo ""
	@echo "========================================"
	@echo "✅ All CI checks passed!"
	@echo "========================================"

CI_LINUX_MACHINE ?= benchbox-agent
CI_LINUX_CMD ?= make test-correctness-gate
.PHONY: ci-linux
ci-linux:
	@if [ "$$(uname -s)" != "Darwin" ] || [ "$$(uname -m)" != "arm64" ]; then \
		echo "ci-linux: skipped -- Apple silicon macOS only (host $$(uname -s)/$$(uname -m)); no-op."; \
		exit 0; \
	fi; \
	if [ "$$(sw_vers -productVersion | cut -d. -f1)" -lt 26 ]; then \
		echo "ci-linux: skipped -- needs macOS 26+ (host $$(sw_vers -productVersion)); no-op."; \
		exit 0; \
	fi; \
	if ! command -v container >/dev/null 2>&1; then \
		echo "ci-linux: Apple 'container' not installed -> 'brew install container'."; \
		exit 1; \
	fi; \
	if ! container machine list 2>/dev/null | awk 'NR>1{print $$1}' | grep -Fqx "$(CI_LINUX_MACHINE)"; then \
		echo "ci-linux: machine '$(CI_LINUX_MACHINE)' not found. One-time setup:"; \
		echo "  container system start"; \
		echo "  container build --arch arm64 --tag local/benchbox-agent docker/benchbox-agent"; \
		echo "  container machine create local/benchbox-agent --name $(CI_LINUX_MACHINE) --home-mount rw --cpus 4 --memory 8G"; \
		exit 1; \
	fi; \
	echo "==> ci-linux: '$(CI_LINUX_CMD)' inside container machine '$(CI_LINUX_MACHINE)'"; \
	container machine run -n $(CI_LINUX_MACHINE) -- bash -lc 'cd "$(CURDIR)" && $(CI_LINUX_CMD)'

.PHONY: typecheck
typecheck:
	uv run -- python -m scripts.typecheck

typecheck-uv: typecheck

.PHONY: validate-imports
validate-imports: lint-imports

.PHONY: catalog-schema-check
catalog-schema-check:
	uv run -- python -m benchbox.core.catalog_schema

.PHONY: dependency-check
dependency-check:
	uv run -- python -m benchbox.utils.dependency_validation $(ARGS)
.PHONY: format
format:
	uv run ruff format .

include $(BENCHBOX_MAKEFILE_ROOT)make/documentation.mk
include $(BENCHBOX_MAKEFILE_ROOT)make/site-inputs.mk


dist: clean
	uv build

run-test:
	uv run -- python $(TEST)

RELEASE_REQUIRED_CONTEXTS := validate-base release-required-result


.PHONY: .release-cut-tree-required
.release-cut-tree-required:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make release-cut VERSION=X.Y.Z" >&2; \
		exit 1; \
	fi; \
	if [ ! -d "$(BENCHBOX_MAKEFILE_ROOT)_project/decisions" ]; then \
		BRANCH=$$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true); \
		if [ "$$BRANCH" != "v$(VERSION)" ]; then \
			echo "Error: release-cut requires the BenchBox development tree unless resuming v$(VERSION) after curation." >&2; \
			exit 2; \
		fi; \
	fi

.PHONY: release-cut
release-cut: .release-cut-tree-required
	@test -n "$(VERSION)" || (echo "Usage: make release-cut VERSION=X.Y.Z" && exit 1)
	sh scripts/release_cut_start.sh "$(VERSION)"
	uv run -- python scripts/update_version.py --version $(VERSION) --update-pyproject
	uv lock
	@if [ -n "$(CHANGELOG_SECTION)" ]; then \
		uv run -- python scripts/generate_changelog_entry.py --version $(VERSION) --section-file "$(CHANGELOG_SECTION)" && \
		echo "==> CHANGELOG.md [$(VERSION)] section written from $(CHANGELOG_SECTION)"; \
	fi
	uv run -- python scripts/generate_changelog_entry.py --version $(VERSION) --require-prepared
	uv run -- python scripts/generate_changelog_entry.py --check-curation --version $(VERSION)
	git rm -rf --ignore-unmatch _project ':(exclude)_project/scripts/explorer_pipeline/**' ':(exclude)_project/scripts/explorer_publish.py' ':(exclude)_project/scripts/results_explorer_snapshot_invariants.py' .claude .codex .gemini tools
	git rm -f --ignore-unmatch .pre-commit-config.yaml .importlinter todo.config.yaml skill-sync.conf .gitattributes .coveragerc_core .dockerignore .env.example .mcp.json AGENTS.md CLAUDE.md GEMINI.md ANTIGRAVITY.md
	git rm -f --ignore-unmatch .github/workflows/seed-corpus.yml .github/workflows/sync-results-data-to-published.yml .github/workflows/validate-submission.yml .github/workflows/oracle-verdict.yml .github/workflows/oracle-gate.yml .github/workflows/omp-pin-bump.yml .github/oracle-reviewers.yml
	git rm -rf --ignore-unmatch tests/unit/scripts/explorer_pipeline tests/unit/explorer tests/unit/scripts/oracle_reviewers tests/integration/oracle tests/unit/workflows/test_oracle_verdict_workflow.py tests/unit/workflows/test_oracle_gate_workflow.py
	git rm -f --ignore-unmatch tests/uat/test_explorer_smoke.py tests/unit/release/test_ruleset_drift_review_coverage.py tests/unit/release/test_ruleset_review_enforcement.py tests/unit/scripts/test_blind_spot_tools.py tests/unit/scripts/test_build_joinorder_data.py tests/unit/scripts/test_check_complexity.py tests/unit/scripts/test_explorer_build_contract.py tests/unit/scripts/test_pr_review_followups.py tests/unit/scripts/test_reference_usage_audit.py tests/unit/scripts/test_scan_explorer_stale_theme.py tests/unit/scripts/test_scan_explorer_tokens.py tests/unit/scripts/test_shrink_rollup.py tests/unit/scripts/test_submission_workflow_waiver.py tests/unit/test_agent_write_preflight.py tests/unit/scripts/test_soundness_paths.py tests/unit/test_cross_surface_applicability.py tests/unit/test_oracle_coverage_map.py tests/unit/test_ruleset_drift.py tests/unit/test_self_binding_detector.py tests/unit/test_site_header_parity.py tests/unit/test_sync_results_workflow.py tests/unit/core/joinorder/test_canonical_queries.py tests/unit/core/test_platform_labels.py tests/unit/workflows/test_validate_submission_comment_security.py tests/unit/workflows/test_detect_orphaned_commits.py tests/unit/workflows/test_validate_submission_vendor_gate.py
	git rm -f --ignore-unmatch tests/integration/test_todo_db_standalone_compat_real.py tests/unit/core/equivalence/test_cross_surface_baseline_autodetect.py tests/unit/docs/test_architecture_decision_surfaces.py
	git rm -f --ignore-unmatch tests/unit/scripts/test_agent_instruction_audit.py tests/unit/scripts/test_audit_sha_check.py tests/unit/scripts/test_browser_gate_aggregate.py tests/unit/scripts/test_check_release_curation.py tests/unit/scripts/test_check_uv_lock_revision.py tests/unit/scripts/test_ci_lint_environment_boundary.py tests/unit/scripts/test_corpus_privacy_invariant.py tests/unit/scripts/test_dev_loop_pr_metrics.py tests/unit/scripts/test_green_unmerged_sweep.py tests/unit/scripts/test_guard_messages.py tests/unit/scripts/test_mirror_partial_validation_policy.py tests/unit/scripts/test_path_filter_decision.py tests/unit/scripts/test_results_explorer_corpus_migrate.py tests/unit/scripts/test_results_explorer_snapshot_invariants.py tests/unit/scripts/test_skill_sync_ci_policy.py tests/unit/scripts/test_soundness_drain_report.py tests/unit/scripts/test_soundness_merge_digest.py tests/unit/scripts/test_soundness_merge_digest_dispatch.py tests/unit/scripts/test_fast_lane_ceiling_check.py tests/unit/scripts/test_timing_policy_check.py tests/unit/scripts/test_todo_db_shadow.py tests/unit/scripts/test_todo_db_standalone_compat.py tests/unit/scripts/test_todo_schema_migration_check.py tests/unit/scripts/test_todo_verification_lint.py tests/unit/scripts/test_todo_wrapper.py
	git rm -f --ignore-unmatch tests/unit/test_auto_merge_hold_is_durable.py tests/unit/test_release_infrastructure.py tests/unit/workflows/test_auto_merge_partial_stack_race.py tests/unit/workflows/test_develop_post_merge_gaps.py tests/unit/workflows/test_merge_group_triggers.py tests/unit/workflows/test_published_results_base_ci.py tests/unit/workflows/test_results_explorer_browser_gate.py tests/unit/workflows/test_results_explorer_dependency_audit.py tests/unit/workflows/test_seed_corpus_pr_base.py tests/unit/workflows/test_validate_submission_changed_bundles.py tests/unit/workflows/test_validate_submission_fail_open.py
	git rm -f --ignore-unmatch tests/integration/worktree/test_pr_process.py tests/integration/worktree/test_worktree_audit.py tests/integration/worktree/test_worktree_finish_preview.py tests/unit/scripts/test_migrate_clickhouse_labels.py tests/unit/scripts/test_check_suites.py tests/unit/scripts/test_pr_arm.py tests/unit/scripts/test_pr_ready_make.py tests/unit/scripts/test_pr_landing.py tests/unit/scripts/test_pr_rerun.py tests/unit/scripts/test_results_explorer_cpu_attestation_backfill.py tests/unit/scripts/test_todo_state_contract_check.py tests/unit/scripts/test_worktree_audit.py tests/unit/workflows/test_publication_preview.py tests/unit/workflows/test_queue_certification.py tests/integration/worktree/test_batch_skill_delivery.py tests/unit/scripts/publication/test_plan_reconciliation.py tests/unit/workflows/test_validate_submission_override_approval.py tests/unit/scripts/test_sqlite_extract_repro.py tests/unit/workflows/test_validate_submission_workflow_guard_mirror.py tests/unit/workflows/test_validate_submission_trusted_checkout.py tests/unit/workflows/test_validate_submission_corpus_allowlist.py tests/unit/workflows/test_corpus_trust_boundary.py tests/unit/scripts/test_branch_prune_merged.py tests/unit/docs/test_publication_architecture.py
	git rm -f --ignore-unmatch tests/unit/scripts/test_heavy_tier_needed.py tests/unit/scripts/test_preflight_targets.py tests/unit/scripts/test_coverage_test_selection.py tests/unit/scripts/test_selection_pr_replays.py tests/unit/scripts/test_oracle_review_check.py tests/unit/scripts/test_oracle_review_check_omp_bump.py tests/unit/workflows/test_omp_pin_bump_workflow.py tests/unit/scripts/test_oracle_review_check_decisions.py tests/unit/scripts/test_oracle_mutation_stage.py tests/unit/scripts/test_oracle_gate.py tests/unit/scripts/test_check_project_references.py tests/unit/scripts/test_regenerate_correctness_gate_digests.py tests/unit/workflows/test_nightly_t3_workflow.py tests/unit/test_ledger_coverage.py tests/unit/workflows/test_stacked_pr_base_guard.py tests/unit/scripts/test_site_parity.py tests/unit/scripts/test_perf_smoke_baseline.py tests/unit/scripts/test_sqlglot_generator.py tests/unit/test_mocker_compose_parity_data_dir.py tests/integration/worktree/test_worktree_reap.py tests/unit/scripts/test_remote_closeout.py tests/unit/scripts/test_coverage_map_staleness.py
	git rm -f --ignore-unmatch tests/unit/scripts/test_inert_paths.py
	git rm -f --ignore-unmatch tests/unit/test_tpchavoc_trino_q1_scale_check.py tests/unit/test_tpchavoc_presto_variant_check.py
	git rm -rf --ignore-unmatch changelog.d
	@LEFTOVER=$$(git ls-files _project changelog.d ':(exclude)_project/scripts/explorer_pipeline/**' ':(exclude)_project/scripts/explorer_publish.py' ':(exclude)_project/scripts/results_explorer_snapshot_invariants.py' .claude .codex .gemini .pre-commit-config.yaml .importlinter todo.config.yaml skill-sync.conf tools .gitattributes .coveragerc_core .dockerignore .env.example .mcp.json AGENTS.md CLAUDE.md GEMINI.md ANTIGRAVITY.md .github/workflows/seed-corpus.yml .github/workflows/sync-results-data-to-published.yml .github/workflows/validate-submission.yml .github/workflows/oracle-verdict.yml .github/workflows/oracle-gate.yml .github/workflows/omp-pin-bump.yml .github/oracle-reviewers.yml tests/unit/workflows/test_oracle_verdict_workflow.py tests/unit/workflows/test_oracle_gate_workflow.py tests/unit/scripts/explorer_pipeline tests/unit/explorer tests/unit/scripts/oracle_reviewers tests/integration/oracle tests/uat/test_explorer_smoke.py tests/unit/release/test_ruleset_drift_review_coverage.py tests/unit/release/test_ruleset_review_enforcement.py tests/unit/scripts/test_blind_spot_tools.py tests/unit/scripts/test_build_joinorder_data.py tests/unit/scripts/test_check_complexity.py tests/unit/scripts/test_explorer_build_contract.py tests/unit/scripts/test_pr_review_followups.py tests/unit/scripts/test_reference_usage_audit.py tests/unit/scripts/test_scan_explorer_stale_theme.py tests/unit/scripts/test_scan_explorer_tokens.py tests/unit/scripts/test_shrink_rollup.py tests/unit/scripts/test_submission_workflow_waiver.py tests/unit/test_agent_write_preflight.py tests/unit/scripts/test_soundness_paths.py tests/unit/test_cross_surface_applicability.py tests/unit/test_oracle_coverage_map.py tests/unit/test_ruleset_drift.py tests/unit/test_self_binding_detector.py tests/unit/test_site_header_parity.py tests/unit/test_sync_results_workflow.py tests/unit/core/joinorder/test_canonical_queries.py tests/unit/core/test_platform_labels.py tests/unit/workflows/test_validate_submission_comment_security.py tests/unit/workflows/test_detect_orphaned_commits.py tests/unit/workflows/test_validate_submission_vendor_gate.py tests/integration/test_todo_db_standalone_compat_real.py tests/unit/core/equivalence/test_cross_surface_baseline_autodetect.py tests/unit/docs/test_architecture_decision_surfaces.py tests/unit/scripts/test_agent_instruction_audit.py tests/unit/scripts/test_audit_sha_check.py tests/unit/scripts/test_browser_gate_aggregate.py tests/unit/scripts/test_check_release_curation.py tests/unit/scripts/test_check_uv_lock_revision.py tests/unit/scripts/test_ci_lint_environment_boundary.py tests/unit/scripts/test_corpus_privacy_invariant.py tests/unit/scripts/test_dev_loop_pr_metrics.py tests/unit/scripts/test_green_unmerged_sweep.py tests/unit/scripts/test_guard_messages.py tests/unit/scripts/test_mirror_partial_validation_policy.py tests/unit/scripts/test_path_filter_decision.py tests/unit/scripts/test_results_explorer_corpus_migrate.py tests/unit/scripts/test_results_explorer_snapshot_invariants.py tests/unit/scripts/test_skill_sync_ci_policy.py tests/unit/scripts/test_soundness_drain_report.py tests/unit/scripts/test_soundness_merge_digest.py tests/unit/scripts/test_fast_lane_ceiling_check.py tests/unit/scripts/test_timing_policy_check.py tests/unit/scripts/test_todo_db_shadow.py tests/unit/scripts/test_todo_db_standalone_compat.py tests/unit/scripts/test_todo_schema_migration_check.py tests/unit/scripts/test_todo_verification_lint.py tests/unit/scripts/test_todo_wrapper.py tests/unit/test_auto_merge_hold_is_durable.py tests/unit/test_release_infrastructure.py tests/unit/workflows/test_auto_merge_partial_stack_race.py tests/unit/workflows/test_develop_post_merge_gaps.py tests/unit/workflows/test_merge_group_triggers.py tests/unit/workflows/test_published_results_base_ci.py tests/unit/workflows/test_results_explorer_browser_gate.py tests/unit/workflows/test_results_explorer_dependency_audit.py tests/unit/workflows/test_seed_corpus_pr_base.py tests/unit/workflows/test_validate_submission_changed_bundles.py tests/unit/workflows/test_validate_submission_fail_open.py tests/integration/worktree/test_pr_process.py tests/integration/worktree/test_worktree_audit.py tests/integration/worktree/test_worktree_finish_preview.py tests/unit/scripts/test_migrate_clickhouse_labels.py tests/unit/scripts/test_check_suites.py tests/unit/scripts/test_pr_arm.py tests/unit/scripts/test_pr_ready_make.py tests/unit/scripts/test_pr_landing.py tests/unit/scripts/test_pr_rerun.py tests/unit/scripts/test_results_explorer_cpu_attestation_backfill.py tests/unit/scripts/test_todo_state_contract_check.py tests/unit/scripts/test_worktree_audit.py tests/unit/workflows/test_publication_preview.py tests/unit/workflows/test_queue_certification.py tests/integration/worktree/test_batch_skill_delivery.py tests/unit/scripts/publication/test_plan_reconciliation.py tests/unit/workflows/test_validate_submission_override_approval.py tests/unit/scripts/test_sqlite_extract_repro.py tests/unit/workflows/test_validate_submission_workflow_guard_mirror.py tests/unit/workflows/test_validate_submission_trusted_checkout.py tests/unit/workflows/test_validate_submission_corpus_allowlist.py tests/unit/workflows/test_corpus_trust_boundary.py tests/unit/scripts/test_branch_prune_merged.py tests/unit/docs/test_publication_architecture.py tests/unit/scripts/test_heavy_tier_needed.py tests/unit/scripts/test_preflight_targets.py tests/unit/scripts/test_coverage_test_selection.py tests/unit/scripts/test_selection_pr_replays.py tests/unit/scripts/test_oracle_review_check.py tests/unit/scripts/test_oracle_review_check_omp_bump.py tests/unit/workflows/test_omp_pin_bump_workflow.py tests/unit/scripts/test_oracle_gate.py tests/unit/scripts/test_check_project_references.py tests/unit/scripts/test_regenerate_correctness_gate_digests.py tests/unit/workflows/test_nightly_t3_workflow.py tests/unit/test_ledger_coverage.py tests/unit/workflows/test_stacked_pr_base_guard.py tests/unit/scripts/test_site_parity.py tests/unit/scripts/test_perf_smoke_baseline.py tests/unit/scripts/test_sqlglot_generator.py tests/unit/test_mocker_compose_parity_data_dir.py tests/integration/worktree/test_worktree_reap.py tests/unit/scripts/test_remote_closeout.py tests/unit/scripts/test_coverage_map_staleness.py tests/unit/scripts/test_inert_paths.py tests/unit/test_tpchavoc_trino_q1_scale_check.py tests/unit/test_tpchavoc_presto_variant_check.py tests/unit/scripts/test_soundness_merge_digest_dispatch.py tests/unit/scripts/test_oracle_review_check_decisions.py tests/unit/scripts/test_oracle_mutation_stage.py); \
	if [ -n "$$LEFTOVER" ]; then \
		echo "ERROR: release curation incomplete; development-only paths still tracked:" >&2; \
		echo "$$LEFTOVER" | sed 's/^/  /' >&2; \
		exit 1; \
	fi
	@for required_dir in results-data _project/scripts/explorer_pipeline; do \
		test -d "$$required_dir" || { echo "ERROR: curated Explorer publication directory is missing: $$required_dir" >&2; exit 1; }; \
		git ls-files "$$required_dir" | grep -q . || { echo "ERROR: curated Explorer publication directory is not tracked: $$required_dir" >&2; exit 1; }; \
	done
	@for required_file in _project/scripts/explorer_publish.py _project/scripts/results_explorer_snapshot_invariants.py; do \
		test -f "$$required_file" && git ls-files --error-unmatch "$$required_file" >/dev/null || { echo "ERROR: curated Explorer publication helper is missing: $$required_file" >&2; exit 1; }; \
	done
	git add pyproject.toml uv.lock benchbox/__init__.py README.md docs/README.md benchbox/utils/VERSION_MANAGEMENT.md CHANGELOG.md
	git commit --no-verify -m "Release v$(VERSION)"
	@echo "==> Aligning release histories: merging origin/release into v$(VERSION) (strategy: ours)"
	@RELEASE_TREE=$$(git rev-parse 'HEAD^{tree}') && \
	git merge -s ours --no-verify --allow-unrelated-histories origin/release \
		-m "Merge release into v$(VERSION) (strategy: ours) to align release histories" && \
	MERGED_TREE=$$(git rev-parse 'HEAD^{tree}') && \
	if [ "$$RELEASE_TREE" != "$$MERGED_TREE" ]; then \
		echo "ERROR: alignment merge changed the curated release tree ($$RELEASE_TREE -> $$MERGED_TREE)" >&2; \
		exit 1; \
	fi
	PRE_COMMIT_ALLOW_NO_CONFIG=1 git push -u origin v$(VERSION)
	gh pr create --base release --head v$(VERSION) --title "Release v$(VERSION)" --body-file .github/RELEASE_PR_TEMPLATE.md
	@for br in $$(git ls-remote --heads origin 'v*' | awk '$$2 ~ /^refs\/heads\/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.+-]+)?$$/ {print $$2}' | sed 's|refs/heads/||' | grep -Fxv "v$(VERSION)"); do \
		echo "==> Deleting prior release branch on origin: $$br"; \
		PRE_COMMIT_ALLOW_NO_CONFIG=1 git push origin --delete "$$br" || true; \
	done
	@echo
	@echo "Release PR opened. Next steps:"
	@echo "  1. Review the PR diff; confirm CHANGELOG and curation are correct."
	@echo "  2. Wait for the required release contexts: $(RELEASE_REQUIRED_CONTEXTS)."
	@echo "  3. make release-finalize VERSION=$(VERSION)"

.PHONY: release-cut-abort
release-cut-abort:
	@test -n "$(VERSION)" || (echo "Usage: make release-cut-abort VERSION=X.Y.Z" && exit 1)
	sh scripts/release_cut_abort.sh "$(VERSION)"

.PHONY: release-finalize
release-finalize:
	@test -n "$(VERSION)" || (echo "Usage: make release-finalize VERSION=X.Y.Z" && exit 1)
	uv run -- python scripts/release_finalize.py --version "$(VERSION)" --required-contexts "$(RELEASE_REQUIRED_CONTEXTS)"
	@echo
	@echo "Check the matching release.yml run before reporting publication."
	@echo "Push-to-release jobs are post-merge signals; release publication relied on $(RELEASE_REQUIRED_CONTEXTS)."
	@echo "develop is intentionally unchanged — dev-only paths persist on develop."

.PHONY: release-prep
release-prep:
	@test -n "$(VERSION)" || (echo "Usage: make release-prep VERSION=X.Y.Z" && exit 1)
	uv run --frozen -- python scripts/release_flow.py prep --version "$(VERSION)"

.PHONY: release-check
release-check:
	@test -n "$(VERSION)" || (echo "Usage: make release-check VERSION=X.Y.Z" && exit 1)
	uv run --locked -- python scripts/release_flow.py check --version "$(VERSION)" $(if $(BASE_REF),--baseline-ref "$(BASE_REF)",) $(if $(SKIP_UPSTREAM_FRESHNESS),--skip-upstream-freshness,) $(if $(RELEASE_PRE_TAG),--pre-tag,)

.PHONY: changelog-entry
changelog-entry:
	uv run -- python scripts/changelog_fragments.py add

.PHONY: changelog-preview
changelog-preview:
	uv run -- python scripts/changelog_fragments.py preview

.PHONY: changelog-check
changelog-check:
	uv run -- python scripts/changelog_fragments.py check --require-empty-unreleased


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

.PHONY: agent-write-preflight
agent-write-preflight:
	@sh scripts/agent_write_preflight.sh

.PHONY: cloud-bootstrap
cloud-bootstrap:
	@sh scripts/cloud_bootstrap.sh

.PHONY: cloud-gateway
cloud-gateway:
	@sh scripts/cloud_gateway.sh "$(or $(ACTION),up)"

.PHONY: cloud-verify
cloud-verify:
	@sh scripts/cloud_verify.sh "$(or $(LEVEL),tests)"

.PHONY: pr-preflight
pr-preflight:
	@set -eu; \
	sh scripts/check_primary_clone_clean.sh; \
	$(MAKE) -s changelog-check; \
	git fetch origin develop --quiet; \
	PY_LIST=$$(mktemp); \
	TEST_LIST=$$(mktemp); \
	LINUX_LIST=$$(mktemp); \
	trap 'rm -f "$$PY_LIST" "$$TEST_LIST" "$$LINUX_LIST"' EXIT; \
	uv run -- python _project/scripts/preflight_targets.py python --base-ref origin/develop > "$$PY_LIST"; \
	uv run -- python _project/scripts/preflight_targets.py tests --base-ref origin/develop > "$$TEST_LIST"; \
	if [ -s "$$PY_LIST" ]; then \
		echo "==> ruff (changed Python files)"; \
		uv run -- ruff check --force-exclude $$(cat "$$PY_LIST"); \
		uv run -- ruff format --check --force-exclude $$(cat "$$PY_LIST"); \
	else \
		echo "No Python files changed; skipping ruff."; \
	fi; \
	if [ ! -s "$$TEST_LIST" ]; then \
		echo "No test files map to the changed paths; CI runs the fast lane."; \
		exit 0; \
	fi; \
	echo "==> tests for changed files"; \
	STATUS=0; \
	env $$(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/-u \1/p') uv run -- python -m pytest -q -n 0 --tb=short --ff $$(cat "$$TEST_LIST") || STATUS=$$?; \
	if [ "$$STATUS" -eq 5 ]; then echo "Mapped tests are all deselected by the default markers."; STATUS=0; fi; \
	if [ "$$STATUS" -eq 0 ]; then \
		uv run -- python _project/scripts/preflight_targets.py linux --base-ref origin/develop > "$$LINUX_LIST"; \
		if [ -s "$$LINUX_LIST" ]; then \
			echo "==> Linux run (change touches platform-branching code or platform skips)"; \
			bash scripts/linux_preflight_pytest.sh "$$LINUX_LIST" || STATUS=$$?; \
		fi; \
	fi; \
	exit "$$STATUS"

.PHONY: pr-preflight-fast-tests
pr-preflight-fast-tests:
	@set -eu; \
	if [ -n "$(PATH_DECISION)" ] || [ -n "$(PATH_LISTS)" ]; then \
		[ -n "$(PATH_DECISION)" ] && [ -f "$(PATH_DECISION)" ] || { echo "PATH_DECISION is required" >&2; exit 2; }; \
		[ -n "$(PATH_LISTS)" ] && [ -d "$(PATH_LISTS)" ] || { echo "PATH_LISTS is required" >&2; exit 2; }; \
		DECISION="$(PATH_DECISION)"; \
		LISTS="$(PATH_LISTS)"; \
	else \
		DECISION=$$(mktemp); \
		LISTS=$$(mktemp -d); \
		trap 'rm -f "$$DECISION"; rm -rf "$$LISTS"' EXIT; \
		git fetch origin develop --quiet; \
		uv run -- python scripts/path_filter_decision.py --base-ref origin/develop --json-out "$$DECISION" --lists-dir "$$LISTS" >/dev/null; \
	fi; \
	$(MAKE) -s pr-content-guard PATH_LISTS="$$LISTS"; \
	if uv run -- python scripts/path_filter_decision.py --json-in "$$DECISION" --check needs-code-ci >/dev/null; then \
		echo "==> fast tests (CI marker selection; coverage remains CI-only)"; \
		uv run -- python -m pytest -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short --timeout=120 -q; \
	else \
		echo "No code changes detected; skipping fast tests."; \
	fi

pr-landing-start:
	uv run -- python scripts/pr_landing.py --repo "$(or $(REPO),BenchBox-dev/BenchBox)" \
		--worktree . --branch "$(or $(BRANCH),$(shell git branch --show-current))" \
		$(if $(WORKTREE_ID),--worktree-id "$(WORKTREE_ID)",) start

pr-landing-withdraw:
	@[ -n "$(PR)" ] || { echo "PR is required" >&2; exit 2; }; \
	uv run -- python scripts/pr_landing.py --repo "$(or $(REPO),BenchBox-dev/BenchBox)" \
		--worktree . --branch "$(or $(BRANCH),$(shell git branch --show-current))" \
		$(if $(WORKTREE_ID),--worktree-id "$(WORKTREE_ID)",) \
		$(if $(PR_NODE_ID),--pr-node-id "$(PR_NODE_ID)",) withdraw --pr "$(PR)" \
		$(if $(HEAD),--expected-head "$(HEAD)",)

pr-landing-ready:
	@set -eu; \
	[ -n "$(PR)" ] || { echo "PR is required" >&2; exit 2; }; \
	[ -n "$(HEAD)" ] || { echo "HEAD is required" >&2; exit 2; }; \
	[ -n "$(EVIDENCE)" ] || { echo "EVIDENCE is required" >&2; exit 2; }; \
	$(if $(ARM),uv run -- python scripts/trunk_revert.py arm-gate --pr "$(PR)" --repo "$(or $(REPO),BenchBox-dev/BenchBox)"; ,)\
	uv run -- python scripts/pr_landing.py --repo "$(or $(REPO),BenchBox-dev/BenchBox)" \
		--worktree . --branch "$(or $(BRANCH),$(shell git branch --show-current))" \
		$(if $(WORKTREE_ID),--worktree-id "$(WORKTREE_ID)",) \
		$(if $(PR_NODE_ID),--pr-node-id "$(PR_NODE_ID)",) ready --pr "$(PR)" \
		--expected-head "$(HEAD)" --evidence-json "$(EVIDENCE)" \
		$(if $(ARM),--arm,) $(if $(BATCH),--require-batch,)

pr-followup-record:
	@[ -n "$(KEY)" ] || { echo "KEY is required" >&2; exit 2; }; \
	@[ -n "$(STATE)" ] || { echo "STATE is required" >&2; exit 2; }; \
	uv run -- python scripts/pr_landing.py --worktree . followup-record --key "$(KEY)" --state-json "$(STATE)"

pr-followup-resume:
	@[ -n "$(KEY)" ] || { echo "KEY is required" >&2; exit 2; }; \
	uv run -- python scripts/pr_landing.py --worktree . followup-resume --key "$(KEY)"

.PHONY: pr-content-guard
pr-content-guard:
	@set -eu; \
	[ -n "$(PATH_LISTS)" ] || { echo "PATH_LISTS is required"; exit 2; }; \
	EXISTING=$$(mktemp); \
	trap 'rm -f "$$EXISTING"' EXIT; \
	$(MAKE) artifact-hygiene; \
	if [ -s "$(PATH_LISTS)/yaml.txt" ]; then \
		: > "$$EXISTING"; \
		while IFS= read -r path; do \
			if [ -e "$$path" ]; then printf '%s\n' "$$path" >> "$$EXISTING"; else echo "Skipping deleted YAML path: $$path"; fi; \
		done < "$(PATH_LISTS)/yaml.txt"; \
		if [ -s "$$EXISTING" ]; then uv run -- pre-commit run check-yaml --files $$(cat "$$EXISTING"); else echo "No existing YAML content paths to lint."; fi; \
	else \
		echo "No YAML content paths changed."; \
	fi; \
	if [ -s "$(PATH_LISTS)/markdown.txt" ]; then \
		: > "$$EXISTING"; \
		while IFS= read -r path; do \
			if [ -e "$$path" ]; then printf '%s\n' "$$path" >> "$$EXISTING"; else echo "Skipping deleted markdown path: $$path"; fi; \
		done < "$(PATH_LISTS)/markdown.txt"; \
		if [ -s "$$EXISTING" ]; then uv run -- pre-commit run markdownlint --files $$(cat "$$EXISTING"); else echo "No existing markdown content paths to lint."; fi; \
	else \
		echo "No markdown content paths changed."; \
	fi; \
	if [ -s "$(PATH_LISTS)/docs.txt" ]; then \
		$(MAKE) docs-validate; \
	else \
		echo "No docs paths changed."; \
	fi

.PHONY: pr-open
pr-open: override TITLE := $(value TITLE)
pr-open: export PR_OPEN_TITLE := $(TITLE)
pr-open: override PR_BODY_FILE := $(value PR_BODY_FILE)
pr-open: export PR_OPEN_BODY_FILE := $(PR_BODY_FILE)
pr-open:
	@set -eu; \
	$(MAKE) -s agent-write-preflight; \
	CURRENT=$$(git branch --show-current); \
	case "$$CURRENT" in \
		develop|main|release) echo "Refusing to open PR from $$CURRENT — switch to a feature branch."; exit 1 ;; \
	esac; \
	if [ -n "$$PR_OPEN_BODY_FILE" ] && [ ! -f "$$PR_OPEN_BODY_FILE" ]; then \
		echo "PR_BODY_FILE does not exist: $$PR_OPEN_BODY_FILE" >&2; \
		exit 1; \
	fi; \
	REPOSITORY="$(or $(REPO),BenchBox-dev/BenchBox)"; \
	case "$$REPOSITORY" in \
		*/*/*|/*|*/|"") echo "REPO must be a GitHub owner/name identity" >&2; exit 2 ;; \
		*/*) ;; \
		*) echo "REPO must be a GitHub owner/name identity" >&2; exit 2 ;; \
	esac; \
	ORIGIN_URL=$$(git remote get-url --push origin) || { echo "Could not resolve the origin remote" >&2; exit 1; }; \
	case "$$ORIGIN_URL" in \
		git@github.com:*) ORIGIN_REPOSITORY="$${ORIGIN_URL#git@github.com:}" ;; \
		ssh://git@github.com/*) ORIGIN_REPOSITORY="$${ORIGIN_URL#ssh://git@github.com/}" ;; \
		https://github.com/*) ORIGIN_REPOSITORY="$${ORIGIN_URL#https://github.com/}" ;; \
		*) echo "origin remote is not a supported GitHub URL or SSH form" >&2; exit 1 ;; \
	esac; \
	ORIGIN_REPOSITORY="$${ORIGIN_REPOSITORY%.git}"; \
	ORIGIN_REPOSITORY="$${ORIGIN_REPOSITORY%/}"; \
	ORIGIN_OWNER="$${ORIGIN_REPOSITORY%%/*}"; \
	case "$$ORIGIN_REPOSITORY" in \
		*/*/*|/*|*/|"") echo "origin remote is not a GitHub owner/name identity" >&2; exit 1 ;; \
		*/*) ;; \
		*) echo "origin remote is not a GitHub owner/name identity" >&2; exit 1 ;; \
	esac; \
	HEAD_SPEC="$$CURRENT"; \
	ORIGIN_REPOSITORY_KEY=$$(printf '%s' "$$ORIGIN_REPOSITORY" | tr 'A-Z' 'a-z'); \
	TARGET_REPOSITORY_KEY=$$(printf '%s' "$$REPOSITORY" | tr 'A-Z' 'a-z'); \
	if [ "$$ORIGIN_REPOSITORY_KEY" != "$$TARGET_REPOSITORY_KEY" ]; then \
		HEAD_SPEC="$$ORIGIN_OWNER:$$CURRENT"; \
	fi; \
	git fetch origin develop --quiet; \
	if ! git merge-base --is-ancestor origin/develop HEAD; then \
		if ! git merge-tree --write-tree origin/develop HEAD >/dev/null 2>&1; then \
			echo "Refusing to open PR: HEAD conflicts with origin/develop. Resolve the conflict first; no refresh merge is attempted." >&2; \
			exit 1; \
		fi; \
	fi; \
	CL_TITLE="$$PR_OPEN_TITLE"; \
	if [ -z "$$CL_TITLE" ]; then CL_TITLE=$$(git log --reverse --format=%s origin/develop..HEAD | sed -n 1p); fi; \
	CL_DIR=$$(mktemp -d); \
	git diff --name-only --diff-filter=ACDMRT origin/develop...HEAD > "$$CL_DIR/changed.txt"; \
	uv run -- python scripts/release_flow.py select --base-sha "$$(git rev-parse origin/develop)" --github-output "$$CL_DIR/select.txt" >/dev/null || { rm -rf "$$CL_DIR"; exit 1; }; \
	CL_IDENTITY=; \
	if grep -qx 'release-identity-changed=true' "$$CL_DIR/select.txt"; then CL_IDENTITY=--release-identity-changed; fi; \
	uv run -- python scripts/changelog_fragments.py required --title "$$CL_TITLE" --changed-files-from "$$CL_DIR/changed.txt" $$CL_IDENTITY || { rm -rf "$$CL_DIR"; echo "Refusing to open PR: add a changelog fragment (make changelog-entry) or fix the title." >&2; exit 1; }; \
	rm -rf "$$CL_DIR"; \
	TRUNK_MODE=$$(uv run -- python scripts/trunk_revert.py gate --branch "$$CURRENT" --repo "$$REPOSITORY"); \
	DRAFT_FLAG=; \
	if [ "$$TRUNK_MODE" = "draft" ]; then DRAFT_FLAG=--draft; fi; \
	REVIEWED=$$(git rev-parse HEAD); \
	uv run -- python -m _project.scripts.oracle_reviewers.cli local-review --policy .github/oracle-reviewers.yml --base-ref origin/develop --head "$$REVIEWED" --body-file "$$PR_OPEN_BODY_FILE" || { echo "Refusing to open PR: the local oracle review did not clear this head. Nothing was pushed." >&2; exit 1; }; \
	[ "$$(git rev-parse HEAD)" = "$$REVIEWED" ] || { echo "Refusing to open PR: HEAD moved during the local oracle review, so the pushed head would not be the reviewed one. Nothing was pushed." >&2; exit 1; }; \
	$(MAKE) -s pr-conflict-scan BRANCH="$$CURRENT" REPO="$$REPOSITORY" || true; \
	git push -u origin "$$CURRENT" || { echo "Push failed for $$CURRENT — aborting before opening a PR (remote branch may be stale)." >&2; exit 1; }; \
	PUBLISHED=$$(uv run -- python scripts/pr_open_rest.py --repo "$$REPOSITORY" --head-owner "$$ORIGIN_OWNER" \
		--branch "$$CURRENT" --head-spec "$$HEAD_SPEC" --title "$$PR_OPEN_TITLE" --body-file "$$PR_OPEN_BODY_FILE" $$DRAFT_FLAG); \
	PR_STATUS="$${PUBLISHED%% *}"; \
	PR_NUMBER="$${PUBLISHED#* }"; \
	URL="$${PR_NUMBER#* }"; \
	PR_NUMBER="$${PR_NUMBER%% *}"; \
	REUSED_PR=0; \
	if [ "$$PR_STATUS" = "reused" ]; then \
		REUSED_PR=1; \
		echo "Reusing existing PR: $$URL"; \
	fi; \
	echo "$$URL" && \
	if [ "$$TRUNK_MODE" = "draft" ] && [ "$$REUSED_PR" != "1" ]; then \
		echo "Opened as a draft because develop is red: mark it ready and arm it only after the owner authorizes this head (see docs/agent/merge-authority.md), or after develop is green."; \
	elif [ "$(READY)" != "1" ]; then \
		echo "Next: make pr-arm once the review fixes are pushed; auto-merge merges it when the required checks pass."; \
	elif [ -n "$(EVIDENCE)$(BATCH)" ]; then \
		if [ "$$REUSED_PR" != "1" ]; then \
			echo "READY=1 with EVIDENCE or BATCH applies to a reused PR, so this new PR is not armed."; \
			echo "Arm it after review with make pr-arm, or with the evidence transaction: make pr-ready REPO=\"$$REPOSITORY\" URL=\"$$URL\" HEAD=\"$$(git rev-parse HEAD)\" EVIDENCE=\"<readiness-evidence.json>\""; \
		else \
			$(MAKE) -s pr-ready REPO="$$REPOSITORY" URL="$$URL" HEAD="$$(git rev-parse HEAD)" EVIDENCE="$(EVIDENCE)" BATCH="$(BATCH)"; \
		fi; \
	else \
		$(MAKE) -s pr-arm PR="$$PR_NUMBER" REPO="$$REPOSITORY" HEAD="$$(git rev-parse HEAD)"; \
	fi

.PHONY: pr-arm-auto-merge
pr-arm-auto-merge:
	@set -eu; \
	[ -n "$(EVIDENCE)" ] || { echo "EVIDENCE is required; use pr-landing-ready with exact readiness evidence" >&2; exit 2; }; \
	REPOSITORY="$(or $(REPO),BenchBox-dev/BenchBox)"; \
	CURRENT=$$(git branch --show-current); \
	PR_NUMBER="$(PR)"; \
	if [ -n "$(URL)" ]; then \
		if [ -n "$$PR_NUMBER" ]; then echo "PR and URL are mutually exclusive" >&2; exit 2; fi; \
		PR_NUMBER=$$(gh pr view --repo "$$REPOSITORY" "$(URL)" --json number --jq '.number') || { echo "Could not resolve URL to a PR in $$REPOSITORY" >&2; exit 1; }; \
	fi; \
	case "$$PR_NUMBER" in *[!0-9]*) echo "PR must resolve to a positive number" >&2; exit 2 ;; esac; \
	if [ -n "$$PR_NUMBER" ] && [ "$$PR_NUMBER" -le 0 ]; then echo "PR must resolve to a positive number" >&2; exit 2; fi; \
	[ -n "$$PR_NUMBER" ] || { echo "PR or URL is required" >&2; exit 2; }; \
	EXPECTED_HEAD="$(HEAD)"; \
	if [ -z "$$EXPECTED_HEAD" ]; then EXPECTED_HEAD=$$(git rev-parse HEAD); fi; \
	$(MAKE) -s pr-landing-ready REPO="$$REPOSITORY" PR="$$PR_NUMBER" HEAD="$$EXPECTED_HEAD" \
		EVIDENCE="$(EVIDENCE)" BATCH="$(BATCH)" ARM=1

PR_ARM_PR_SET := $(if $(filter undefined,$(origin PR)),,1)
PR_ARM_HEAD_SET := $(if $(filter undefined,$(origin HEAD)),,1)
PR_ARM_REPO_SET := $(if $(filter undefined,$(origin REPO)),,1)
.PHONY: pr-rerun
pr-rerun: override PR := $(value PR)
pr-rerun: override REPO := $(value REPO)
pr-rerun: export PR_RERUN_PR := $(PR)
pr-rerun: export PR_RERUN_REPO := $(or $(REPO),BenchBox-dev/BenchBox)
pr-rerun:
	@uv run -- python scripts/pr_rerun.py

.PHONY: pr-arm
pr-arm: override PR := $(value PR)
pr-arm: override HEAD := $(value HEAD)
pr-arm: override REPO := $(value REPO)
pr-arm: export PR_ARM_PR := $(PR)
pr-arm: export PR_ARM_PR_SET := $(PR_ARM_PR_SET)
pr-arm: export PR_ARM_HEAD := $(HEAD)
pr-arm: export PR_ARM_HEAD_SET := $(PR_ARM_HEAD_SET)
pr-arm: export PR_ARM_REPO := $(REPO)
pr-arm: export PR_ARM_REPO_SET := $(PR_ARM_REPO_SET)
pr-arm:
	@set -eu; \
	if [ -n "$${PR_ARM_PR:-}" ]; then \
		uv run -- python scripts/trunk_revert.py arm-gate --pr "$$PR_ARM_PR" --repo "$${PR_ARM_REPO:-BenchBox-dev/BenchBox}"; \
	else \
		uv run -- python scripts/trunk_revert.py arm-gate --branch "$$(git branch --show-current)" --repo "$${PR_ARM_REPO:-BenchBox-dev/BenchBox}"; \
	fi; \
	uv run -- python scripts/pr_arm.py

REMOTE_CLOSEOUT_TARGETS := pr-arm-remote pr-landing-remote-bind pr-landing-remote-record \
	pr-landing-remote-manifest pr-landing-remote-review pr-landing-remote-withdraw pr-landing-remote-ready
REMOTE_CLOSEOUT_VARIABLES := PR HEAD REPO BRANCH ITEM RECORD BUILDER_HEAD OWNER_EXPLAINED MODEL REVIEWER DRY_RUN RESUME
.PHONY: $(REMOTE_CLOSEOUT_TARGETS)
$(foreach name,$(REMOTE_CLOSEOUT_VARIABLES),$(eval $(REMOTE_CLOSEOUT_TARGETS): override $(name) := $$(value $(name))))
$(foreach name,$(REMOTE_CLOSEOUT_VARIABLES),$(eval $(REMOTE_CLOSEOUT_TARGETS): export REMOTE_CLOSEOUT_$(name) := $$($(name))))
$(REMOTE_CLOSEOUT_TARGETS): export PYTHONDONTWRITEBYTECODE := 1
REMOTE_ARM_GATE = if [ -n "$${REMOTE_CLOSEOUT_PR:-}" ]; then \
		uv run --no-project -- python scripts/trunk_revert.py arm-gate --pr "$$REMOTE_CLOSEOUT_PR" --repo "$${REMOTE_CLOSEOUT_REPO:-BenchBox-dev/BenchBox}"; \
	else \
		uv run --no-project -- python scripts/trunk_revert.py arm-gate --branch "$${REMOTE_CLOSEOUT_BRANCH:-$$(git branch --show-current)}" --repo "$${REMOTE_CLOSEOUT_REPO:-BenchBox-dev/BenchBox}"; \
	fi
pr-arm-remote:
	@set -eu; $(REMOTE_ARM_GATE); \
	uv run --no-project -- python scripts/remote_closeout.py arm
pr-landing-remote-bind:
	@uv run --no-project -- python scripts/remote_closeout.py bind
pr-landing-remote-record:
	@uv run --no-project -- python scripts/remote_closeout.py record
pr-landing-remote-manifest:
	@uv run --no-project -- python scripts/remote_closeout.py manifest
pr-landing-remote-review:
	@uv run --no-project -- python scripts/remote_review.py
pr-landing-remote-withdraw:
	@uv run --no-project -- python scripts/remote_closeout.py withdraw
pr-landing-remote-ready:
	@set -eu; $(REMOTE_ARM_GATE); \
	uv run --no-project -- python scripts/remote_closeout.py ready

.PHONY: trunk-revert
trunk-revert:
	@$(MAKE) -s agent-write-preflight
	@case "$(PR)" in ""|*[!0-9]*) echo "PR=<merged PR number> is required" >&2; exit 2 ;; esac
	@uv run -- python scripts/trunk_revert.py revert --pr "$(PR)" --repo "$(or $(REPO),BenchBox-dev/BenchBox)"

.PHONY: trunk-status
trunk-status:
	@uv run -- python scripts/trunk_revert.py status --repo "$(or $(REPO),BenchBox-dev/BenchBox)"

.PHONY: pr-ready
pr-ready:
	@[ -n "$(PR)" ] || [ -n "$(URL)" ] || { echo "PR or URL is required" >&2; exit 2; }
	@[ -n "$(HEAD)" ] || { echo "HEAD is required" >&2; exit 2; }
	@if [ -n "$(EVIDENCE)$(BATCH)" ]; then \
		$(MAKE) -s pr-arm-auto-merge REPO="$(or $(REPO),BenchBox-dev/BenchBox)" PR="$(PR)" URL="$(URL)" HEAD="$(HEAD)" EVIDENCE="$(EVIDENCE)" BATCH="$(BATCH)"; \
	elif [ -n "$(URL)" ]; then \
		[ -z "$(PR)" ] || { echo "PR and URL are mutually exclusive" >&2; exit 2; }; \
		REPOSITORY="$(or $(REPO),BenchBox-dev/BenchBox)"; \
		PR_INFO=$$(gh pr view --repo "$$REPOSITORY" "$(URL)" --json number,url --jq '"\(.number) \(.url)"') || { echo "Could not resolve URL to a PR in $$REPOSITORY" >&2; exit 1; }; \
		PR_NUMBER="$${PR_INFO%% *}"; PR_URL=$$(printf '%s' "$${PR_INFO#* }" | tr 'A-Z' 'a-z'); \
		WANT=$$(printf '%s' "$$REPOSITORY" | tr 'A-Z' 'a-z'); \
		case "$$PR_URL" in "https://github.com/$$WANT/pull/"*) ;; *) echo "URL names a PR outside $$REPOSITORY; refusing to arm it" >&2; exit 2 ;; esac; \
		$(MAKE) -s pr-arm PR="$$PR_NUMBER" REPO="$$REPOSITORY" HEAD="$(HEAD)"; \
	else \
		$(MAKE) -s pr-arm PR="$(PR)" REPO="$(or $(REPO),BenchBox-dev/BenchBox)" HEAD="$(HEAD)"; \
	fi

.PHONY: shrink-rollup
shrink-rollup:
	@git fetch origin develop --quiet
	@uv run --project _project/scripts -- python _project/scripts/shrink_rollup.py

.PHONY: pr-fanout
pr-fanout:
	@MAIN_CLONE=$$(dirname "$$(realpath "$$(git rev-parse --git-common-dir)")"); \
	TMP=$$(mktemp); \
	LOGDIR=$$(mktemp -d); \
	trap 'rm -f "$$TMP"; rm -rf "$$LOGDIR"' EXIT; \
	git worktree list --porcelain | sed -n 's/^worktree //p' | while IFS= read -r wt; do \
		[ "$$(realpath "$$wt")" = "$$MAIN_CLONE" ] && { echo "(skip $$wt: main clone)"; continue; }; \
		BR=$$(git -C "$$wt" branch --show-current 2>/dev/null); \
		case "$$BR" in develop|main|release|"") echo "(skip $$wt: branch=$$BR)"; continue ;; esac; \
		IDX=$$(($${IDX:-0} + 1)); \
		printf '%06d|%s\0' "$$IDX" "$$wt" >> "$$TMP"; \
	done; \
	if [ ! -s "$$TMP" ]; then exit 0; fi; \
	xargs -0 -n 1 -P "$(PR_FANOUT_JOBS)" sh -c 'logdir="$$1"; record="$$2"; idx="$${record%%|*}"; wt="$${record#*|}"; br=$$(git -C "$$wt" branch --show-current 2>/dev/null); { echo "==> $$wt [$$br]"; ( cd "$$wt" && $(MAKE) -s pr-open ) || { echo "(failed: $$wt)"; exit 1; }; } > "$$logdir/$$idx.log" 2>&1' sh "$$LOGDIR" < "$$TMP"; \
	STATUS=$$?; \
	for log in "$$LOGDIR"/*.log; do [ -e "$$log" ] && cat "$$log"; done; \
	exit $$STATUS

.PHONY: pr-refresh
pr-refresh:
	@CURRENT=$$(git branch --show-current); \
	case "$$CURRENT" in \
		develop|main|release|"") echo "Refusing to refresh $$CURRENT — switch to a feature branch worktree."; exit 1 ;; \
	esac; \
	git fetch origin develop --quiet && \
	git merge --no-edit origin/develop && \
	$(MAKE) -s pr-open

.PHONY: pr-conflict-scan
pr-conflict-scan:
	@CURRENT="$(BRANCH)"; \
	[ -n "$$CURRENT" ] || CURRENT=$$(git branch --show-current); \
	gh api --method GET --paginate "repos/$(or $(REPO),BenchBox-dev/BenchBox)/pulls?base=develop&state=open&per_page=100" \
		--jq '.[] | "\(.number) \(.head.ref)"' 2>/dev/null | \
	while read num branch; do \
		[ "$$branch" = "$$CURRENT" ] && continue; \
		git fetch origin "$$branch" --quiet 2>/dev/null || continue; \
		git rev-parse --verify --quiet "origin/$$branch^{commit}" >/dev/null || continue; \
		out=$$(git merge-tree --write-tree --name-only HEAD "origin/$$branch" 2>/dev/null); \
		[ $$? -eq 1 ] || continue; \
		files=$$(printf '%s\n' "$$out" | awk 'NR>1 && NF==0{exit} NR>1{printf "%s%s", sep, $$0; sep=", "}'); \
		echo "  ⚠ textual conflict with PR #$$num ($$branch) in $$files — coordinate before landing"; \
	done; true

.PHONY: pr-status
pr-status:
	@if [ "$(ALL_OPEN)" = "1" ] || [ "$(ALL_OPEN)" = "true" ] || [ "$(ALL_OPEN)" = "yes" ]; then \
		echo "All open develop PRs (bounded to $(PR_STATUS_ALL_OPEN_LIMIT)):"; \
		LIMIT="$(PR_STATUS_ALL_OPEN_LIMIT)"; \
	else \
		LIMIT="$(PR_STATUS_LIMIT)"; \
	fi; \
	gh pr list --base develop --state open --limit "$$LIMIT" --json number,title,headRefName,statusCheckRollup,autoMergeRequest \
		--template '{{range .}}#{{.number}} {{.title}} ({{.headRefName}}){{"\n"}}  auto-merge: {{if .autoMergeRequest}}ON{{else}}OFF{{end}}{{"\n"}}  checks: {{range .statusCheckRollup}}{{.name}}={{.conclusion}} {{end}}{{"\n\n"}}{{end}}'

.PHONY: pr-review-followups-list
pr-review-followups-list:
	@uv run --project _project/scripts -- python _project/scripts/pr_review_followups.py list \
		--base "$(PR_REVIEW_BASE)" \
		--limit-prs "$(PR_REVIEW_PR_LIMIT)" \
		--max-comments "$(PR_REVIEW_MAX_COMMENTS)" \
		$(if $(filter 1 true yes,$(PR_REVIEW_INCLUDE_RESOLVED)),--include-resolved) \
		$(if $(filter 1 true yes,$(PR_REVIEW_INCLUDE_POST_MERGE)),--include-post-merge) \
		$(if $(filter 1 true yes,$(PR_REVIEW_FAIL_ON_PENDING)),--fail-on-pending) \
		$(if $(PR_REVIEW_REPO),--repo "$(PR_REVIEW_REPO)") \
		$(if $(PR_REVIEW_SINCE),--since "$(PR_REVIEW_SINCE)") \
		$(if $(PR_REVIEW_UNTIL),--until "$(PR_REVIEW_UNTIL)")

.PHONY: pr-review-followups
pr-review-followups:
	@uv run --project _project/scripts -- python _project/scripts/pr_review_followups.py run \
		--base "$(PR_REVIEW_BASE)" \
		--limit-prs "$(PR_REVIEW_PR_LIMIT)" \
		--max-comments "$(PR_REVIEW_MAX_COMMENTS)" \
		--executor-sandbox "$(PR_REVIEW_EXECUTOR_SANDBOX)" \
		--executor-approval "$(PR_REVIEW_EXECUTOR_APPROVAL)" \
		$(if $(PR_REVIEW_REPO),--repo "$(PR_REVIEW_REPO)") \
		$(if $(PR_REVIEW_SINCE),--since "$(PR_REVIEW_SINCE)") \
		$(if $(PR_REVIEW_UNTIL),--until "$(PR_REVIEW_UNTIL)") \
		$(if $(PR_REVIEW_EXECUTOR_MODEL),--executor-model "$(PR_REVIEW_EXECUTOR_MODEL)") \
		$(if $(filter 1 true yes,$(PR_REVIEW_INCLUDE_POST_MERGE)),--include-post-merge) \
		$(if $(filter 0 false no,$(PR_REVIEW_REPLY)),--no-reply) \
		$(if $(filter 0 false no,$(PR_REVIEW_SUBMIT)),--no-submit) \
		$(if $(filter 1 true yes,$(PR_REVIEW_RESUME)),--resume)

.PHONY: dev-loop-metrics
dev-loop-metrics:
	@set -e; \
	TMP=$$(mktemp -d); \
	trap 'rm -rf "$$TMP"' EXIT; \
	SINCE=$$(uv run -- python -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) - timedelta(days=int("$(DEV_LOOP_METRICS_DAYS)"))).date().isoformat())'); \
	echo "Fetching develop-post-merge metrics since $$SINCE (limit $(DEV_LOOP_METRICS_LIMIT))..."; \
	RUN_IDS=$$(gh run list --workflow develop-post-merge.yml --branch develop --event push --created ">=$$SINCE" --limit "$(DEV_LOOP_METRICS_LIMIT)" --json databaseId --jq '.[].databaseId' 2>/dev/null || true); \
	for id in $$RUN_IDS; do \
		gh run download "$$id" -n metrics -D "$$TMP/$$id" >/dev/null 2>&1 || true; \
	done; \
	FILES=$$(find "$$TMP" -type f -name '*.json' | sort); \
	if [ -z "$$FILES" ]; then \
		echo "Metrics artifacts: 0"; \
		echo "PR-to-merged P50: n/a"; \
		echo "PR-to-merged P95: n/a"; \
		echo "Post-merge red rate: n/a (0/0)"; \
		echo "Conflict rate: n/a (0/0)"; \
		echo "runner minutes total: 0"; \
		exit 0; \
	fi; \
	jq -r -s ' \
		def nums($$k): [.[].[$$k] | select(type == "number")]; \
		def ceil_num: if . == floor then . else floor + 1 end; \
		def pct($$a; $$p): \
			if ($$a | length) == 0 then null \
			else ($$a | sort) as $$s | (((($$s | length) * $$p / 100) | ceil_num) - 1) as $$idx | $$s[$$idx] end; \
		def fmt: if . == null then "n/a" else tostring end; \
		def rate($$n; $$d): if $$d == 0 then "n/a" else (((100 * $$n / $$d) | tostring) + "%") end; \
		. as $$rows | \
		($$rows | length) as $$total | \
		(nums("pr_open_to_merged_seconds")) as $$merge_seconds | \
		([$$rows[] | select(.post_merge_red == true)] | length) as $$red | \
		([$$rows[] | select(.conflict_on_merge == true)] | length) as $$conflicts | \
		((nums("ci_runner_minutes") | add) // 0) as $$runner | \
		[ \
			"Metrics artifacts: \($$total)", \
			"PR-to-merged P50: \(pct($$merge_seconds; 50) | fmt) seconds", \
			"PR-to-merged P95: \(pct($$merge_seconds; 95) | fmt) seconds", \
			"Post-merge red rate: \(rate($$red; $$total)) (\($$red)/\($$total))", \
			"Conflict rate: \(rate($$conflicts; $$total)) (\($$conflicts)/\($$total))", \
			"runner minutes total: \($$runner)" \
		] | .[]' $$FILES
	@echo "---"; \
	echo "Dev-loop PR metrics (CI-failure baseline, see dev-loop-metrics-ci-failure-baseline-2):"; \
	test -f _project/scripts/dev_loop_pr_metrics.py && uv run -- python _project/scripts/dev_loop_pr_metrics.py --days "$(DEV_LOOP_METRICS_DAYS)" || true

CI_USAGE_OUTPUT ?= .cache/ci-usage-report/report-$(CI_USAGE_SINCE)_$(CI_USAGE_UNTIL).json

.PHONY: ci-usage-report
ci-usage-report:
	@test -n "$(CI_USAGE_SINCE)" -a -n "$(CI_USAGE_UNTIL)" || { echo "usage: make ci-usage-report CI_USAGE_SINCE=YYYY-MM-DD CI_USAGE_UNTIL=YYYY-MM-DD [CI_USAGE_OUTPUT=path] [CI_USAGE_ARGS=...]"; exit 2; }
	uv run -- python _project/scripts/dev_loop_pr_metrics.py --usage-report --since "$(CI_USAGE_SINCE)" --until "$(CI_USAGE_UNTIL)" --output "$(CI_USAGE_OUTPUT)" $(CI_USAGE_ARGS)

include $(BENCHBOX_MAKEFILE_ROOT)make/worktrees.mk

include $(BENCHBOX_MAKEFILE_ROOT)make/worktree-maintenance.mk

.PHONY: branch-prune-merged
branch-prune-merged:
	@command -v gh >/dev/null 2>&1 || { echo "gh CLI required for branch-prune-merged" >&2; exit 1; }
	@DRY_RUN='$(if $(filter 1,$(DRY_RUN)),1,$(if $(strip $(DRY_RUN)),invalid,))' \
		uv run -- python scripts/branch_prune_merged.py

.PHONY: uat-artifact-hygiene
uat-artifact-hygiene:
	@uv run --no-sync -- python -m tests.uat.artifact_hygiene \
		$(if $(OUTPUT),--output "$(OUTPUT)",) \
		$(if $(THRESHOLD_BYTES),--threshold-bytes "$(THRESHOLD_BYTES)",)

.PHONY: uat-cell
uat-cell:
	@if [ -z "$(PLATFORM)" ] || [ -z "$(BENCHMARK)" ] || [ -z "$(SCALE)" ]; then \
		echo "Usage: make uat-cell PLATFORM=<name> BENCHMARK=<name> SCALE=<float>" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli \
		--platform "$(PLATFORM)" \
		--benchmark "$(BENCHMARK)" \
		--scale "$(SCALE)" \
		$(if $(PHASES),--phases "$(PHASES)",) \
		$(if $(COMPRESSION),--compression "$(COMPRESSION)",) \
		$(if $(TIMEOUT_S),--timeout-s "$(TIMEOUT_S)",) \
		$(if $(LOG_DIR),--log-dir "$(LOG_DIR)",)

.PHONY: uat-validate
uat-validate:
	@if [ -z "$(RESULTS_DIR)" ] || [ -z "$(OUTPUT_TSV)" ]; then \
		echo "Usage: make uat-validate RESULTS_DIR=<dir> OUTPUT_TSV=<path> [FLOOR=0.80]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli validate \
		--results-dir "$(RESULTS_DIR)" \
		--output-tsv "$(OUTPUT_TSV)" \
		$(if $(FLOOR),--floor "$(FLOOR)",)

.PHONY: uat-report
uat-report:
	@if [ -z "$(CELLS_JSONL)" ] || [ -z "$(OUTPUT_TSV)" ]; then \
		echo "Usage: make uat-report CELLS_JSONL=<path> OUTPUT_TSV=<path> [RUNGS=...] [CROSS_SCALE_FLOOR=N]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli report \
		--cells-jsonl "$(CELLS_JSONL)" \
		--output-tsv "$(OUTPUT_TSV)" \
		$(if $(RUNGS),--rungs "$(RUNGS)",) \
		$(if $(CROSS_SCALE_FLOOR),--cross-scale-floor "$(CROSS_SCALE_FLOOR)",)

.PHONY: uat-package
uat-package:
	@if [ -z "$(CONFIG)" ] || [ -z "$(SUBMISSIONS_DIR)" ] || [ -z "$(RESULTS)" ]; then \
		echo "Usage: make uat-package CONFIG=<path> SUBMISSIONS_DIR=<path> RESULTS=\"r1.json r2.json ...\"" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli package \
		--config "$(CONFIG)" \
		--submissions-dir "$(SUBMISSIONS_DIR)" \
		$(foreach r,$(RESULTS),--result "$(r)")

UAT_BRING_UP_KNOWN_PLATFORMS := cedardb clickhouse-server databend doris influxdb lakesail pg-duckdb pg-mooncake postgresql presto questdb singlestore starrocks timescaledb trino velox

.PHONY: uat-bring-up
uat-bring-up:
	$(if $(strip $(PLATFORM)),$(if $(filter $(PLATFORM),$(UAT_BRING_UP_KNOWN_PLATFORMS)),,$(error unknown platform '$(PLATFORM)'; supported: $(UAT_BRING_UP_KNOWN_PLATFORMS))),)
	@if [ -z "$(PLATFORM)" ]; then \
		echo "Usage: make uat-bring-up PLATFORM=<name> [TIMEOUT_S=300] [DRY_RUN=1] [BENCHMARK_RUNS_DIR=~/Developer/benchmark_runs]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python scripts/uat-bring-up/uat_bring_up.py \
		--platform "$(PLATFORM)" \
		$(if $(TIMEOUT_S),--timeout-s "$(TIMEOUT_S)",) \
		$(if $(BENCHMARK_RUNS_DIR),--benchmark-runs-dir "$(BENCHMARK_RUNS_DIR)",) \
		$(if $(DRY_RUN),--dry-run,)

.PHONY: uat-prepull
uat-prepull:
	$(if $(strip $(PLATFORM)),$(if $(filter $(PLATFORM),$(UAT_BRING_UP_KNOWN_PLATFORMS)),,$(error unknown platform '$(PLATFORM)'; supported: $(UAT_BRING_UP_KNOWN_PLATFORMS))),)
	@if [ -z "$(PLATFORM)" ]; then \
		echo "Usage: make uat-prepull PLATFORM=<name> [PREPULL_TIMEOUT_S=900] [DRY_RUN=1]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python scripts/uat-bring-up/uat_bring_up.py \
		--platform "$(PLATFORM)" \
		--prepull-only \
		$(if $(PREPULL_TIMEOUT_S),--prepull-timeout-s "$(PREPULL_TIMEOUT_S)",) \
		$(if $(DRY_RUN),--dry-run,)

.PHONY: uat-docker-cleanup
uat-docker-cleanup:
	@uv run --no-sync -- python -m tests.uat._cli docker-cleanup \
		$(if $(ENGINE),--engine "$(ENGINE)",) \
		$(if $(MODE),--mode "$(MODE)",) \
		$(if $(PREFIX),--prefix "$(PREFIX)",) \
		$(if $(APPLY),--apply,)

.PHONY: uat-sweep
uat-sweep:
	@if [ -z "$(CONFIG)" ]; then \
		echo "Usage: make uat-sweep CONFIG=<path> [PLATFORMS=<a,b>] [DRY_RUN=1]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli sweep --config "$(CONFIG)" \
		$(if $(PLATFORMS),--platforms "$(PLATFORMS)",) \
		$(if $(DRY_RUN),--dry-run,)

.PHONY: uat-smoke
uat-smoke:
	@uv run --no-sync -- python -m tests.uat._cli sweep --config tests/uat/configs/uat-smoke.yaml

.PHONY: uat-stress
uat-stress:
	@uv run --no-sync -- python -m tests.uat._cli stress \
		$(if $(CONFIG),--config "$(CONFIG)",) \
		$(if $(PLATFORM),--platform "$(PLATFORM)",) \
		$(if $(BENCHMARK),--benchmark "$(BENCHMARK)",) \
		$(if $(SCALE),--scale "$(SCALE)",)

.PHONY: uat-gate-check
uat-gate-check:
	@if [ -z "$(STAGE1)" ] || [ -z "$(STAGE2)" ] || [ -z "$(STAGE3)" ]; then \
		echo "Usage: make uat-gate-check STAGE1=\"<run-dir> ...\" STAGE2=\"<run-dir> ...\" STAGE3=\"<run-dir> ...\" [OUTPUT=<path>]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli gate-check \
		--stage1 $(STAGE1) \
		--stage2 $(STAGE2) \
		--stage3 $(STAGE3) \
		$(if $(OUTPUT),--output "$(OUTPUT)",) \
		$(if $(DIST_DIGEST),--expected-distribution-digest "$(DIST_DIGEST)",)

.PHONY: uat-execute
uat-execute:
	@if [ -z "$(CONFIG)" ]; then \
		echo "Usage: make uat-execute CONFIG=<path>" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python -m tests.uat._cli execute \
		--config "$(CONFIG)" \
		$(if $(DATABASES_ROOT),--databases-root "$(DATABASES_ROOT)",) \
		$(if $(NO_CLEANUP),--no-cleanup,)

include $(BENCHBOX_MAKEFILE_ROOT)make/help.mk
