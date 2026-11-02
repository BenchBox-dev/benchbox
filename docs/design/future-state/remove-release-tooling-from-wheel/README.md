<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# benchbox-maintainer Future State

Related TODO: `remove-release-tooling-from-wheel`

## Status (2026-08-13)

**Blocked on evidence for further extraction.** The default wheel already
contains no `benchbox-maintainer`, `benchbox/release`, or maintainer/sync
package paths. The product publishing paths that remain in the wheel are not
maintainer-package evidence and must not be removed under this proposal.

Measured on `origin/develop` at `723126bf3` with `uv build --wheel`. Reproduction
steps are in `_project/decisions/future-state-extraction-evidence-2026-08-13.md`.

| Measure | Result |
| --- | --- |
| Wheel | 10,219,657 bytes; 1,325 archive entries |
| Release/maintainer/sync package-path entries | 0 |

The remaining gate is demand, CI-minute impact, and release-cost evidence for
creating a companion maintainer package. No CI-minute or release-cost
measurement was taken. No extraction or packaging change is authorized by this
status.

## Future State

BenchBox core ships benchmarking, comparison, and reporting features only.
Maintainer-only release automation, repository sync flows, and content
validation move into a separate maintainer package with its own entry points and
dependency profile.

## Why This Is Valuable

- End users no longer install or discover maintainer commands by accident.
- Maintainer automation can evolve without distorting the core package boundary.
- CI and release workflows gain an explicit toolchain instead of depending on
  product-package internals.

## How The End State Is Used

Core users install only BenchBox:

```bash
uv add benchbox
benchbox run --platform duckdb --benchmark tpch --scale 0.01
```

Maintainers install the maintainer package explicitly:

```bash
uv add benchbox-maintainer
benchbox-maintainer sync status
benchbox-maintainer release prepare
benchbox-maintainer content-validate docs/
```

## BenchBox After The Refactor

- `benchbox` remains the public runtime CLI.
- Release and sync logic are no longer part of the default install surface.
- BenchBox may keep a short-lived compatibility shim for migration, but the
  steady state is a clean separation between product and maintainer tooling.

## Non-Goals

- Changing benchmark execution semantics
- Folding maintainer dependencies back into the default BenchBox install
