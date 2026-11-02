<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Future State Proposals

These documents describe the intended end state for high-impact cleanup and
refactoring TODOs. Each proposal is tied to a planning item under
`_project/TODO/main/planning/` and makes the destination architecture explicit:
what gets pruned, excluded from the wheel, or reorganized within BenchBox.

The priority labels below are planning hypotheses, not measured demand or cost
claims. Package size, cold-import time, CI touchpoints, user demand, and support
obligations must be measured before a distribution or extraction decision is
treated as actionable. The support-tier freeze is
`_project/decisions/architecture-support-tier-commitment.md`. Current extraction
evidence is
`_project/decisions/future-state-extraction-evidence-2026-08-13.md`. The latest
bounded CI-burden measurement, collected 2026-08-31, is recorded in
[`_project/analysis/ci-waste-remeasure-2026-08-31.md`](https://github.com/BenchBox-dev/BenchBox/blob/develop/_project/analysis/ci-waste-remeasure-2026-08-31.md);
it does not authorize extraction and the candidates below remain blocked on
their stated evidence gates.

## Sequencing and Priority

Not all proposals should be pursued simultaneously. Adversarial review
established the following priority tiers based on coupling analysis and effort.
The current wheel/import measurements do not establish demand, CI-minute
savings, release-cost savings, or a material install-size win for further
extraction, so the two Tier 1 cleanup proposals are evidence-gated rather than
implementation-ready.

**Tier 1 candidates: evidence-gated (no further extraction authorized):**
- **artifactlinks**: Completed in v0.2.1 — the old generic publishing layer was
  pruned. `benchbox/core/publishing/` now hosts the live, CLI-integrated
  bundle-publish subsystem; do not prune it. See
  [Prune publishing](prune-publishing-subsystem/README.md).
- **benchbox-maintainer**: The current wheel contains no `benchbox-maintainer`,
  `benchbox/release`, or maintainer/sync package paths. Any further companion
  package split is **Blocked on evidence** for demand, CI burden, and release
  cost.
- **benchbox-experimental**: The current wheel still contains 24
  `benchbox.experimental` entries (307,255 uncompressed bytes). Further
  extraction is **Blocked on evidence** for demand, install-size benefit, CI
  burden, and release cost.

**Tier 2: Active proposals and evidence gates:**
- **Benchmark family plugin seam**: **Pilot complete on SSB; further family
  migration is blocked on evidence.** Do not migrate another family until a
  runtime consumer of `phases()` / `result_metadata()` or a measured
  family-migration cost table provides new evidence. See the accepted decision
  record in `_project/decisions/arch-pilot-evaluation-2026-08-20.md`.
- **MCP APIs**: Already an optional extra. Formalize 3 internal API refs as public exports. Defer distribution split until post-v1.0.
- **Monitoring**: **Blocked on evidence**. The current wheel contains five
  monitoring entries and `psutil>=5.9.0` remains a core dependency; no
  measured install-size win justifies an optional extra yet.

Two proposals were discarded during adversarial review:
- **sqlplankit** (query-plan extraction): 37-file blast radius, shared-type
  boundary problem (`QueryPlanDAG` embedded in core result model), no external
  demand. Internal boundary improvements can be done without a package split.
- **todo-dag**: `_project/` does not ship to users, no second consumer exists.
  Extracting a package for single-repo contributor tooling is package sprawl.

## Proposals

- [Architecture contract decision index](contract-index.md) - active planning
  index for public-contract and future-state decisions
- [Benchmark family plugin seam](benchmark-family-plugin-seam/README.md) -
  **Pilot complete; further family migration blocked on evidence**
- [Prune publishing](prune-publishing-subsystem/README.md) - **Completed (v0.2.1)**; retained as a historical record (the path now hosts the live bundle-publish subsystem)
- [Remove release tooling from wheel](remove-release-tooling-from-wheel/README.md) - **Blocked on evidence** for further extraction
- [Isolate experimental subsystems](isolate-experimental-core-subsystems/README.md) - **Blocked on evidence** for further extraction
- [Gate monitoring behind optional extra](gate-monitoring-behind-optional-extra/README.md) - **Blocked on evidence**; remains in the default wheel
- [Formalize MCP internal APIs](formalize-mcp-internal-apis/README.md) - **Medium** priority
