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

## Local snapshots on macOS

Time Machine and other backup agents keep local APFS snapshots of the root
volume. A snapshot holds every block that was live when it was taken, including
data deleted afterwards, and `df` does not show that space as free until the
snapshot is removed. Deleting live files can therefore free almost nothing.

List them:

```bash
tmutil listlocalsnapshots /
```

The owner removes a Time Machine snapshot by its date (no `sudo` was needed on
the maintainer host):

```bash
tmutil deletelocalsnapshots 2026-11-20-140045
```

Example, 2026-11-22: the root volume was at 46 GiB free. Deleting 32.8 GB of
duplicate generated data returned 0.4 GB. Deleting two local snapshots
(`2026-11-20-140045` and `2026-11-22-085225`) then took free space from 47 GiB
to about 300 GiB. An Arq backup snapshot (`com_haystacksoftware_arqagent_...`)
was present and left alone.

Snapshots whose names do not start with `com.apple.TimeMachine` belong to other
tools; leave them to those tools.

BenchBox only reports them. On macOS the low-disk refusal (the run guard,
`make disk-check`, and the `make heavy-run` admission refusal on a disk floor)
and `make heavy-status` add a line with the snapshot count and names, `none`,
or `unmeasured` when `tmutil` fails or does not answer within the 5 second
per-command limit. The line is omitted on Linux and when `tmutil` is missing.
The query never fails or delays the guard or admission beyond that limit, and
BenchBox never deletes, thins or mounts a snapshot.

## Retention recommendations

These are recommendations only. Deleting anything is the owner's decision, and
exact paths need owner approval first. One exception: a session deletes the
`datagen` and `databases` directories it created under a run root of its own
(`[RUN-DATA-001]` in `AGENTS.md`), because nothing else reads them and they can
be regenerated.

1. Local snapshots: check them first (see the next section). Deleting data
   frees nothing while a snapshot still holds it.
2. Worktrees: remove worktrees of merged branches with the existing worktree
   lifecycle tooling. Each carries its own virtual environment.
3. uv cache: `uv cache prune` removes unused entries; `uv cache clean` removes
   all and is rebuilt on demand.
4. `datagen` and `databases`: keep the scale factors in active use and
   regenerate the rest. They are the largest regenerable consumer.
5. Dated experiment directories: archive finished ones to external storage
   after their results are published, instead of keeping them on the root
   volume.
6. Point `BENCHBOX_OUTPUT_DIR` at a secondary volume if one is available.

The sustained-headroom target and the alert for event-log degradation depend on
host actions that only the owner can take (freeing space, moving output, host
monitoring). The guard only ensures benchmark runs stop early.
