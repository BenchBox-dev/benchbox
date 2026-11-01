# UAT Framework — Design Document

> **Status:** implemented; maintained as the UAT framework design contract.
> **Source TODO:** `_project/TODO/main/planning/uat-framework-tests-uat-runner.yaml`
> **Triggering finding:** `_project/blind-spots/2026-05-03-130000-stress-script-uat-driver-drift.md`
> **Sibling spec (consumed, not duplicated):** `_project/specs/uat-methodology-blind-spot-remediation.md`
> **Author inputs:**
>   - retired stress-test shell workflow (superseded by `make uat-stress`)
>   - `_project/handoffs/results-explorer-uat-retrospective-20260502.md` (the bespoke driver this spec replaces)
>   - `benchbox.validation.bundle` (shared public bundle validator consumed by the validate phase)
>   - `_project/DONE/main/active/uat-template-success-metric-terminal-state-and-gating.yaml` (Finding 3 vocab)

## 1. Reframe — what this spec is and is not

### 1.1 The drift gap

The retired shell stress workflow and the 2026-05-02 UAT driver were two
parallel surfaces for matrix-shape execution that drifted. The framework
consolidates the reusable pieces:

- timeout/gtimeout/perl-fallback wrappers (lines 90-123, 359-429)
- registry enumeration via `benchbox.core.benchmark_registry`
  (lines 232-263)
- per-platform port maps, `--platform-option` tables, CLI flags, uv
  extras (lines 142-192, 548-555)
- TCP probing with sentinel-file cache (lines 197-227)
- result-JSON path extraction (line 478)

The 2026-05-02 sweep needed all of that plus:

- scale ladder with early-stop (previous-rung failure or wall-clock > 180s)
- reuse-aware database cleanup (preserve `datagen/`, prune `databases/`
  at safe boundaries)
- sentinel file at sweep start for `find -newer` queries
- matrix-summary TSV
- validator-clean-rate roll-up (now in-process via `benchbox.validation.bundle`)
- submission packaging with explicit terminal-state declaration
- explorer build + Playwright smoke

None of the second list was reusable afterwards. It survived only as
artifacts under `~/Developer/benchmark_runs/logs/uat_20260502/` plus
the retrospective prose. The framework makes that machinery reusable
through one typed config/orchestrator path.

### 1.2 Why this is separate from the methodology spec

The methodology spec (`uat-methodology-blind-spot-remediation.md`)
resolves *how UAT TODOs are authored and reviewed*: cross-scale
coverage convention, validator-clean-rate metric, terminal-state
vocab plus `gating: true` open-question schema. Its two
implementation TODOs are now in DONE.

This spec resolves *how UAT machinery is built and reused*. Sibling,
not competitor. The two methodology TODOs ship the inputs this
framework consumes:

- `benchbox.validation.bundle` — invoked in-process by `make uat-validate`
- `submit_terminal_state` four-word vocab — read from YAML by
  `make uat-package`

### 1.3 What this spec covers

A single Python framework under `tests/uat/` (tracked, version-
controlled), exposed via `make uat-*` operator targets, with
composable phases driven by YAML configs under `tests/uat/configs/`.
Stress testing becomes a degenerate config (one scale, one platform
group, no validate/package/explorer phases).

### 1.4 What this spec does not cover

- Cloud-platform UAT (Snowflake, BigQuery, Redshift, Athena,
  Databricks, ClickHouse Cloud) — deferred to a separate
  `uat-framework-cloud-platforms` TODO if/when the explorer's cloud
  facets are ready.
- ~~Throughput-phase coverage~~ — **shipped** (`throughput-uat-and-ci-coverage`):
  `tests/uat/throughput.py`, `execute.official`/`execute.streams`/
  `execute.seed`, and a `throughput` segment in `execute.phases_arg` route
  to `benchbox run-official --streams N` (the only CLI surface that honors
  N>1 concurrent streams; see `tests.uat.matrix.benchbox_run_official_argv`).
  Nightly-only in practice — `tests/uat/configs/uat-throughput-duckdb-nightly.yaml`
  run from `.github/workflows/nightly-v2.yml` — because multi-stream cells
  multiply wall-clock cost; not part of the default fast-lane matrix.
- A `benchbox uat` CLI subcommand — UAT is a project-developer
  concern, `benchbox` is a project-user concern. `make` targets are
  the developer entrypoint for `worktree-*`, `blind-spots-*`,
  `pr-*`, `dev-loop-metrics`; UAT joins the same pattern.

## 2. tests/uat/ Python package layout

```
tests/uat/
├── __init__.py
├── README.md                      # developer guide (W11 deliverable)
├── matrix.py                      # platform/benchmark enumeration + reachability
├── runner.py                      # single-cell execution (subprocess + timeout + log capture)
├── config.py                      # typed YAML schema validation
├── timeouts.py                    # signal-based timeout wrapper
├── cleanup.py                     # reuse-aware datagen/databases cleanup
├── docker_assets.py               # UAT-owned Docker compose lifecycle helpers
├── docker_cleanup.py              # Docker-engine teardown/recovery (make uat-docker-cleanup)
├── container_cleanup.py           # Apple `container`-engine teardown/recovery sibling
├── artifact_hygiene.py            # local-artifact hygiene guard (make uat-artifact-hygiene)
├── cells_io.py                    # cells.jsonl + accounting-sidecar codec
├── gate_summary.py                # uat_gate_summary.json evidence artifact (make uat-gate-check)
├── throughput.py                  # multi-stream throughput/concurrent cell support
├── throughput_baseline.py         # per-runner-class Throughput@Size baselines and rolling median
├── ladder.py                      # scale-ladder + early-stop logic
├── phases/
│   ├── __init__.py
│   ├── preflight.py               # disk, docker, noisy-neighbor scan
│   ├── enumerate.py               # registry-driven cell helper used by execute
│   ├── execute.py                 # iterates ladder, invokes runner per cell, applies cleanup
│   ├── validate.py                # in-process bundle validation + validator TSV
│   ├── package.py                 # invokes benchbox publish run / submit --service per submit_terminal_state
│   └── report.py                  # TSV roll-up + cross-scale coverage assertion
├── orchestrator.py                # composes phases per YAML phases: list (uat-sweep entry point)
├── configs/                       # tracked YAML configs
│   ├── README.md                  # content policy (template vs historical vs generated-shard)
│   ├── uat-2026-05-02.yaml        # historical replay (W10 deliverable)
│   ├── stress-default.yaml        # canned preset for `make uat-stress` (W9 deliverable)
│   └── generated-rerun-shards/    # generated/frozen operator rerun evidence, not templates
└── test_*.py                      # fast-test coverage (port maps, ladder pruning, schema, etc.)
```

**Responsibility split.**

