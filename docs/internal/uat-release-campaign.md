# UAT release campaign

Maintainer procedure for the three-stage UAT campaign and its report. The UAT
framework itself is described in the [UAT framework](https://benchbox.dev/docs/operations/uat-framework.html).

## Three-stage UAT campaign

A UAT campaign produces a report (a COMPLETED report per config with a commit
SHA). It runs in three stages, in this order, so that all
native and dataframe platforms finish before any Docker stack starts — the
ordering the 2026-05-28/29 evidence violated. First time on a machine: work
through the [local provisioning](https://benchbox.dev/docs/operations/uat-local-provisioning.html) "First-run checklist"
before starting stage 1.

Stages (run each to completion before starting the next):

1. **Native SQL + dataframe** — `tests/uat/configs/release-gate-01-native-dataframe.yaml` (scales 0.01/0.1/1)
2. **Docker non-OLTP** — `tests/uat/configs/release-gate-02-docker-nonoltp.yaml` (scales 0.01/0.1/1)
3. **Docker OLTP** — `tests/uat/configs/release-gate-03-docker-oltp.yaml` (scale 0.01)

(Stage 1 covers release-gate stages 1–2 of the contract — native SQL then
dataframe — in a single Docker-free sweep; stages 2 and 3 are the Docker tiers.)

Run rules:

- Use a **fresh run root** under `BENCHBOX_OUTPUT_DIR=<checkout-parent>/benchmark_runs`
  (the three configs leave their output templates unset, so the env var sets
  the base — see "Output artefacts"); never resume into the failed 2026-05-28/29
  dirs (they are non-evidentiary).
- One platform / one Docker stack at a time (`execute.parallel_platforms` is
  hard-rejected). A single Docker stack's compose-up failure records FAIL and
  the sweep advances; it does not truncate the run.
- For slow Docker stacks (e.g. LakeSail), set `cleanup.docker_start_timeout_s`
  from a measured healthy startup (see "Managed Docker startup failures are
  non-fatal") before the stage-2 run.

Every sweep writes a machine-readable `uat_gate_summary.json` beside
`cells.jsonl` (versioned schema; verdict `green|red`, or `dry_run` for
dry-run sweeps): config name, source provenance, container engine,
completion timestamp, per-phase exit codes, accounting counts, validator
clean rate vs floor, cross-scale pairs vs floor, and explorer-smoke status.

Ordering + aggregation check: after the three runs,

```bash
make uat-gate-check STAGE1=<stage1-run-dir> STAGE2=<stage2-run-dir> STAGE3=<stage3-run-dir>
```

reads the three stage summaries (a stage may be several shard run directories
produced with `make uat-sweep ... PLATFORMS=<a,b>`; the gate then requires the
union of the shard cells to equal the stage's planned cells, one source commit
and distribution digest across shards, and the cross-scale floor to be met by
the summed shard pairs), verifies from their machine-recorded
`completed_at` timestamps that no Docker `action=up` in stages 2/3 preceded
stage-1 completion (nor stage 3 before stage-2 completion), enforces the
mechanized campaign-report items below, and writes the combined evidence file
to `_project/release-evidence/uat-gate-summary.json`. Exit 0 means the
campaign report is complete. The report may be reviewed or committed as
historical evidence; `scripts/release_readiness_check.py` does not require it
for `validate-base` (see `docs/internal/release-guide.md`).

`cross_scale_coverage_min_pairs` in each config is the report-phase teeth: a
breach forces a non-zero report exit, so a partial or regressed sweep cannot
be reported as complete. The values are derived, not hand-picked: floor =
max(stage minimum, floor(0.8 × cross-scale-eligible pairs from
`enumerate_cells_with_pruning`)) — each config carries its derivation comment,
and `tests/uat/test_config.py` pins the sound band.

### Campaign report: COMPLETE / HOLD

`make uat-gate-check` classifies this report: all stages verdict-green
(every phase exit 0, incl. validator and cross-scale floors), accounting
sidecar present (`unreachable_is_estimated=false`), explorer smoke actually
ran for stages that configure it, one clean `source_commit_sha` across
stages (`source_dirty=false`), and no ordering violations. Exit 0 = COMPLETE;
any HOLD reason is printed and lands in the evidence file's `reasons`. It is a
campaign report, not a release-cut precondition.

Still useful before recording the report: DuckDB (the reference) is green or
its cells are explicitly pruned, and no `NO_JSON` cell lacks captured error
text.

## v0.4.0 release-gate envelope evidence

This is the measurement record behind the release-gate runtime-envelope
exclusions in `tests/uat/compatibility.py`.

As of 2026-08-25, DataFusion `datavault` and SQLite `tpcds` and `tpcds_obt`
are excluded from the 1200-second native release-gate cell.

### DataFusion DataVault

For DataFusion DataVault, the clean Stage 1 sweep passed SF0.01 and SF0.1, but
the SF1 process was killed while running query 18 after 145.9 seconds. A later fix
corrected the DataFusion spill-pool construction; a clean post-fix replay
confirmed that a 12 GiB fair spill pool was active, but the host still killed
query 18. This is the largest pool the 16 GiB release-host envelope can admit.
Because compatibility rules are platform/benchmark scoped, the release gate
prunes the full scale ladder under
`uat.compat.datafusion.datavault.release_gate_runtime_envelope`; ordinary
diagnostic sweeps still enumerate every scale.

| Probe | Source commit | Result | Artifact SHA-256 |
| --- | --- | --- | --- |
| Stage 1 native UAT | `1d86c31845e1293d2e6bf8adeeab6da2ce4e433a` | SF0.01 and SF0.1 passed; SF1 query 18 was killed | `cells.jsonl`: `b82e3cd019515bd4be57e0f53cf45a9ff2546819599bf0844da8fdbfe5b44cce` |
| Post-fix SF1 replay | `f27fa6363517616e3573dd385ab75734207bb0a2` | 12 GiB fair spill pool applied; process killed | `1558c7ee5cd5d9561bc8e76b1d2ccceb527a4fa5f7e6950805aef8ea5de997f7` |

Replay the focused SF1 cell from a clean worktree at
`f27fa6363517616e3573dd385ab75734207bb0a2`:

```bash
BENCHBOX_OUTPUT_DIR="<checkout-parent>/benchmark_runs" \
  uv run --no-sync -- benchbox run --platform datafusion \
  --benchmark datavault --scale 1.0 --queries 18 --iterations 1 -vv \
  --non-interactive --phases power \
  --output "<checkout-parent>/benchmark_runs/datagen"
```

### SQLite TPC-DS

For canonical TPC-DS, the native UAT sweep timed out at about 1200 seconds at
each requested scale: 0.01, 0.1, and 1.0. A bounded SF0.01 replay processed
queries 2 through 12; successful queries completed in at most 0.60 seconds and
query 5 failed immediately on unsupported `ROLLUP`, then query 13 ran for more
than 300 seconds without completing. A second bounded replay processed queries
14 through 47 quickly apart from immediate SQL-compatibility failures, then
query 48 also ran for more than 300 seconds without completing. The generated
SQLite stream also contains eleven queries that require unsupported `ROLLUP`
semantics, four of which additionally require `GROUPING`. The release gate
therefore prunes the complete platform/benchmark scale ladder under
`uat.compat.sqlite.tpcds.release_gate_runtime_envelope`; diagnostic sweeps still
enumerate and run it. The timeout evidence is provisional: a separate known
SQLite TPC-DS adapter issue causes immediate cursor/connection failures, not a
300s timeout, so it does not settle this runtime envelope.

The durable evidence record is SHA-bound below. The UAT output root was
`<checkout-parent>/benchmark_runs`; its run logs are in the `logs/`
subdirectory. The bounded-probe logs were temporary local files, so their
digests and replay commands are recorded here rather than treating `/tmp` as
durable storage.

| Probe | Source commit | Result | Artifact SHA-256 |
| --- | --- | --- | --- |
| Stage 1 native UAT | `1d86c31845e1293d2e6bf8adeeab6da2ce4e433a` | SQLite TPC-DS SF0.01, SF0.1, and SF1 each timed out after 1200.2s | `cells.jsonl`: `b82e3cd019515bd4be57e0f53cf45a9ff2546819599bf0844da8fdbfe5b44cce` |
| Q2-Q25 bounded replay | `20a2dc36b21607a7d1242bc5c456c4011a3df30d` | Q13 still running at 300s cutoff | `40116787be6d5e493f1f20478b467d65b25ff4dfd797fdf02c52aab2f54141e7` |
| Q14-Q50 bounded replay | `20a2dc36b21607a7d1242bc5c456c4011a3df30d` | Q48 still running at 300s cutoff | `638854cb5367739e0935f7743550be123fe8d0db124fbeec17ae95c685953fae` |

Run the release-gate reproduction in a clean linked worktree at
`1d86c31845e1293d2e6bf8adeeab6da2ce4e433a`:

```bash
BENCHBOX_OUTPUT_DIR="<checkout-parent>/benchmark_runs" \
  make uat-sweep CONFIG=tests/uat/configs/release-gate-01-native-dataframe.yaml
```

Run the bounded query reproductions in a clean linked worktree at
`20a2dc36b21607a7d1242bc5c456c4011a3df30d`. They use the same output
root, scale, one iteration, and five-minute alarm:

```bash
BENCHBOX_OUTPUT_DIR="<checkout-parent>/benchmark_runs" \
  /usr/bin/time -l perl -e 'alarm shift; exec @ARGV' 300 \
  uv run --no-sync -- benchbox run --platform sqlite --benchmark tpcds \
  --scale 0.01 \
  --queries 2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25 \
  --iterations 1 -vv --non-interactive --phases power \
  --output "<checkout-parent>/benchmark_runs/datagen"

BENCHBOX_OUTPUT_DIR="<checkout-parent>/benchmark_runs" \
  /usr/bin/time -l perl -e 'alarm shift; exec @ARGV' 300 \
  uv run --no-sync -- benchbox run --platform sqlite --benchmark tpcds \
  --scale 0.01 \
  --queries 14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50 \
  --iterations 1 -vv --non-interactive \
  --phases power --output "<checkout-parent>/benchmark_runs/datagen"
```

### SQLite TPC-DS OBT

For TPC-DS OBT, the canonical SF1 artifact contains 5,041,336 rows and 518
columns. A bounded native probe through `ParquetFileHandler` inserted 1,325,000
rows in 304.9 seconds, with 4,345 rows/s overall and 2,350 rows/s in the final
25,000-row interval. A projection from the final 625,000 rows is 1,391 seconds
for loading alone. The table has no indexes, primary keys, or foreign keys;
the observed limit is SQLite parameter binding and single-transaction WAL
growth, with external readers seeing no rows until the loader commits. The
probe and original killed-run evidence were captured outside the repository
under `BENCHBOX_OUTPUT_DIR=<checkout-parent>/benchmark_runs` and `/tmp`.

This exclusion is represented by
`uat.compat.sqlite.tpcds_obt.release_gate_runtime_envelope`. It prunes the
requested `0.01`, `0.1`, and `1.0` ladder entries before the existing TPC-DS OBT
minimum-scale fallback can substitute `1.0`; it is not a blanket skip or a
timeout increase. A future atomic SQLite bulk-loader optimization must replace
the rule only after a bounded native measurement fits the same contract.
