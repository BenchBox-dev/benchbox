# Development-loop retirement audit

Evidence date: 2026-11-07. Source tree: `b65307b83eada4e6f2b76d9e1e393a48970bf578`.
The safety-property ledger remains authoritative. This audit does not waive its
`release-canary-freshness` blocker or certify release readiness.

## Retirement provenance

The search set comes from removed and renamed files and modified interfaces in
merged pull requests, not from the earlier proposed deletion lists.

| Retired capability or interface | Source | Disposition |
| --- | --- | --- |
| Original PR, post-merge, queue-shadow, gap-detector and publication-lane workflows | [Unit-based CI retirement](https://github.com/BenchBox-dev/benchbox/pull/2450) | Required units and Trunk replace the validation paths; historical run records remain. |
| PR ruleset-drift and distribution jobs, merge-group wiring | [CI job retirement](https://github.com/BenchBox-dev/benchbox/pull/2582) | Release admission, canary and Trunk retain the safety properties; the drift token is not retired. |
| Automatic merge-on-open workflow and soundness helper | [Automatic merge retirement](https://github.com/BenchBox-dev/benchbox/pull/2650) | Exact-head arming, the required hold check and App-bound review gate remain. |
| Core site deployment and publication control plane | [Publication scheduling retirement](https://github.com/BenchBox-dev/benchbox/pull/2671), [core site removal](https://github.com/BenchBox-dev/benchbox/pull/3096), [landing-unit removal](https://github.com/BenchBox-dev/benchbox/pull/3104), [tooling-reference cleanup](https://github.com/BenchBox-dev/benchbox/pull/3105), [historical-page cleanup](https://github.com/BenchBox-dev/benchbox/pull/3106) | The site repository owns deployment. Core retains site-inputs attestations and submission validation. |
| Original nightly and cross-platform workflows and slow-test report | [Nightly ports](https://github.com/BenchBox-dev/benchbox/pull/3627), [workflow retirement](https://github.com/BenchBox-dev/benchbox/pull/3637), [historical-run lookup repair](https://github.com/BenchBox-dev/benchbox/pull/3733) | Nightly T3 retains the accepted domains and deliberately reads retired nightly runs for throughput baselines. |
| Obsolete replay, queue-certification, gap-detection and process-report scripts and tests | [First script retirement](https://github.com/BenchBox-dev/benchbox/pull/3676), [remaining script retirement](https://github.com/BenchBox-dev/benchbox/pull/3688) | Live callers were migrated. The PR metrics collector, review follow-ups, timing policy and fast-duration guard remain. |
| Tox configuration, unused markers and duplicate-code hook helper | [Configuration cleanup](https://github.com/BenchBox-dev/benchbox/pull/3739) | Coverage-specific pytest configuration and fail-closed path routing remain in use. |
| Legacy release workflows, release-cut scripts and branch expectations | [Legacy release retirement](https://github.com/BenchBox-dev/benchbox/pull/3961), [settings cleanup](https://github.com/BenchBox-dev/benchbox/pull/3963) | Tag-triggered release admission remains; the canary-freshness evidence gap is not waived. |
| Generated Makefile inventory and redundant target wrappers | [Inventory replacement](https://github.com/BenchBox-dev/benchbox/pull/4008), [test-family consolidation](https://github.com/BenchBox-dev/benchbox/pull/4010), [equivalence-report consolidation](https://github.com/BenchBox-dev/benchbox/pull/4011), [module extraction](https://github.com/BenchBox-dev/benchbox/pull/4014), [remaining parameterized families](https://github.com/BenchBox-dev/benchbox/pull/4022) | The documented-target check replaces the generated inventory contract. Historical monolith evidence remains unchanged. |
| Queue-canary runbook, duplicated push-drop inventory and obsolete restoration instructions | [Maintainer documentation cleanup](https://github.com/BenchBox-dev/benchbox/pull/4030) | Current Trunk recovery and PR-base policy are retained. |

For file-level provenance, query each source with:

```bash
gh api --paginate 'repos/BenchBox-dev/benchbox/pulls/2450/files?per_page=100' \
  --jq '.[] | select(.status == "removed" or .status == "renamed") | [.filename, .previous_filename]'
```

The site-removal change includes 1,231 files. Its workflow, publication-script and
test removals were inspected; the audit does not claim an individual semantic
review of every removed site asset. The documentation-ownership check separately
requires site-owned pages and generators to be absent from core.

## Remaining callers and intentional history

The audit found and corrected these live references:

- `make dev-loop-metrics` queried the deleted `develop-post-merge.yml`, suppressed
  retrieval errors and could print zero artifacts and runner minutes. It now
  invokes the retained merged-PR collector directly. Use `make ci-usage-report`
  for current all-attempt runner usage and post-merge signals.
- The cross-surface baseline workflow's generated PR body named removed
  per-benchmark report targets. It now gives
  `make cross-surface-equivalence-report CROSS_SURFACE_BENCHMARK=<gate>`.
- The tag-protection diagnostic named the deleted release workflow and the old
  branch ancestry. It now identifies the human-controlled tag-creation boundary
  for `release-v2.yml`; no enforcement predicate changed.
- Maintainer runbooks no longer instruct operators to restore the retired TODO
  export secret or dispatch the retired quarterly report workflow. The quarterly
  review is manual, and its strategy link points to the retained internal page.

These references are intentional and must not be treated as active callers:

| Location | Reason to retain |
| --- | --- |
| `nightly-v2.yml` lookup of retired `nightly.yml` runs | Reads retained scheduled throughput baseline artifacts; it does not dispatch the deleted workflow. |
| `make/monolith-baseline.json`, `make/migration-proof.json` | Historical interface and migration evidence. |
| Dated records under `_project/decisions`, `_project/audits`, `_project/blind-spots`, baseline data and tracker exports | Record the tree, commands and decisions that existed when captured. The dated oracle-extraction inventory's old comment-scope command is such an inventory, not an executable instruction source. |
| `CHANGELOG.md`, duration-calibration fixtures and cross-surface divergence analyses | Release history and captured measurement data, not current Make callers. |
| Negative workflow-identity tests and forbidden-secret tests | Assert rejection of retired release identities and secret names. |
| Historical publication, credential and tracker-extraction runbooks | Explicitly marked historical; they do not describe the current control plane. |
| Nightly UAT template's retired-workflow header and registered comment-policy exception | Accepted historical header; the actual scheduled caller is Nightly T3. |

`RELEASE_READINESS_OVERRIDE_SHA` and `RELEASE_READINESS_OVERRIDE_REASON` are not
retired source-level interfaces: the canary still accepts them, while the tag
release explicitly unsets them. The repository-variable name inventory contained
neither on the evidence date. No variables or secrets were changed by this audit.
The live incident-label search returned only `incident:release-canary-red`.

## Workflow and safety-property coverage

The retained workflow inventory is the `.github/workflows/` table in
[the property ledger](dev-loop-property-ledger.md). It covers all 42 workflow
files, including the previously missing `ci.yml` row, and names the accountable
operational roles. Purpose and safety classification stay beside each file
rather than in a second inventory.

Every existing HIGH-RISK row other than release readiness is recorded as proven;
PROCESS-ONLY rows have explicit waiver reasons. Those proofs retain their named
source revisions and do not become fresh live ruleset evidence merely because
this audit references them.

Release readiness is still blocked: the retired path required the newest canary
to be green, under 48 hours old and on an ancestor of the release head, while the
current tag-release workflow does not enforce that freshness condition. A manual
check is not equivalent evidence. Close this blocker only after the automated
release-evidence check is merged and exercised, or after an explicit owner waiver
is recorded in the ledger.

## Measurement snapshot

The baseline is [the 2026-09-28 capture](../../_project/archive/dev-loop-v2/baseline.md)
at `64f2f1f603083fc3f6b9c3b13d12ce73f97e6fd3`. It recorded 216 targets from the
retired generated inventory. Re-running the specified Make database method on
that exact commit gives 211, so 211 is the comparable target-count baseline.
The later capability-preserving consolidation decision supersedes the original
90-target goal; it does not justify deleting a retained capability to meet a count.

| Metric | Historical baseline | Audit observation |
| --- | --- | --- |
| Public Make targets, same database method | 211 | 190 |
| Retained workflow files | 46 | 42 |
| T1 CI job p95, including job setup | 1,458 s | 669 s; n=2, cleanup-only cohort, not a trailing-30-day measurement |
| T2 CI job p95, collector through last shard | 1,761 s | 980 s; n=2, the same cleanup-only cohort |
| Merges per day | 401 merged PRs / 30 days | Unavailable: no current merged-PR cohort result is asserted. |
| PR CI run-level retry rate | No comparable all-attempt denominator captured | Unavailable from the legacy report; requires the all-attempt usage report. First-pass green rate is not retry rate. |
| Red-Trunk incidents | Historical report says zero, using queue failures and incident labels | Not measured by the legacy collector. Count failed `develop` push attempts until recovery using current Trunk history, not incident-label absence. |

The timed-out collector source is Git blob
`bea8ba830091645bf956133498d70aad059011cf`, available at the audit source revision.
No missing measurement above is a zero or a threshold pass.

The bounded timing cohort consists only of successful first-attempt full CI runs
on the final merged heads of the two cleanup changes:

- [Makefile consolidation run 42393941936](https://github.com/BenchBox-dev/benchbox/actions/runs/42393941936),
  head `5f358660623fdfb9acacb147f9c0c917277d27a0`.
- [Documentation cleanup run 42398430449](https://github.com/BenchBox-dev/benchbox/actions/runs/42398430449),
  head `8f0e4706427625dbbdcf39d2717e95890d0ed7ca`.

The authenticated Actions API supplied attempt-1 job timestamps. T1 selects the
single successful `test (ubuntu-latest, 3.14)` job, including setup. T2 measures
from `medium-collect` start through the last of all four shard completions.
The existing `_pct` and `_partitioned_medium_seconds` helpers at
`ec29389e7e8f6492c39cce84f799e5273d47287e` compute the two distributions.
This deliberately narrow cohort cannot certify a change against the 30-day
baseline. Its observed p95s exceed the nominal seven- and fifteen-minute goals.

The full 30-day timing cohort remains unavailable: the legacy collector timed out
after 3,600 seconds without a snapshot, and its fast-job selector still names
Python 3.12. Neither the timeout nor the bounded observation is a substitute for
the separate 30-day comparison.

The specified record-based Make database filter and the maintained
`scripts.check_make_targets.targets` parser both returned 211 and 190. To
reproduce the count, run the current parser from the audited checkout, passing
the path to either the baseline or audited checkout:

```bash
uv run -- python -c 'import sys; from pathlib import Path; from scripts.check_make_targets import targets; print(len(targets(Path(sys.argv[1]))))' /path/to/checkout
```

The parser invokes `make -pRrq :`. The deliberate nonexistent `:` target makes
Make exit 2 after emitting its database; the parser accepts that expected result.

For resumable current usage data, keep raw output and cache outside the repository:

```bash
make ci-usage-report \
  CI_USAGE_SINCE=2026-10-09 CI_USAGE_UNTIL=2026-11-07 \
  CI_USAGE_OUTPUT=/tmp/dev-loop-ci-usage-20261107.json \
  CI_USAGE_ARGS='--repo BenchBox-dev/BenchBox --cache-dir /tmp/dev-loop-ci-usage-cache --workers 4'
```

The report's `window` supplies actual UTC bounds; its end is capped at collection
time. Record its completeness and missing fields before deriving a rate. For PR
CI, define retry rate as rerun runs divided by runs in the `ci.yml` /
`pull_request` cohort. For Trunk, use the report's `develop` push incident groups,
including failed attempts before later success. Do not infer T1/T2 p95 from total
runner minutes or from a single successful run.

The separate 30-day comparison starts after the last cleanup merge, not after the
baseline capture or the first successful audit command. It cannot be certified
before that observation window has elapsed.
