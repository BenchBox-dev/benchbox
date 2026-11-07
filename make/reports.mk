.PHONY: tpchavoc-equivalence-report
tpchavoc-equivalence-report:
	uv run -- python -m benchbox.core.tpchavoc.equivalence $(if $(TPCHAVOC_ENGINE),--engine $(TPCHAVOC_ENGINE))

.PHONY: tpchavoc-dataframe-equivalence-report
tpchavoc-dataframe-equivalence-report:
	uv run -- python -m benchbox.core.tpchavoc.dataframe_equivalence

EXECUTION_ENGINE ?=
EXECUTION_ENGINE_OPTION = $(if $(strip $(EXECUTION_ENGINE)),--execution-engine $(EXECUTION_ENGINE),)

CROSS_SURFACE_ARGS_tpcds := --backend expression --backend datafusion

.PHONY: cross-surface-equivalence-report
cross-surface-equivalence-report:
	@[ -n "$(CROSS_SURFACE_BENCHMARK)" ] || { echo "CROSS_SURFACE_BENCHMARK is required: a gate name from benchbox.core.equivalence.cross_surface.GATES, for example tpch or joinorder_synthetic" >&2; exit 2; }
	uv run -- python -m benchbox.core.equivalence.cross_surface --benchmark $(CROSS_SURFACE_BENCHMARK) $(if $(CROSS_SURFACE_BACKEND),--backend $(CROSS_SURFACE_BACKEND),$(CROSS_SURFACE_ARGS_$(CROSS_SURFACE_BENCHMARK)) $(EXECUTION_ENGINE_OPTION))

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

.PHONY: cross-surface-applicability-report
cross-surface-applicability-report:
	uv run -- python _project/scripts/cross_surface_applicability_sweep.py
