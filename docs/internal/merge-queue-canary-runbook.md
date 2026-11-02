# Native Merge Queue Canary Rehearsal Runbook

> Historical: the merge queue was retired on 2026-10-03 (see `_project/decisions/merge-queue-retirement-2026-10-03.md`), so this rehearsal no longer applies.

This runbook provides the operational procedure and scenario checklist for conducting an end-to-end rehearsal of the GitHub Native Merge Queue on `BenchBox-dev/BenchBox` prior to production enablement on `refs/heads/develop`.

---

## 1. Objectives & Safety Invariants

- **Isolation Invariant:** The production `develop-squash-only` ruleset must remain untouched during initial canary runs. Rehearsals run against a dedicated test branch (e.g. `smoke/merge-queue-canary`) or staged sandbox ruleset.
- **Contract Proof:** Prove that all six required status checks (`core`, `explorer`, `results-data`, `docs`, `landing`, `tooling`) report successfully under `merge_group: checks_requested`.
- **Soundness Gate Proof:** Prove that pull requests touching `SOUNDNESS_PREFIXES` are strictly withheld from auto-enqueueing.

---

## 2. Rehearsal Scenarios & Verification Matrix

### Scenario 1: Documentation / Content PR (Fast Lane)
- **Action:** Open PR modifying markdown documentation only (e.g. `docs/about/overview.md`).
- **Execution:**
  1. Open and arm PR with a serial readiness evidence file: `make pr-open READY=1 EVIDENCE=<readiness.json>`
  2. Verify PR enters the merge queue upon passing initial branch checks.
  3. Inspect the spawned `merge_group` workflow run:
     - `ci-paths` classifies `safe-content-only: true`.
     - Code lanes skip; `content-guard` passes; `core` reports success because no path touches it.
     - `docs` builds the site and requires the exact-base visual comparison when a render input changed.
     - `explorer`, `results-data`, and `landing` report success (no changes in their paths).
     - `tooling` reports success for the unchanged soundness surface, and its `ruleset-drift` job executes the trusted base check and reports green.
  4. Verify PR squash-merges cleanly into target branch.

### Scenario 2: Code PR (Full Test Matrix)
- **Action:** Open PR modifying core Python code and unit tests (e.g. `benchbox/core/` non-soundness path).
- **Execution:**
  1. Open and arm PR with a serial readiness evidence file: `make pr-open READY=1 EVIDENCE=<readiness.json>`
  2. Verify PR enters the merge queue.
  3. Inspect the spawned `merge_group` workflow run:
     - `ci-paths` classifies `needs-code-ci: true`.
     - `code-lint`, `code-test`, `medium-test`, `correctness-gate`, and `plan-capture-gate` execute fully on the speculative merge tree, and `core` aggregates green.
     - `tooling` reports success for the unchanged soundness surface, and its `ruleset-drift` job reports green.
     - `explorer`, `results-data`, `docs`, and `landing` report success for unaffected paths.
  4. Verify atomic squash merge completes.

### Scenario 3: Soundness PR Negative Control (Missing or Failing Gate)
- **Action:** Open PR modifying a soundness-critical path (e.g. `benchbox/core/expected_results/` or `_project/scripts/soundness_paths.py`).
- **Execution:**
  1. With no `oracle-gate` status on the PR head (or a failing one), attempt to open with auto-merge and exact readiness evidence: `make pr-open READY=1 EVIDENCE=<readiness.json>`
  2. Assert that the readiness transaction refuses with: `soundness paths changed and the required 'oracle-gate' status at <head> is absent, not success` (or `failure`/`pending` for a failing gate), and that `make pr-arm` refuses the same head.
  3. Verify on GitHub that `autoMergeRequest` is `null` (auto-merge withheld).
  4. Positive control: once the `benchbox-oracle` App posts a passing `oracle-gate` status on the same head, `make pr-open READY=1` and `make pr-arm` arm the PR; a soundness PR arms only with that passing status on the head.

---

## 3. Operator Verification Checklist

| Check | Expected Outcome | Command / Check Method |
|---|---|---|
| 1. Workflow YAML Validity | Pass with zero syntax errors | `python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/ci.yml', '.github/workflows/nightly-v2.yml']]"` |
| 2. Unit Tests Passing | All workflow tests green | `uv run -- python -m pytest tests/unit/workflows/ -q` |
| 3. Soundness Tests Passing | All soundness tests green | `uv run -- python -m pytest tests/unit/scripts/test_soundness_paths.py -q` |
| 4. Status Check Match | Exact required check names match ruleset | `rg -n '^  (core|explorer|results-data|docs|landing|tooling):$' .github/workflows/ci.yml` |
| 5. Trusted Base Drift | Checkout uses `merge_group.base_sha` | `rg -n 'merge_group.base_sha' .github/workflows/ci.yml` |

---

## 4. Rollback Readiness

If any failure occurs during canary rehearsal or rollout:
1. Disable `merge_queue` on the target ruleset.
2. Confirm standard `develop-squash-only` ruleset is restored.
3. Validate with `python scripts/ruleset_drift_check.py --ruleset develop-squash-only`.
