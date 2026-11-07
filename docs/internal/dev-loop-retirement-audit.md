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
| T1 CI job p95, including job setup | 1,458 s | 943 s; n=43 successful, unretried CI runs in the bounded cohort below |
| T2 CI job p95, collector through last shard | 1,761 s | 1,165 s; n=43, the same bounded cohort |
| Merges per day | 401 merged PRs / 30 days | 45 merged `develop` PRs / 1 day |
| PR CI run-level retry rate | No comparable all-attempt denominator captured | 39 of 140 runs, or 27.9%; reruns are not necessarily flakes |
| Red-Trunk incidents | Historical report says zero, using queue failures and incident labels | 3 incident groups opened in the bounded window; all recovered within it. Carry-in red state is not measured. |

### Current bounded cohort

The current snapshot covers runs created from `2026-11-06T18:00:00Z` through
`2026-11-07T18:00:00Z`, inclusive. Authenticated API retrieval completed on
2026-11-07 at approximately 19:09 UTC. This is a one-day current snapshot, not
the separate post-cleanup 30-day comparison.

The workflow-specific listings were complete: 140 `ci.yml` / `pull_request`
runs and 45 `trunk.yml` / `push` / `develop` runs. Pagination returned the same
number of unique run IDs as each endpoint's `total_count`, below the 1,000-run
search cap. The merged-PR search returned all 45 results with
`incomplete_results=false`.

Reproduce the cohort queries with:

```bash
gh api --paginate --slurp \
  'repos/BenchBox-dev/benchbox/actions/workflows/ci.yml/runs?event=pull_request&created=2026-11-06T18:00:00Z..2026-11-07T18:00:00Z&per_page=100'
gh api --paginate --slurp \
  'repos/BenchBox-dev/benchbox/actions/workflows/trunk.yml/runs?event=push&branch=develop&created=2026-11-06T18:00:00Z..2026-11-07T18:00:00Z&per_page=100'
gh api --method GET search/issues \
  -f q='repo:BenchBox-dev/benchbox is:pr is:merged base:develop merged:2026-11-06T18:00:00Z..2026-11-07T18:00:00Z' \
  -f per_page=100
```

The retry numerator counts listed CI runs whose observed `run_attempt` exceeds
one; the denominator includes all listed CI runs, including failures and
cancellations. Attempts are observed at retrieval time, not frozen at the cohort
end. Separate replacement runs do not count as reruns. This is a run-level retry
proxy, not a measured flaky-test rate.

Timing selects the 43 CI runs whose latest conclusion was success and
`run_attempt` was one. Every selected run had a complete attempt-1 jobs response,
one successful `test (ubuntu-latest, 3.14)` job and all five medium jobs.
T1 uses that fast job's start and completion timestamps, including setup.
T2 uses `medium-collect` start through the last of all four shard completions.
The existing `_elapsed_seconds`, `_partitioned_medium_seconds` and `_pct`
helpers at `d408b43bd1b0a89953ed6d4b3b4f0d3f22a67864` derive the durations and p95.
Failed, cancelled and rerun CI runs are excluded from the timing distributions.
Both observed p95s exceed the nominal seven- and fifteen-minute goals.

The selected run IDs are:

```text
42276330949, 42291373929, 42294001933, 42297773394, 42298302467, 42300885453, 42302973276, 42303066477
42314865320, 42321241073, 42321630334, 42322831612, 42324358336, 42324641405, 42327299667, 42327397198
42328071196, 42329613436, 42329707372, 42330740235, 42331963709, 42333488467, 42333658561, 42335435960
42341229787, 42342740969, 42343459298, 42344340347, 42345646964, 42347149599, 42351845692, 42356878831
42358981121, 42384221820, 42389122473, 42389759390, 42389955577, 42393293583, 42393941936, 42394716919
42396595088, 42396615190, 42398430449
```

Retrieve each selected run's jobs with
`gh api --paginate --slurp repos/BenchBox-dev/benchbox/actions/runs/<id>/attempts/1/jobs?per_page=100`.

Trunk uses each run's latest metadata plus every earlier attempt's metadata
from `actions/runs/<id>/attempts/<number>`. The existing `run_facts`,
`_trunk_outcomes` and `_trunk_incidents` helpers at the same revision group the
33 conclusive attempt results whose completion timestamps fall inside the
window. No job-duration data is needed for this incident calculation.

| First red run | Opened, UTC | Recovered, UTC |
| --- | --- | --- |
| [42366385936](https://github.com/BenchBox-dev/benchbox/actions/runs/42366385936) | 2026-11-07 11:14:22 | 2026-11-07 14:34:18 |
| [42366385936](https://github.com/BenchBox-dev/benchbox/actions/runs/42366385936), later attempt | 2026-11-07 14:44:48 | 2026-11-07 15:30:54 |
| [42390745012](https://github.com/BenchBox-dev/benchbox/actions/runs/42390745012) | 2026-11-07 16:40:59 | 2026-11-07 16:59:02 |

This counts groups opening among the observed results, not red state carried
into the window. It does not use the absence of incident labels as evidence.

### Full-cohort collection limits

The legacy 30-day collector timed out after 3,600 seconds without a snapshot;
its fast-job selector still names Python 3.12. Its source is Git blob
`bea8ba830091645bf956133498d70aad059011cf` at the audit source revision.
The all-workflow usage report below also timed out after 3,600 seconds, after
reporting 3,000 of 36,479 runs processed. Its resumable response cache remains
outside the repository. Neither timeout is a zero, a threshold pass, or a
substitute for the separate 30-day comparison. The bounded snapshot above does
not claim complete runner usage, required-gate timing or flaky-test attribution.

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