| Module | Responsibility | Lines (est.) |
|---|---|---|
| `__init__.py` | UAT package marker and documentation string | — |
| `matrix.py` | Registry-driven benchmark enumeration, platform grouping, compose-derived TCP reachability probe (connection facts now owned by `docker_assets`) | 250 |
| `runner.py` | Build `benchbox run` argv per cell; capture stdout+stderr to per-run log; extract result-JSON path; submit classification via shared `benchbox.core.results.submit_classification` | 120 |
| `config.py` | Load YAML, validate against schema (Section 3), expose typed dataclass access | 180 |
| `_cli.py` | UAT CLI entrypoint: argument parsing, sweep/execute/validate/report/package subcommands, output wiring | — |
| `timeouts.py` | Signal-based timeout (POSIX process-group kill ladder) | 80 |
| `cleanup.py` | Track cell completions; prune `databases/` at safe reuse boundaries; preserve `datagen/` | 150 |
| `compatibility.py` | Platform/benchmark compatibility rules; record compatibility-pruned cells with rule metadata | — |
| `docker_assets.py` | Single connection registry: compose-file map, compose-derived host ports + platform options, safe project-scoped compose commands | 180 |
| `docker_cleanup.py` | Docker stack teardown at platform boundaries; project-scoped down/volume handling | — |
| `container_cleanup.py` | Apple `container`-engine sibling of `docker_cleanup.py`; backs `make uat-docker-cleanup ENGINE=container` | — |
| `artifact_hygiene.py` | Local-artifact hygiene guard: flags worktree-local `benchmark_runs/` growth whenever the resolved output root is outside the worktree — including the default `../benchmark_runs`, not only a configured one; report-only (`make uat-artifact-hygiene`) | — |
| `cells_io.py` | `cells.jsonl` + accounting-sidecar read/write codec; single schema shared by `orchestrator.py` and `_cli.py` | — |
| `gate_summary.py` | Writes/reads the versioned `uat_gate_summary.json` per-sweep evidence artifact; powers `make uat-gate-check` cross-stage aggregation | — |
| `throughput.py` | Multi-stream throughput/concurrent cell support via `benchbox run-official --streams`; TPC-compliant scale-factor gate | — |
| `throughput_baseline.py` | Per-run Throughput@Size baseline records keyed by runner class, and their rolling median; shares the `throughput` bucket ceiling with `throughput.py` | — |
| `ladder.py` | Per-(platform, benchmark) rung order; wall-clock and exit-code early-stop; pruning bookkeeping | 100 |
| `preflight_budget.py` | Disk free-space floor budgeting and cell-key accounting | — |
| `phases/__init__.py` | UAT phase package marker and phase contract documentation string | — |
| `phases/preflight.py` | Disk space (configurable cutoff), docker reachability, host load reading | 80 |
| `phases/enumerate.py` | Resolve final cell list for execute given config filters and registry truth; honour min/max scale | 100 |
| `phases/execute.py` | Sequential iteration over (platform, benchmark, rung); invokes runner+ladder+cleanup; owns Docker platform-boundary lifecycle | 220 |
| `phases/validate.py` | Call `benchbox.validation.bundle` in-process; write validator TSV; compute clean-rate floor | 100 |
| `phases/package.py` | Read `submit_terminal_state`; invoke `benchbox publish run --target` (maintainer redacted form; uses the automatic pseudonym salt (`BENCHBOX_MACHINE_ID_SALT` or `~/.benchbox/submission_salt`, created on first use) and writes no community manifest) for local-stage, or `benchbox submit --service` for cloud-uploaded; `draft-pr`/`merged-to-published-results` are **stubs** (dispatcher only, per `PR_STUB_TERMINAL_STATES`) that emit the same argv as `local-stage` plus an operator warning -- PR-opening to `published-results` is not implemented | 130 |
| `phases/report.py` | Read each phase's outputs; emit `matrix_summary.tsv`; cross-scale coverage check | 130 |
| `orchestrator.py` | Walk YAML `phases:` list in order; surface phase failures; respect `dry_run:` toggle | 100 |

**Budget reconciliation (revised 2026-06-01, `uat-loc-budget-reconciliation`; re-measured
2026-07-19, `uat-config-schema-spec-realignment`).**
Original estimate: ~1,500 LOC across 13 modules. 2026-06-01 measured actual: ~6,476
production LOC across 21 modules (+ ~5,730 test LOC, ~1,729 YAML) — see that
reconciliation's own causes (four unlisted modules, under-estimated phases/plumbing).

**2026-07-19 re-measurement.** Five modules landed since 2026-06-01 —
`artifact_hygiene.py`, `cells_io.py`, `container_cleanup.py`,
`gate_summary.py`, `throughput.py`, all in-charter (artifact
hygiene, gate evidence, and throughput are release-gate-orchestration
deliverables; `cells_io.py`/`container_cleanup.py` are refactors that gave
existing inline logic its own module). Test LOC (~10,806) and YAML (~1,880)
remain hand-tracked.

