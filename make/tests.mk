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

.PHONY: test-fast
test-fast:
	BENCHBOX_TEST_TIER=t1 uv run -- python -m pytest -m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short -rs --timeout=120

.PHONY: test-unlock
test-unlock:
	@python3 -I benchbox/utils/host_lock.py clear

.PHONY: heavy-status
heavy-status:
	@uv run -- python -m benchbox.utils.host_lock status

.PHONY: heavy-sentinel
heavy-sentinel:
	@uv run -- python -m benchbox.utils.host_lock sentinel $(SENTINEL_ARGS)

.PHONY: heavy-sentinel-install
heavy-sentinel-install:
	@uv run -- python -m benchbox.utils.host_lock sentinel install $(SENTINEL_ARGS)

.PHONY: heavy-sentinel-uninstall
heavy-sentinel-uninstall:
	@uv run -- python -m benchbox.utils.host_lock sentinel uninstall $(SENTINEL_ARGS)

.PHONY: heavy-run
heavy-run:
	@test -n "$$CMD" || { echo "usage: make heavy-run CMD='<command>'"; exit 2; }
	@uv run -- python -m benchbox.utils.host_lock run --admit --guard -- sh -c "$$CMD"

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

.PHONY: test-marker
test-marker:
	@[ -n "$(TEST_MARKER)" ] || { echo 'TEST_MARKER is required, for example TEST_MARKER="tpch or tpcds"' >&2; exit 2; }
	uv run -- python -m pytest -m "$(TEST_MARKER)" --tb=short

.PHONY: test-pyspark
test-pyspark:
	./scripts/run_pyspark_tests.sh

TPCDS_OBT_EQUIVALENCE_SF ?= 0.01
.PHONY: tpcds-obt-equivalence
tpcds-obt-equivalence:
	BENCHBOX_TEST_TIER=t2 BENCHBOX_OBT_EQUIVALENCE_REQUIRED=1 BENCHBOX_OBT_EQUIVALENCE_SF=$(TPCDS_OBT_EQUIVALENCE_SF) uv run -- python -m pytest tests/integration/test_tpcds_obt_equivalence.py --tb=short -n 0 --timeout=3600 $(PYTEST_ARGS)

.PHONY: test-ci
test-ci:
	uv run -- python -m pytest -c pytest-ci.ini -m "not (slow or stress or resource_heavy or live_integration)" --cov=benchbox --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml --cov-fail-under=0 --timeout=300
