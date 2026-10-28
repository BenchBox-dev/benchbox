# tests/uat — Developer Guide

This is the framework developer's guide. The operator guide is
https://benchbox.dev/docs/operations/uat-framework.html; the design contract is
`_project/specs/uat-framework.md`.

## Layout

```
tests/uat/
├── __init__.py
├── _cli.py                        # argparse entry points for every make uat-* target
├── matrix.py                      # platform/benchmark dicts + reachability + registry import
├── runner.py                      # single-cell execution
├── config.py                      # typed YAML schema validation
├── timeouts.py                    # signal-based subprocess timeout
├── ladder.py                      # scale-ladder + early-stop
├── cleanup.py                     # reuse-aware database pruning
├── docker_assets.py               # UAT-owned Docker compose mapping/lifecycle helpers
├── orchestrator.py                # sweep orchestration (make uat-sweep entry point)
├── phases/
│   ├── preflight.py
│   ├── enumerate.py               # cell helper used by execute; not a configured phase
│   ├── execute.py
│   ├── validate.py
│   ├── package.py
│   └── report.py
├── configs/
│   ├── stress-default.yaml        # TEMPLATE preset for `make uat-stress`
│   ├── uat-2026-05-02.yaml        # HISTORICAL replay of the sweep
│   └── generated-rerun-shards/    # Generated frozen evidence, not templates
├── fixtures/
│   └── uat-2026-05-02-matrix-summary-header.tsv
├── test_*.py                      # fast-test coverage (mark.fast)
└── test_replay_2026_05_02.py      # slow-marked structural-parity assertion
```

## Adding a phase

1. Create `tests/uat/phases/<name>.py` with a top-level `run_<name>`
   function that takes a `UATConfig` (or just the inputs it needs) and
   returns a `tests.uat.phases.PhaseResult`-compatible dataclass.
2. Add `<name>` to `tests/uat/config.py::VALID_PHASES`.
3. Wire the phase into `tests/uat/orchestrator.py::run_sweep`.
4. Add an argparse subparser handler in `tests/uat/_cli.py`.
5. Add a `make uat-<name>` target to the Makefile.
6. Add fast tests to `tests/uat/test_<name>.py`.

## Running tests

```bash
uv run -- python -m pytest tests/uat -q -m fast

uv run -- python -m pytest tests/uat -q -m "fast or slow"
```

The first command runs only the fast tests, which is the default for `make test-fast`. The second runs all tests, including the slow-marked replay assertion.

## Matrix And Connection Sources Of Truth

`tests/uat/matrix.py` is the framework-owned source of truth for UAT
platform and benchmark groups, uv extras, and per-platform CLI flags.
`tests/uat/docker_assets.py` owns Docker compose connection facts:
reachability endpoints, compose-derived host ports, and platform options
that must follow those ports. Keep updates in the same PR as the tests
that exercise the affected matrix or connection behavior.

## Sequential platform execution

`config.execute.parallel_platforms` exists as a reserved field that
must be `False`. Setting it `True` raises at config load time; the
execute phase additionally asserts `False` at runtime as a second
line of defence. Do not delete either guard. Origin of the rule:
UAT W3 line 222 in
`_project/handoffs/results-explorer-uat-retrospective-20260502.md`.

## Runtime output root

UAT sweeps default to the shared `~/Developer/benchmark_runs` root,
not the current worktree. `output.benchmark_runs_dir_template` is
resolved once per sweep and passed to every `benchbox run` subprocess
as `BENCHBOX_OUTPUT_DIR`; preflight and reuse-aware database cleanup
derive their default roots from the same value. Keep these paths
aligned whenever adding a phase that reads or writes run artefacts.

## Docker lifecycle boundary

Docker lifecycle belongs in `phases/execute.py`, not in platform
adapters and not in `benchbox run`. The execute phase is the only layer
that knows when all cells for one platform have finished and the next
platform is about to start, so it can preserve same-platform reuse while
releasing UAT-owned Docker volumes before the next platform.

`docker_assets.py` owns the platform → compose-file mapping, project-name
sanitizer, and command builders. Automated commands must always include
`docker compose -p <uat-owned-project> -f <compose-file>` and must never
use global prune commands. The current managed-start contract is:

- `cleanup.docker_manage_platforms: false` (default): keep existing
  external-stack behavior; reachability probes decide whether Docker
  platforms run or skip, and Docker cleanup is reported as disabled.
- `cleanup.docker_manage_platforms: true`: start a deterministic
  `benchbox-uat-...` compose project before probing a Docker platform and
  tear down only that project in a `finally` block at platform completion.
- `cleanup.docker_platform_switch: volumes`: the storage-saving mode;
  generates `down -v --remove-orphans` for the UAT-owned project only.

Compose files must remain project-scoped for managed startup. Do not add
fixed `container_name` values to UAT-managed compose files; they bypass
the `docker compose -p <uat-owned-project>` namespace and can collide
with other local runs. Keep fast tests for this mapping in sync with
`matrix.PLATFORM_GROUPS["docker"]`.

Interrupted-run recovery is explicit:

```bash
make uat-docker-cleanup
make uat-docker-cleanup APPLY=1
```

The first command is a dry run that prints an inventory and the commands it would run. The second applies the cleanup.

The recovery command removes only compose-labelled projects whose name
starts with the UAT prefix (`benchbox-uat` by default). It also reports
non-UAT Docker resources with creation time and a manual cleanup command,
but it never deletes them automatically.

## Config lifecycle

Files under `tests/uat/configs/` are classified by first-line header and,
for generated shards, by location:

- `# TEMPLATE`: editable starting points for new sweeps.
- `# HISTORICAL`: evidence snapshots; avoid editing in place unless framework
  behavior changed and the same PR updates the relevant tests/docs.
- `generated-rerun-shards/`: generated frozen rerun evidence, not reusable
  templates.

Clone a template for a new sweep:

```bash
cp tests/uat/configs/stress-default.yaml tests/uat/configs/uat-<new>.yaml
```

There is no frozen-hash file or hash-enforcement test.

## Dependencies

The framework imports registry truth directly from `benchbox.core.*`
helpers and validates result bundles in-process through
`benchbox.validation.bundle`. It does not shell out to validator scripts.
Package still delegates to `benchbox submit`.
