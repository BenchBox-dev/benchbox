<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Prune publishing subsystem — COMPLETED (v0.2.1)

> **Status: Completed / superseded. Do not act on the original proposal.**
>
> This proposal targeted an *older* generic publishing layer (the
> `ArtifactManager` — `artifacts.py`, `config.py`, `permalink.py`,
> `publisher.py`) whose coupling analysis found no active consumers. That layer
> was **pruned in v0.2.1** (commit `0182c955`, 2026-04-27): the four files above
> were deleted, and the **same commit** introduced a new, different subsystem at
> the same package path — the schema-v2 result-bundle publisher
> (`benchbox/core/publishing/bundle_publisher.py`, `store.py`).
>
> `benchbox/core/publishing/` today is **live, CLI-integrated code**, not dead
> code: it backs the registered `benchbox publish` command
> (`benchbox/cli/commands/__init__.py`) and `benchbox run --publish`, and is
> covered by `tests/unit/core/publishing/` and
> `tests/integration/test_publish_cli.py`. **Do not delete it.**

## History

The original future-state analysis (below, for the record) concluded that the
generic artifact/permalink layer had no active supported use and should be
pruned rather than extracted. That conclusion was correct for the code as it
existed then, and the prune was executed in v0.2.1. Because a new bundle-publish
subsystem was subsequently built at the same path, this proposal is retained only
as a completed record.

The original "unused generic layer, safe to remove" framing describes code that
no longer exists; nothing currently under `benchbox/core/publishing/` is
removable dead code.

## Original proposal (historical)

The original generic layer was to end in one of two explicit states: extract a
reusable `artifactlinks` library if the audit showed real reuse potential, or
delete the subsystem if it showed no active supported use. The audit found zero
consumers (no CLI integration, no runtime imports, no result-export coupling),
so deletion was selected. `benchbox/core/results/exporter.py` was and remains
independent and unaffected.
