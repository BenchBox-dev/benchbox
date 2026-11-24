# GitHub-hosted measurement workflow

Memory-heavy tuning evidence does not fit the shared maintainer Macs (16 GiB, under memory
pressure from other work). It runs on GitHub-hosted Linux runners through
`.github/workflows/measurement.yml`, started by hand with `workflow_dispatch`. The repository is
public, so standard `ubuntu-latest` runners (4 vCPU, 16 GB RAM) are free. This is hosted CI, not
live vendor cloud.

## Host class

Evidence from this workflow is its own host class: `github-hosted ubuntu-latest, 4 vCPU, 16 GB RAM`.
It has its own A/A calibration in every cell, and is never merged with evidence measured on a Mac
or on another host class. Each verdict records the class next to scale, memory rung and engine
version. The run directory records the CPU model, memory, kernel, runner image version and commit
under `env/runner.txt`.

## Cells

| Cell | What it measures |
|---|---|
| `duckdb-tpch-sf10`, `duckdb-ssb-sf10`, `duckdb-tpcds-sf10` | The DuckDB tuned template at SF10 in four arms plus an A/A copy: `N` notuning, `N2` notuning (calibration), `C` constraints only, `S` sort order only, `T` the full template. `C` and `S` are derived from the shipped template by `scripts/tuning_evidence/duckdb_arms.py`. Load time is counted. |
| `clickhouse-tpcds-sf1` | ClickHouse server TPC-DS SF1: `N`, `N2` (A/A), `S` (tuned layout, notuning session) and `T` (full template) in a Docker container limited to 5.25 GiB (5376 MiB, swap disabled), so the release memory envelope is unchanged. |
| `clickhouse-tpch-q21` | ClickHouse server TPC-H Q21 at SF1 under the same 5.25 GiB container. Runs Q6 (control) and Q21 in the four arms, then `scripts/tuning_evidence/capture.py` records the error, peak memory from `system.query_log` and the join algorithm per arm, and sweeps candidate join and spill settings on the notuning arm. |
| `starrocks-tpch-sf1`, `starrocks-ssb-sf1`, `starrocks-tpcds-sf1` | StarRocks at SF1 in a Docker container limited to 8 GiB: `N`, `N2` (A/A) and `T` (the tuned template). Each arm waits for automatic statistics jobs and compaction before timing. |

Inputs: `cell`, `seed` (default 42), `rounds` (default 9, at least 7) and `ref` (commit to
measure, default the dispatched commit). The workflow only dispatches from the default branch.

## Running a cell

```bash
gh workflow run measurement.yml -f cell=duckdb-ssb-sf10 -f seed=42 -f rounds=9
gh run list --workflow measurement.yml --limit 3
gh run watch <run-id>
gh run download <run-id> -D <dir>
```

The artifact `measurement-<cell>-<run-id>` holds `harness/harness.json`, `harness/summary.md`, the
run log, `env/` (runner and container records) and, for Q21, `capture/capture.md` and
`capture/capture.json`. DuckDB database files are kept out of the artifact.

## Recording evidence

Apply the benefit rule (ADR-3 addendum, PR #2881) to `harness.json`, then write the result into the
same files earlier evidence used: `benchbox/core/tuning/profiles/template_evidence.yaml` and
`_project/analysis/clickhouse-tuning-evidence-sf1/` (`findings.md` and `cells/`). State the host
class and the workflow run id with each entry.
