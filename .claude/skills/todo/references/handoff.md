# Batch handoff

Create a self-contained prompt for continuing a multi-TODO batch or closing a
reviewed batch. Assume the next session knows only what the prompt states.

Include these sections in order:

1. **Objective and item set** — batch label, every TODO id, and whether the
   next action is the Todo skill's `batch` action or `closeout` action; neither
   is a CLI command.
2. **Tracker preflight** — call `get_instructions` with `expect_store` set to
   the project's `owner/repo` before any write; state the state remote URL and
   the state branch from its "Bound store" section.
3. **Current state** — live PR states, branch names, required checks, and the
   batch ledger path. Query them while writing the handoff.
4. **Order and dependencies** — recommended sequence and the reason for each
   ordering constraint.
5. **Rules and boundaries** — project instructions, worktree and staging
   rules, integration branch, toolchain, approval gates, and prohibited work.
   Include only rules supported by the project or the item descriptions.
6. **Known failure modes** — symptom, cause when known, and the safe recovery
   or avoidance step.
7. **Decisions and unavailable evidence** — identify decisions the next agent
   should make and verification that remains blocked. Never mark an unverified
   claim complete.
8. **Verification and review gates** — exact commands, required evidence, and
   the result that permits completion or closure.
9. **Revision marker** — use the repository's convention. If handoffs use
   revisions, state what changed from the prior revision.

Do not claim work is committed, tested, pushed, merged, or complete unless it
was verified while writing the handoff. Separate confirmed facts from advice.

Keep the handoff concise. Omit raw diffs, verbose test output, and
conversational history; the next session needs the conclusion and the evidence
pointer, not the transcript that produced it.
