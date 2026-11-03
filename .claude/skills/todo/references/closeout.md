# Batch close-out

Use `closeout` when the user's message explicitly invokes it for a named batch;
the authorization rule is `shared-review-protocol/SKILL.md` §1
(`[REVIEW-AUTH-001]`). A request asking only for review or validation stays
review-only: report findings and stop, and `closeout` then requires a later
user message authorizing remediation and tracker closure. Name the batch, the
review report, and the exact revisions reviewed. When `closeout` is authorized
but no review report exists yet, produce the review phase in-turn as a stage of
the authorized work, then proceed. When `closeout` is not authorized, collect
those inputs without editing and ask for the required authorization.

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

Disposition every finding in the report using
`shared-review-protocol/references/review-response.md` before editing. Record
the evidence that disposition requires; a finding you cannot classify becomes
a verification item, not a dismissal.

1. Fix every Critical and Required finding that still applies, or record the
   evidence that disproves it.
2. Follow `references/batch.md` for worktrees, verification, explicit staging,
   commits, and PRs. In a prepared batch the fix is a leader commit on the
   integration branch followed by the head advance and binding move, not a new
   member PR.
3. Re-run the checks that prove each fix. This is implementation verification,
   not a new review scope.
4. Turn unfixed Nit or Consider items into documented skips (classified as
   `DEFER` with a tracking reference or `REBUT` with rationale per
   `shared-review-protocol/references/review-response.md`), or into follow-up
   items with `create_item(needs=[<batch item>])` rather than widening scope.

## Phase 3 — close tracker state

Prepared feature members remain open until they are landed, the final PR is
bound to the current integration head, and that bound PR has **actually merged**.
Closeout fixes are leader commits followed by the head advance and CAS binding
move, not member PRs; withdraw auto-merge before publishing them.

Validate the completed successful final run against its current relevant
head/checkout/receipt-command/ownership/PR bindings. If drift requires a new
invocation, execute every member's receipt command and the combined fast tier
afresh, invalidating old success first. The derived `acceptance_required` list
is a tracker minimum, not the consumer's verification set. Then the leader
re-takes each member and calls `finish` with complete bound final evidence.
Never close solely because a receipt exists, a branch is an ancestor, or the PR
is green. Heads cannot change once any member is done. After all members finish,
follow merged-only member cleanup and leader-last worktree finish in
`references/batch.md`.

1. If you hold the claim, call `finish(id=..., generation=...)`.
2. To close externally merged work without a live claim, `take` the item first,
   then `finish`. If someone else holds the claim, tell the human to close it.
3. Leave blocked items `open`/`blocked` and record the exact unblock condition.
4. Dropping an item as unnecessary is a human decision — report it with the
   fixing revision identified, do not `drop` it yourself. When the owner
   decides to drop, call `drop(id=..., reason=...)` with their decision and
   any successor item as the `reason` (todo-db 0.12.0 or later); do not add a
   separate `context_append` note.

## Report

Report `TODO | finding | disposition | evidence | PR` for each item, using the
shared disposition vocabulary. Include remaining blockers and state whether the
named batch is fully closed.
