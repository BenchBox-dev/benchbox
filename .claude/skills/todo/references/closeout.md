# Batch close-out

Authorization: SKILL.md, "Close-out authorization". Name the batch, the review
report, and the exact revisions reviewed. When `closeout` is authorized and no
review report exists, run the review in-turn as its first stage.

One authorized `closeout` call covers fixes for the reported findings,
follow-up PRs and follow-up items, and tracker closure for the named batch. It
does not authorize unrelated work or a new review scope.

## Phase 1 — refresh evidence

Before editing, confirm that the reviewed revisions and tracker items still
match the report:

- the commit and PR exist and target the expected branch;
- the merge and required checks have the reported state;
- the reviewed findings still apply to the current code;
- `show_item(id=...)` matches the expected tracker state and active claim.

Record changed or superseded findings. Do not silently apply a stale review.

## Phase 2 — remediate

Disposition every finding with
`shared-review-protocol/references/review-response.md` before editing.

1. Fix every Critical and Required finding that still applies, or record the
   evidence that disproves it.
2. Follow `references/batch.md` for worktrees, verification, explicit staging,
   commits, and PRs. In a prepared batch the fix is a leader commit on the
   integration branch followed by the head advance and binding move, not a new
   member PR.
3. Turn unfixed Nit or Consider items into documented skips (classified as
   `DEFER` with a tracking reference or `REBUT` with rationale per
   `shared-review-protocol/references/review-response.md`), or into follow-up
   items with `create_item(needs=[<batch item>])` rather than widening scope.

## Phase 3 — close tracker state

Close members per `references/batch.md`, Finish. Never close solely because a
receipt exists, a branch is an ancestor, or the PR is green.

1. If you hold the claim, call `finish(id=..., generation=...)`.
2. To close externally merged work without a live claim, `take` the item, then
   `finish`. If another worker holds the claim, inspect it with `show_item`; use
   `takeover` only when that session cannot resume (SKILL.md); otherwise ask the
   holder or the human.
3. Leave blocked items `open`/`blocked` and record the exact unblock condition.
4. Dropping an item as unnecessary is a human decision — report it with the
   fixing revision identified, do not `drop` it yourself. Once the owner
   decides, see SKILL.md, Planning.

## Report

Report `TODO | finding | disposition | evidence | PR` for each item, using the
shared disposition vocabulary. Include remaining blockers and state whether the
named batch is fully closed.
