# ADR: Move Explorer Publishing Out of the BenchBox CLI

## Status

Accepted, and implemented. `benchbox explorer` is no longer registered on the
CLI, and `benchbox/cli/commands/explorer.py` and
`benchbox/core/explorer_pipeline/` have been removed. Subsequently,
`results-explorer/` moved to `BenchBox-dev/benchbox-site`
([`adr-site-repo-split.md`](adr-site-repo-split.md)), and core now invokes
`_project/scripts/explorer_publish.py` through `scripts/site_inputs.py`. The
sections below describe the tree and consumers as they stood when this decision
was taken, not the current post-split layout.

## Date

2026-05-14

## Context

`benchbox explorer build` publishes the static DuckDB read model consumed by
`results-explorer`. That work is a maintainer and CI publishing operation, not
a benchmark-runner workflow. Today it is registered on the public `benchbox`
Click surface and backed by importable package code under
`benchbox/core/explorer_pipeline/`.

### Provenance Facts

The current surface was not introduced by a discrete reviewed API decision.
The evidence was re-validated for TODO `explorer-cli-surface-adr`:

- `git show --stat f9d08d38b -- benchbox/cli/commands/explorer.py` shows
  `benchbox/cli/commands/explorer.py` added as a 221-line file.
- `git log -1 --format=%s%n%n%b f9d08d38b` identifies the commit as
  `Migration: populate develop branch from private working tree (Phase 4 w4)`.
- `gh pr view 46 --json title,body | head -2` shows PR #46 was
  `docs(contributing): name the version JSON key + non-uv fallback`, not an
  Explorer CLI PR.
- `git log --all --diff-filter=A --oneline -- benchbox/cli/commands/explorer.py`
  shows only `f9d08d38b` and the later consistency-fix PR `d28dbe66f`.
- `git blame .github/workflows/docs.yml -L 100,108` shows the stale
  "added in PR #46" breadcrumb came from release commit `0182c9556c`.

The in-tree breadcrumb is therefore misleading, and the CLI surface has no
durable design artifact.

## Current Surface Inventory

### Live Command Callers

Historical TODOs, handoffs, and audits also mention the old command, but those
records should keep the command that was actually run at the time. The migration
PR should update only live callers and active docs.

| Site | Category | Current binding | Migration cost |
| --- | --- | --- | --- |
| `.github/workflows/docs.yml:103` | CI publish | `uv run benchbox explorer build --data-dir results-data/ --output results-explorer/public/data/` | One-line command replacement; delete the false PR #46 note at line 105. |
| `benchbox/cli/commands/__init__.py:22,62,106` | Public Click registration | imports, registers, and exports `explorer_group` | Small removal; verifies through `benchbox --help`. |
| `benchbox/cli/commands/explorer.py` | CLI wrapper | Click group with `build` and hidden `build-contract` | Move behavior to a maintainer script entry point. |
| `benchbox/core/explorer_pipeline/contract.py:9` | Contract source | declares `"command": "benchbox explorer build"` | One-line command update plus version bump if contract shape changes. |
| `results-explorer/scripts/explorer-build-contract.mjs:17,30,38` | Node contract reader | expects `benchbox explorer build` and calls `benchbox explorer build-contract` | Multi-line update; this should remain the JS source of truth for fixture callers. |
| `results-explorer/scripts/generate-browser-fixtures.mjs:489` | Fixture generation | runs `uv run -- ...contract.commandArgs` | Mostly automatic once contract command args change; error string update. |
| `tests/uat/phases/explorer_smoke.py:51` | UAT smoke | constructs `["benchbox", "explorer", "build", ...]` | Small helper update plus fast tests. |
| `tests/uat/test_explorer_smoke.py` | UAT tests | asserts the legacy argv | Small assertion updates. |
| `tests/unit/scripts/test_explorer_build_contract.py` (moved from `tests/unit/cli/`) | Contract tests | imports the migrated `_project.scripts.explorer_pipeline` contract | Relocated to mirror the migrated source. |
| `docs/internal/browser-test-architecture.md:35,119,130` | Developer docs | describes `benchbox explorer build` | Three doc replacements. |
| `docs/internal/results-phase-2-runbook.md` | Operations runbook | describes rerunning Explorer build/deploy | Replace with maintainer command where applicable. |
| `results-explorer/src/db.ts:196` | Runtime remediation string | tells users to run `benchbox explorer build` | Replace with published-snapshot guidance and maintainer-only command. |
| `results-explorer/src/lib/__tests__/duckdbColumnGuard.test.ts:75` | Frontend test | asserts old remediation string | Update with the runtime message. |
| `results-explorer/src/__tests__/userFacingStringHygiene.test.ts:117` | String hygiene test | allows a sample with old command | Update fixture text. |
| `results-explorer/test-fixtures/source/README.md:7` | Fixture docs | says fixture generation runs old command | One-line doc update. |
| `_project/specs/uat-framework.md` | Active UAT spec | describes `benchbox explorer build` in the UAT phase | Update if the spec remains active; historical examples can stay if explicitly historical. |
| `CLAUDE.md:88` and `Makefile:1611-1614` | UAT make target | expose `make uat-explorer-smoke`, not the Explorer build command directly | Usually unchanged; only comments/docs need adjustment if they name the old command. |

