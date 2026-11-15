COVERAGE_SCOPE ?= routine
COVERAGE_FILTER_fast := -m "fast and not (slow or stress or resource_heavy or live_integration or cloud_import)" --timeout=120
COVERAGE_FILTER_routine := -m "not (stress or resource_heavy or live_integration)"
COVERAGE_FILTER_opt-in :=
COVERAGE_DEFAULT_REPORTS_fast := term
COVERAGE_REPORTS ?= $(or $(COVERAGE_DEFAULT_REPORTS_$(COVERAGE_SCOPE)),term html xml)
COVERAGE_REPORT_term := --cov-report=term-missing:skip-covered
COVERAGE_REPORT_term-full := --cov-report=term-missing
COVERAGE_REPORT_html := --cov-report=html:htmlcov
COVERAGE_REPORT_xml := --cov-report=xml:coverage.xml

.PHONY: coverage
coverage:
	@case "$(COVERAGE_SCOPE)" in fast|routine|opt-in) ;; *) echo "COVERAGE_SCOPE must be fast, routine or opt-in, not '$(COVERAGE_SCOPE)'" >&2; exit 2 ;; esac
	@$(if $(filter-out term term-full html xml,$(COVERAGE_REPORTS)),echo "COVERAGE_REPORTS accepts only term term-full html xml; not '$(filter-out term term-full html xml,$(COVERAGE_REPORTS))'" >&2; exit 2,true)
	uv run -- python -m pytest -c pytest-ci.ini $(COVERAGE_FILTER_$(COVERAGE_SCOPE)) --cov=benchbox --cov-branch $(foreach report,$(COVERAGE_REPORTS),$(COVERAGE_REPORT_$(report))) --cov-fail-under=0


.PHONY: complexity-check
complexity-check:
	uv run -- python _project/scripts/check_complexity.py

.PHONY: complexity-report
complexity-report:
	uv run -- python _project/scripts/check_complexity.py --no-fail --top 30

.PHONY: quality-governance-typecheck
quality-governance-typecheck:
	uv run ty check --error all _project/scripts/check_complexity.py

.PHONY: lint
lint:
	uv run ruff check .
	$(MAKE) comment-policy-check
	$(MAKE) windows-antipatterns-check

.PHONY: uv-lock-revision-check
uv-lock-revision-check:
	uv run -- python _project/scripts/check_uv_lock_revision.py $(if $(BASE_REF),--baseline-ref "$(BASE_REF)",)
	for project in _project/sql-translation-ml _project/scripts; do uv lock --check --project "$$project" || exit 1; done

.PHONY: sqlglot-adopt-wheel
sqlglot-adopt-wheel:
	@test -n "$(WHEEL)" -a -n "$(EXPECTED_VERSION)" -a -n "$(FORK_SHA)" -a -n "$(UPSTREAM_SHA)" || { echo "usage: make sqlglot-adopt-wheel WHEEL=path/to/benchbox_sqlglot-X.whl EXPECTED_VERSION=X.Y.Z.N FORK_SHA=<sha> UPSTREAM_SHA=<sha> [SHA256=<digest>] [DRY_RUN=1]"; exit 2; }
	uv run -- python -m scripts.adopt_sqlglot_wheel --wheel "$(WHEEL)" --version "$(EXPECTED_VERSION)" --fork-sha "$(FORK_SHA)" --upstream-sha "$(UPSTREAM_SHA)" $(if $(SHA256),--sha256 "$(SHA256)",) $(if $(DRY_RUN),--dry-run,)

.PHONY: sqlglot-advisory-check
sqlglot-advisory-check:
	uv run -- python scripts/check_sqlglot_advisories.py

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

.PHONY: comment-policy-scan
comment-policy-scan:
	@case "$(COMMENT_POLICY_MODE)" in \
		strict|report) uv run -- python scripts/check_comment_policy.py --mode "$(COMMENT_POLICY_MODE)" ;; \
		scope) uv run -- python scripts/check_comment_cleanup_scope.py --check-drift $(if $(BASE_REF),--base "$(BASE_REF)",) ;; \
		*) echo "COMMENT_POLICY_MODE must be strict, report or scope" >&2; exit 2 ;; \
	esac

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
	@case "$(DUPLICATE_CHECK_FORMAT)" in ""|text|json) ;; *) echo "DUPLICATE_CHECK_FORMAT must be text or json" >&2; exit 2 ;; esac
	uv run -- python scripts/check_duplicate_code.py $(if $(filter json,$(DUPLICATE_CHECK_FORMAT)),--json,)

.PHONY: duplicate-check-verbose
duplicate-check-verbose:
	uv run -- python scripts/check_duplicate_code.py --verbose --top-n 30

