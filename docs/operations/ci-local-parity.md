# CI / local lint parity

`make ci-lint` lets you run every lint guard CI enforces on a develop PR
locally, before you push. A guard only in `.github/workflows/ci.yml` first
fires on a pushed PR, costing a full CI round trip for a failure you could
catch locally in seconds.

## The invariant

Every guard that the `lint` job (job id `code-lint`) in `ci.yml` runs after
its dependency-install step must also run in the Makefile's `ci-lint` recipe,
matched by **command**, not by a similarly named local target.
`tests/system/test_ci_lint_parity.py` parses `ci.yml`, the source of truth,
and fails if `ci-lint` lacks a `lint`-job guard command or if an exclusion
names a renamed or removed step, so a stale exclusion cannot hide a guard that
runs nowhere. Name matches are not enough: a local target can share a step's
name but run different logic, and so not check what CI checks.

## Adding a new lint guard

To add a guard step to the `lint` job in `ci.yml`:

1. Give the step an `id: guard-<slug>` and `continue-on-error: true` (see
   "Report-all: one CI cycle, every guard's result" below for why).
2. Add the same command to the `ci-lint:` recipe in the `Makefile`: a
   `$(MAKE) <target>` line if the guard has a `make` target, otherwise the
   CI step's `uv run ...` or script call. Follow it with a
   `[ $$? -eq 0 ] || failed="$$failed <slug>"` line, as existing guard pairs
   do.
3. If the guard has more than a couple of lines of logic that would
   otherwise live only in the workflow YAML, first move it to a script
   under `scripts/` that both `ci.yml` and `ci-lint` call. A second copy in
   the Makefile is exactly the drift this invariant prevents.
4. Run `uv run -- python -m pytest tests/system/test_ci_lint_parity.py -q`.
   A failure means step 2 was missed, the command text does not match
   verbatim, or step 1's `id` or `continue-on-error` is missing or malformed.
   `test_guard_steps_follow_naming_convention` enforces the convention;
   `test_lint_job_guards_run_in_ci_lint` enforces command parity.

## Report-all: one CI cycle, every guard's result

The `lint` job runs about twenty-five independent guards. Each sets
`continue-on-error: true`, so every guard runs and shows as its own Actions UI
step with its own log and timing. A PR that trips two unrelated guards learns
of both in one run.

**Naming convention (the aggregation keys on this, not on a hand-maintained
list):** every independent guard step's `id:` starts with `guard-` (for
example `guard-ruff`, `guard-audit-deps`). Setup steps (checkout,
`setup-python`, `setup-uv`, install dependencies) get no `id: guard-*` and
no `continue-on-error`: a setup failure should stop the job at once, since
guards that never ran because `uv sync` failed have nothing to report.

The job's last step, `lint-guard-summary`, receives the Actions `steps`
context (`${{ toJSON(steps) }}`) through an env var, not by interpolation into
the script. For each step id starting with `guard-`, it checks `outcome`, the
raw result *before* `continue-on-error` applies (`conclusion` would show
`success` for every failed-but-continued guard). The step writes a pass/fail
table to `$GITHUB_STEP_SUMMARY` and exits nonzero if any guard's `outcome` was
`failure`. It has no `continue-on-error`, so that
exit fails the `code-lint` job. Nothing here softens the check; it only
changes *when* you learn that a second guard also failed.

Defining the guard set by the prefix, not a hand-written list, is
deliberate: a new guard without the prefix would run and fail unseen by the
summary, and its own
`continue-on-error: true` would swallow the job-level failure.
`tests/system/test_ci_lint_parity.py::test_guard_steps_follow_naming_convention`
pins the convention: every guard has the prefix and `continue-on-error`, and
every non-guard step has neither. `test_lint_guard_summary_step_exists` pins
that the aggregator step exists, is named exactly `lint-guard-summary`, runs
with `if: always()` (so it still runs and reports after an earlier guard
fails), and is the job's last step.