### Python Import Surface

Imports outside `benchbox/core/explorer_pipeline/` fall into three groups:

| Importer | Examples | Classification | Migration cost |
| --- | --- | --- | --- |
| CLI wrapper | `benchbox/cli/commands/explorer.py` imports the contract and `ExplorerPipeline` | Wrapper only; should move with the entry point | Small. |
| Unit tests | `tests/unit/scripts/explorer_pipeline/*`, `tests/unit/scripts/test_explorer_build_contract.py` (both relocated from `tests/unit/core/`/`tests/unit/cli/` to mirror the migrated source), `tests/unit/core/test_platform_labels.py` | Test coverage, not a production API promise | Relocated to mirror the migrated source. |
| Historical project docs | `_project/DONE/main/active/explorer-emit-comparison-artifact-from-pipeline.yaml` | Completed planning record | Do not rewrite. |

There are no production benchmark-runner imports of
`benchbox.core.explorer_pipeline.*` outside the CLI wrapper.

## Forces

| Force | Why it matters |
| --- | --- |
| User vs maintainer surface separation | `benchbox --help` should show benchmarker workflows, not site-publishing internals. |
| CI stability | Docs deploy, browser fixtures, and UAT need a deterministic command that runs in a repository checkout. |
| Import hygiene | Keeping Explorer publishing under `benchbox.core` makes it look like a supported package API. |
| Migration cost | This is a refactor; the output contract and generated DuckDB snapshot must not change. |
| Reversibility | If the Explorer later becomes a productized command, it should be possible to wrap the internal tool intentionally. |
| Dependency containment | The publishing pipeline depends on BenchBox internals and should not create a new installable dependency story unless there is an actual external consumer. |

## Options Considered

### 1. Hide Only

Mark `explorer_group` hidden and leave the implementation under `benchbox/`.

Benefits:

- Cheapest migration.
- No caller changes if hidden subcommands remain callable.

Drawbacks:

- Fails the core goal: the command and `benchbox.core.explorer_pipeline`
  remain shipped package surface.
- Leaves the false docs workflow breadcrumb in place unless separately fixed.
- Future users can still discover or depend on the command through scripts.

### 2. New Sibling Package

Create `benchbox_explorer/` with its own console script, for example
`benchbox-explorer-publish`.

Benefits:

- Clean conceptual boundary from `benchbox`.
- Provides a stable binary if external users ever need to publish Explorer
  snapshots themselves.

Drawbacks:

- Still creates a public installable surface, just with a different name.
- Adds packaging, release, docs, and dependency-management work for a tool that
  currently has only in-repo CI and maintainer consumers.
- Harder to run from `results-explorer` fixture scripts without deciding
  whether the sibling package is installed by default.

### 3. In-Tree Maintainer Script Under `_project/scripts`

Move the entry point to `_project/scripts/explorer_publish.py` and move the
pipeline package to `_project/scripts/explorer_pipeline/`. Invoke it as:

```bash
uv run -- python _project/scripts/explorer_publish.py build --data-dir results-data --output results-explorer/public/data
```

The hidden contract command becomes:

```bash
uv run -- python _project/scripts/explorer_publish.py build-contract
```

Run this through the repository-root `pyproject.toml`, not through
`uv run --project _project/scripts`. The publisher depends on the BenchBox
source tree and its normal runtime dependencies; `_project/scripts/pyproject.toml`
is for isolated administrative utilities that deliberately avoid the package
runtime.

Benefits:

- Removes both the public Click command and the `benchbox.core` Explorer
  publishing import surface from the shipped package.
- Matches the existing convention that `_project/scripts` hosts internal
  maintainer tooling that is not part of the wheel.
- Works in every current live caller because CI, UAT, and fixture generation
  all run from a repository checkout.
- Does not introduce a second package or console-script lifecycle.

Drawbacks:

- The command is longer than a console script.
- It is not available from an installed `benchbox` wheel. That is deliberate,
  but docs and error messages must stop implying end users should run it.
- Tests must move or update imports from `benchbox.core.explorer_pipeline`.

