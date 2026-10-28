# Agent governance

Repository documentation for agent review, identity, attribution, and
audit-record conventions. This tree is **not** part of the public
site. `docs/development/` is the human contributor handbook.

## Placement

Choose the first row that matches.

| Question | Home | Published? |
|---|---|---|
| Must every agent session load it? | `AGENTS.md` (byte-budgeted) | No |
| Canonical cross-project behavior? | The `skill-sync-skills` catalog checkout named in `skill-sync.conf` (`shared-*` skills) | No |
| BenchBox binding, rationale, or harness boundary? | `docs/agent/` | No |
| How a human runs CI, UAT, release, or GitHub admin? | `docs/operations/` | Only if nav needs it |
| How a human adds a platform, runs tests, or extends a benchmark? | `docs/development/` | Yes |
| Product or architecture decision? | `docs/design/adr/` | Yes |
| Generated inventory or completed worksheet? | `_project/` | No |

Referenced-from-outside `_project/` is not enough to publish. Publication also
requires a human audience that belongs on benchbox.dev. A new
`docs/development/agent-*.md` fails `make agent-instructions-check`.

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
| `primary-clone-guard.md` | Dirty-primary check, absolute worktree paths for workers, and the lost-edit cause |
| `remote-closeout.md` | Closing out a PR from the primary clone without a checkout |

The eval runbook is `docs/internal/agent-instruction-evaluation.md`. The
scenario corpus is `_project/evals/agent-instructions/`.