.PHONY: duplicate-check-delta
duplicate-check-delta:
	uv run -- python scripts/check_duplicate_code.py --delta-vs "$(if $(BASE_REF),$(BASE_REF),origin/develop)"

.PHONY: mutation-test
mutation-test:
	@echo "Running mutation tests on critical modules..."
	uv run -- mutmut run
	@echo "--- Mutation test results ---"
	uv run -- mutmut results

.PHONY: review-mutation-test
review-mutation-test:
	@echo "Running mutation tests on the review decision modules against the review tests..."
	uv run -- python _project/scripts/review_mutation.py run
	@echo "--- Review mutation test results ---"
	uv run -- python _project/scripts/review_mutation.py report

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
	uv run -- python scripts/check_public_contract_drift.py; \
	[ $$? -eq 0 ] || failed="$$failed public-contract-drift"; \
	$(MAKE) doc-contracts-guard; \
	[ $$? -eq 0 ] || failed="$$failed doc-contracts"; \
	$(MAKE) audit-deps; \
	[ $$? -eq 0 ] || failed="$$failed audit-deps"; \
	$(MAKE) audit-raw-check; \
	[ $$? -eq 0 ] || failed="$$failed audit-raw-check"; \
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
	uv run --isolated --no-project --with "$$wheel" -- benchbox --help > /dev/null; \
	uv venv --python 3.12 .venv; \
	uv pip install --python .venv/bin/python "$$wheel[duckdb]"; \
	.venv/bin/benchbox run --platform duckdb --benchmark tpch --scale 0.01; \
	test -n "$$(find benchmark_runs/results -name '*.json' -print -quit)"
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

CI_LINUX_MACHINE ?= $(or $(BENCHBOX_HEAVY_LINUX_MACHINE),benchbox-agent)
CI_LINUX_CMD ?= make test-correctness-gate
CI_LINUX_NEED_GIB ?= 2
HOST_LOCK_CLI = uv run -- python -m benchbox.utils.host_lock
CI_LINUX_ENV = -e BENCHBOX_TEST_LOCK_DIR=/tmp/benchbox-lock -e BENCHBOX_HEAVY_ADMIT_MIN_GIB=1.5 -e BENCHBOX_HEAVY_MIN_FREE_DISK_GIB=2
.PHONY: ci-linux
ci-linux: export CI_LINUX_CMD := $(CI_LINUX_CMD)
ci-linux: export BENCHBOX_HEAVY_LINUX_MACHINE := $(CI_LINUX_MACHINE)
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
		echo "  container machine create local/benchbox-agent --name $(CI_LINUX_MACHINE) --home-mount rw --cpus 4 --memory 6G"; \
		echo "  container machine stop $(CI_LINUX_MACHINE)"; \
		exit 1; \
	fi; \
	echo "==> ci-linux: '$(CI_LINUX_CMD)' inside container machine '$(CI_LINUX_MACHINE)'"; \
	mkdir -p "$$HOME/.cache"; \
	CMD_FILE=$$(mktemp "$$HOME/.cache/benchbox-ci-linux-cmd.XXXXXX"); \
	trap 'rm -f "$$CMD_FILE"' EXIT; \
	printf 'cd "%s"\nexport UV_PROJECT_ENVIRONMENT="$${UV_PROJECT_ENVIRONMENT:-$$HOME/.cache/benchbox-linux-venv}"\n%s\n' "$(CURDIR)" "$$CI_LINUX_CMD" > "$$CMD_FILE"; \
	echo "==> ci-linux: the host lock and watchdog cover host memory pressure and disk; the VM footprint is reported separately and bounded by its own allowance (BENCHBOX_HEAVY_VM_ALLOWANCE_GIB); the machine is stopped after the run unless BENCHBOX_KEEP_LINUX_MACHINE=1 or another run uses it, and the sentinel stops it when idle" >&2; \
	NEED_GIB=$$($(HOST_LOCK_CLI) machine-need --machine $(CI_LINUX_MACHINE) --fallback-gib $(CI_LINUX_NEED_GIB)) || NEED_GIB=""; \
	NEED_GIB=$${NEED_GIB:-$(CI_LINUX_NEED_GIB)}; \
	$(HOST_LOCK_CLI) run --phase ci-linux --gate ci-linux --admit --need-gib "$$NEED_GIB" --guard -- bash scripts/linux_machine_run.sh $(CI_LINUX_MACHINE) $(CI_LINUX_ENV) -- bash -l "$$CMD_FILE"

.PHONY: typecheck
typecheck:
	uv run -- python -m scripts.typecheck

.PHONY: validate-imports
validate-imports: lint-imports

.PHONY: catalog-schema-check
catalog-schema-check:
	uv run -- python -m benchbox.core.catalog_schema

.PHONY: format
format:
	uv run ruff format .
