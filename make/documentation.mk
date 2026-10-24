.PHONY: docs-linkcheck
docs-linkcheck: query-docs
	@uv run -- python scripts/check_doc_external_links.py

.PHONY: docs-validate
docs-validate:
	@uv run -- python scripts/check_docs_ownership.py
	@echo "Validating example file references..."
	@uv run -- python scripts/validate_example_references.py
	@echo ""
	@echo "Checking example file syntax..."
	@uv run -- python scripts/check_example_syntax.py
	@echo ""
	@echo "Generating per-query template pages (link targets, not committed)..."
	@uv run -- python scripts/generate_query_docs.py
	@echo ""
	@echo "Checking repo-local doc relative links..."
	@uv run -- python scripts/check_doc_relative_links.py

prompt-quickstarts-check:
	@uv run -- python scripts/generate_landing_quickstarts.py

.PHONY: query-docs
query-docs:
	uv run -- python scripts/generate_query_docs.py

.PHONY: docs-generate
docs-generate: query-docs prompt-quickstarts-check compat-docs-check doc-contracts-check docs-reference-inventory-check

API_CONTRACT_VENV ?= $(or $(RUNNER_TEMP),$(TMPDIR),/tmp)/benchbox-api-contract-venv

.PHONY: api-contract-check
api-contract-check:
	@rm -rf "$(API_CONTRACT_VENV)"
	@uv run -- python scripts/check_api_contract_symbols.py venv --dir "$(API_CONTRACT_VENV)"
	@uv run -- python scripts/check_api_contract_symbols.py check --python "$(API_CONTRACT_VENV)/bin/python"

.PHONY: docs-check
docs-check: docs-validate docs-generate
	@echo ""
	@echo "✅ All documentation checks passed!"

.PHONY: compile-tpcds-binaries
compile-tpcds-binaries:
	bash _sources/compilation/scripts/compile-all-platforms.sh --native

.PHONY: parity-fixtures
parity-fixtures:
	uv run python tests/parity/generate_visualization_fixtures.py

.PHONY: compat-docs
compat-docs:
	uv run -- python scripts/generate_compat_docs.py

.PHONY: compat-docs-check
compat-docs-check:
	uv run -- python scripts/generate_compat_docs.py --check
	uv run -- python -m benchbox.sql_compat.inventory --output /tmp/benchbox-compat-inventory.jsonl --check-ddl-drift

.PHONY: doc-contracts
doc-contracts:
	uv run -- python scripts/doc_contracts.py --output doc-contracts.json

.PHONY: doc-contracts-check
doc-contracts-check:
	uv run -- python scripts/doc_contracts.py --check

.PHONY: docs-reference-inventory
docs-reference-inventory:
	uv run -- python scripts/doc_reference_inventory.py

.PHONY: docs-reference-inventory-check
docs-reference-inventory-check:
	uv run -- python scripts/doc_reference_inventory.py --check

.PHONY: doc-contracts-guard
doc-contracts-guard: doc-contracts-check docs-reference-inventory-check

.PHONY: pricing-data
pricing-data:
	uv run -- python scripts/generate_pricing_data.py

.PHONY: pricing-data-check
pricing-data-check:
	uv run -- python scripts/generate_pricing_data.py --check

.PHONY: platform-manifest
platform-manifest:
	uv run -- python _project/scripts/platform_manifest.py

.PHONY: platform-manifest-check
platform-manifest-check:
	uv run -- python _project/scripts/platform_manifest.py --check

.PHONY: parity-check
parity-check:
	@tmpdir=$$(mktemp -d) && \
	uv run -- python tests/parity/generate_visualization_fixtures.py --out $$tmpdir && \
	diff -r --exclude='.gitkeep' tests/parity/fixtures $$tmpdir && \
	echo "parity-check: fixtures match Python source" && \
	rm -rf $$tmpdir || \
	(echo "parity-check FAILED: fixtures are out of date - run 'make parity-fixtures' to regenerate (or 'make guards-fix' to regenerate every mechanical drift-guard artifact)" && rm -rf $$tmpdir && exit 1)
