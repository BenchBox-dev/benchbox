# Historical UAT campaign evidence

Committed, machine-readable UAT campaign evidence. One file per campaign:
`uat-gate-summary.json`, written by `make uat-gate-check` from the
three-stage campaign sweeps (see `docs/operations/uat-framework.md`
"Three-stage UAT campaign" and `docs/operations/release-guide.md`).

Conventions:

- The **operator** may review and commit the evidence file deliberately after
  `make uat-gate-check` exits 0 — sweeps and the gate-check never write to
  the git tree themselves.
- The file is historical campaign evidence, not a release input and not a
  release gate; `release-v2.yml` runs its own artifact-bound release UAT.
- Each release cycle overwrites the file; history is the git log.

## Provenance binding (added 2026-08-09)

Each stage sweep's `uat_gate_summary.json` now carries `artifact_digests`
(`cells_jsonl`, `accounting_sidecar`, `uat_lifecycle.log` as `sha256` hex
or `null` when honestly absent at sweep time). The digests are computed
**at artifact-write time** inside the sweep process (tests/uat/orchestrator)
and stored in the summary the operator later hands to `make uat-gate-check`.
At gate-check time the handler recomputes `sha256` **from the stage
directories passed on the command line** and rejects a mismatch (edited
artifact), an absent digest on old evidence ("regenerate evidence" HOLD),
or `source_commit_sha='unknown'` (git failure swallowed at sweep start).
The combined evidence (`uat-gate-summary.json`) preserves
`stage_artifact_digests` per stage.

**Threat model:** digests are recomputed on the operator machine, so they
prove the committed summary matches the stage directories the operator
pointed `gate-check` at — they are integrity against accident and drift
(hand-authored green file with a real SHA, stale artifact reuse), not
against a malicious operator who can regenerate digests after forging an
artifact. Without signing infrastructure (explicitly out of scope for this
item), provenance is not tamper-proof. See
`_project/blind-spots/2026-07-18-190500-evidence-tree-sha-provenance-mismatch.md`
(the finding this item closes; the finding itself now lives in the
findings table under the same id, imported FROZEN).
