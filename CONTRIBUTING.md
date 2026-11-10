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

3. Install the git hooks:

   ```bash
   pre-commit install
   ```

   This installs the hooks configured in `.pre-commit-config.yaml`:
   - **pre-commit**: ruff format/check, codespell, YAML / markdown lint, comment policy, Windows antipattern ratchet, timing-policy check, and the agent write-location, handoff, and identity guards.
   - **commit-msg**: rejects agent `Co-Authored-By` and session trailers.

   Run `make pr-preflight` on the committed tree before pushing; it lints the changed Python files, runs the tests mapped from them and writes a receipt that `make pr-open` requires. Nothing runs automatically on push.

   If `pre-commit install` errors with `Cowardly refusing to install hooks with core.hooksPath set` and points at the default `.git/hooks` location, run `git config --unset-all core.hooksPath` and try again — that's a redundant override left over from earlier tooling.

## Branches & PR gate

`develop` is the long-lived development branch and the repository's default branch; **all changes land via PR**. Releases are tags on `develop` commits (see `docs/internal/release-guide.md`). PRs target `develop` and squash-merge with linear history.

Required CI on `develop` reports five unit results (`core`, `explorer`, `results-data`, `docs` and `tooling` from `.github/workflows/ci.yml`), plus `hold` and `review-gate`. `review-gate` is posted by the `benchbox-oracle` app from `develop` code and passes on a result-affecting PR only when the app's latest review of its current head reports success (or a listed attester has posted the stand-in approval for that head) and the reviewer's threads are resolved. Soundness-path review applies even when the author uses the owner's account; once a finished branch is armed, auto-merge lands it when required checks are green and all review threads are resolved.

### CI lanes

The canonical policy is [Merge and trunk governance](docs/internal/merge-queue-governance.md).
The workflows serve different purposes:

| Lane | Trigger and purpose |
| --- | --- |
| PR (`ci.yml`) | Path-aware checks on the PR head. All five unit outcomes report even when their work is unaffected; an untouched unit succeeds, while a required job that fails or unexpectedly skips fails its unit. |
| Trunk (`trunk.yml`) | Pushes to `develop` test the merged state: Python 3.12 fast tests, the full four-shard medium tier without coverage, correctness and required-local-case gates, and distribution artifacts. Push runs are serialized: the running run continues and only the newest pending push is retained; intermediate waiting pushes are replaced, not individually tested. |
| Coverage producer (`trunk-coverage.yml`) | Scheduled six times daily and manually dispatched, not run on every merge. Runs the medium tier with per-test coverage and publishes the map used by PR test selection. Changes to the Trunk or coverage lane's own inputs can also invoke its medium-only workflow from PR CI. |
| Nightly T3 (`nightly-v2.yml`) | Daily at 07:30 UTC and manually dispatched. Broad, live, platform and performance checks report by domain; see [Nightly T3 validation](docs/internal/nightly-t3.md) for artifacts and failure ownership. |

`.github/ci-units.yml` assigns paths to one or more units; unknown paths fall back to `core`,
an empty change set or a classifier self-protection change runs every unit, and Markdown changes
and deleted/renamed paths also select `docs`. A touched `core` or `tooling` unit runs lint/type
checks and the Ubuntu Python 3.14 fast suite. Other content-only changes run the applicable
content/docs checks without that suite; a `.md` suffix alone does not make a change content-only.
For example, `CONTRIBUTING.md`, agent instructions and selected internal runbooks explicitly
route to code CI.

`.github/path-filters.yml` adds content, skill-integrity, packaging, binary-framing and
visualization checks. Coverage-impact selection currently runs in shadow mode: it records
the proposed selection but escalates to the complete medium tier. Selected Windows tests,
tuning, SQL-execution evidence, publication/API contracts and Trunk-lane changes have
additional conditional jobs. Read the `ci-paths` summary and decision artifacts for the
actual routing of a head rather than assuming every PR runs every job.

## Development Workflow

