LIVE_PLATFORMS := databricks snowflake bigquery redshift athena firebolt firebolt-core starburst motherduck \
	pg-duckdb pg-mooncake cedardb gizmosql
LIVE_SERIAL_PLATFORMS := firebolt-core cedardb gizmosql

.PHONY: test-live
test-live:
	@if [ -n "$(LIVE_PLATFORM)" ] && ! printf ' %s ' $(LIVE_PLATFORMS) | grep -q " $(LIVE_PLATFORM) "; then \
		echo "Unknown LIVE_PLATFORM '$(LIVE_PLATFORM)'; choose one of: $(LIVE_PLATFORMS)" >&2; \
		exit 2; \
	fi
	@echo "Running live tests for $(or $(LIVE_PLATFORM),every platform) (requires that platform's credentials or local service; see .env.example)"
	$(if $(filter gizmosql,$(LIVE_PLATFORM)),BENCHBOX_REQUIRE_LIVE_GIZMOSQL=1 )uv run -- python -m pytest -m "$(if $(LIVE_PLATFORM),live_$(subst -,_,$(LIVE_PLATFORM)),live_integration)" --tb=short -v$(if $(filter $(LIVE_SERIAL_PLATFORMS),$(LIVE_PLATFORM)), -n 0)

DOCKER_PLATFORMS := clickhouse trino presto postgresql starrocks doris databend influxdb cedardb firebolt questdb singlestore
DOCKER_TEST_STATE_DIR ?= /tmp/benchbox-docker-projects

CONTAINER_ENGINE ?= docker
COMPOSE := $(CONTAINER_ENGINE) compose
COMPOSE_UP_NEED_GIB ?= 2
COMPOSE_ADMIT = $(HOST_LOCK_CLI) admit --need-gib $(COMPOSE_UP_NEED_GIB) >&2 || exit $$?;

define require_data_dir_if_mounted
case " lakesail velox " in *" $(1) "*) case "$$BENCHBOX_DATA_DIR" in "") echo "ERROR: BENCHBOX_DATA_DIR must be exported before bringing up docker/$(1) -- its compose file binds the data directory at the SAME absolute path inside the container as on the host, because the server resolves client-sent file paths server-side; an unset value is silently accepted as an empty mount by both docker compose and mocker. Example: export BENCHBOX_DATA_DIR=$$HOME/benchbox-data" >&2; exit 1 ;; /*) : ;; *) echo "ERROR: BENCHBOX_DATA_DIR must be an ABSOLUTE path (got '$$BENCHBOX_DATA_DIR') -- docker/$(1)'s compose file binds it at the SAME path inside the container as on the host, so a relative value can never match. Example: export BENCHBOX_DATA_DIR=$$HOME/benchbox-data" >&2; exit 1 ;; esac ;; esac
endef

define compose_down_fresh
case "$$BENCHBOX_DATA_DIR" in /*) ;; *) BENCHBOX_DATA_DIR="/tmp/benchbox-teardown-placeholder"; export BENCHBOX_DATA_DIR ;; esac; $(COMPOSE) -p "$(1)" -f "$(2)" down -v; if [ "$(CONTAINER_ENGINE)" = "mocker" ]; then awk '/^volumes:/{f=1;next} f&&/^[^ ]/{f=0} f&&/^  [A-Za-z0-9._-]+:/{k=$$1;sub(/:.*/,"",k);print k}' "$(2)" 2>/dev/null | while read -r _k; do mocker volume rm "$(1)-$$_k" >/dev/null 2>&1 || true; mocker volume rm "$(1)"_"$$_k" >/dev/null 2>&1 || true; done; fi
endef

test-docker-up-%:
	@set -e; \
		$(call require_data_dir_if_mounted,$*); \
		state_dir="$(DOCKER_TEST_STATE_DIR)"; \
		project_file="$$state_dir/$*.project"; \
		mkdir -p "$$state_dir"; \
		project_name="$$(cat "$$project_file" 2>/dev/null || true)"; \
		if [ -z "$$project_name" ]; then \
			$(COMPOSE_ADMIT) \
			project_name="benchbox-$*-test-$$(date +%s)-$$RANDOM"; \
		fi; \
		status=1; \
		cleanup() { \
			if [ $$status -ne 0 ]; then \
				{ $(call compose_down_fresh,$$project_name,docker/$(or $(DOCKER_COMPOSE_DIR_$*),$*)/docker-compose.yml) ; } >/dev/null 2>&1 || true; \
				rm -f "$$project_file"; \
			fi; \
		}; \
		trap cleanup EXIT INT TERM; \
		$(COMPOSE) -p "$$project_name" -f docker/$(or $(DOCKER_COMPOSE_DIR_$*),$*)/docker-compose.yml up -d --wait; \
		printf '%s\n' "$$project_name" > "$$project_file"; \
		status=0

test-docker-down-%:
	@set -e; \
		state_dir="$(DOCKER_TEST_STATE_DIR)"; \
		project_file="$$state_dir/$*.project"; \
		project_name="$$(cat "$$project_file" 2>/dev/null || true)"; \
		if [ -z "$$project_name" ]; then \
			echo "No tracked Docker test stack for $*"; \
			exit 0; \
		fi; \
		$(call compose_down_fresh,$$project_name,docker/$(or $(DOCKER_COMPOSE_DIR_$*),$*)/docker-compose.yml); \
		rm -f "$$project_file"

DOCKER_COMPOSE_DIR_pg-extensions := postgres-extensions
DOCKER_TEST_MARKER_pg-extensions := live_pg_duckdb or live_pg_mooncake
DOCKER_TEST_MARKER_firebolt := live_firebolt_core

test-docker-%:
	@echo "Running $* Docker integration tests"
	@set -e; \
		$(COMPOSE_ADMIT) \
		project_name="benchbox-$*-test-$$(date +%s)-$$RANDOM"; \
		cleanup() { { $(call compose_down_fresh,$$project_name,docker/$(or $(DOCKER_COMPOSE_DIR_$*),$*)/docker-compose.yml) ; } || true; }; \
		trap cleanup EXIT INT TERM; \
		$(call require_data_dir_if_mounted,$*); \
		$(COMPOSE) -p "$$project_name" -f docker/$(or $(DOCKER_COMPOSE_DIR_$*),$*)/docker-compose.yml up -d --wait; \
		uv run -- python -m pytest -m "$(or $(DOCKER_TEST_MARKER_$*),live_$*)" --tb=short -v -n 0

PARITY_PLATFORMS ?= questdb postgresql
.PHONY: test-docker-parity
test-docker-parity:
	@bash _project/scripts/mocker_compose_parity.sh $(CONTAINER_ENGINE) $(PARITY_PLATFORMS)
