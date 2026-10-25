# Nightly perf gate: sensitivity review

**Audience:** maintainers who own the `t3:perf` nightly domain or the Release
Performance Smoke check, and anyone deciding whether to tighten them.
**Scope:** what the DuckDB TPC-H SF=0.01 comparison can and cannot detect today.
It records detection limits found by an adversarial review after the
decimal-boundary fix, with evidence from the code and the checked-in baseline.
It does not change behaviour. The unresolved hosted 640 ms result is a separate
investigation and is not assessed here.

How the gate is built, how the floors were derived and how to refresh the
baseline are in [perf-smoke.md](perf-smoke.md). Domain labels and issue
handling are in [nightly-t3.md](nightly-t3.md).

## What the gate checks

`.github/workflows/nightly-v2.yml` (`perf` job) and
`.github/workflows/perf-smoke.yml` run one DuckDB TPC-H SF=0.01 power phase
(3 measured iterations after one warmup) and then:

```
benchbox compare _project/baselines/perf_smoke_duckdb_tpch_001.json <result> \
  --fail-on-regression 10% --min-regression-delta 7ms --min-aggregate-regression-delta 82ms
```

`_check_regression` in `benchbox/cli/commands/compare.py` returns a failure when
**any** of these holds:

- `total_execution_time` or `average_query_time` changed by more than 10% **and**
  by more than 82 ms (`_metric_slowdown_exceeds`).
- A query changed by more than 10% **and** by more than 7 ms
  (`_query_slowdown_exceeds`).

Both inputs come from `ResultExporter.compare_results`
(`benchbox/core/results/exporter.py`): the two summary timings
(`summary.timing.total_ms`, `avg_ms`) and one time per query id taken by
`get_query_map`, which keeps the **last** entry per id. In the baseline that is
the third measured iteration of each of the 22 queries, ranging 4.4 to 12.3 ms
(sum 183.6 ms); the summary total of 547.4 ms sums all 66 measured executions.

Everything else in the result, including `run.total_duration_ms` (1728 ms in the
baseline), `phases.*.duration_ms`, warmup entries, per-query `status`, and
`environment.cpu_model`, is outside the comparison.

## Reproducing the measurements

Every figure below comes from mutating a copy of the checked-in baseline and
running the real `benchbox compare` command with the gate options above. Per
case: copy the baseline, apply the mutation to the `queries` entries (every
iteration of the named query unless stated), recompute `summary.timing.total_ms`
and `avg_ms` from the measured entries, run the command, and read the exit code.
Exit code 1 means the gate fired; 0 means it passed.

```python
import copy, json, subprocess
base = json.load(open("_project/baselines/perf_smoke_duckdb_tpch_001.json"))

def gate(mutate):
    doc = copy.deepcopy(base)
    mutate(doc)
    measured = [q for q in doc["queries"] if q["run_type"] != "warmup" and q["ms"] is not None]
    total = sum(q["ms"] for q in measured)
    doc["summary"]["timing"].update(total_ms=round(total, 1), avg_ms=round(total / len(measured), 1))
    json.dump(doc, open("/tmp/current.json", "w"))
    return subprocess.run(
        ["uv", "run", "--", "benchbox", "compare", "_project/baselines/perf_smoke_duckdb_tpch_001.json",
         "/tmp/current.json", "--fail-on-regression", "10%",
         "--min-regression-delta", "7ms", "--min-aggregate-regression-delta", "82ms"],
    ).returncode
```

## Measured results

Measured at commit `c10155d8a` against the baseline refreshed 2026-10-08
(547.4 ms total, EPYC 7763).

| Case | Mutation | Total after | Exit |
|------|----------|-------------|------|
| Query doubles | smallest query 4.4 → 8.8 ms (all iterations) | 561.2 ms (+13.8) | 0 |
| Query at the floor | one query +7.0 ms | 568.4 ms | 0 |
| Query just over | one query +7.2 ms | 569.0 ms | 1 |
| Uniform slowdown | every entry ×1.14 | 623.9 ms (+76.5) | 0 |
| Uniform slowdown | every entry ×1.16 | 635.1 ms (+87.7) | 1 |
| Three slow queries | three slowest queries +7 ms, all iterations | 610.4 ms (+63.0) | 0 |
| Four slow queries | four slowest queries +7 ms, all iterations | 631.4 ms (+84.0) | 1 |
| Query missing | query 18 absent from the result | 517.0 ms | 0 |
| Query failed | query 18 `status: FAILED`, `ms: 0.0` or `null` | 517.0 ms | 0 |
| Orchestration | `run.total_duration_ms` ×3 | 547.4 ms | 0 |
| Data load | `data_loading.duration_ms` and `load_time_ms` ×10 | 547.4 ms | 0 |
| Warmup | every warmup entry ×5 | 547.4 ms | 0 |
| First iteration | first measured iteration of every query ×10 | 2197.1 ms | 1 (aggregate only) |

