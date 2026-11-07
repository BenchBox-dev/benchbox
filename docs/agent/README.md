# Agent governance

Repository documentation for agent review, identity, attribution, and
audit-record conventions. This tree is **not** part of the public
site. Maintainer documents live in `docs/internal/`.

## Placement

Choose the first row that matches.

| Question | Home | Published? |
|---|---|---|
| Must every agent session load it? | `AGENTS.md` (byte-budgeted) | No |
| Canonical cross-project behavior? | The `skill-sync-skills` catalog checkout named in `skill-sync.conf` (`shared-*` skills) | No |
| BenchBox binding, rationale, or harness boundary? | `docs/agent/` | No |
| How a maintainer runs CI, UAT, release, or GitHub admin? | `docs/internal/` | No |
| Product or architecture decision? | `docs/internal/adr/` | No |
| User-facing page (platform guides, benchmarks, how-tos)? | The `BenchBox-dev/benchbox-site` repository | Yes |
| Generated inventory or completed worksheet? | `_project/` | No |

Referenced-from-outside `_project/` is not enough to publish. Publication also
requires a human audience that belongs on benchbox.dev, and user-facing pages
are owned by the site repository, not this one.

## Contents

| File | Role |
|---|---|
| `review-protocol.md` | Active BenchBox binding of `shared-review-protocol` |
| `review-protocol-legacy.md` | Superseded rationale; do not bind new surfaces to it |
| `identity-instruction-boundary.md` | Why an external “set agent git identity” instruction is destructive |
| `attribution-surfaces.md` | No standing agent footers on owner-posted GitHub surfaces |
| `audit-evidence-provenance.md` | SHA-field convention for `_project/audits/` |
| `handoffs.md` | Where resumable session state goes, and why handoff files are retired |
| `cloud-builder.md` | How a cloud session builds, verifies, and hands off one change |
| `merge-authority.md` | Owner holds, stand-in approvals (requires owner-named PR, head SHA, and prior instruction comment), and stale failed check suites; ruleset relaxation is owner-only |
| `emergency-merge.md` | Base-pinned gate breakage: agents reproduce, record evidence, and stop; stand-in evidence matches `merge-authority.md`; ruleset relaxation is owner-only |
| `primary-clone-guard.md` | Primary-clone change report, absolute worktree paths for workers, and the lost-edit cause |
| `remote-closeout.md` | Closing out a PR from the primary clone without a checkout |
| `preflight-receipt.md` | The tree-bound receipt `make pr-preflight` writes and `make pr-open` requires, the known-failure allowlist, and what the hosted gate does not read |
| `heavy-work.md` | Host lock, admission, `make heavy-status`/`heavy-run`, and the known limits behind `[HEAVY-LOCK-001]` |
| `maintainer-bundles.md` | Automatic pseudonym salt, staging a local run with `make corpus-stage-local`, and the guard-refusal rule |
| `batch-feature-delivery.md` | The default feature batch loop for explicitly assigned cohesive batches: leader and member roles, the `batch-start`, `batch-member-create`, `batch-member-check`, `batch-land`, `batch-advance`, `batch-status`, `batch-verify`, `batch-evidence`, and `batch-finish` targets, the brief-size limit, rollback, and recovery |
| `batch-tracker-contract.md` | The tractable v2 batch contract BenchBox consumes: `register_batch`, `take` then `prepare`, `advance_batch_head`, `bind_batch_pr`, `finish`, and recovery, with the leader as the only tracker writer |

The eval runbook is `docs/internal/agent-instruction-evaluation.md`. The
scenario corpus is `_project/evals/agent-instructions/`.