### 4. Co-Locate Under `results-explorer/scripts`

Move the Python publisher next to the React app scripts.

Benefits:

- Strong locality with the only runtime consumer.
- Makes the "publishes the Explorer app" ownership obvious.

Drawbacks:

- Mixes a Python package and Python tests into the Node application tree.
- The publisher still needs BenchBox Python internals and the repository root;
  locating it under `results-explorer/` does not remove that coupling.
- Less consistent with existing internal Python tooling, which already lives
  under `_project/scripts`.

## Decision Matrix

Scores are 1-5, higher is better.

| Dimension | Weight | Hide only | Sibling package | `_project/scripts` | `results-explorer/scripts` |
| --- | ---: | ---: | ---: | ---: | ---: |
| Removes user-facing `benchbox` CLI surface | 0.25 | 2 | 5 | 5 | 5 |
| Removes shipped `benchbox.core` import surface | 0.20 | 1 | 5 | 5 | 5 |
| Fits current consumers | 0.20 | 5 | 3 | 5 | 4 |
| Avoids new packaging/release surface | 0.15 | 5 | 1 | 5 | 4 |
| Migration simplicity | 0.10 | 5 | 3 | 4 | 3 |
| Future reversibility | 0.10 | 2 | 4 | 4 | 3 |
| **Weighted total** | **1.00** | **2.75** | **3.95** | **4.85** | **4.20** |

## Recommendation

Choose option 3: move the Explorer publishing entry point and the
Explorer-pipeline implementation to `_project/scripts`.

This is the only option that fully removes the accidental public BenchBox
surface while avoiding a new productized package for a maintainer-only tool.
The known callers all execute inside the repository, so they do not need an
installed console script.

## Sub-question A: Does `benchbox/core/explorer_pipeline/` relocate?

Yes. The inventory shows no production benchmark-runner dependency on this
package outside the `benchbox explorer` wrapper. Leaving it under
`benchbox.core` would preserve the exact public-ish import surface this ADR is
trying to unwind. Move it to `_project/scripts/explorer_pipeline/` and update
tests to import the new internal location.

## Sub-question B: Is a one-release compatibility alias warranted?

No. BenchBox is pre-1.0, the command is a maintainer publishing tool, and every
known live caller is in this repository. Keeping a `benchbox explorer` shim
would leave the stale surface callable and would require users to see a
deprecation warning for a command they should not run. The migration PR should
hard-cut live callers and remove the Click registration.

## Sub-question C: What should stale-snapshot users see?

The frontend should not tell non-maintainers to run an internal publishing
command. The runtime error should distinguish two audiences:

- End users or local app developers should refresh the published snapshot or
  use the dev snapshot workflow once `explorer-dev-snapshot-dx` lands.
- Maintainers rebuilding the public snapshot should run the canonical
  `_project/scripts/explorer_publish.py build` invocation.

The canonical invocation should live in the Explorer build contract so Node
scripts and remediation tests do not duplicate it.

## Consequences For The Migration TODO

`explorer-cli-surface-migration` should:

1. Move `benchbox/cli/commands/explorer.py` behavior into
   `_project/scripts/explorer_publish.py`.
2. Move `benchbox/core/explorer_pipeline/` to
   `_project/scripts/explorer_pipeline/`.
3. Remove `explorer_group` import, registration, and `__all__` export from
   `benchbox/cli/commands/__init__.py`.
4. Update the build contract command to
   `uv run -- python _project/scripts/explorer_publish.py build`.
5. Update the contract reader to call
   `uv run -- python _project/scripts/explorer_publish.py build-contract`.
6. Update CI docs build, browser fixture generation, UAT smoke, tests, active
   docs, and the frontend remediation string from the inventory above.
7. Delete the false PR #46 note from `.github/workflows/docs.yml`.
8. Verify `uv run benchbox --help | grep -c '^\\s*explorer'` returns `0`.

## Rejected Alternatives

1. **Hide-only.** Rejected because it treats discoverability as the problem,
   while the real problem is an accidental shipped CLI and import surface.
2. **Sibling package now.** Rejected because no current consumer needs an
   installed public package, and adding one would create a second API lifecycle
   before BenchBox has proven external demand for self-hosted Explorer
   publishing.
3. **Move only the Click wrapper.** Rejected because
   `benchbox.core.explorer_pipeline` would remain a public-looking package
   despite serving only the Explorer publisher.
4. **Put Python under `results-explorer/scripts`.** Rejected because it couples
   Python package tests and BenchBox internals into the Node app tree. It is
   better than the status quo, but `_project/scripts` matches existing
   maintainer-tool conventions more directly.
