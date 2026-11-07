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
		echo "Usage: make uat-bring-up PLATFORM=<name> [TIMEOUT_S=300] [DRY_RUN=1] [BENCHMARK_RUNS_DIR=~/Developer/benchmark_runs] [DOWN=1 [REMOVE_VOLUMES=1]]" >&2; \
		exit 2; \
	fi
	@uv run --no-sync -- python scripts/uat-bring-up/uat_bring_up.py \
		--platform "$(PLATFORM)" \
		$(if $(TIMEOUT_S),--timeout-s "$(TIMEOUT_S)",) \
		$(if $(BENCHMARK_RUNS_DIR),--benchmark-runs-dir "$(BENCHMARK_RUNS_DIR)",) \
		$(if $(DOWN),--down,) \
		$(if $(DOWN),$(if $(REMOVE_VOLUMES),--remove-volumes,),) \
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
