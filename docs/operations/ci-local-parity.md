# CI / local lint parity

`make ci-lint` exists
so that every lint guard CI enforces on a develop PR can also run locally, before
you push. A guard that only exists in `.github/workflows/ci.yml` fires for the
first time on a pushed PR -- that costs a full remote CI round trip for a
failure you could have caught in seconds locally.

## The invariant

Every guard the `lint` job (job id `code-lint`) in `ci.yml` runs, after its
dependency-install step, must also run in the Makefile's `ci-lint` recipe --
at the **command** level, not just under a similarly-named local target.
`tests/system/test_ci_lint_parity.py` parses `ci.yml` (the source of truth)
and pins this: it fails if a `lint`-job guard command is missing from
`ci-lint`, and it fails if an exclusion entry references a step that was
renamed or removed (so the exclusion can't quietly rot into cover for a
guard nobody runs anywhere).

Command-level, not name-level, matters here: a local target can share a
step's name while running different logic -- treating a name match as
parity would hide that the local run isn't actually checking what CI
checks.

## Adding a new lint guard

When you add a new guard step to the `lint` job in `ci.yml`:

1. Give the step an `id: guard-<slug>` (see "Report-all: one CI cycle,
   every guard's result" below for why) and `continue-on-error: true`.
2. Add the equivalent command to the `ci-lint:` recipe in the `Makefile`
   (a `$(MAKE) <target>` line if the guard already has a `make` target, or
   the same `uv run ...` / script invocation the CI step uses), followed
   by its own `[ $$? -eq 0 ] || failed="$$failed <slug>"` bookkeeping line
   -- copy the pattern of an existing guard pair in the recipe.
3. If the guard has meaningful inline logic (more than a couple of lines)
   and would otherwise live only inside the workflow YAML, extract it to a
   script under `scripts/` first and have both `ci.yml` and `ci-lint` call
   that script. Duplicating logic into the Makefile as a second
   implementation is exactly the drift this invariant exists to prevent.
4. Run `uv run -- python -m pytest tests/system/test_ci_lint_parity.py -q`.
   If it fails: either step 2 was missed, the command text doesn't match
   verbatim, or step 1's `id`/`continue-on-error` are missing or malformed
   (`test_guard_steps_follow_naming_convention` enforces the convention;
   `test_lint_job_guards_run_in_ci_lint` enforces command parity).

## Report-all: one CI cycle, every guard's result

The `lint` job runs around fifteen independent guards. Every guard step sets
`continue-on-error: true` so a failure doesn't stop the job: every guard
still runs, and each one's pass/fail is visible as its own step in the
Actions UI with its own log and timing. A PR that trips two unrelated guards
learns about both in one CI run.

**Naming convention (this is what the aggregation is keyed on, not a
hand-maintained list):** every independent guard step's `id:` starts with
the prefix `guard-` (e.g. `guard-ruff`, `guard-audit-deps`). Setup steps
(checkout, `setup-python`, `setup-uv`, install dependencies) are not
guards -- they get no `id: guard-*` and no `continue-on-error`, because a
setup failure should stop the job immediately (there's nothing meaningful
to report about guards that never ran because `uv sync` failed).

The job's last step, `lint-guard-summary`, aggregates: it reads the
Actions `steps` context (`${{ toJSON(steps) }}`, passed in via an env var
rather than interpolated directly into the script), finds every step id
that starts with `guard-`, and checks that step's `outcome` (the raw
per-step result *before* `continue-on-error` is applied -- `conclusion`
would show `success` for every failed-but-continued guard and defeat the
whole point). It writes a pass/fail table to `$GITHUB_STEP_SUMMARY` and
exits nonzero if any guard's `outcome` was `failure`. Because
`lint-guard-summary` itself has no `continue-on-error`, that nonzero exit
makes the `code-lint` job's own result `failure`. Nothing here softens the
check; it only changes *when* you find out a second guard also failed.

Deriving the guard set from the `guard-` id prefix (rather than hand-listing
step ids in the aggregator) is deliberate: a new guard added without the
prefix would otherwise run, fail, and be silently invisible to the
summary and its own `continue-on-error: true` would swallow the job-level
failure entirely. `tests/system/test_ci_lint_parity.py::test_guard_steps_follow_naming_convention`
pins the convention (every guard has the prefix + `continue-on-error`,
every non-guard step has neither), and
`test_lint_guard_summary_step_exists` pins that the aggregator step exists,
is named exactly `lint-guard-summary`, runs with `if: always()` (so it
still executes -- and reports -- after an earlier guard fails), and is the
job's last step.

