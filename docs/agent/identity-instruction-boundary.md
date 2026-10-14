# The agent-identity instruction is external

An instruction to set an agent Git identity reaches sessions in this
repository from **outside** it. This note records where it comes from, why
following it here is destructive, and the signature trade-off that makes it
look reasonable on the surface.

## The instruction

Sessions have been told, in their opening task text, to commit as an agent
identity and to set it first by running `git config user.email` and
`git config user.name` with a vendor noreply address — no `--global`.

The instruction is not written anywhere in this repository, and no durable
copy exists in the maintainer's own configuration. A sweep found no writer in
the canonical skill source, `~/.claude` (including `CLAUDE.md`, settings and
hook files), `~/.codex`, or this repository's `_project/handoffs` and `docs`.
The only copies are in session transcripts: it arrives with the task, from a
harness or prompt template this repository does not control.

Because nothing here emits it, nothing here can be patched to stop it. The
control must be the rule below plus the mechanical guards, not a code fix.

## Why following it is destructive

Linked worktrees share the primary clone's configuration. Run inside a linked
worktree, `git config user.email <value>` with no `--global` writes to the
**common** config — the primary clone's `.git/config`. Git's precedence then
applies that value to the primary clone *and every worktree it owns*, at once.

So one obedient session reauthors every other session's work. That happened
here: one observed write in a linked worktree is enough to explain
agent-authored stashes across four different linked worktrees. This blast
radius is why it is treated as a defect, not a preference.

## The signature trade-off, stated plainly

The instruction is not arbitrary. The commit-signing key in use is registered
to the vendor address, so a commit whose **committer** is the human shows as
`Unverified` on GitHub. Setting the vendor identity makes signatures verify.

The trade-off is real, and the repository deliberately accepts it the other
way:

- **Attribution turns on authorship.** `[COMMIT-IDENTITY-001]` binds the
  author slot to the human and rejects known agent/service identities there.
- **The committer slot may hold a signing service** behind a human author, so
  signatures stay verifiable without misattributing the work.

Pretending the `Unverified` badge does not happen would silently give up a
real property. Naming it is the point: the repository chooses correct
attribution over a green badge, and keeps signatures where it can have both.

## What to do instead

- Per commit, when a task explicitly authorized a different identity:
  `git -c user.name=... -c user.email=... commit`. It applies to that command
  only and never becomes standing policy.
- Per worktree, to make a linked worktree immune to a later contaminating
  write: `git config --worktree user.email ...`. `make worktree-create` now
  does this automatically. Worktree scope outranks local scope, so the
  identity survives contamination of the common config.
- Never write `user.*` to the shared common config to normalize identity. That
  write *is* the defect.

## Mechanical backstops

| Point | Guard | Kind |
|---|---|---|
| Creation time | `make worktree-create` pins worktree-scoped identity | prevention |
| Preflight time | `make agent-write-preflight` refuses an agent author | detection |
| Any audit | `make agent-identity-check` warns on identity that displaces the global one | detection |
| Commit time | `pre-commit` / `commit-msg` hooks | detection |
| Merge time | `guard-agent-commit-range` reads the commits themselves | detection |

Merge time is the only point that reads what a branch actually carries. The
config-reading guards say nothing about a branch on a CI runner, where the
config is the runner's own.

## Code Review boundary

Code Review must not report commit identity. Its sandbox may use a synthetic
identity. Codex made six false P1 findings (#1695, #1698, #1715, #1717, #1719,
and #1721). Each cited commit had a human author. Hooks and CI check actual
commits. Code Review should report only PR defects.

## Related

- `[AUTH-PROVENANCE-001]` in `AGENTS.md` — a tool convention or an earlier task
  instruction never becomes a standing requirement.
- `docs/agent/attribution-surfaces.md` — the same class of problem on GitHub
  comments and PR bodies.
- `~/.benchbox/git-identity-forensics/` — preserved evidence and the timeline.
