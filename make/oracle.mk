.PHONY: oracle-coverage-map
oracle-coverage-map:
	uv run -- python _project/scripts/generate_oracle_coverage_map.py

.PHONY: oracle-coverage-map-check
oracle-coverage-map-check:
	uv run -- python _project/scripts/generate_oracle_coverage_map.py --check

.PHONY: oracle-backoff-reset
oracle-backoff-reset:
	@test -n "$(PR)" -a -n "$(HEAD)" -a -n "$(REASON)" || { echo "Usage: make oracle-backoff-reset PR=<number> HEAD=<40-character head SHA> REASON='<why>'" >&2; exit 1; }
	python3 scripts/theseus_cli.py run -- reset-backoff --policy .github/oracle-reviewers.yml --pr "$$PR" --head "$$HEAD" --reason "$$REASON"

.PHONY: omp-candidate-check
omp-candidate-check:
	@test -n "$(VERSION)" || { echo "Usage: make omp-candidate-check VERSION=<omp version> [PIN=<file whose omp section names the candidate>] [BINARY=<local omp binary for this platform>]" >&2; exit 1; }
	python3 scripts/theseus_cli.py run -- omp-candidate-check check --version "$$VERSION" --pin "$${PIN:-.github/oracle-reviewers.yml}" --omp-config .github/oracle-omp-config.yml --fixtures tests/fixtures/oracle_reviewers/omp_candidate $${BINARY:+--binary "$$BINARY"}
