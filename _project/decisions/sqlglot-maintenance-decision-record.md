# SQLGlot downstream maintenance decision and delivery evidence

This record closes `sql-transpiler-maintenance-decision`. It links the decision
documents and reviews and records what was delivered. Except for the 2026-11-05
supersession below, it makes no new decision.

## Supersession, 2026-11-05: delivery boundary

The owner moved fork refresh and wheel delivery to the fork. This section
supersedes the refresh ownership and manual adoption statements under "Refresh
and rollback ownership" and the source-review-at-core convention recorded for
fork-diff governance (tracker item `sqlglot-fork-review-h1-governance`).

Fork `BenchBox-dev/benchbox-sqlglot` owns:

- upstream discovery, deterministic refresh, the fork pull request and its
  protected merge (`.github/workflows/benchbox-upstream.yml`,
  `downstream/controller.py`; off unless `BENCHBOX_UPSTREAM_ENABLED` is set);
- source, runtime, engine and distribution verification;
- verified wheel delivery (`.github/workflows/benchbox-delivery.yml`): one
  version maps to one wheel hash, the receipt job reports
  `delivered benchbox-sqlglot <version> sha256:<hash>`, and the artifact
  `benchbox-sqlglot-delivery-a<attempt>` holds `delivery.json` (schema 1). An
  expired artifact is recovered by rerunning the canonical distribution
  workflow at the same source SHA.
- the fork writer App, which exists only in the fork. Upstream, build and
  producer exceptions are fork issues; the runbook is `downstream/MAINTAINING.md`
  in the fork.

BenchBox core only consumes the delivery:

- `.github/workflows/sqlglot-delivery.yml` and `scripts/sqlglot_delivery.py`
  verify the trusted producer origin fixed in `.github/sqlglot-delivery.json`
  and run `scripts/adopt_sqlglot_wheel.py` with the required expected version,
  fork SHA, upstream SHA and wheel hash.
- The workflow opens one App-authored adoption PR at a time and arms it only
  through the existing `scripts/pr_arm.py` rules: never after a human hold or
  disarm; only `trunk-red-disarm` disarms may be re-armed.
- A reverted pair goes in the reviewed `suppressed` list.
- `SQLGLOT_DELIVERY_ENABLED` is the kill switch; the default is dry run.
- Wrapper, install and adoption exceptions are core issues. The core App has
  contents and pull-requests write only, and core holds no fork-write
  credential once activation retires the legacy token.
- Core no longer reviews fork source at adoption. The oracle reviews the actual
  core PR as for any PR; Theseus is unchanged. Fork-side diagnostics are native
  `git diff` in the fork and the delivery record.
- Manual fallback: `make sqlglot-adopt-wheel WHEEL=<path> SHA256=<digest>
  EXPECTED_VERSION=<version> FORK_SHA=<sha> UPSTREAM_SHA=<sha>`.

Legacy and retiring until activation removes them: the core
`.github/workflows/sqlglot-fork-refresh.yml`, `scripts/sqlglot_fork_refresh.py`,
`SQLGLOT_FORK_REFRESH_TOKEN`, `make sqlglot-fork-diff` and the PR-template
fork-diff checkbox. `scripts/check_sqlglot_upstream_freshness.py` and
`scripts/check_sqlglot_advisories.py` stay release policy, not adoption
orchestration.

## Decision documents

- Distribution plan: `_project/decisions/sqlglot-soft-fork-plan.md`.
- External plan reviews and every accepted, narrowed and rebutted finding
  disposition (Muse M1-M17, Antigravity A1-A13, and the wheel-only delivery
  amendment): `_project/decisions/sqlglot-soft-fork-review.md`.
- Transpilation ownership plan: `_project/decisions/sqlglot-fork-transpilation-plan.md`.
- ADR: `docs/development/adr/adr-sqlglot-fork-owns-transpilation.md`, which
  supersedes in part `adr-sqlglot-use-and-non-use.md` and
  `adr-sql-compat-phase-aware-pipeline.md`.

## Selected maintenance boundary

The owner selected a maintained, permanent soft fork,
`BenchBox-dev/benchbox-sqlglot`, that owns SQL transpilation. The reviewed plan
replaced the earlier undecided SQLGlot-versus-Polyglot comparison as the
adoption gate.

