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

dist: clean
	uv build

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

.PHONY: corpus-stage-local
corpus-stage-local:
	@test -n "$(RESULT)" -a -n "$(OUT)" || { echo "Usage: make corpus-stage-local RESULT=<run result json> OUT=<staging dir>" >&2; exit 1; }
	uv run -- python scripts/stage_seed_corpus_bundle.py --internal-manifest "$(RESULT)" "$(OUT)"
