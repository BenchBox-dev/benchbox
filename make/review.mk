.PHONY: review-backoff-reset
review-backoff-reset:
	@test -n "$(PR)" -a -n "$(HEAD)" -a -n "$(REASON)" || { echo "Usage: make review-backoff-reset PR=<number> HEAD=<40-character head SHA> REASON='<why>'" >&2; exit 1; }
	python3 scripts/theseus_cli.py run -- reset-backoff --policy .github/review.yml --pr "$$PR" --head "$$HEAD" --reason "$$REASON"

.PHONY: omp-candidate-check
omp-candidate-check:
	@test -n "$(VERSION)" || { echo "Usage: make omp-candidate-check VERSION=<omp version> [PIN=<file whose omp section names the candidate>] [BINARY=<local omp binary for this platform>]" >&2; exit 1; }
	python3 scripts/theseus_cli.py run -- omp-candidate-check check --version "$$VERSION" --pin "$${PIN:-.github/review.yml}" --omp-config .github/review-omp-config.yml --fixtures tests/fixtures/review_candidates/omp_candidate $${BINARY:+--binary "$$BINARY"}
