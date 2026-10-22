# Oracle review protocol: decisions, evidence and rounds

Date: 2026-10-08
Status: Decided and in place: verdict schema 2, computed decisions, review
evidence checks, per-harness read rules, follow-up rounds, carried decisions,
the DO NOT SHIP strike limit and the protocol marker.
Related: `_project/scripts/oracle_reviewers/`, `.github/oracle-reviewers.yml`,
`docs/operations/oracle-review-v2.md`,
`_project/decisions/oracle-review-v2-shadow-2026-10-05.md` (its blocking
severities rule is superseded).

## Context

Two defects showed up in the shadow period.

- **Repeated full reviews.** One pull request received six full reviews on
  five heads, with 22 review threads for about eight issues. Every follow-up
  ran as a full review because scoping was allowed only after a success, the
  retry state from `pull_request_target` runs was not trusted, and rebases
  reset the review basis. Fingerprints of path plus title let a retitled
  finding open a new thread, and the very-high tier blocked Medium findings
  that the review process itself deferred.
- **Hollow successes.** A Codex reviewer given a file-list brief (the diff was
  over the brief cap) answered with no findings and a summary saying it could
  not review, and the pipeline recorded success. The brief forbade running
  commands, Codex reads files only through shell commands, and any valid
  verdict without a blocking finding counted as a pass.

## Decision

- **One decision per review.** The reviewer answers SHIP, SHIP_WITH_FIXES or
  DO_NOT_SHIP, or reports its review incomplete. Code computes the result from
  the defect list, and the stricter outcome wins: defects make SHIP into SHIP
  WITH FIXES, and more than 10 defects or a DO_NOT_SHIP answer give DO NOT SHIP
  with the summary only. An actually empty SHIP_WITH_FIXES, with no open prior
  defect, is invalid rather than promoted to SHIP. Findings outside a follow-up's
  changed files remain uncounted. Severity orders defects and never gates.
  Too many defects is a decision, not an invalid verdict.
- **Canonical stored verdicts stay strict.** The original mandatory-property
  rule served Codex structured outputs, which are no longer used. At reviewer
  ingress, missing non-decision text and a closed list of harmless finding/prior
  metadata are normalized; dictionary-form prior statuses are converted without
  changing IDs or statuses. A DO_NOT_SHIP still needs its summary. Duplicate
  JSON keys, unfilled response-template placeholders, unknown top-level keys
  and missing decision-bearing fields are invalid. Stored artifacts are never
  normalized, and contradictory decisions are rejected during replay too.
- **Evidence.** A defect must cite a real file and line in the head commit; a
  defect that does not is kept and marked, so it still fails the change rather
  than handing it to the next reviewer. Under a file-list brief, a verdict that
  would ship must name every changed soundness file that exists at the head, and
  the reviewer's trace must show a successful read of each: a Read or Grep
  naming it in Claude's `stream-json` events (a turn count is not evidence,
  because the structured-output call is itself a turn), or a Codex
  content-reading command with the exact path as an operand (command output,
  listings, existence checks, search patterns and the staged diff do not
  count). Inline briefs carry the diff, so no read is required. muse and agy
  leave no read trace; only the citation check and their own report apply to
  them. A failed check records the reviewer as absent (`incomplete`), never as
  a pass.
- **Read rules per harness.** The brief's read rule is filled when the review
  runs: read-only shell commands for Codex (its sandbox already blocks
  writes), Read, Grep and Glob for Claude, workspace file tools for muse.
  `reads_files` in the policy marks reviewers that can read files; only a hard
  read-only reviewer that reads files takes a file-list brief. agy's headless
  plan mode denies reads, so agy has `reads_files: false`.
- **Rounds.** The first review of a pull request covers it whole. After SHIP
  WITH FIXES, a follow-up reviews only files whose patch changed since that
  review, must give every earlier defect a status (fixed, not fixed or
  withdrawn) with evidence, and counts new defects only in changed files. After
  DO NOT SHIP, a changed patch restarts with a full review that quotes the
  previous summary. A patch that is unchanged after a rebase carries the
  decision to the new head. A retarget or a tier change restarts.
- **Strike limit.** After three DO NOT SHIP decisions on one pull request the
  oracle stops reviewing it and asks for a new pull request. A new pull request
  number resets the count, so the limit is friction against review loops, not
  a security control. The human stand-in keeps working on such a pull request.
- **State in the App's own reviews.** The round record is a hidden marker on
  the last line of each oracle review, written by code after the sanitised
  model text. Model text cannot carry an HTML comment, so it cannot forge one.
- **Threads.** The oracle never resolves review threads. Authors resolve them,
  as before; a follow-up lists which defects it verified as fixed.

## Rejected options

- **Line-level scoping of follow-ups.** The compare API reports a rebased head
  as diverged and loses deletions. File-level comparison of per-file patch
  hashes survives rebases.
- **Bot auto-resolution of threads on a later SHIP.** One wrong SHIP would
  clear the gate's open-thread condition for good.
- **A second reviewer for every muse or agy SHIP on a file-list brief.** Left
  open until shadow data shows whether their self-reports can be trusted.
