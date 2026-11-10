<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# CI Checkout Depth and History Requirements

This document records the checkout depth baseline, classification of checkouts across
all GitHub Actions workflows, the catalog of history-dependent tests, and the rationale
for retaining full history on specific jobs.

## 1. Baseline Checkout Durations

Measured from recent GitHub Actions runs on `ubuntu-latest` with `actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09`
and `fetch-depth: 0`:

| Job | Workflow | Baseline Checkout Duration |
|---|---|---|
| `medium-collect` | `ci.yml` | 20 s |
| `medium-test (shard 1)` | `ci.yml` | 24 s |
| `test (ubuntu-latest, 3.14)` | `ci.yml` | 24 s |
| `correctness-gate (a)` | `ci.yml` | 23 s |
| `correctness-gate (b)` | `ci.yml` | 26 s |
| `ci-paths` | `ci.yml` | 24 s |
| `lint` | `ci.yml` | 24 s |
| `TPC-H binary framing (macos)` | `ci.yml` | 10 s |
| `TPC-H binary framing (windows)` | `ci.yml` | 11 s |

Jobs with `fetch-depth: 0` download the full commit history, all branches, and all tags,
averaging 20–26 seconds of runner time per job before dependency caching or compilation begins.

## 2. Checkout Classification

Checkouts are classified into four classes based on their actual git requirements:

- **Tree-only (T):** Needs only the checked-out commit tree (`HEAD`). `git show HEAD:...`,
  `git ls-files`, `git check-attr`, and compilation require no commit history. Safe for `fetch-depth: 1`.
- **Exact-object / Ref (X):** Needs specific commits, trees, or objects from another ref
  (e.g. comparing against `published-results` tip or reading an exact recorded SHA from a fixture).
  Safe with a shallow checkout plus a targeted `git fetch --depth=1 origin <ref>`.
- **Ancestry / Range (A):** Uses `git merge-base`, `git rev-list`, `git log A..B`, `A...B` three-dot diffs,
  or `git merge-base --is-ancestor`. Must retain sufficient commit ancestry or full depth.
- **Tag / Release (G):** Uses `git tag --list`, `git describe`, or annotated tag inspection.
  Must retain tags and tag ancestry.

### Classification Table

| Workflow | Job | Class | Current Depth | Target Depth | Rationale |
|---|---|---|---|---|---|
| `trunk.yml` | `required-local-cases` | T | 0 | 1 | Runs make target with no git commands beyond `git rev-parse HEAD`. |
| `trunk.yml` | `tpcds-parity-draws` | T | 0 | 1 | Parity report generation uses no git history. |
| `trunk.yml` | `correctness-gate` | T | 0 | 1 | Make-driven correctness tests require only HEAD tree. |
| `ci.yml` | `correctness-gate` | T | 0 | 1 | Same as trunk correctness-gate. |
| `ci.yml` | `example-validation` | T | 0 | 1 | `make docs-validate` uses only `git ls-files`. |
| `corpus-drift-check.yml` | `check` | T+X | 0 | 1 | Compares develop HEAD against explicitly fetched `published-results`. |
| `submission-validator-drift-check.yml` | `check` | T+X | 0 | 1 | Compares develop against explicitly fetched `published-results`. |
| `comment-policy-nightly.yml` | `scan` | T | 0 | 1 | Runs `scripts/run_comment_policy.py --full` which compares HEAD against HEAD. |
| `nightly-v2.yml` | `drift` | T+X | 0 | 1 | Directional comparison against explicitly fetched `published-results`. |
| `ci.yml` | `code-test` | T (blocked) | 0 | 1 (after test fix) | Fast tier; blocked by `test_check_corpus_bijection` needing recorded commit object. |
| `trunk.yml` | `fast` | T (blocked) | 0 | 1 (after test fix) | Fast tier; same dependency as code-test. |
| `ci.yml` | `medium-collect` | T (blocked) | 0 | 1 (after test fix) | Pytest collection; blocked by `test_ledger_seed` import-time fetch. |
| `trunk.yml` | `medium-collect` | T (blocked) | 0 | 1 (after test fix) | Same as ci.yml medium-collect. |
| `trunk-coverage.yml` | `medium-collect` | T (blocked) | 0 | 1 (after test fix) | Same as ci.yml medium-collect. |
| `ci.yml` | `medium-test` | A+X (blocked) | 0 | 1 (after test fix) | 4 shards; blocked by 3 tests needing history or base ref. |
| `trunk.yml` | `medium-test` | A+X (blocked) | 0 | 1 (after test fix) | Same as ci.yml medium-test. |
| `trunk-coverage.yml` | `medium-test` | A+X (blocked) | 0 | 1 (after test fix) | Same as ci.yml medium-test. |

