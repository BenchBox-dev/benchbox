<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Architecture Contract Decision Index

This index connects future-state proposals and compatibility registry decisions
to the active implementation surfaces they govern. It does not replace the
[public contracts page](https://benchbox.dev/docs/reference/public-contracts.html);
it is the planning-side index for decisions that are not fully implemented yet.

| Decision | Status | Owner | Blocking gate | Current implementation surface |
|---|---|---|---|---|
| Formalize MCP internal API references as public exports | Future-state proposal, Tier 2 | mcp | Export tests for the three symbols and no `benchbox.cli` command imports from MCP. | `docs/design/future-state/formalize-mcp-internal-apis/README.md`, `benchbox/mcp/` |
| Define benchmark family plugin seam before core package extraction | Pilot complete on SSB; further migration stopped pending new evidence | benchmark-api | Keep public wrapper tests and the SSB plugin contract green; do not migrate another family until a runtime consumer of `phases()` / `result_metadata()` or a measured family-migration cost table provides new evidence. | `docs/design/future-state/benchmark-family-plugin-seam/README.md`, the [public contracts page](https://benchbox.dev/docs/reference/public-contracts.html), `benchbox/core/benchmark_loader.py`, `benchbox/core/ssb/family.py` |
| Remove maintainer/release tooling from the wheel | Blocked on evidence for further extraction | packaging/release | Measure demand, CI-minute impact, and release cost after confirming the current wheel has no maintainer-package paths. | `docs/design/future-state/remove-release-tooling-from-wheel/README.md`, `MANIFEST.in`, `pyproject.toml` |
| Isolate experimental core subsystems | Blocked on evidence for further extraction | architecture | Measure demand, install-size benefit, CI burden, and release cost before creating a companion package or extra; current wheel contents remain unchanged. | `docs/design/future-state/isolate-experimental-core-subsystems/README.md`, `benchbox.experimental`, `pyproject.toml` |
| Gate monitoring behind an optional extra | Blocked on evidence | core-runtime | Demonstrate an install-size win and a second-consumer or demand case; `psutil` remains core and monitoring remains in the default wheel. | `docs/design/future-state/gate-monitoring-behind-optional-extra/README.md`, `benchbox/monitoring/`, `pyproject.toml` |
| Preserve top-level wrapper facades while benchmark API cleanup proceeds | Active compatibility boundary | benchmark-api | Wrapper facade tests stay green or a deprecation row/migration lands. | the [backward-compatibility policy](https://benchbox.dev/docs/reference/backward-compatibility.html), `tests/unit/test_wrapper_facades_fast.py`, top-level `benchbox/*.py` wrappers |
| Retain `benchbox.core.base_benchmark.BaseBenchmark` during staged migration | Deprecated compatibility surface | core-runtime | Dedicated migration/removal item completes and updates the compatibility registry. | the [backward-compatibility policy](https://benchbox.dev/docs/reference/backward-compatibility.html), `benchbox/core/base_benchmark.py` |
| Keep composition below CLI and MCP in the core kernel | Accepted architecture boundary | architecture | Remove allowlisted core-to-platforms edges in the ADR's order; canonicalize the SQL execute primitive before broad injection work. | the [runtime composition boundary ADR](https://benchbox.dev/docs/development/adr/adr-runtime-composition-boundary.html), `benchbox/core/run_service.py`, `benchbox/core/runner/runner.py` |

## Review Rule

When a future-state proposal becomes implementation work, the PR should update
both this index and the public contract map if it changes a public,
beta-public, generated, deprecated, or experimental surface.
