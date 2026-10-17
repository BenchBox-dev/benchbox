# Quality gate policy

Status: active

Owner: quality governance

Measured: 2026-08-08; quality baseline `d649c027f0`, duplicate correction recheck `4e25e77b1a`

## Decision

BenchBox quality gates differ on purpose. Each must state its scope, its blocking
and advisory thresholds, and which CI jobs run it. One gate passing does not prove another ran.

| Gate | Command and scope | Fails when | Advisory visibility | Ignored or excepted | Reproducible baseline | Required in |
| --- | --- | --- | --- | --- | --- | --- |
| Configured Ruff lint | `uv run ruff check .`; tracked Python from the repository root | Any enabled rule fires. C901 only when `CC > 18`. | None; every selected diagnostic fails. | 34 global rule ignores, 5 per-file entries, and 8 discovery exclusions. `_project` is **not** excluded. | 0 diagnostics | Develop PR `guard-ruff`, release lint/test, nightly, and `make ci-lint` |
| Ruff format | `uv run ruff format --check .`; configured discovery scope | Any formatting diff | None | Same discovery rules as configured Ruff | 0 files | Same workflows as configured Ruff |
| Test coverage | Fast suite with `--cov=benchbox` in `make ci-test`, `test.yml`, and `ci.yml` | Suite-wide coverage below 70% (`--cov-fail-under=70`); 70 is the blocking CI floor. | `tests/conftest.py` warns below 80%. The 80% threshold is intentionally advisory. | None | Measured on each run | `make ci-test`, `test.yml`, `ci.yml` |
| Custom complexity policy | `make complexity-check`; isolated Ruff C901 at threshold 1 over `benchbox` | Unexcepted `CC > 18`. Invalid, stale, unowned, drifted, overlong, or expired exception metadata always fails, even with `--no-fail`. Also a missing policy or target, or empty or unparseable measurements. | Every `12 <= CC <= 18` score is printed, including targets of any future hard exception. | 0 current hard exceptions | 7,486 functions with `CC > 1`; 277 advisory; 0 hard; max CC 18 (remeasured 2026-10-17 at `1efc892a`) | Develop PR `guard-complexity-policy` and `make ci-lint` |
| Repository ty pass | `uv run ty check`; configured `benchbox` and `tests` scope | A configured error rule fires | Warnings are printed without failing | 7 globally ignored rule families | 641 warnings, 0 errors | Develop PR, release lint/test, nightly, and `make ci-lint` |
| Quality-governance strict type island | `make quality-governance-typecheck`; only `_project/scripts/check_complexity.py` | Any rule (`ty check --error all`) | None | No architecture or production modules; their existing TODOs own them | 0 diagnostics | Develop PR `guard-quality-governance-typecheck` and `make ci-lint` |
| Duplicate delta | `make duplicate-check-delta`; AST Type-2 clones in `benchbox` versus PR merge-base | Any increase in duplicated lines | Full changed-group report on failure | 2 excluded path patterns, 6 global function names, 38 path-scoped rules | Merge-base 6,198/282 groups; branch 6,187/281; **-11, passing** | Develop PR `guard-duplicate-delta` and `make ci-lint` |
| Duplicate absolute | `make duplicate-check`; same AST scanner | More than 8,073 duplicated lines, when run | Report, always | Same duplicate scanner configuration | 6,187 of 8,073 | Local/campaign only; not required CI |

"Required" means the gate feeds a required workflow result, not just that a
command exists. The develop PR lint job uses `guard-*` step IDs,
`continue-on-error`, and the fail-closed `lint-guard-summary`; `make ci-lint`
mirrors each command, and the parity test rejects any CI command missing from
`make ci-lint`.

## Implementations and consumers examined

This policy is based on how these seven files behave, not on their comments
alone:

1. `pyproject.toml`: `[tool.ruff]`, `[tool.ruff.lint]`, and the exact
   `ruff==0.16.7` dev dependency set discovery and rules.
2. `Makefile` targets `lint`, `ci-lint`, `complexity-check`,
   `quality-governance-typecheck`, `duplicate-check`, and
   `duplicate-check-delta` define local runs and report-all behavior.
3. `.github/workflows/ci.yml` defines required develop-PR jobs and steps and the duplicate
   delta base SHA.
