# Implement a TODO

Drive a claimed tracker item from start to completion using MCP tools.

## The implementation sequence

### 1. Claim the work

A batch member skips this section and makes no tracker call (see §2).

1. Call `list_items(ready_only=true)` to find claimable work.
2. Call `take(id=...)` to claim the item. Store the returned `generation`. The
   response already carries the context to begin: title, priority, needs, unmet
   needs, and a description excerpt.
3. Call `show_item(id=...)` only if you need sections beyond the excerpt.

### 2. Do the work

Apply `shared-change-framework/SKILL.md` Section 1 before editing source code.
Then edit, test locally, and keep the change focused on the task. There are no
work units to tick off and no scope gate to satisfy — the task description and
your judgment define the work.

If the claim may outlast its lease (default 24h), call
`renew(id=..., generation=...)`. Same generation, no milestones needed.

The work ends in one of two ways; pick by whether the item belongs to an active
registered batch (`show_item` batch metadata, or the task you were dispatched
with).

- **Batch member (prepared delivery).** A member worker does not claim, prepare,
  or finish: the leader is the only tracker writer, and the tracker refuses
  everyone else's claim on an active batch (`E_BATCH_OWNER`). The member commits
  and checks its change in its own worktree, merges in the current integration
  head, and hands a landing request to the leader (see `references/batch.md`). No
  PR. The leader later `take`s the member and calls `prepare`, which records the
  receipt and releases the claim without marking the item done; `finish` comes
  only after the final tree is verified. Do not use `prepare` for ordinary work
  or to bypass external, review, or approval dependencies.
- **Standalone item.** Deliver through the change framework's branch, commit,
  push, and PR workflow, then `finish` as below.

### 3. Finish or hand back

For a standalone item:

1. Call `finish(id=..., generation=...)` to close the item once its delivery is
   complete. For a batch member, never call `finish` after the member work or
   after `prepare`; the leader finishes members at batch closeout.
2. If you must abandon work, call `release(id=..., generation=...)` so the task
   returns to the ready queue.

If you find necessary work outside the task, `create_item` a follow-up with
`needs` pointing at the current item rather than widening scope silently (a
batch member reports the follow-up to the leader instead).