## Blind spots

### 1. Per-query: a doubling can pass (accepted limit of the 7 ms floor)

The per-query rule needs more than 7 ms *and* more than 10%. At SF=0.01 every
query's last execution is 4.4 to 12.3 ms, so the 7 ms term alone decides:

- The smallest query (4.4 ms) can reach 11.4 ms (2.59x) unflagged. The median
  query (about 8.3 ms) can reach 15.3 ms (1.84x). The slowest (12.3 ms) can
  reach 19.3 ms (1.57x).
- Doubling the smallest query (4.4 → 8.8 ms on its last execution) moves the
  summary total by 13.8 ms when all three measured iterations double, far below
  the 82 ms aggregate floor.

The floor is 1.25 times the largest per-query spread (5.4 ms, Q18) across
nine nightly results, rounded up. It protects against scheduling jitter, and the
cost is that it cannot be both jitter-proof and sensitive for queries this
short. A smaller floor would flake: Q18 alone varied 5.4 ms between healthy
nights. The 5.4 ms spread is a maximum over nine samples, so a tenth night can
exceed it, and a lower floor would fail sooner.

### 2. Aggregate: roughly 15% of total time passes, and a few small regressions stack silently

The aggregate floor of 82 ms is larger than 10% of the 547.4 ms baseline
(54.7 ms), so the floor, not the percentage, is the binding term. A uniform
slowdown passes up to +82 ms, which is +15.0% (`×1.14` passes, `×1.16` fails).

Per-query and aggregate floors do not add up to a tighter bound: three queries
can each be 7 ms slower in every iteration (+63 ms total) without either check
firing, and a fourth tips the aggregate (+84 ms). In practice a regression that
touches a handful of short queries by an equal amount is invisible until it
touches more than three of them.

### 3. `average_query_time` can never fail the aggregate check

`_check_regression` applies the same millisecond floor to
`average_query_time` as to the total. The average is the total divided by 66, so
it would need to rise by more than 82 ms (about 10x its 8.3 ms value) to clear
the floor. The metric adds no signal beyond `total_execution_time`; it is
redundant at this scale rather than harmful.

### 4. Baseline built from the slowest runner class weakens sensitivity elsewhere

The baseline uses only EPYC 7763 results (five of nine sources, 539.8 to
586.1 ms) so a slow night is not a false positive. `compare` does not look at
`environment.cpu_model`; the same 629.4 ms trip level (547.4 + 82) applies on
every runner class. The headroom on the faster hardware seen in the sources:

| Runner class (source total) | Regression hidden before the gate fires |
|-----------------------------|------------------------------------------|
| EPYC 7763 (539.8 to 586.1 ms), the baseline class | +89.6 ms (16.6%) from the fastest night |
| Xeon 8573C (534.0 ms) | +95.4 ms, 17.9% |
| Xeon 8370C (518.8 ms) | +110.6 ms, 21.3% |
| EPYC 9V74 (409.7 to 465.0 ms) | +164.4 ms (35.4%) from 465.0 ms; +219.7 ms (53.6%) from 409.7 ms |

The runner pool is not controlled, so which row applies to a given night is
luck. This is the intended trade recorded in perf-smoke.md (no false failures on
slow hardware, less sensitivity on fast hardware). Note the converse: nothing in
the output tells a reader which class ran a failing night. The only guidance is
the manual check in the perf-smoke troubleshooting note, and the result file
carries `environment.cpu_model` for it.

For contrast, the nightly throughput gate (`tests/uat/throughput.py`,
`tests/uat/throughput_baseline.py`) compares against a rolling median of the
latest 10 records from the same runner class (CPU model and core count), needs 5
samples before it gates (until then it only reports) and fails below 80% of that
median. The two gates use different philosophies: perf-smoke has a static
cross-class baseline, throughput has a per-class rolling one.

### 5. `run.total_duration_ms` and phase durations are not compared

Orchestration overhead is out of the signal. `run.total_duration_ms` (1728 ms in
the baseline; 1078 ms power phase, 442 ms load, 95 ms schema, 50 ms validation)
tripled with no failure, and a 10x slower data load also passed. This is
consistent with perf-smoke.md ("one phase (power)") but conflicts with its
first paragraph, which says the job exists to surface result-builder,
translation, validation and lazy-loading regressions: those costs sit mostly in
the unmeasured part of the run. Only the power-phase query timings are read.