The canonical loop is **branch → edit → preflight → `make pr-open` → arm → monitor to merge**. Once the branch is finished, arm it with `make pr-arm`, which checks the live PR for a hold and arms auto-merge on the exact head; GitHub merges it when its required checks are green. After the merge, `trunk.yml` tests `develop`; if it fails because of your change, revert first with `make trunk-revert PR=<n>`. You are done when the PR is merged, not when it is open or green: after a spurious failure, re-run the failed jobs with `make pr-rerun` and re-arm (`make pr-arm` again); after a real failure, fix and push. Stop and hand back only for the exceptions listed in `AGENTS.md` `[WRITE-CLOSEOUT-001]`.

The steps below are the ordinary serial flow for standalone, unrelated, cross-repository,
review-separated, or approval-separated work. Related items go on one feature branch with
one PR per feature. Feature delivery mode is opt-in: the owner names the batch, it has two
or more members, and the installed registered-batch capability and its prerequisites are
verified. Changes that install the capability or enable the loop must use serial delivery;
they cannot certify themselves.

For a feature batch, one leader owns the integration branch/worktree, tracker, ledger, and
one final PR to `develop`. Each member owns its worktree, implementation, scoped checks,
and landing report; members neither open PRs nor write to the tracker. Before landing, the
leader reviews the member's exact patch, and before the final PR is armed, every member's
recorded checks run again on the final tree. Arming discovers batch membership from the
tracker, so an outage refuses every arm, including an ordinary PR's. If the leader's session
dies, a new leader recovers ownership with `make batch-recover`, which can also move the
integration checkout to another clean clone.
Follow [Batch delivery modes](docs/agent/batch-feature-delivery.md) for capability checks,
member handoff, integration, and final-PR delivery rather than applying the serial steps
independently to each member.

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

   `make pr-preflight` checks changelog fragments, runs Ruff on changed Python files and runs
   mapped tests, with a conditional Linux run for platform-sensitive mapped tests. It does not
   run the CI content guard or the full fast lane. It also writes a receipt for the tree it ran on
   (see [Preflight receipt](#preflight-receipt)); `make pr-open` refuses to push without a passing receipt
   for the tree being pushed, so commit first, run preflight, then open the PR. `make pr-open` pushes and
   uses the GitHub REST API; it refuses to run from `develop` or `release`. Without `TITLE`, a new PR uses the
   oldest branch commit's subject. On an existing PR, `make pr-open` pushes and then updates only supplied title
   and body fields that differ from the current text, together in one request. Omitting a field preserves it;
   line-ending and trailing-whitespace differences do not cause an edit. Pass the final text when you open the PR;
   after a fix, update it in the `make pr-open` that pushes the fix. Leave out figures that change with each push,
   such as test counts. Use `make pr-rerun`, not a title or body edit, to retry CI: an edit runs no tests and
   only copies the verdict of the newest full CI run on the same commit. An edit made while the full run is
   missing or in progress fails at once instead of waiting; the check is re-run automatically when that full run
   completes.

   Before it pushes, `make pr-open` runs the same review driver as CI on the committed `HEAD`, from a trusted
   export of `origin/develop` that the branch cannot change, and refuses when the shared judgement is DO NOT
   SHIP or leaves a Critical or High defect open, including one carried from an earlier review of the PR. A
   Medium-only SHIP WITH FIXES proceeds. It needs the `TYPESAFE_API_KEY` classifier key (keychain service
   `TYPESAFE_API_KEY`, added with `security add-generic-password -s TYPESAFE_API_KEY -a "$USER" -w`) and refuses
   without it. A finding that is a false positive can be disputed instead of overridden: pass
   `REBUTTALS_FILE=<json>` (a JSON object of finding id, such as `c1-D1`, to a rebuttal with a line that starts
   `Dispute:` and cites file:line counter-evidence) and the reviewer that raised it re-judges it under the CI
   dispute rules. A finding still open after two re-reviews is escalated to the owner, who alone may adjudicate
   it (`ADJUDICATE='WITHDRAW|MAINTAIN D<n> - <instruction>'`, passed only when the owner names the head, the
   finding and the decision in the current request). `make pr-open` embeds the findings record in the PR body as
   a hidden marker; CI reads it as claims only. A review that cannot run (no key, `origin/develop` not
   fetchable, a quota or reviewer absence, a held PR, a secret value on disk, or an
   `omp` that does not match the CI pin) refuses regardless. The one exception is an availability cause
   (the gateway is unreachable, rejects the client token, or serves no provider that can review the change):
   `make pr-open` then pushes and opens a draft whose body ends with `Local review: unavailable (<reason>); the
   hosted review decides`, never arms it (an existing PR keeps its state and gets the marker), and `make pr-arm` refuses it until the hosted `review-gate` passes
   for the head.
   It pushes and arms exactly the commit it reviewed. The review is advisory, may differ from the CI review,
   and posts nothing to GitHub. See `docs/internal/review-v2.md`, Local pre-PR review.

5. **Arm it and monitor until it merges:**

   ```bash
   make pr-arm
   ```

   `PR=<n>` is optional. `make pr-arm` refuses on a hold label, requested changes, a draft, or a head that is not
   pushed.

   `make pr-arm` reads the live PR first, so a `no-auto-merge` label or a requested change stops it instead of
   being mistaken for a failing check; remove a hold deliberately to release it. Before editing an armed PR,
   withdraw it with the revision transaction (`make pr-landing-withdraw`, see `docs/agent/review-protocol.md`),
   push the correction, rerun CI and obtain the reviewer's success review (or the stand-in approval) on the new head before arming:
   a later `--match-head-commit` cannot undo a merge of the old head.
   `make pr-ready PR=<n> HEAD=<sha>` arms an open PR through `make pr-arm`. `BATCH=1` asserts the PR is a
   registered batch PR and refuses otherwise. With `EVIDENCE` it runs the readiness evidence transaction, where the
   evidence file must declare `delivery_mode` and, for a batch, the complete binding; a single PR does not need it.
   Whatever is declared, a PR the tracker registers as a batch PR needs full batch evidence.
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

   For a feature batch, ordinary worktree cleanup is not a substitute for finishing the
   active batch. Only after every member is `done`, the batch is completed, and the final
   PR has actually merged, the leader runs `make batch-finish` from the integration worktree.
   It defaults to a dry run; `EXECUTE=1` removes eligible member worktrees and branches,
   then the integration worktree and local branch last, after fresh proof. See
   [Finish (leader)](docs/agent/batch-feature-delivery.md#6-finish-leader) for the exact gates.

   Inspect open PRs at any time with `make pr-status`.

## Pre-Push Validation

There are two layers, and you only need the first:

**Required local gate (narrower than CI):**

```bash
make pr-preflight
```

This first reports primary-clone cleanliness and checks changelog fragments. It runs `ruff check`
and `ruff format --check` on the Python files you changed, then runs mapped tests without xdist,
so it does not wait on the shared test lock. A changed `tests/**/test_*.py` runs itself; a changed
`benchbox/**/<module>.py` or `scripts/<module>.py` runs every `tests/**/test_<module>.py`.
Tests that failed in the previous run go first (`--ff`), then the rest run. After a successful
mapped run, changes touching platform-branching code or platform skips can select an additional
Linux mapped-test run. If no test maps to your change it runs no tests; CI decides which lanes apply.

It does not run the full fast lane, type checking, content validation or the other lint guards.
CI is path-aware and authoritative. Elapsed time depends on runner queues and on which
conditional lanes a head selects; the merge gate is check results, not elapsed time.
To run the full fast lane before pushing, use `make pr-preflight-fast-tests`; to run the CI
lint guards use `make ci-lint`.

### Preflight receipt

`make pr-preflight` writes `~/.cache/benchbox/preflight/<tree>.json` (override the directory with
`BENCHBOX_PREFLIGHT_RECEIPT_DIR`), where `<tree>` is `git rev-parse HEAD^{tree}`. It records each step's
command, exit code, outcome, test counts, duration and the last 40 lines of output. A run on a dirty working
tree, or one that changes the tree while it runs, writes no receipt.

`make pr-open` refuses, before it runs the review or pushes anything, when there is no receipt for the tree of
`HEAD`, when the receipt records a failed step, or when the tree changed since the run (any new commit that
changes content; a rebase, merge or reworded commit that leaves the tree identical keeps the receipt valid).
The receipt must also cover the branch: it records the merge base it selected against and which steps the
selection required (`ruff-check` and `ruff-format` when Python files changed, `pytest` when tests mapped, `linux-pytest`
when the Linux run was selected), and `make pr-open` refuses a receipt that lacks a required step or was
selected against a base that does not cover the PR's changes (for example `make pr-preflight BASE=HEAD`).
It then adds a short "Preflight receipt" section to the PR body, replacing the one from the previous push.
The section is a claim from the author's machine: the hosted review gate never reads it and CI remains the
proof.

A Linux-leg failure that is environment-only is accepted only when it is listed in
`.github/preflight-known-failures.json` (a soundness path, so an edit gets the full review). Only the
`linux-pytest` step can be allowlisted, and only for a pytest exit code of 1 (failed tests; an interrupted,
crashed or killed run never qualifies). Each entry names the step, the exact pytest node id, the reason and the
tracking item; the file is read from the committed tree being preflighted and pushed, so adding or removing an entry is a
diff that reviews like any code change. Every failed test in the step must be listed, and the receipt and PR body name
each accepted failure. An unlisted failure, or output that does not account for every failure, fails the step.

When the PR fast suite runs, `core` also enforces the aggregate
[fast-test duration policy](docs/internal/nightly-t3.md#fast-test-duration-policy).
It compares calibrated summed test durations with the committed baseline, not job wall time
or an individual test's speed. A text-only PR edit reuses the same-head full-run verdict;
it does not rerun this gate or any tests. Use `make pr-rerun` to retry CI, as described above.

**Optional thoroughness check** — only useful when you've changed cross-cutting things (CI workflows, packaging, docs inputs, integration paths):

```bash
make ci-local
```

This runs the broader CI mirror:

| Step | Target |
| --- | --- |
| Lint + format + type checking | `make ci-lint` |
| Fast tests with coverage | `make ci-test` |
| Integration smoke tests | `make test-integration-smoke` |
| Documentation validation + input generation (not a site build) | `make ci-docs` |
| Package build + install test | `make test-package` |

Or run any of those individually. Additional one-offs: `make security-audit` and `make spellcheck`.

`make ci-docs` runs `docs-validate` and `docs-generate`. The documentation site and its build
live in [BenchBox-dev/benchbox-site](https://github.com/BenchBox-dev/benchbox-site), not this repository.

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
integration tests, `make test-fast` selects tests carrying the `fast` marker, and `make test-ci` is the CI profile
with coverage and reports. `fast` is a selection marker, not a promise that each test finishes in under a second;
the aggregate CI duration gate is described in the [fast-test duration policy](docs/internal/nightly-t3.md#fast-test-duration-policy).

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

See [Adding a DataFrame Platform](https://benchbox.dev/docs/development/adding-dataframe-platform.html) for detailed instructions.

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
are cut by maintainers from `develop`: a release preparation PR (`make release-prep VERSION=X.Y.Z`)
merges like any other PR, then an annotated `vX.Y.Z` tag on a
commit with a green `trunk.yml` run starts `release-v2.yml`, which publishes to
PyPI. The steps are in
[`docs/internal/release-guide.md`](docs/internal/release-guide.md) and the
publishing contract in
[`docs/internal/release-artifacts.md`](docs/internal/release-artifacts.md).

Never edit `CHANGELOG.md`; its `[Unreleased]` section stays empty and CI fails
a pull request that adds text there. A pull request that changes shipped code
(`benchbox/`, packaging files) adds one fragment under `changelog.d/` with
`make changelog-entry` (internal changes: `make changelog-entry CHANGELOG_INTERNAL="reason"`).
Preview with `make changelog-preview` and validate with `make changelog-check`.
`make pr-open` runs the same required-entry check against your PR title before
pushing. Fragments are one file per pull request, so they cannot conflict.
Title edits re-run the entry rule in the `tooling (text edit)` check.
See [changelog.d/README.md](changelog.d/README.md).

We follow semantic versioning.

## License

By contributing to BenchBox, you agree that your contributions will be licensed under the project's MIT license.