`make ci-lint` is report-all locally too. The recipe is one shell invocation
(via `\` line continuations) under `set +e` that runs every guard command in
turn, collects failures in a `$$failed` list instead of stopping at the first
nonzero exit, then prints a consolidated `❌ FAILED guards:` list and
exits nonzero if the list is not empty. Each guard's output streams live,
unbuffered; only its exit code is checked after it runs. Because the recipe
body is one logical shell line, the parity test strips each guard command's
trailing `; \` marker before comparing it with the `ci.yml` command text
(see `_normalize_recipe_lines` in `tests/system/test_ci_lint_parity.py`).

If a guard truly cannot run locally, add it to the `EXCLUDED_STEPS` dict in
the parity test with a concrete reason. Never omit it silently, and do not
weaken the CI guard so a lossier local equivalent can "pass."

## Guards `ci-lint` skips when it runs on a CI runner itself

`make ci-lint` may itself run on an ephemeral GitHub-hosted runner. Most guards
inspect the checked-out tree, the installed venv, or a registry the repo
ships, so they mean the same there. A couple read state that exists only on a
developer machine; on a runner they would fail for a reason unrelated to the
code, or pass while checking nothing:

- `agent-identity-check` resolves `git config user.*`, which an ephemeral
  runner lacks. `agent-commit-range-check`, which reads the commits a branch
  actually carries, is the merge-time control and always runs, in both
  `ci-lint` and `ci.yml`.
- `skill-sync-check` needs developer-local skill source checkouts. Its CI
  coverage is `ci.yml`'s `skill-integrity` job, which clones the sources and
  runs the full preview/apply/verify/check cycle.

Before each of these guards, the `ci-lint` recipe calls a small gate script.
It skips the guard only when the script prints the exact token `SKIP` and
`GITHUB_ACTIONS=true`. The decision travels on stdout, not the exit status,
so a broken or missing gate runs the guard rather than silently dropping it.
A guard not in the gate's table always runs, on a runner as locally. GitHub
Actions sets `GITHUB_ACTIONS`; never set it by hand, so local runs are
unaffected.

## Local preflight

`make pr-preflight`, run by the pre-push hook, lints the changed Python files
with ruff and runs the tests mapped from the changed paths, failed tests
first. CI-only checks and their local commands: `ty`
(`uv run ty check`), import-linter (`make lint-imports`), comment policy
(`make comment-policy-check`), the content guard
(`make pr-preflight-fast-tests`), skill integrity
(`make skill-integrity-check`), and UAT hygiene (`make uat-artifact-hygiene`).
The full fast lane is `make pr-preflight-fast-tests`; all `ci-lint` guards run
in `make ci-lint`.

## `pr-preflight-fast-tests` and the content guard

`make pr-preflight-fast-tests` always runs
`pr-content-guard` (YAML/markdown/docs hygiene + artifact hygiene), whatever
the branch's `needs-code-ci` path-filter decision; that decision gates only
the fast-test pytest run. Run directly, it creates any classifier JSON and
path lists the caller did not supply. It runs under the shared test lock.

## Hosted-only guard inventory

`tests/system/test_ci_lint_parity.py` also checks guard-shaped steps in other
merge-gating workflows. Each must name a local equivalent or a written
reason in the test's `MERGE_GATE_EXEMPTIONS` table, so no new check step
becomes a silent CI-only failure.

The `medium-test` job installs the locked TypeScript parser used by
`tests/unit/scripts/test_comment_policy.py`; `make test-medium` runs the same
reviewed-JavaScript flow test locally.

The local equivalent of the `code-test` step that runs changed tests on the
curated release tree is
`uv run -- python scripts/release_curation_dry_run.py --changed-since origin/develop`.
With no arguments it runs the full fast selection on that tree.

Bundled generator integrity has local equivalents.
`uv run -- python scripts/bundled_binary_manifest.py` checks the shipped
source tree. After `uv build --out-dir /tmp/benchbox-dist`,
`uv run -- python scripts/verify_distribution_binaries.py /tmp/benchbox-dist/*.whl /tmp/benchbox-dist/*.tar.gz`
compares both distributions with the source manifest. From outside the
checkout, an installed wheel checks its own file membership and hashes with
`python -m benchbox.utils.binary_manifest`.