`make ci-lint` mirrors the same report-all shape locally: the whole
recipe runs as one shell invocation (via `\` line continuations) with
`set +e`, running every guard command in turn and collecting failures
into a `$$failed` list instead of `make` aborting at the first nonzero
exit, then printing a consolidated `❌ FAILED guards:` list and exiting
nonzero if the list is non-empty. Guard output still streams live as each
guard runs -- nothing is buffered or captured, only the exit code is
checked after each command. Because the `ci-lint` recipe body is one
logical shell line,
the parity test's line-matching normalizes away each guard command's
trailing `; \` continuation marker before comparing it against the `ci.yml`
command text -- see `_normalize_recipe_lines` in
`tests/system/test_ci_lint_parity.py`.

If a guard genuinely cannot run locally, add it to the `EXCLUDED_STEPS` dict in the
parity test with a concrete reason -- do not silently omit it, and do not
weaken the CI guard itself so a lossier local equivalent can "pass."

## Guards `ci-lint` skips when it runs on a CI runner itself

`make ci-lint` may itself run on an ephemeral GitHub-hosted runner. Most
guards are equally meaningful there, because they inspect the checked-out
tree, the installed venv, or a registry the repo ships. A couple of guards
instead read state that only exists on a developer machine, and on a runner
would either fail for a reason unrelated to the code or pass while checking
nothing:

- `agent-identity-check` resolves `git config user.*`, which an ephemeral
  runner does not have. `agent-commit-range-check`, which reads the commits a
  branch actually carries, is the merge-time control and always runs, both in
  `ci-lint` and in `ci.yml`.
- `skill-sync-check` needs developer-local skill source checkouts. Its CI
  coverage comes from `ci.yml`'s `skill-integrity` job, which clones the
  sources and runs the full preview/apply/verify/check cycle.

The `ci-lint` recipe calls a small gate script before each of these guards
and skips the guard only when the script prints the exact token `SKIP` and
`GITHUB_ACTIONS=true`. The decision travels on stdout, not the exit status,
so a broken or missing gate runs the guard rather than silently dropping it.
A guard not listed in the gate's table always runs, on a runner exactly as it
does locally. `GITHUB_ACTIONS` is set by GitHub Actions itself and never
toggled by hand, so local runs are unaffected.

## Local preflight

`make pr-preflight` lints the changed Python files with ruff and runs the tests
mapped from the changed paths, failed tests first. The pre-push hook runs it.
Checks that run only in CI, with the command to run each locally: `ty`
(`uv run ty check`), import-linter (`make lint-imports`), comment policy
(`make comment-policy-check`), the content guard
(`make pr-preflight-fast-tests`), skill integrity
(`make skill-integrity-check`), UAT hygiene (`make uat-artifact-hygiene`); the
full fast lane is `make pr-preflight-fast-tests` and every `ci-lint` guard is
`make ci-lint`.

## `pr-preflight-fast-tests` and the content guard

`make pr-preflight-fast-tests` is the full local fast lane. It always runs
`pr-content-guard` (YAML/markdown/docs hygiene + artifact hygiene), regardless
of whether the branch's `needs-code-ci` path-filter decision is true. The
`needs-code-ci` decision gates only the fast-test pytest run. Direct invocation creates the classifier JSON and path lists when the caller did not
supply them. It runs under the shared test lock.

## Hosted-only guard inventory

`tests/system/test_ci_lint_parity.py` also checks guard-shaped steps in other
merge-gating workflows. Each such step must name a local equivalent or carry
a written reason in the test's `MERGE_GATE_EXEMPTIONS` table, so a new check
step cannot become a silent CI-only failure.

The `code-test` step that runs changed tests on the curated release tree has a
local equivalent:
`uv run -- python scripts/release_curation_dry_run.py --changed-since origin/develop`.
With no arguments it runs the full fast selection on that tree.

Bundled generator integrity has local equivalents. Run
`uv run -- python scripts/bundled_binary_manifest.py` to check the shipped
source tree. After `uv build --out-dir /tmp/benchbox-dist`, run
`uv run -- python scripts/verify_distribution_binaries.py /tmp/benchbox-dist/*.whl /tmp/benchbox-dist/*.tar.gz`
to compare both distributions with the source manifest. An installed wheel
can check its own file membership and hashes with
`python -m benchbox.utils.binary_manifest` from outside the checkout.