- BenchBox ships only the pinned pure-Python runtime wheel, loaded from package
  data as `benchbox._vendor.sqlglot`. There is no PyPI fork project, Git
  dependency, unpacked source tree or runtime download.
- BenchBox calls the fork through `translate_sql_query` and the wrapper functions
  beside it (`translate_ddl`, `apply_rewrite`). Benchmark definitions, execution,
  validation, DataFrame translation, governance metadata and the
  platform-to-dialect mapping stay in BenchBox.
- A fork change reaches users only through a BenchBox release. Publishing a
  BenchBox release stays a separate owner-authorized action.

## Delivered revisions

| Item | Evidence |
| --- | --- |
| Initial wheel-only adoption (`sqlglot-soft-fork-adoption`) | BenchBox PR #3139, merge `85524edddddb5ef53788665f09fc18b9949219e2` |
| Fork tooling and private wheel build (`sqlglot-soft-fork-source`, `-distribution`, `-publish`) | Fork PRs #1 to #4 (`BenchBox-dev/benchbox-sqlglot`) |
| Ownership plan and ADR | BenchBox PRs #3180, #3198, #3203 |
| Fork-diff and soundness-path governance | BenchBox PR #3345 |
| Runtime adoption of fork 30.25.0.1 | BenchBox PR #3587, merge `432934279005ea25ed000125358b5e0937aa08fd` |
| Current runtime (fork 30.27.0.1 on upstream v30.27.0) | Fork PR #79 (`BenchBox-dev/benchbox-sqlglot`), merge `1ba4aec7bf6cbca5ec3b5a7cc4cf916aca02aa96` |
| Freshness waivers for upstream v30.25.0, v30.26.0 and v30.27.0 (each removed by the adoption that supersedes it) | BenchBox PRs #3586, #3740, #3807 |
| Advisory visibility gate | BenchBox PR #3453, merge `818268a2b7c92f6490698d845432efe2787f4c8d` |

Current release identity, from `benchbox/_vendor/sqlglot.json`:

- fork version `30.27.0.1` (upstream `30.27.0`, fork revision 1);
- fork commit `1ba4aec7bf6cbca5ec3b5a7cc4cf916aca02aa96` (fork PR #79);
- upstream commit `98782638dd6dbdedebad0e6ff6edc6e69598b81e`;
- wheel `benchbox_sqlglot-30.27.0.1-py3-none-any.whl`, SHA256
  `9698de8f6f1e225d885eb1da245d6360abaea8417af927ecc35fc353ed73c5f2`.

Earlier refreshes (upstream v30.24.0 and v30.25.0) and the adoption slices A1 to
A5 are recorded in the tracker as done, with the BenchBox PRs in the git history.

## Refresh and rollback ownership

- The BenchBox maintainer owns refreshes. The fork runbook is
  `downstream/MAINTAINING.md` in the fork: replay carried patches onto an
  explicit newer upstream tag in a disposable candidate, stop on conflicts, then
  merge upstream ancestry into a descendant integration branch.
- Freshness and advisory gates enforce the cadence:
  `scripts/check_sqlglot_upstream_freshness.py` (a newer upstream tag fails until
  the fork is refreshed or an owner-reviewed, expiring waiver is added to
  `scripts/sqlglot_refresh_waiver.json`) and
  `scripts/check_sqlglot_advisories.py` (no waiver). Both run in
  `make release-check`, the release workflow and `release-readiness`.
- Adoption runs `make sqlglot-adopt-wheel WHEEL=<path> SHA256=<digest>`, which
  updates the pin and the runtime baseline together and restores them on failure.
  See `docs/internal/release-guide.md`.
- Rollback reverts the adoption pull request, restoring the previous wheel, pin
  and baseline together. The initial rollback restored the pre-migration imports
  and public SQLGlot 30.18.0; its drill passed (plan, "Measured results"). Neither
  rollback rewrites immutable fork refs.
- An empty carried patch set triggers an owner decision on retiring the fork,
  never an automatic namespace change.

## Unfinished research

`sql-transpiler-comparison-evidence` was finished (PR #3596). The Polyglot
research items remain open and are not resumed by this record:
`polyglot-benchbox-integration-assessment` and
`polyglot-source-aware-division-spike`. Neither is claimed complete, and
neither gates the fork. Public publication of any artifact still requires explicit owner
approval of the concrete artifacts.
