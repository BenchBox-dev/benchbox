.PHONY: oracle-coverage-map
oracle-coverage-map:
	uv run -- python _project/scripts/generate_oracle_coverage_map.py

.PHONY: oracle-coverage-map-check
oracle-coverage-map-check:
	uv run -- python _project/scripts/generate_oracle_coverage_map.py --check
