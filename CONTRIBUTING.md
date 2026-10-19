<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Contributing to BenchBox

Code contributions that improve the quality and reliability of BenchBox are always welcomed.

This document provides guidelines and instructions for contributing.

## Development Environment Setup

### Prerequisites

- Python 3.11+
- Git
- [uv](https://docs.astral.sh/uv/) (fast Python package manager)
- [GitHub CLI](https://cli.github.com/) (`gh`) — required for the one-shot PR flow below

### Setting Up Your Environment

1. Clone the repository:

   ```bash
   git clone https://github.com/BenchBox-dev/BenchBox.git
   cd BenchBox
   ```

2. Install the package in development mode:

   ```bash
   make develop
   ```

   `make develop` is equivalent to `uv sync --group dev`.

3. Install the pre-commit + pre-push hooks:

   ```bash
   pre-commit install
   ```

   This installs two hooks (configured in `.pre-commit-config.yaml`):
   - **pre-commit**: ruff format/check, codespell, YAML / markdown lint, timing-policy check.
   - **pre-push**: `pr-preflight` — runs `make pr-preflight` (ruff on the changed Python files and the tests mapped from them) before each push.
   Existing clones that installed hooks before the pre-push stage was added should re-run `pre-commit install` once.

   If `pre-commit install` errors with `Cowardly refusing to install hooks with core.hooksPath set` and points at the default `.git/hooks` location, run `git config --unset-all core.hooksPath` and try again — that's a redundant override left over from earlier tooling.

## Branches & PR gate

`develop` is the long-lived development branch and the repository's default branch; **all changes land via PR**. `release` is release-only (handled by the version-branch flow — see `docs/internal/release-guide.md`). PRs target `develop` and squash-merge with linear history.

Required CI on `develop` reports five unit results (`core`, `explorer`, `results-data`, `docs` and `tooling` from `.github/workflows/ci.yml`), plus `oracle-review`, which passes on a result-affecting PR only when the `benchbox-oracle` app's latest review of its current head reports success (or a listed attester has posted the stand-in approval for that head) and the oracle's threads are resolved. `.github/ci-units.yml` and `.github/path-filters.yml` classify each PR: content-only PRs run content validation and skip Python fast tests, while code, infra, workflow, tooling, and unknown paths run the lint/type + Ubuntu Python 3.14 fast-test checks. Python 3.12 runs the same fast suite after each merge to `develop` (`trunk.yml`) and nightly (`nightly.yml`). Soundness-path review applies even when the author uses the owner's account; once a finished branch is armed, auto-merge lands it when required checks are green and all review threads are resolved.

## Development Workflow

The canonical loop is **branch → edit → preflight → `make pr-open` → arm → monitor to merge**. Once the branch is finished, arm it with `make pr-arm`, which checks the live PR for a hold and arms auto-merge on the exact head; GitHub merges it when its required checks are green. After the merge, `trunk.yml` tests `develop`; if it fails because of your change, revert first with `make trunk-revert PR=<n>`. You are done when the PR is merged, not when it is open or green: after a spurious failure, re-run the failed jobs with `make pr-rerun` and re-arm (`make pr-arm` again); after a real failure, fix and push. Stop and hand back only for the exceptions listed in `AGENTS.md` `[WRITE-CLOSEOUT-001]`.

1. **Create a feature worktree off `develop`.** Agents must keep the main clone read-only:

   ```bash
   WORKTREE_PATH=../BenchBox.wt-feat-your-thing
   make worktree-create BRANCH=feat/your-thing WORKTREE_PATH="$WORKTREE_PATH"
   cd "$WORKTREE_PATH"
   make agent-write-preflight
   uv sync --group dev
   ```

2. **Make your changes.** Iterate with the fast lane:

   ```bash
   make test
   make format
   make lint
   make typecheck
   ```

   `make test` runs the fast lane (about a minute); `make format` runs `ruff format .`, `make lint` runs
   `ruff check .`, and `make typecheck` runs `ty check`.

   The pre-commit hook re-runs format/lint/etc. at commit time, so if you forget, the commit will fix or block as appropriate.

3. **Commit using [Conventional Commits](https://www.conventionalcommits.org/)** (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `ci:`, `refactor:`):

   ```bash
   git add path/to/file.py path/to/test.py
   git commit -m "fix: resolve race in foo loader"
   ```

   Stage explicit paths; never use `git add -A`.

4. **Run the local preflight, then open the PR:**

   ```bash
   make pr-preflight
   make pr-open TITLE="fix(scope): what the change does" PR_BODY_FILE=pr-body.md
   ```

   `make pr-preflight` runs the local lint and the path-aware content guard or fast tests. `make pr-open` pushes and
   uses the GitHub REST API; it refuses to run from `develop` or `release`. Without `TITLE`, a new PR uses the
   oldest branch commit's subject. On an existing PR, `make pr-open` pushes and then updates only supplied title
   and body fields that differ from the current text, together in one request. Omitting a field preserves it;
   line-ending and trailing-whitespace differences do not cause an edit. Pass the final text when you open the PR;
   after a fix, update it in the `make pr-open` that pushes the fix. Leave out figures that change with each push,
   such as test counts. Use `make pr-rerun`, not a title or body edit, to retry CI.

5. **Arm it and monitor until it merges:**

   ```bash
   make pr-arm
   ```

   `PR=<n>` is optional. `make pr-arm` refuses on a hold label, requested changes, a draft, or a head that is not
   pushed.

   `make pr-arm` reads the live PR first, so a `no-auto-merge` label or a requested change stops it instead of
   being mistaken for a failing check; remove a hold deliberately to release it. Before editing an armed PR,
   withdraw it with the revision transaction (`make pr-landing-withdraw`, see `docs/agent/review-protocol.md`),
   push the correction, rerun CI and obtain the oracle's success review (or the stand-in approval) on the new head before arming:
   a later `--match-head-commit` cannot undo a merge of the old head.
   `make pr-ready PR=<n> HEAD=<sha>` arms an open PR through `make pr-arm`. With `EVIDENCE` or `BATCH` it runs the
   readiness evidence transaction used to deliver a prepared batch, where the evidence file must declare
   `delivery_mode` and the complete prepared-batch binding; a single PR does not need it.
   The `no-auto-merge` label blocks arming only; to hold an armed PR, run `gh pr merge <n> --disable-auto` or
   `make pr-landing-withdraw PR=<n> HEAD=<sha>`, then add the label.

6. **After merge**, remove the clean linked worktree. The remote branch normally auto-deletes through the repository setting; sweep stale local branches separately:

   ```bash
   make worktree-remove WORKTREE_PATH="$WORKTREE_PATH"
   make branch-prune-merged DRY_RUN=1
   make branch-prune-merged
   ```

   `make worktree-remove` removes the linked worktree. `make branch-prune-merged DRY_RUN=1` previews branches with
   complete historical merge evidence (it requires `gh`); without `DRY_RUN` it deletes only exact, merge-reachable
   historical heads.

   Inspect open PRs at any time with `make pr-status`.

## Pre-Push Validation

There are two layers, and you only need the first:

**Required gate (mirrors what CI enforces):**

```bash
make pr-preflight
```

This runs `ruff check` and `ruff format --check` on the Python files you changed, then runs the tests for them without xdist, so it does not wait on the shared test lock. A changed `tests/**/test_*.py` runs itself; a changed `benchbox/**/<module>.py` or `scripts/<module>.py` runs every `tests/**/test_<module>.py`. Tests that failed in the previous run go first (`--ff`), then the rest run. If no test maps to your change it runs no tests and says CI will run the fast lane.

It does not run the full fast lane, type checking, or the other lint guards. CI runs all of them on every PR within about 20 minutes and is the authoritative check. To run the full fast lane before pushing, use `make pr-preflight-fast-tests`; to run the CI lint guards use `make ci-lint`.

**Optional thoroughness check** — only useful when you've changed cross-cutting things (CI workflows, packaging, docs build, integration paths):

```bash
make ci-local
```

This runs the broader CI mirror:

| Step | Target |
| --- | --- |
| Lint + format + type checking | `make ci-lint` |
| Fast tests with coverage | `make ci-test` |
| Integration smoke tests | `make test-integration-smoke` |
| Documentation build | `make ci-docs` |
| Package build + install test | `make test-package` |

Or run any of those individually. Additional one-offs: `make security-audit` and `make spellcheck`.

Skip `make ci-local` for everyday changes — `make pr-preflight` is the right gate. The required checks gate the merge, so the cost of being wrong is a re-push and `make pr-arm` for the new head.

## Testing

This section is the canonical starting point for contributor test commands.
The root README covers product use and links here instead of duplicating the
test matrix. See [tests/README.md](tests/README.md) for test categories,
markers, parallel-run rules, and troubleshooting.

We use pytest for testing. Common testing commands:

```bash
make test
make test-unit
make test-integration
make test-fast
make test-ci
```

`make test` is a fast local run without coverage. `make test-unit` and `make test-integration` run only unit or
integration tests, `make test-fast` runs tests marked fast (under a second), and `make test-ci` is the CI profile with
coverage and reports.

The default `pytest.ini` is optimized for quick local feedback. Use the CI profile when you need coverage, HTML reports,
or JUnit XML output.

```bash
uv run -- python -m pytest
uv run -- python -m pytest -c pytest-ci.ini
uv run -- python -m pytest -m fast
```

The first command is the fast local run, the second uses the CI-equivalent instrumentation, and the third runs only
fast tests.

The collection hook applies simple defaults: tests in `tests/unit` automatically receive the `unit` and `fast` markers, `tests/integration` receive `integration` with a default `medium` speed, and files in `tests/performance` are marked `performance` and `slow`. Add explicit markers to override those defaults for individual tests.

### Running the PySpark suite

PySpark 4.x only supports Java 17 and 21, so we provide `scripts/with_supported_java.sh` to automatically pick a compatible JDK (it prefers macOS’ `/usr/libexec/java_home -v 21`, then 17, and falls back to whatever `JAVA_HOME` you already exported). No shims are added for older runtimes.

To run every PySpark unit test:

```bash
make test-pyspark
```

The target shells out to the helper script, which validates the Java version and executes `tests/unit/platforms/dataframe/test_pyspark_df.py`. You can also wrap any BenchBox command that touches PySpark - benchmarks, smoke runs, etc.-like this:

```bash
scripts/with_supported_java.sh benchbox run --platform pyspark-df --benchmark tpch --scale 0.01 --non-interactive
```

CI jobs install Temurin 21 and invoke the same helpers so PySpark stays active everywhere automatically.

## Adding a New Benchmark

To add a new benchmark, create a new file in the `benchbox` directory and implement the `BaseBenchmark` interface. See existing benchmarks for examples.

## Adding DataFrame Platform Support

BenchBox uses a family-based architecture for DataFrame platforms. To add a new platform:

1. **Determine the family**: Expression (Polars, PySpark) or Pandas (Pandas and cuDF)
2. **Implement DataFrameContext**: Provides table access and family-specific helpers
3. **Implement platform adapter**: Handles data loading and query execution
4. **Register the platform**: Add to `DATAFRAME_ADAPTERS` registry
5. **Add tests**: Unit tests and integration tests

See [Adding a DataFrame Platform](docs/development/adding-dataframe-platform.md) for detailed instructions.

### DataFrame Query Guidelines

When implementing new DataFrame queries:

- Each query needs both `expression_impl` and `pandas_impl` functions
- Use the `DataFrameContext` protocol for table access
- Use `ctx.col` and `ctx.lit` for expression family implementations
- Use string-based column access for pandas family implementations
- Test both families independently

## Code Style

We use ruff for fast Python linting and formatting with a 120-character line length. Type hints are required and enforced with ty. Our pre-commit hooks will help enforce these standards.

- **Line length**: 120 characters
- **Formatter**: ruff (replaces black and isort)
- **Linter**: ruff (replaces flake8)
- **Type checker**: ty

## Documentation

Please update documentation when adding or modifying features. Documentation pages are Markdown under `docs/`; `make docs-check` validates examples, links between pages and generated inputs. The site repository renders them for benchbox.dev.

## Site and Explorer changes

benchbox.dev, including the Results Explorer, is built and deployed from
[`BenchBox-dev/benchbox-site`](https://github.com/BenchBox-dev/benchbox-site),
not from this repository.

Data and pipeline changes reach the site automatically. After each successful
push to `develop`, `.github/workflows/site-inputs.yml` builds the certified
`site-inputs-<sha>` bundle and sends a `core-bundle` dispatch to the site
repository, whose deploy fetches and verifies that bundle.
A failed bundle build opens or updates a `site-bundle-failed` issue in this
repository. The reporter comments only when the failure class or the failing
commit is new, and the next successful bundle build on `develop` comments on
and closes issues the reporter opened itself; it never closes incidents owned
by the site-side deploy relay. A run marked as an expected-failure probe never
opens, comments, or closes an incident.

The site, the Results Explorer app, the landing page, the blog and blog drafts
live only in `benchbox-site`. To change them, open a pull request there.

## Releasing

External contributions land via PR against `develop` (squash-merge). Releases
are cut by maintainers via the version-branch flow documented in
[`docs/internal/release-guide.md`](docs/internal/release-guide.md):

1. `make release-cut VERSION=X.Y.Z` from a clean linked worktree whose HEAD is
   the freshly fetched `origin/develop` commit. It cuts `vX.Y.Z` with a
   release-curated tree and opens a PR against `release`. The target refuses
   the primary clone and any checkout that is dirty, ahead, or behind.
2. Review the PR and wait for `validate-base` and `release-required-result`.
3. `make release-finalize VERSION=X.Y.Z` merges and tags the release; `release.yml`
   publishes to PyPI.

Use `make release-cut-abort VERSION=X.Y.Z` to back out an uncommitted cut from
the worktree that created it.

We follow semantic versioning.

## License

By contributing to BenchBox, you agree that your contributions will be licensed under the project's MIT license.