### Exemptions: Jobs Retaining Full History (`fetch-depth: 0`)

The following jobs **must not** use shallow checkouts because their core function requires git ancestry:

- **`ci.yml` `ci-paths`:** Uses three-dot diffs (`$EVENT_BASE_SHA...HEAD`), `git merge-base --is-ancestor`,
  `path_filter_decision.py`, and `coverage_test_selection.py` ancestry traversals to route PR tests.
- **`ci.yml` `comment-policy`:** Runs `scripts/run_comment_policy.py` which derives the ownership base
  via `git merge-base` with the base branch and verifies ancestry against bootstrap commits.
- **`trunk.yml` `comment-policy-scan`:** Pinned by `test_trunk_workflow.py:158` to assert full checkout depth.
- **`trunk-coverage.yml` `coverage-map-staleness`:** Runs `coverage_map_staleness.py` which executes
  `git rev-list --count map..base` to count commits behind develop.
- **`trunk-coverage.yml` `medium-coverage-map`:** `fetch-map` verifies `--is-ancestor` against develop.
- **`soundness-merge-digest.yml`:** Walks `git log --first-parent since..ref` to build the soundness digest.
- **`site-inputs.yml`:** Checks `git merge-base --is-ancestor` between the parent core commit and current head.
- **`validate-submission.yml`:** Runs three-dot diff `git diff BASE...MERGE`.
- **`release-v2.yml` `admit`:** Inspects annotated tags (`git cat-file -t TAG`), checks `--is-ancestor`
  against develop, and verifies tag signatures.
- **`release-v2.yml` `uat-reuse`:** Verifies that the candidate RC tag commit is an ancestor of the release commit.

## 3. History-Dependent Tests Catalog

Audit of the test suite identified three test modules with hidden git history dependencies:

1. **`tests/unit/test_canary_impact_live.py:30-37`**
   - **Behavior:** Executes `git diff --name-only <sha>^ <sha>` for four historical commit SHAs
     (`68fb5a071`, `5e9284a2c`, `ed3e39a9c`, `2384c3ee7`).
   - **Failure in shallow clone:** Hard failure (`CalledProcessError`) because historical parent commits are not present.
   - **Remediation:** Provide frozen changed-file fixtures for the test cases so real git history is not queried.

2. **`tests/uat/test_no_cli_surface_drift.py:348-360`**
   - **Behavior:** Resolves `origin/develop` and calls `git merge-base origin/develop HEAD` on the working repository.
   - **Failure in shallow clone:** On a PR branch checked out at depth 1, `origin/develop` is absent so the test skips silently;
     if fetched at depth 1, `git merge-base` finds no common ancestor and fails.
   - **Remediation:** Accept `BENCHBOX_BASE_REF` or skip explicitly with a documented reason when running in a shallow environment without base ancestry.

3. **`tests/unit/scripts/publication/test_ledger_seed.py:38-70`**
   - **Behavior:** At module import time, runs `git cat-file -e RECORDED_SOURCE` (from `publication/ledger-seed.json`).
     If the commit is missing, it executes an unbounded `git fetch --no-tags origin published-results:refs/remotes/origin/published-results`.
   - **Failure in shallow clone:** Causes every test collection step (`medium-collect`) and medium shard to pull the full
     git history of the `published-results` branch over the network.
   - **Remediation:** Guard the import-time fetch so it does not run during test collection, or pre-fetch only the specific recorded commit object.

4. **`tests/unit/scripts/publication/test_check_corpus_bijection.py:53-56`**
   - **Behavior:** Marked `fast`; runs `git ls-tree` and `git show` on `RECORDED_SOURCE` without a fetch fallback.
   - **Remediation:** Pre-fetch the specific recorded object or isolate the test to avoid requiring full history in the fast tier.
