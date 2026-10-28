# UAT charter pointer

The UAT release-gate framework that ships here (`tests/uat/`) is governed by a
design contract that is **not** stored on the release branch. This stub keeps
the gate and its charter discoverable together so `main` is never used as the
"what was approved" baseline by mistake.

- **Design contract (charter):** `_project/specs/uat-framework.md` — lives on
  the `develop` branch.
- **Operator-facing guide:** https://benchbox.dev/docs/operations/uat-framework.html.

The charter defines UAT's chartered scope: evidence-producing **release-gate
orchestration** over named artifacts (matrix-summary TSV, validator-clean-rate
rollup, packaging with explicit terminal state, resume manifest, explorer
smoke). UAT consumes canonical BenchBox surfaces rather than forking policy.

> Maintenance note: this pointer is intentionally minimal. Do not duplicate the
> spec or the operations guide here — update those sources and keep this file a
> pointer.
