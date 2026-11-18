# Adversarial Review

Review completed work skeptically and use evidence for every conclusion.
Authorization follows `shared-review-protocol/SKILL.md` §1 [REVIEW-AUTH-001]:
a review-only request ends at findings, with no edits, tracker closes,
commits, or publication.

## Scope

Choose one scope and name it in the report:

| Scope | Covers |
|---|---|
| `session` | Work completed in the current or a named prior session |
| `change` | A diff, branch, PR, or set of PRs |
| `feature` | One feature or subsystem across its recent changes |
| `project` | A whole project, including whether its value justifies its complexity |

## Review method

- Treat self-reports, commit messages, and PR descriptions as claims. Check
  the revision, CI result, implementation, and tests before accepting them.
- Treat known or suspected problems as the starting point. Check for other
  problems in the same scope.
- Apply the selected domain wrapper's review rubric. For example, use the
  code five-axis review for code, the documentation persona review for docs,
  and the critique rubric for blog posts.
- Apply the L2 blind-spot audit and L3 problem reframe from
  [REVIEW-DEPTH-001]. Keep concrete defects in the severity table.

## Verdict

For `session`, `change`, or `feature`, choose `Ship`, `Ship with caveats`, or
`Do not ship`. List every caveat or blocker.

For `project`, choose `Keep`, `Simplify`, or `Retire`. Support the verdict
with evidence about value, complexity, maintenance cost, and alternatives.

## Multiple reviewers

When several reviewers cover the same revision, keep their outputs separable
through synthesis. Dispatch, isolation, and quorum rules are owned by
`shared-agent-execution/references/external-harnesses.md` (Reviewer panels).

- Preserve attribution. Every finding names the reviewer that produced it, so
  `[grok]`, `[claude]`, or `[muse]` stays attached through the merged report.
- Distinguish consensus from a solo finding. A finding several reviewers
  reached independently carries more weight than one reviewer's alone; say
  which is which rather than presenting a flat list.
- Surface contradictions. When reviewers disagree about the same code, report
  both positions with their evidence and let the user resolve it. Do not
  average, drop, or silently pick a side.
- Report absent reviewers. A reviewer that failed, timed out, or was excluded
  did not concur. Name who reported and who did not.
- Do not let a reviewer fix its own findings. Adjudication and remediation
  belong to the author under `references/review-response.md`.

## Report

1. Scope, exact revisions reviewed, and verdict.
2. Severity table with `file:line` evidence.
3. Claims verified and claims that failed verification.
4. L2 blind spots and any L3 reframe.
5. What is done well.

Route defects through [REVIEW-DEFECT-001], solution-fit findings without
accompanying defect as action items under [REVIEW-FIT-001], and capture through
[REVIEW-CAPTURE-001].