4. `.github/workflows/lint.yml`, `test.yml`, and `nightly.yml` run configured
   Ruff and ty for releases and compatibility. They do not imply the `_project`
   complexity checker exists on curated release branches.
5. `_project/scripts/check_complexity.py` measures custom scores in isolation,
   checks Ruff's concise-output count, and owns the temporary hard-exception
   lifecycle.
6. `scripts/check_duplicate_code.py` defines absolute and merge-base delta clone
   semantics, deliberately with one scanner configuration.
7. `tests/system/test_ci_lint_parity.py` guards workflow and Makefile commands
   against drift; `tests/unit/scripts/test_check_complexity.py` pins quality
   configuration and required CI wiring.

## Authority and legacy artifacts

Only `make complexity-check`, `make complexity-report`, and their shared
implementation `_project/scripts/check_complexity.py` define complexity
policy. The old `scripts/check_complexity.py` copy was removed because it
parsed retired configuration and created a second authority.
`quality/complexity/classification-matrix.yaml` is historical and gates
nothing; do not update it as if its entries were active exceptions.

The complexity targets and every other retained Make recipe that runs code
under `_project/` work only in the development tree. A curated release keeps
their names for a stable Make interface, but they fail there with one shared,
explicit development-tree message. Shipping the checkers was rejected: they check
development governance and would enlarge shipped tooling without adding runtime
capability.

## Ruff scope: configured discovery versus explicit paths

Configured discovery once excluded `_project`, so `uv run ruff check .`
skipped it. With Ruff's `force-exclude` false, explicitly passing
`_project/scripts` still linted it: exactly two lint diagnostics, plus five
files in a separate format check. Both were fixed, then the exclusion removed.

The configured check now passes. `_project` holds 62 tracked Python files;
Git ignores the local `_project/scripts/.venv`, and Ruff's
`respect_gitignore = true` keeps it and its vendored packages out of discovery.
No generated or vendor ignore, rule ignore, or per-file suppression was added.

Ruff is pinned exactly because `preview = true` can enable new behavior on
upgrade. The existing required `uv-lock-revision-check` fails a `pyproject.toml`
dependency change without its `uv.lock` update. To upgrade Ruff:

1. Change the exact dev requirement with `uv add`.
2. Review every newly activated diagnostic.
3. Commit manifest, lock, and fixes together; roll them back together.

Never pair a new pin with an old lock, or suppress new rules to get a pass.

## Complexity measurement and exceptions

Per-function McCabe and file cloc are hygiene; they do not measure the cost
of adding a SQL platform, DataFrame platform, or benchmark family. Architecture
reviews use that extension cost as the main complexity metric
(`docs/agent/review-protocol.md`). This gate only sets the hard CC ceiling and
advisory band.

All 152 former function exclusions were remeasured against current Ruff C901
output before removal:

| Classification | Count | Decision |
| --- | ---: | --- |
| Exceeds custom hard ceiling (`CC > 20`) | 0 | No exception is justified. |
| Advisory (`12 <= CC <= 20`) | 84 | Remove the exclusion; keep the score visible. |
| Below advisory (`2 <= CC < 12`) | 44 | Remove stale metadata. |
| Target absent or no longer reported at threshold 1 (`CC <= 1`) | 24 | Remove stale metadata. |
| **Total measured** | **152** | **No current exception retained.** |

Configured Ruff and the custom checker are separate gates and must not be
conflated. Configured Ruff
uses `max-complexity = 18` and fails on `CC > 18` within configured discovery.
The custom checker uses `max_complexity = 18`, fails on `CC > 18` in
`benchbox`, and also governs exception metadata. It runs the installed pinned
Ruff with `--isolated` and only `C901`, threshold 1, and concise output,
ignoring `pyproject.toml` Ruff selects, ignores, per-file ignores, excludes,
and preview settings. So a project-level C901 ignore cannot hide a custom hard
score, and a custom exception does not suppress configured Ruff C901.

The checker still reads its policy from `pyproject.toml`: the file,
`[tool.benchbox.complexity]` table, thresholds, `max_exception_days`, and
`exclusions` key are required even when a command-line option overrides a
threshold. Ruff return-1 output must hold only parseable C901
diagnostics and exactly one matching `Found N errors.` summary. The check fails
closed on unexpected output, relevant stderr, a missing scan root, or zero
measurements under the authoritative source root.