Whether this is intended scope or missing coverage is a decision, not a defect.
Measuring it is cheap (the value is already in the result) but its noise is
unmeasured: the baseline's sources file records only query totals, so nobody has
yet characterised the night-to-night spread of `total_duration_ms` on hosted
runners. Without that spread any floor for it would be a guess.

### 6. Query-level coverage gaps in `compare`

- **A query missing from the current result is skipped.**
  `compare_results` iterates baseline queries and `continue`s when the current
  result lacks the id, so a dropped query raises no failure and, because the
  total shrinks, looks like an improvement (517.0 ms in the case above).
- **A failed query reads as an improvement.** `_extract_query_map` maps a
  missing `ms` to 0 and `compare_results` treats a 0 baseline as "no change" and
  a 0 current as faster. `status` is never consulted, so a `FAILED` entry with
  `ms: 0.0` or `null` passes `compare`.
- **Last execution only.** Per-query judgement uses the third iteration alone, so
  a spike in the first or second measured iteration, or in warmup, is invisible
  per query (the first-iteration case above fired only through the total,
  because it was ×10 across all 22 queries).

For the failed and missing query cases the run step before `compare` may fail the
job on its own; this review did not establish what `benchbox run` returns when a
single query fails at SF=0.01, so the two cases are recorded as `compare`
behaviour, not as end-to-end nightly behaviour.

### 7. Fixed baseline, no trend memory, no retry

- The baseline is static. Slow drift is therefore eventually caught (each night is
  compared with the same reference), but a baseline refresh from recent nightlies
  re-bases the reference on whatever has crept in. A ≤15% creep passes both
  before and after the refresh.
- A failed `t3:perf` night has no retry and no per-night noise estimate. Two
  neighbouring nights are never compared with each other, so a transient bad
  runner and a real regression look the same in the job log. The nightly job has
  no skip label (`skip-perf-smoke` only exists on the PR workflow) and the
  `t3:quarantine` job runs `pytest -m quarantine` tests only; the perf gate
  cannot be quarantined, so a persistent flake is handled by editing the floor
  or baseline, not by an expiring waiver.
- `perf-smoke.yml` runs only for pull requests into `release`; changes
  merged to `develop` are covered by the nightly run, one day later.
- The floor derivation lives in the baseline's sources file for the per-query
  floor only. `perf_smoke_baseline.py` does not compute or record the 82 ms
  aggregate floor, so the aggregate figure is defended by prose in perf-smoke.md.

## Assessment: accepted policy versus missing coverage

| Blind spot | Verdict | Rationale |
|------------|---------|-----------|
| 1 per-query 7 ms floor | Accepted policy | The floor is the measured jitter bound; shrinking it would reintroduce host-noise failures. |
| 2 ~15% aggregate pass-through | Accepted policy, documented | Derived from the observed same-class spread (64.9 ms); the sensitivity cost is the price of the noise protection. |
| 3 `average_query_time` redundancy | Low-value cleanup | No missed regression; the metric cannot fire but total covers it. |
| 4 slowest-class baseline | Accepted policy; per-runner variant is optional | Trade recorded in perf-smoke.md. A per-class baseline needs about five results per class and four hosted classes appear in nine nights. |
| 5 orchestration time unmeasured | Gap relative to stated purpose | In scope per the first paragraph of perf-smoke.md, but no noise data exists. Measure before choosing a threshold. |
| 6 missing/failed query passes `compare` | Gap | `compare` should not report success when the current result lacks a baseline query or contains a failed one. Needs confirmation of what `run` does first. |
| 7 static baseline, no retry | Accepted policy | Simplicity; revisit only if flake rate rises. |

## Recommended decision (needs owner confirmation)

The tracker item asks for an owner decision. This page records the evidence-based
recommendation, not the decision:

1. Keep the 7 ms per-query and 82 ms aggregate floors. They are the measured
   noise bounds, and the doubling case is a deliberate limit, not a defect.
2. Add a guard that makes `compare` fail when a baseline query is absent from
   the current result or any current query has a non-success status, independent
   of thresholds. This closes blind spot 6 without touching host-noise handling.
3. Collect `run.total_duration_ms` and phase durations on the nightly runs for
   about two weeks, measure their spread per runner class, then decide whether an
   orchestration floor (blind spot 5) is warranted. Do not set one before that.
4. Do not add a per-runner baseline until the hosted pool is known to be stable
   enough to give five results per class within the 14-day artifact retention.
5. Record the CPU model in the failing-job summary so the troubleshooting note
   (compare the night's CPU with the baseline's) needs no manual artifact
   download.

Items 2, 3 and 5 each need an implementation TODO linked to the tracker item
before it is closed. The perf-smoke.md cross-reference and the update to issue
#2778 are the responsibility of the owner once they confirm or amend the list.
