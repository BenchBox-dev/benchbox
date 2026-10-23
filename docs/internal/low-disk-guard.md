# Low-disk guard

Benchmark runs write large datasets, databases and results under the benchmark
runs directory (`BENCHBOX_OUTPUT_DIR`, default `../benchmark_runs` next to the
repository). A full root volume breaks every other tool on the host as well:
issue trackers, event logs and CI artifact staging fail with `ENOSPC`. The guard
makes a run fail loudly while there is still room to act.

## What it does

- `benchbox/utils/disk_guard.py` compares free bytes with total bytes for a path
  (the nearest existing parent when the path does not exist yet).
- `execute_run` in `benchbox/core/run_service.py` calls it before the adapter is
  built or any data is generated. Every entry point that runs a benchmark goes
  through it: the CLI orchestrator and the MCP `run_benchmark` tool. It checks
  the benchmark runs directory and, when it is a local path, the run output
  root. Cloud output paths are not checked.
- Below the threshold the run fails with a message that names free and total
  bytes, the percentage, the threshold, and the largest top-level entries under
  the path. The scan has a 10 second budget; entries cut short are marked
  partial.
- The guard never deletes, moves or prunes anything.

## Configuration

| Setting | Effect |
| --- | --- |
| `BENCHBOX_MIN_FREE_DISK_PERCENT` | Minimum free percentage, default `5`. `0` disables the check. Must be in `[0, 100)`. |

## Manual check

```bash
make disk-check
make disk-check DISK_PATH=/path/to/output MIN_FREE_PERCENT=10
```

Exit status is 0 when there is enough space, 1 when below the threshold, 2 for an
invalid threshold.

## Host survey (2026-10-23, one maintainer host)

Root volume: 927 GB, 840 GB used, 88 GB available (91% used). The incident that
motivated the guard was the same volume at 100%.

| Consumer | Size | Regenerable |
| --- | --- | --- |
| benchmark runs directory | 320 GB | Partly (see below) |
| 91 `BenchBox.wt-*` worktrees (about 2 GB each, mostly per-worktree `.venv`) | 171 GB | Yes, from git and `uv sync` |
| primary clone `BenchBox/` | 25 GB | Yes, apart from unpushed work |
| `~/.cache` | 63 GB, of which uv cache 54 GB | Yes |
| `~/Library/Caches` | 12 GB | Yes |
| `/tmp` | 0 | n/a |
| Docker data (`~/.docker`) | 28 MB | n/a |

Inside the benchmark runs directory: `datagen` 145 GB, `databases` 65 GB,
`tpch_sf100` 34 GB, `tpcds_sf100` 33 GB, `polars-version-matrix-20261009`
20 GB, `polars-sf10-pilot-20261009` 11 GB. Generated data and database files
can be rebuilt by rerunning generation; result files and the dated experiment
directories are evidence and are not regenerable without rerunning the
benchmarks.

## Retention recommendations

These are recommendations only. Deleting anything is the owner's decision, and
exact paths need owner approval first.

1. Worktrees: remove worktrees of merged branches with the existing worktree
   lifecycle tooling. Each carries its own virtual environment.
2. uv cache: `uv cache prune` removes unused entries; `uv cache clean` removes
   all and is rebuilt on demand.
3. `datagen` and `databases`: keep the scale factors in active use and
   regenerate the rest. They are the largest regenerable consumer.
4. Dated experiment directories: archive finished ones to external storage
   after their results are published, instead of keeping them on the root
   volume.
5. Point `BENCHBOX_OUTPUT_DIR` at a secondary volume if one is available.

The sustained-headroom target and the alert for event-log degradation depend on
host actions that only the owner can take (freeing space, moving output, host
monitoring). The guard only ensures benchmark runs stop early.