If a function genuinely requires a temporary custom hard exception, add an
array-table entry, not a string allowlist:

```toml
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/path/module.py:function_name"
line = 123
score = 21
owner = "owning-workstream"
rationale = "Why the branching is currently irreducible and what removes it."
expires = "2026-09-01"
```

The target, line, and score must match exactly one current Ruff measurement
above 20. `max_exception_days = 90` bounds every review window: the expiry must
be after today and at most 90 calendar days away. `make complexity-check` fails
on a missing owner or rationale, an expiry outside that window, a moved target,
a changed score, or a score no longer above 20.

`--no-fail` hides unexcepted score failures for reporting only, never metadata
failures. Remove or renew an expired entry only after remeasurement and owner
review, never by raising the ceiling or choosing a distant expiry.

To roll back a checker defect, revert the checker, its Make targets, both
required PR steps, its tests, and this policy together. Never make CI pass by
deleting only the required step, switching it back to report-only, or raising
`max_complexity`.

## Duplicate gate decision

The delta gate stays the required PR check. It asks whether the branch added
duplicated lines versus the merge-base, without accepting today's total as
permanent. The absolute 8,073 ceiling stays a local and campaign safety
check; as the only required gate, it would allow 1,886 more duplicated lines
above the measured 6,187.

The first measurement correctly failed at `6,198 -> 6,199` (`+1`). Governance
did not rebaseline, make a check report-only, or add an ignore. The cause was a new +12 clone group between
`DataFrameQueryRuntime.load_table` and the dataframe benchmark mixin, offset by
an unrelated -11 `_attach_applied_tuning_ledger` removal. The owning adapter
fix `4e25e77b1a` narrowed the protocol method and removed the +12 group.

After that fix, `6,198/282 -> 6,187/281` (`-11`): both gates pass. The earlier
failure shows the delta gate caught a real regression that governance did not
weaken or absorb.

## Cross-surface known-divergence baseline

`benchbox/core/equivalence/cross_surface_baseline.yaml` lists cells the
cross-surface SQL and DataFrame equivalence gates tolerate. Each
`<gate>: {<query>_<backend>: <reason>}` entry records one understood, verified,
non-discriminating presentational difference. It is not a general ignore list:
an entry mutes regressions on that exact cell.

The gate fails when an entry stops reproducing, so a stale entry cannot hide a
different regression on the same key. Prune with
`make cross-surface-update-baseline BENCHMARK=<gate>`, which removes only
entries that no longer reproduce and refuses to write if the run has any other
failure. Commit the pruned file in a reviewed change.

Entries that need an executable acceptance predicate, such as a
`ClassifiedDivergence` pinned to a column position, numeric bound, or error
text, stay in `benchbox/core/equivalence/cross_surface.py`. The `h2odb`,
`read_primitives`, and `clickbench` gates merge their section of the file with
those in-code entries. Sections are keyed by gate name. The file is now empty
(`{}`): no gate tolerates a baseline-only divergence.

## Rejected alternatives and deferrals

| Alternative | Why rejected |
| --- | --- |
| Keep all 152 complexity exclusions and add metadata | None currently exempts a hard failure; metadata would be ceremony and keep 84 advisory scores hidden. |
| Make every complexity advisory a hard failure | 180 functions are advisory today; that is architecture work, not honest governance-only enforcement. |
| Lower the custom ceiling from 20 to 18 | Configured Ruff already fails `CC > 18`; repeating it would blur the separate metadata and scanning contract without adding coverage. |
| Make all 641 repository ty warnings hard | It would pull in production and architecture islands owned by earlier TODOs, breaking the governance-only boundary. |
| Add `_project` per-file Ruff ignores | The explicit scan found only two fixable diagnostics; fixing them passed the configured scope without weakening rules. |
| Replace required duplicate delta with the absolute ceiling | Absolute headroom is too large to stop a PR regression; it would have hidden the first measured +1. |
| Rebaseline duplicate absolute to the current 6,187 | Delta already passes at -11; lowering the campaign ceiling without a fresh clone-classification campaign would pin an arbitrary snapshot. |

Architecture complexity reductions, production strict-type islands, and
duplicate consolidation stay with their owning TODOs. This policy neither
changes production behavior nor claims those deferrals complete.