**LOC budget (2026-08-06).** Exact per-module counts are deliberately *not*
committed here. They were, from 2026-07-21 (`uat-spec-module-loc-table-autogen`,
#1258) until 2026-08-06: an `Actual` column and a generated per-bucket summary,
checked byte-for-byte by CI. That kept the numbers honest but made every UAT
change rewrite this file — all 11 commits touching the spec in that window were
100% regenerated numbers and zero prose — and because the per-bucket summary is
a handful of *shared* lines, PRs touching entirely disjoint UAT modules still
collided there, and collided again on every replayed commit during a rebase. It
was simultaneously the sole merge conflict across PRs #1618, #1627 and #1631.

The charter question is "has UAT grown out of scope?", which is answered by
magnitude, not by an exact line count. So `_project/specs/uat-loc-budget.json`
commits per-bucket and total **ceilings**, and
`_project/scripts/uat_loc_table.py --check` (pre-commit + CI) fails when the
tree exceeds one — the same committed-budget pattern as
`_project/evals/agent-instructions/scenarios.json` and the Ruff C901 cap. An
ordinary UAT change now moves a number nobody stores, so it produces no diff
here and no conflict.

Run the script with no arguments for the current per-bucket numbers and headroom.
Raising a ceiling is a deliberate scope decision: it belongs in its own PR, with
the charter argument in the PR body. A routine change needing a bump *is* the
signal this gate exists to raise.

The chartered scope is "release-gate orchestration" (§10) — evidence
artifacts, the six phases, Docker lifecycle, and throughput are all
in-charter, so the load-bearing buckets remain non-discretionary.

Revised budget: **~9,800 production LOC across ~26 modules + tests**, reviewed
per-bucket above rather than against a single aggregate number.

## 3. YAML config schema

Schema is the source of truth for what a UAT config can express.
Validation lives in `config.py`; each field below is enforced.

```yaml
# tests/uat/configs/<sweep-name>.yaml

# Identity --------------------------------------------------------------
name: "uat-2026-05-02"          # required, str. Used in log dir name.
description: "..."              # optional, str. One-line description.

# Phase composition -----------------------------------------------------
phases:                          # required, list[str]. Order matters.
  - preflight
  - execute
  - validate
  - package
  - report
# Allowed: preflight, execute, validate, package, report.
# Stress preset omits validate/package. Entries must be a
# subsequence of that canonical order (the orchestrator walks `phases:`
# literally, not reordered) and must not repeat -- both rejected at load
# time (see "Schema enforcement" below).

dry_run: false                   # optional, bool, default false. When
                                 # true, every phase prints what it
                                 # would do without invoking benchbox.
                                 # Used by W10 structural-parity test.

# Matrix ----------------------------------------------------------------
platforms:
  groups: ["sql", "dataframe"]   # optional, list[str]. Subset of:
                                 # sql, native-sql, fast, slow, dataframe,
                                 # docker, docker-fast, docker-slow, all.
  include: []                    # optional, list[str]. Specific platform
                                 # names to add beyond groups.
  exclude: []                    # optional, list[str]. Names to drop.

benchmarks:
  groups: ["all"]                # optional, list[str]. Subset of:
                                 # tpc, primitives, industry, academic,
                                 # timeseries, realworld, aiml,
                                 # experimental, all.
  include: []                    # optional, list[str].
  exclude: []                    # optional, list[str].

scales:
  rungs: [0.01, 0.1, 1.0]        # optional, list[float], default [0.01].
                                 # Per-(platform, benchmark) ladder
                                 # rungs. Filtered by registry
                                 # min_scale/max_scale at enumerate.
  override: null                 # optional, float|null. When set, uses
                                 # this single scale per cell (matches
                                 # bash --scale override). Mutually
                                 # exclusive with rungs -- explicitly
                                 # setting both is rejected at load time.
                                 # Rung/override entries must be numbers;
                                 # a bool (e.g. `rungs: [true]`) is rejected.

# Execution -------------------------------------------------------------
execute:
  per_cell_timeout_s: 600        # optional, int, default 600. Hard
                                 # wall-clock cap per cell.
  per_cell_memory_limit_gib: 0   # optional, number >= 0, default 0 (off).
                                 # When > 0, the cell's process tree is
                                 # sampled once a second; if its combined
                                 # resident set exceeds this many GiB the
                                 # tree is killed and the cell fails with
                                 # exit 137 and a `# UAT_MEMORY_LIMIT`
                                 # log line, so one runaway cell cannot
                                 # exhaust the host and end the sweep.
  early_stop_after_s: 180        # optional, int, default 180. If a
                                 # rung's wall-clock exceeds this,
                                 # subsequent higher rungs for that
                                 # (platform, benchmark) are pruned.
  early_stop_on_failure: true    # optional, bool, default true. Prune
                                 # higher rungs on any non-zero exit.
  phases_arg: "load,power"       # optional, str, default "load,power".
                                 # Passed as --phases to benchbox run (or
                                 # run-official when official: true). Must
                                 # be a string -- a list (e.g. `[load]`) is
                                 # rejected rather than str()-coerced.
  compression: null              # optional, str|null. Passed as
                                 # --compression when set.
  extra_args: []                 # optional, list[str], default []. Extra
                                 # argv appended verbatim after every other
                                 # flag (e.g. `["--tuning", "tuned"]`).
  skip_unreachable: true         # optional, bool, default true. TCP
                                 # probe failures are skipped, not
                                 # counted as failures.
  parallel_platforms: false      # required to be false; reserved field
                                 # rejected at validation time. UAT W3
                                 # line 222: parallel platforms
                                 # contaminate timings.
  platform_chunking: false       # optional, bool, default false. When
                                 # true, execute walks one platform at a
                                 # time (already sequential) and, after
                                 # each platform, prunes that platform's
                                 # loaded databases through the reuse-
                                 # aware helpers. Chunking operates
                                 # inside one stage; it does not change
                                 # release-gate stage order or the single
                                 # cells.jsonl stream. Datagen is never
                                 # pruned. The concurrent vs chunked
                                 # loaded-database terms come from
                                 # measured inventory rows at the
                                 # configured rungs -- never a guessed
                                 # 15 GiB constant.
  official: false                # optional, bool, default false. When
                                 # true, cells run via `benchbox
                                 # run-official --streams N` instead of
                                 # `benchbox run` -- see tests.uat.throughput.
                                 # Requires scales.rungs to be TPC-compliant
                                 # scale factors.
  streams: null                  # optional, int|null. Concurrent stream
                                 # count for run-official. Requires
                                 # official: true; required (non-null) when
                                 # official: true and phases_arg includes
                                 # "throughput".
  seed: null                     # optional, int|null. Passed as --seed to
                                 # run-official when set.

cleanup:
  preserve_datagen: true         # optional, bool, default true. Reserved
                                 # safety knob; false is rejected because
                                 # UAT never deletes generated source data.
  prune_databases: true          # optional, bool, default true. Prune
                                 # at safe reuse boundaries.
  docker_manage_platforms: false # optional, bool, default false.
                                 # false = externally managed stacks;
                                 # UAT only probes/skips. true = UAT
                                 # starts a project-scoped compose stack
                                 # before each Docker-backed platform.
  docker_platform_switch: "off"  # optional, enum: off, containers,
                                 # volumes, images. Non-off values are
                                 # valid only when docker_manage_platforms
                                 # is true. volumes generates targeted
                                 # `docker compose -p <uat> ... down -v
                                 # --remove-orphans` at platform switch.
  docker_project_prefix: "benchbox-uat"
                                 # optional, str. Prefix for deterministic
                                 # UAT-owned compose project names.
  docker_start_timeout_s: 300    # optional, int, default 300. Timeout for
                                 # compose up/down lifecycle commands.
  docker_fixed_container_name_policy: "fail"
                                 # optional, enum: fail, override, allow.
                                 # Current managed mode rejects compose
                                 # files with fixed container_name values
                                 # until a safe override is registered.

# Preflight phase -------------------------------------------------------
preflight:
  free_space_min_gib: 5          # optional, int, default 5. Hard stop
                                 # at sweep start AND watched mid-sweep
                                 # by `cleanup`; if free space falls
                                 # below this, the sweep aborts before
                                 # the next cell. Lives under preflight
                                 # because it is a sweep-abort guardrail,
                                 # not a per-cell cleanup tunable.
  free_space_path: "~/Developer/benchmark_runs"
                                 # optional, str, default
                                 # "~/Developer/benchmark_runs". Path
                                 # `df` is queried against.
  docker_required: false         # optional, bool, default false. When
                                 # true, preflight fails if `docker ps`
                                 # is not reachable. Set true for
                                 # configs that include any Docker
                                 # platform group.
  noisy_neighbor_warn_load: 8.0  # optional, float, default 8.0. Host
                                 # 1-min load average above this prints
                                 # a warning but does not abort.
  local_platforms_check: false   # optional, bool, default false. When
                                 # true, preflight probes every requested
                                 # platform before the sweep starts instead
                                 # of deferring to per-cell reachability
                                 # skips: automated platforms get one
                                 # `make uat-bring-up` attempt, non-automated
                                 # ones abort preflight with an
                                 # operator-facing message. Default false
                                 # preserves the historical behaviour
                                 # (unreachable platforms become
                                 # execute-phase skipped_unreachable cells).

# Disk-budget estimate --------------------------------------------------
# `tests/uat/data/disk_budget_table.tsv` is an advisory, checked-in
# inventory keyed by (platform, benchmark, scale_factor). Preflight
# prints `Disk budget estimate: ... GiB` before workload execution and
# gates on it: the largest configured scale rung's estimated peak, summed
# across every requested platform, against every required root.
#
# The estimate is a LOWER BOUND, never a certification. Cells with no row
# contribute 0 GiB and are counted as unknown; rows may additionally
# declare `peak_database_gib_status = unmeasured` (every checked-in row
# currently does, so the loaded-database term is identically zero for want
# of data). Measured rows are also summed per platform: concurrent demand
# is the sum, chunked demand is the max. Preflight prints those terms and
# `Disk budget coverage:` / `Disk budget verdict:` on EVERY run, warns
# when coverage is partial, and renders the free-space requirement as
# `required >= N GiB` in that case -- a passing gate means "no shortfall
# detected against the measured subset", not "fits". Refusing on the
# lower bound stays sound in either state; the abort names the computed
# shortfall instead of discovering it mid-sweep.
#
# `preflight.free_space_min_gib` remains the hard cutoff via
# `max(min_free_gib, estimate)` in `check_disk_headroom` -- the only
# enforcement of the configured floor once a disk-budget config is passed
# (which `preflight_kwargs_from_config` always does). Setting it to 0
# disables every disk gate here. Never populate `peak_database_gib` by
# estimating; only a measured sweep may flip a row to `measured`.
#
# Execute also checks before each inventory-covered cell, reserving the
# row's known datagen/transient growth above the configured floor. Datagen
# is reserved once per (benchmark, scale) source because it is reused across
# platforms. Missing rows and unmeasured database terms stay explicit
# lower-bound coverage gaps; the post-cell floor remains the backstop for
# demand the inventory cannot predict. The lifecycle log records the
# prediction and whether its database component was measured.

# Resume (retired) -------------------------------------------------------
# The resume manifest (`<log-dir>/resume.json`, `--resume <manifest>`)
# was removed -- see uat-resume-retirement-artifact-durability. It was
# verified fragile (no version/config/commit check on replay, only ever
# written for free-space aborts, unreachable from `make`) and datagen
# reuse already makes a full rerun cheap. The abort-safe artifacts
# (cells.jsonl, compatibility_pruned.jsonl, matrix_summary.partial.tsv)
# stay and are now written atomically -- see "Artifact provenance and
# abort evidence" in docs/operations/uat-framework.md.

# Validate phase --------------------------------------------------------
validate:
  validator_clean_rate_floor: 0.80   # optional, float, default 0.80.
                                     # If rate < floor, validate phase
                                     # exit is non-zero (advisory).

# Package phase ---------------------------------------------------------
package:
  submit_terminal_state: "local-stage"   # required when package phase
                                         # enabled. One of:
                                         #   local-stage,
                                         #   cloud-uploaded,
                                         #   draft-pr,          (STUB -- see below)
                                         #   merged-to-published-results. (STUB)
  service: null                          # optional, str|null. Required
                                         # when state=cloud-uploaded;
                                         # passed as --service.
# draft-pr/merged-to-published-results are accepted vocabulary (the
# submit_terminal_state validation treats them as valid states) but the
# package phase's PR-opening behavior for them is UNIMPLEMENTED --
# `tests/uat/phases/package.py` dispatches both to the same argv as
# local-stage and prints an operator warning naming the config that any
# result submitted this way is a local stage, not a published-results PR.

# Report phase ----------------------------------------------------------
report:
  matrix_summary_tsv: "matrix_summary.tsv"     # optional, str.
                                                # Filename under log dir.
  cross_scale_coverage_min_pairs: null         # optional, int|null,
                                                # default null. When set,
                                                # report exits non-zero
                                                # if the count of
                                                # (platform, benchmark)
                                                # pairs with all rungs
                                                # validator-clean falls
                                                # below this. OPT-IN per
                                                # methodology spec
                                                # Finding 1 scoping.

# Compatibility phase ----------------------------------------------------
compatibility:
  release_gate_runtime_envelopes: false  # optional, bool, default false.
                                         # When true, applies the runtime-
                                         # envelope prunes in
                                         # `tests/uat/compatibility.py` (for
                                         # example, a slow PostgreSQL-family
                                         # cell). When false, those cells stay
                                         # enumerable for diagnostics.

# Output paths ----------------------------------------------------------
output:
  benchmark_runs_dir_template: "~/Developer/benchmark_runs"
                                       # optional, str, default
                                       # "~/Developer/benchmark_runs". Root
                                       # for datagen/, databases/, results/
                                       # (BENCHBOX_OUTPUT_DIR for every
                                       # `benchbox run` subprocess); distinct
                                       # from logs_dir_template below.
  logs_dir_template: "~/Developer/benchmark_runs/logs/uat_{date}_{time}"
                                       # optional, str. {date} expands
                                       # to YYYYMMDD, {time} to HHMMSS,
                                       # both at sweep start. {time} in
                                       # the DEFAULT is what makes two
                                       # same-day sweeps land in distinct
                                       # dirs instead of one overwriting
                                       # the other's mode="w" artifacts.
                                       # Existing explicit templates that
                                       # never mention {time} (every
                                       # checked-in config today) render
                                       # unchanged -- the placeholder is
                                       # simply absent from the output.
  submissions_dir_template: "~/Developer/benchmark_runs/submissions/{name}"
                                       # optional, str. {name} expands
                                       # to top-level `name:` field.
```

**Schema enforcement.**

| Rule | Effect |
|---|---|
| Unknown field in any section (root or nested) | Validation error at load time |
| `phases:` references unknown phase | Validation error at load time |
| `phases:` contains a duplicate entry | Validation error at load time |
| `phases:` entries out of canonical order (e.g. `report` before `execute`) | Validation error at load time |
| `parallel_platforms: true` |
| `package` in `phases:` without `package.submit_terminal_state` |
| `package.submit_terminal_state` not in 4-word vocab | Validation error, **only when `package` is in `phases:`** |
| `submit_terminal_state: cloud-uploaded` without `service:` |
| `scales.rungs` explicitly set together with `scales.override` |
| `scales.rungs`/`scales.override` entries are bool | Validation error (bool is an int subclass; would otherwise silently coerce, e.g. `rungs: [true]` -> `1.0`) |
| `execute.phases_arg` not a string (e.g. a list) | Validation error (would otherwise `str()`-coerce to a nonsense value) |
| `output.*_template` not a string (e.g. a mapping) | Validation error (same str()-coercion hazard) |
| `validator_clean_rate_floor` outside `[0.0, 1.0]` |
| `preflight.free_space_min_gib` < 0 | Validation error (must be non-negative — `_require_nonnegative_float`) |
| `preflight.free_space_min_gib` == 0 | **Not** an error — an explicit disk-gate opt-out that turns the free-space floor OFF, emitted as a loud `[disk-gate] DISABLED` warning (`disk_gate_disabled_warning`), not a validation failure |
| `preflight` not in `phases:` but `preflight.free_space_min_gib` set | Validation warning (telemetry-only run) |
| `platforms.include` / `benchmarks.include` entry absent from the registry | **Not** a `ConfigError` — `resolve_platforms`/`resolve_benchmarks` silently drop the id, and `enumerate_cells_with_pruning` records a visible `pruned-registry` accounting row per dropped platform/benchmark (see Section 10's compatibility-pruning note and `tests.uat.matrix.missing_platforms_from_include`/`missing_benchmarks_from_include`) |

Three of the `package:` rules above are conditioned on phase membership
(not on the section merely being present) so that a stale `package:`
block on a config that never runs the `package` phase does not block
loading — mirroring the existing `preflight` leniency in the last row.

**Not currently enforced.** `report.cross_scale_coverage_min_pairs` set
while `report` is absent from `phases:` is inert (the floor is simply
never evaluated, since the report phase that would check it never runs)
rather than a `ConfigError`. This differs from earlier drafts of this
spec, which claimed it as a validation error; no code path ever
implemented that check.

**Default-off teeth.** Per the methodology spec's Finding 1 scoping,
`cross_scale_coverage_min_pairs` defaults `null` (no enforcement).
Sweep authors opt in explicitly.

## 4. Make targets

Args below are the real Makefile signatures (`Makefile` targets `uat-*`);
`[...]` marks an optional make-var.

| Target | Args | Purpose |
|---|---|---|
| `make uat-cell` | `PLATFORM= BENCHMARK= SCALE=` `[PHASES=] [COMPRESSION=] [TIMEOUT_S=] [LOG_DIR=]` |
| `make uat-execute` | `CONFIG=` `[DATABASES_ROOT=] [NO_CLEANUP=1]` |
| `make uat-validate` | `RESULTS_DIR= OUTPUT_TSV=` `[FLOOR=0.80]` |
| `make uat-package` | `CONFIG= SUBMISSIONS_DIR= RESULTS="r1.json r2.json ..."` |
| `make uat-report` | `CELLS_JSONL= OUTPUT_TSV=` `[RUNGS=0.01,0.1,1.0] [CROSS_SCALE_FLOOR=N]` |
| `make uat-sweep` | `CONFIG=` `[DRY_RUN=1]` |
| `python -m tests.uat._cli preflight` | `--config <path>` |
| `make uat-stress` | `[PLATFORM=] [BENCHMARK=] [SCALE=] [CONFIG=]` |
| `make uat-artifact-hygiene` | `[OUTPUT=<root>] [THRESHOLD_BYTES=N]` |
| `make uat-bring-up` | `PLATFORM=` `[TIMEOUT_S=300] [DRY_RUN=1] [BENCHMARK_RUNS_DIR=]` |
| `make uat-prepull` | `PLATFORM=` `[PREPULL_TIMEOUT_S=900] [DRY_RUN=1]` |
| `make uat-docker-cleanup` | `[ENGINE=docker\|container] [MODE=owned\|images\|max] [APPLY=1] [PREFIX=benchbox-uat]` |
| `make uat-gate-check` | `STAGE1= STAGE2= STAGE3=` `[OUTPUT=<path>]` |

**Invocation contract.**

- All targets accept `BENCHBOX_OUTPUT_DIR` for runs root, defaulting
  to `~/Developer/benchmark_runs/`.
- All targets exit non-zero on phase failure; the report phase exit
  code is the highest non-zero seen across phases.
- Logs land under `output.logs_dir_template` resolved from the YAML
  (or, for single-cell debugging, under `~/Developer/benchmark_runs/
  logs/uat_<date>/`).
- `make uat-stress` accepts `PLATFORM=`, `BENCHMARK=`, and `SCALE=`
  make variables for focused stress runs. Internally it loads
  `tests/uat/configs/stress-default.yaml` and applies a closed set of
  overrides defined below.

**Env-var override contract for `make uat-stress`.** Only three
overrides are recognised; any other `MAKE_VAR=` is ignored (no
silent passthrough):

| Env var | YAML field overridden | Type | Effect when unset |
|---|---|---|
| `PLATFORM` | `platforms.include` (set to `[$PLATFORM]`) and `platforms.groups` cleared | str |
| `BENCHMARK` | `benchmarks.include` (set to `[$BENCHMARK]`) and `benchmarks.groups` cleared | str |
| `SCALE` | `scales.override` (set to `float($SCALE)`); `scales.rungs` cleared | float |

If `PLATFORM` and `BENCHMARK` are both set, the YAML's `groups`
fields are both cleared and `include` lists are both replaced.
If `SCALE` and YAML's `scales.rungs` are both present, `SCALE`
wins.

Other targets (`make uat-cell`, `make uat-execute`, `make
uat-sweep`, etc.) do not honour these env vars; they read the
YAML config verbatim. The override mechanism is specific to the
canned stress preset.

**No `benchbox` CLI surface change (weaker than originally chartered).**
UAT follow-up work must not change user-visible `benchbox` commands or
options. This was originally enforced by
`tests/uat/test_no_cli_surface_drift.py` failing on any click
command/option/argument/group decorator change plus a `submit()`/`run()`
signature diff, with only the internal `submit.py` validator refactor
allowlisted. That guard has since been loosened by intervening,
individually-chartered CLI changes: `ALLOWED_INTERNAL_CLI_FILES` now
allowlists 29 `benchbox/cli/` files (module-level, not decorator-level --
any change to those files' CLI surface is unchecked), and
`ALLOWED_HIDDEN_COMPAT_CLI_FILES` (a subset of the above, currently 8
files including `submit.py` and `run.py`) skips the click-surface AST
diff entirely for files whose signature was intentionally changed by a
landed PR (`--funding`/`--notes` on `submit`, `--analyze-plans` on `run`).
The guard's real, current guarantee: click surface drift is caught only
for `benchbox/cli/` files **not** in `ALLOWED_INTERNAL_CLI_FILES`, and the
`submit`/`run`/`convert`/`visualize` signature-diff check
(`FORBIDDEN_CLI_SURFACE_FUNCTIONS`) only fires for files not in
`ALLOWED_HIDDEN_COMPAT_CLI_FILES`. It is a drift *tripwire* for
un-reviewed changes, not a hard freeze on the whole CLI surface.

## 5. Stress Workflow Retirement

`make uat-stress` is the sole local stress-test entrypoint. It loads
`tests/uat/configs/stress-default.yaml`, applies the closed
`PLATFORM=`, `BENCHMARK=`, and `SCALE=` override set described above,
and runs through the same typed config/orchestrator path as every other
UAT sweep. The retired shell workflow has no compatibility shim.

## 6. Replay artifact spec — `tests/uat/configs/uat-2026-05-02.yaml`

Encode the 2026-05-02 sweep as a historical replay config:

```yaml
name: "uat-2026-05-02"
description: "Historical replay of the 2026-05-02 results-explorer multi-scale corpus sweep"

phases: [preflight, execute, validate, package, report]

platforms:
  groups: ["sql", "dataframe"]
  exclude: []
benchmarks:
  groups: ["all"]
scales:
  rungs: [0.01, 0.1, 1.0]
execute:
  per_cell_timeout_s: 600
  early_stop_after_s: 180
  early_stop_on_failure: true
  phases_arg: "load,power"
  skip_unreachable: true
cleanup:
  preserve_datagen: true
  prune_databases: true
  docker_manage_platforms: false
  docker_platform_switch: "off"
preflight:
  free_space_min_gib: 5
  free_space_path: "~/Developer/benchmark_runs"
validate:
  validator_clean_rate_floor: 0.80
package:
  submit_terminal_state: "local-stage"
report:
  matrix_summary_tsv: "matrix_summary.tsv"
  cross_scale_coverage_min_pairs: null
output:
  logs_dir_template: "~/Developer/benchmark_runs/logs/uat_20260502_replay"
```

**Structural parity assertion (W10) — header-only, not a row-count
replay.** The slow-marked test `tests/uat/test_replay_2026_05_02.py`
runs the orchestrator in dry-run mode against this config and asserts
only that `tests.uat.phases.report.REPORT_HEADER` (the live code's
column list) matches the checked-in fixture
`tests/uat/fixtures/uat-2026-05-02-matrix-summary-header.tsv` byte-for-byte
(both currently 13 tab-separated columns, below). The test's own
docstring is explicit: "This is not a row-count replay." No candidate/
attempted/pruned cell count from the 2026-05-02 retrospective (1,530
candidate, 527 attempted, 133 ladder-pruned, 870 reachability-skipped) is
asserted anywhere in this test — `run_sweep`'s dry-run mode records 0 for
every phase and never writes the TSV body, so there is nothing to compare
row counts against. A second fast test,
`test_replay_config_has_expected_shape`, separately smoke-checks a
handful of the YAML's own keys (`name`, `scales.rungs`,
`execute.per_cell_timeout_s`, `execute.early_stop_after_s`,
`package.submit_terminal_state`, `report.cross_scale_coverage_min_pairs`).

```
platform | benchmark | scale | status | terminal_state | elapsed_s | log_path | result_path | submit_terminal_state | validator_status | source_commit_sha | source_dirty | throughput_check
```

The header has grown twice since the original 9-column TSV this section
once documented: `submit_terminal_state` was inserted in PR #247 between
`result_path` and `validator_status`, and `terminal_state`,
`source_commit_sha`, `source_dirty`, `throughput_check` were added later
(source-provenance and throughput-check plumbing) without a corresponding
spec update until now. External consumers parsing the TSV by positional
index must re-derive the column offset from `REPORT_HEADER`
(`tests/uat/phases/report.py`) rather than trust this document's prior
9- or 10-column count.

Wall-clock match is impossible (dry-run prints intent without
invoking benchbox); structural (header) parity is the bar.

**Historical status.** This config is historical evidence. The file's
first line includes `# HISTORICAL — record of the 2026-05-02 sweep. Do
not edit; if behaviour drifts from this config, fix the framework OR
clone to a dated successor.` Section 9 expands on the content policy.

## 7. Open questions for the user

Reply yes/no per question (or "accept defaults" to take all five
defaults). Anything unaddressed defaults to the "Default" line below.

1. **`tests/uat/` vs `tests/uat_runner/` for the package directory
   name?**
   - Default: `tests/uat/`. Brevity matches `tests/integration/`,
     `tests/e2e/`, `tests/performance/`.
   - Consequence of `tests/uat_runner/`: clearer intent but four
     extra characters in every import path.

2. **Does the W1 spec require an independent reviewer beyond the
   user?**
   - Default: no. User reviews; the adversarial framing in Section 8
     mitigates self-bias finding 084354.
   - Consequence of yes: file a sub-TODO for an independent reviewer
     pass before W2 unlocks.

3. **Should `make uat-stress` replace the retired shell workflow?**
   - Decision: yes. The framework owns stress-test matrix iteration;
     no thin delegator or compatibility shim remains.

4. **`tests/uat/configs/uat-2026-05-02.yaml` — historical replay
   (immutable historical record) or starting template (editable)?**
   - Default: historical replay. New sweep configs cloned from it via
     `cp tests/uat/configs/uat-2026-05-02.yaml tests/uat/configs/
     uat-NEW.yaml`. Section 9 codifies this.
   - Consequence of "starting template": reduces config sprawl but
     loses the historical-fidelity guarantee that motivates W10's
     parity test.

5. **`submit_terminal_state` default for the stress preset — should
   `make uat-stress` have NO package phase, or emit `local-stage` by
   default?**
   - Default: stress preset omits `package` from `phases:` entirely.
     Terminal-state is irrelevant for stress runs (verification
     focus is "does it run", not "does the bundle validate").
   - Consequence of "local-stage default": stress runs auto-package;
     extra disk+wall-clock for no return because the bundles are
     never submitted.

## 8. Adversarial framing — strongest argument against each design choice

Per blind-spot finding 084354 (self-bias risk in stress-tests),
self-designed adversarial framing tends to under-weight failure
modes the proposer didn't already consider. The user is the
independent reviewer; this section starts the conversation, not
ends it.

### 8.1 Against building a Python framework at all

> "Convention plus a small wrapper is sufficient. The next sweep author
> can read prior run artifacts for patterns and write a new bespoke
> driver. Framework adds engineering cost and a YAML schema to learn for
> marginal benefit."

**Counter.** The 2026-05-02 sweep author did read prior artifacts and
still re-invented timeout handling, matrix reporting, validation
rollups, and packaging. The "read for patterns" path empirically
produces drift, not reuse. The framework is the reusable boundary for
that machinery, not a new abstraction for its own sake.

### 8.2 Against `tests/uat/` location

> "UAT is operator tooling, not a test category. `tests/` should hold
> pytest tests. Put the framework in `_project/scripts/uat/` or
> `tools/uat/` and don't pollute the test directory."

**Counter.** Per user direction (2026-05-03), `_project/` is a mixed
dev-private tree (handoffs, blind-spots, audits, baselines); critical
machinery must live in tracked, version-controlled directories. The
existing `tests/` siblings (`integration/`, `e2e/`, `performance/`,
`system/`, `validation/`, `parity/`, `contracts/`) cover test-flavoured
infrastructure that doesn't fit the unit-test mould either. UAT is
structurally a long, side-effect-heavy, multi-platform test category;
`tests/uat/` follows the precedent. The fast tests added in W2/W3/W4
ARE pytest, covering unit-level logic (port maps, ladder pruning,
schema validation).

### 8.3 Against YAML configs

> "YAML is over-engineered for what amounts to a function call. Just
> let sweep authors write a Python script that calls `tests.uat.runner.
> sweep(...)` directly. Configs become docstrings."

**Counter.** The 2026-05-02 sweep parameters need to be replayable
deterministically. A Python script is mutable code; a YAML config
under version control is the historical record. Section 9's historical
content policy depends on YAML being declarative data, not code. The
schema doubles as documentation: a sweep author reads the schema in
Section 3 and knows what the framework can express.

### 8.4 Against `make` targets (vs a `benchbox uat` subcommand)

> "Adding a `benchbox uat` subcommand keeps everything in one CLI.
> Users already know `benchbox`; learning `make uat-*` is friction."

**Counter.** `benchbox` ships on PyPI to project users. UAT is a
project-developer concern; sweep machinery on PyPI bloats the user
CLI surface and pulls dev-only deps into the install. The repo
already uses `make` for every other developer concern (`worktree-*`,
`blind-spots-*`, `pr-*`, `dev-loop-metrics`); UAT joins the same
pattern. The friction of one `make` target name is real but
bounded; the friction of bloated user-CLI surface is permanent.

### 8.5 Against an explicit `submit_terminal_state` field

> "The four-word vocab is over-formalised. Sweep authors know what
> they meant. Inferring from the presence of a `--service` flag or
> a PR-target branch is enough."

**Counter.** Inference is exactly what bit the 2026-05-02 sweep
(Finding 3 in the methodology spec). The agent inferred `local-stage`
from validation evidence; the TODO could not have *forced* a
different choice. Required, declarative, no inference. The validation
rule in Section 3 ("required when package phase enabled") makes the
declaration unavoidable.

### 8.6 Against the structural-parity replay test

> "It's a slow test that asserts a TSV row count matches a historical
> file. The historical file is one snapshot; future code changes that
> legitimately alter ladder pruning will keep failing this test
> forever."

**Counter.** The test is `@pytest.mark.slow` and excluded from the
fast-test default. It runs on demand or when the framework's matrix
logic changes. When ladder pruning legitimately changes, the
historical file is updated as part of the same PR (the file is in
`~/Developer/benchmark_runs/logs/`, off-tree; the test compares a
snapshot copy committed under `tests/uat/fixtures/`). The
fixture-update flow makes intentional changes explicit.

### 8.7 Against opt-in `cross_scale_coverage_min_pairs`

> "Optional teeth nobody enables are decorative. Either default it on
> (forces UAT authors to think about cross-scale coverage) or remove
> it entirely (the methodology spec already covers this via convention)."

**Counter.** The methodology spec (Finding 1) explicitly scoped
tooling enforcement OUT — false-positive risk from refusing `done`
on a satisfied-but-via-absence-note unit. Default-off keeps the
framework consistent with that scoping. Removing the field entirely
forecloses the option of adding teeth later if the convention drifts;
keeping it opt-in lets a future sweep author enable it without a
schema change.

### 8.8 Against the W1 user-approval gate

> "An 11-work-unit TODO with a hard spec gate is over-process. Just
> ship W2-W11 and review at PR time. The user reviews PRs anyway."

**Counter.** Each W has its own PR per the TODO's "vertical slices"
discipline. Without a spec gate, the user reviews 11 PRs against an
implicit shared design. The spec gate is one review of one document
that preempts wrong directions across all 11. The 2026-05-02 sweep
ran with implicit shared design; the retrospective is the cost.

## 9. `tests/uat/configs/` content policy

The `tests/uat/configs/` tree holds three lifecycle classes, distinguished by
file-header comment and, for generated rerun shards, by location:

### 9.1 Editable templates

```yaml
# TEMPLATE — copy and edit for sweep-specific runs.
name: "stress-default"
...
```

- Mutable; PRs may edit them.
- New sweep authors clone a template, for example `cp
  tests/uat/configs/stress-default.yaml tests/uat/configs/uat-<new>.yaml`,
  and edit the copy.
- The canned `stress-default.yaml` is the canonical example; future
  templates (e.g. a `tpch-only-fast-platforms.yaml`) follow the same
  header convention.
- `# TEMPLATE` also covers canonical, repeatedly-run operational configs
  that are edited in place across releases rather than cloned per sweep
  (e.g. `release-gate-0{1,2,3}-*.yaml`, `uat-enabled-platforms-*.yaml`,
  `uat-throughput-duckdb-nightly.yaml`) -- the distinguishing trait is
  "mutable, not a one-shot historical snapshot", not "always cloned".

### 9.2 Historical replay configs

```yaml
# HISTORICAL — record of the 2026-05-02 sweep. Do not edit; if behaviour
# drifts from this config, fix the framework OR clone to a dated successor.
name: "uat-2026-05-02"
...
```

- Historical evidence; avoid editing in place.
- There is no hash ceremony. Reviews should reject gratuitous edits,
  but the framework does not maintain a separate frozen-file guard.

### 9.3 Generated rerun shards

Generated rerun shards are operational evidence emitted by a sweep's manual
follow-up -- an operator re-running a triaged subset of failed cells in a
smaller, cell-scoped config (often one file per platform). They live under
`tests/uat/configs/generated-rerun-shards/`, carry a generated/frozen header,
and must not be cloned as reusable starting points. This is unrelated to the
retired `--resume <manifest>` mechanism (see the "Resume (retired)" note in
Section 3): these shards are ordinary sweep configs, not runtime state.

### 9.4 PR conventions

| Action | Header | Reviewer expectation |
|---|---|---|
| Add a new editable template | `# TEMPLATE` | Verify the template's `phases:` list and defaults match a real reuse case. |
| Edit a `# TEMPLATE` file | `# TEMPLATE` | Standard review; no special protocol. |
| Add a new historical replay (post-sweep snapshot) | `# HISTORICAL` | Verify the `name:` field encodes the sweep's date/identity. |
| Edit a `# HISTORICAL` file | (manual review) | Prefer a dated successor unless the framework contract itself drifted. |
| Add a generated rerun shard | Generated header under `generated-rerun-shards/` | Verify it is frozen evidence from a named sweep, not a template. |

The conceptual policy is "templates are starting points; historical configs and
generated rerun shards are evidence."

**Enforcement.** `tests/uat/test_config.py::test_top_level_config_carries_recognized_lifecycle_header`
(uat-config-schema-spec-realignment w4) fails the fast lane if any top-level
config under `tests/uat/configs/` is missing a recognized `# TEMPLATE` /
`# HISTORICAL` first line -- this policy is no longer convention-only.
A companion corpus guard (`test_every_corpus_config_loads_and_enumerates_nonempty`,
w3) loads and enumerates every checked-in config, including
`generated-rerun-shards/`, asserting a non-empty cell set and zero unknown
`platforms.include`/`benchmarks.include` entries.

## 10. What UAT does NOT assert

The UAT framework is a release-gate orchestration layer: it proves that
configured platform/benchmark cells can be enumerated, executed,
validated, packaged, reported, and cleaned up under the current operator
workflow. It does not replace deeper benchmark-correctness suites.

> **Vocabulary (charter decision, 2026-05-31).** "Release-gate orchestration"
> is the chartered term for this layer. The earlier develop-only "certification"
> wording was never chartered here. Decision: Path A — use release-gate
> vocabulary for the operations surface; do **not** charter a standing
> cert-operations surface beyond the named evidence artifacts. The mechanical
> rename landed in the UAT runbook, staged configs, ordering helper, and tests:
> `docs/operations/uat-framework.md` "Three-stage UAT campaign",
> `tests/uat/configs/release-gate-0{1,2,3}-*.yaml`, and
> `tests/uat/phases/report.py:release_gate_ordering_violations`.

In particular, UAT does not assert query cardinality, per-query
measurement coverage, or stored answer row-count invariants for every
local SQL platform. Those checks remain in
`tests/integration/test_local_platform_benchmark_matrix.py`, which runs
real local platform x benchmark cases under the integration/stress
markers and validates the result payload against expected query and
row-count behavior. Keep that test when changing UAT: it covers a
different contract than the UAT execute/report phases.

The shared local-SQL platform set lives in `tests/uat/matrix.py` as
`LOCAL_SQL_PLATFORMS` so UAT platform grouping and the integration
matrix cannot drift silently. Changes to that tuple must account for
both release-gate ergonomics and the heavier invariant test's runtime
cost.

## 11. Migration of existing artifacts

| Artifact | Action | Owner |
|---|---|---|
| retired shell stress workflow | Removed; `make uat-stress` is the sole local stress entrypoint | W11 |
| `scripts/uat_validator_rollup.py` | Removed; `tests/uat/phases/validate.py` owns the roll-up in-process |
| `scripts/validate_submission.py` | Thin CLI wrapper around `benchbox.validation.bundle`; still used by published-results CI |
| `benchbox/validation/bundle.py` | Shared public bundle validator mirrored to `published-results` with the script wrapper |
| `~/Developer/benchmark_runs/logs/uat_20260502/` | Snapshot a copy of `matrix_summary.tsv` to `tests/uat/fixtures/uat-2026-05-02-matrix-summary.tsv` for the W10 parity test |
| `_project/handoffs/results-explorer-uat-retrospective-20260502.md` | No retrofit; historical document |
| `tests/uat/configs/uat-2026-05-02.yaml` | New file at W10; HISTORICAL |
| `tests/uat/configs/stress-default.yaml` | New file at W9; TEMPLATE |
| `Makefile` | Add `uat-cell` (W3), `uat-execute` (W4), `uat-validate` (W5), `uat-package` (W6), `uat-explorer-smoke` (W7), `uat-report` (W8), `uat-sweep`/`uat-stress` (W9) |
| `CLAUDE.md` Pre-approved Commands | Add `make uat-*` entries | W11 |
| `docs/operations/uat-framework.md` | New operator guide |
| `tests/uat/README.md` | New developer guide |

## 12. Implementation sequencing and PR cadence

One PR per work unit, vertical slices. No monolithic delivery.

| Work unit | Delivers | Test added | Make target |
|---|---|---|
| W1 | This spec | (none) | (none) |
| W2 | `tests/uat/matrix.py`, `tests/uat/test_matrix.py` (matches the parent TODO's W2 scope verbatim — matrix machinery only) | Fast | (none) |
| W3 | `tests/uat/runner.py`, `tests/uat/timeouts.py`, `tests/uat/config.py` (minimal — only the fields W3 needs: top-level `name`, `execute.per_cell_timeout_s`, `execute.phases_arg`), `tests/uat/test_runner.py`, `tests/uat/test_timeouts.py`, `tests/uat/test_config.py` | Fast | `make uat-cell` |
| W4 | `tests/uat/ladder.py`, `tests/uat/cleanup.py`, `tests/uat/phases/{preflight,enumerate,execute}.py`, full `config.py` schema coverage (remaining sections), fast tests | Fast | `make uat-execute` |
| W5 | `tests/uat/phases/validate.py`, fast test | Fast | `make uat-validate` |
| W6 | `tests/uat/phases/package.py`, fast test | Fast | `make uat-package` |
| W7 | `tests/uat/phases/explorer_smoke.py`, fast test | Fast | `make uat-explorer-smoke` |
| W8 | `tests/uat/phases/report.py`, fast test | Fast | `make uat-report` |
| W9 | `tests/uat/orchestrator.py`, `tests/uat/configs/stress-default.yaml`, fast test for orchestrator | Fast | `make uat-sweep`, `make uat-stress` |
| W10 | `tests/uat/configs/uat-2026-05-02.yaml`, `tests/uat/fixtures/uat-2026-05-02-matrix-summary.tsv`, `tests/uat/test_replay_2026_05_02.py` | Slow | (none) |
| W11 | `docs/operations/uat-framework.md`, `tests/uat/README.md`, `CLAUDE.md` updates, optional bash-script delegation | (no new tests) | (none) |

**Verification at each W.** Per the parent TODO's `verification:` block,
each PR adds tests and the sweep verification commands stay green. The
final-state verification commands (after W11) are:

```bash
test -f _project/specs/uat-framework.md
make uat-cell PLATFORM=duckdb BENCHMARK=tpch SCALE=0.01
make uat-stress PLATFORM=duckdb BENCHMARK=tpch
make uat-sweep CONFIG=tests/uat/configs/uat-2026-05-02.yaml DRY_RUN=1
uv run -- python -m pytest -m fast -q
test ! -f scripts/local_stress_test.sh
test ! -f scripts/uat_validator_rollup.py
uv run -- python -m tests.uat._cli --help
uv run -- python -m pytest tests/uat/test_no_cli_surface_drift.py -q
```

## 13. Open risks, watched but not blocking

- **Risk: timeout wrapper semantics drift.**
  Mitigation: the fast test exercises the timeout wrapper with a
  3-second sleep and a 1-second cap; exit-code semantics remain
  (124 on timeout).
- **Risk: registry-driven enumeration races a registry rename.**
  Mitigation: the framework imports `benchbox.core.benchmark_registry`
  directly (no eval, no shell-out); a registry rename surfaces as a
  Python ImportError at enumerate time, not a silent skip.
- **Risk: parallel-platform restriction is enforced at config load
  but bypassable via direct `phases/execute.py` invocation.**
  Mitigation: `phases/execute.py` itself reads
  `config.execute.parallel_platforms` and asserts False; the YAML
  validation is the first line of defence, the runtime assertion is
  the second.
- **Risk: the 2026-05-02 retrospective TSV row count drifts from the
  W10 fixture.** Mitigation: the fixture is a snapshot copy committed
  under `tests/uat/fixtures/`; the historical log file under
  `~/Developer/benchmark_runs/` may be tidied off the host without
  breaking the parity test.

## Appendix — pointers to source material

- Source TODO: `_project/TODO/main/planning/uat-framework-tests-uat-runner.yaml`
- Source blind-spot: `_project/blind-spots/2026-05-03-130000-stress-script-uat-driver-drift.md`
- Sibling spec (consumed): `_project/specs/uat-methodology-blind-spot-remediation.md`
- 2026-05-02 sweep retrospective: `_project/handoffs/results-explorer-uat-retrospective-20260502.md`
- Bundle validator (consumed): `benchbox.validation.bundle`
- Retired shell stress workflow: superseded by `make uat-stress`
- Self-bias caveat: `_project/blind-spots/2026-05-03-084354-stress-test-self-bias.md`
- Spec ergonomics caveat: `_project/blind-spots/2026-05-03-084923-spec-approval-ergonomics.md`
