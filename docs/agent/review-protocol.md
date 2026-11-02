# Agent review protocol

BenchBox bindings for `shared-review-protocol`. The canonical skill governs
behavior; this file records project-specific storage, evidence, and review axes.
It supersedes `docs/agent/review-protocol-legacy.md`.

## Project bindings

- `[REVIEW-AUTH-001]`, `[REVIEW-DEFECT-001]`, `[REVIEW-DEPTH-001]` and
  `[REVIEW-L2-001]` are governed verbatim by the canonical skill. Named here to
  satisfy `[REVIEW-PARITY-001]` without restating behavior, which
  `shared-review-protocol` section 5 forbids this file from doing.
- `[REVIEW-CAPTURE-001]` Finding drafts live under `~/.todo-db/finding-drafts/<project>/`;
  tracker operations use the `todo-db-mcp` server.
- `[REVIEW-PARITY-001]` The canonical skill governs behavior; this file contains
  only BenchBox-specific bindings.

## Audit evidence provenance

Numbers bind to their measurement tree. `make audit-sha-check` enforces it;
see `docs/agent/audit-evidence-provenance.md`.

## Review remediation evidence

Remediation closeout follows `docs/agent/pr-review-evidence.md`: per-instance
mechanical enumeration, a rejected case per instance, and a
producer-to-persistence-to-consumer seam trace. Records live in
`_project/audits/remediation-contract-evidence.md`.

## Revision readiness

Revisions run inside one transaction (`scripts/pr_landing.py`, `make
pr-landing-start/withdraw/ready`): record the start-revision identity
(repo, PR, expected head, branch, worktree), withdraw readiness
(auto-merge disabled and re-verified) before the first edit, and re-verify
on the exact head before enqueue. Wrong-PR resolution, unpublished work,
head races, and durable holds refuse loudly; a merge that wins the race
stops modification and routes to follow-up. Batch readiness additionally
binds the final head to the tracker's integration head and final-PR binding,
every member receipt, current acceptance, the recomputed diff and member
scopes, and a usable local member review. Serial mode remains the default for
independent or approval-separated work. Feature delivery mode is opt-in only
after the active todo-db MCP server advertises the registered-batch v2
capability. It has one shared integration branch, one integrator, no
feature-base PRs, no new CI skips, and no change to hosted/native review, merge,
deployment, or authority boundaries (`docs/agent/batch-feature-delivery.md`).

## Architecture and plan review axes

- **Operational corpus.** Inventory the operational corpus: test:source
  ratio, parsed Make API, overlapping docs, explorer/Python contracts, and
  agent-instruction surface before judging complexity. Simplification plans
  must name those surfaces.
- **Extension-cost.** Primary extension-cost metric: files/contracts to add one SQL
  platform, one DataFrame platform, and one benchmark family. McCabe/cloc
  are hygiene (`docs/internal/quality-gate-policy.md`).
- **Prior-decision.** `[REVIEW-PLAN-RECON-001]` BenchBox decision surfaces are
  the future-state index/tiers, migration gates, readiness docs, and open tracker items.
- **CI synchronize fan-out.** For savings/skip/path-filter plans, list every
  same-event workflow, split runner vs wall minutes, and change siblings or
  lower the target. See `docs/internal/repo-admin-settings.md`.
