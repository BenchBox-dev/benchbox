<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Self-hosted oracle review

```{tags} contributor, operations, ci
```

`.github/workflows/oracle-verdict.yml` reviews soundness-path pull requests
with agent CLIs and posts a non-required `oracle-verdict` status and a review
through the owner's GitHub App. The required `oracle-review` check reads that
App's pull request reviews (see Cut-over). The policy lives in
`.github/oracle-reviewers.yml`. The decision records are
`_project/decisions/oracle-review-v2-shadow-2026-10-05.md` and, for verdicts,
evidence and review rounds, `_project/decisions/oracle-review-protocol-2026-10-08.md`.

The soundness manifest explicitly lists `.github/workflows/oracle-verdict.yml`,
in addition to covering all workflows by directory prefix. A pull request changing
only this workflow is subject to the same required `oracle-review` gate as other
soundness changes; an absent, pending or failed oracle decision does not pass on
its own.

## Setup

1. Create a GitHub App owned by the repository owner with **Commit statuses:
   write** and **Pull requests: write**, and install it on this repository
   only.
2. Create the `oracle` environment and limit its deployment branches to
   `develop`. Create it before the workflow first runs: GitHub creates a
   missing environment with no protection rules.
3. Add these environment secrets to `oracle`:
   - `ORACLE_APP_ID` and `ORACLE_APP_PRIVATE_KEY`, used only by the `post`
     job;
   - `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`, used only by the
     claude jobs;
   - `OPENAI_API_KEY`, used only by the codex jobs through
     `codex login --with-api-key`;
   - `META_API_KEY`, used only by the muse jobs. Each muse job installs the
     latest `muse-stable` release through `https://dev.meta.ai/install.sh`,
     unpinned, and writes the installed version to its job summary. A
     compromised download would run with this key and a read-only checkout of
     this public repository, and nothing else. The owner accepted that on
     2026-10-05 and confirmed it on 2026-10-09.
   - `GEMINI_API_KEY`, a Gemini API key from Google AI Studio, used only by
     the agy jobs. Each agy job installs a pinned Antigravity CLI release
     from Google's public bucket, checks its SHA-256, and sets
     `modelProvider: "gemini"` so the CLI reads the key from the
     environment. Until the secret exists, each agy attempt is recorded as
     `auth: GEMINI_API_KEY is not set` without running the CLI.
4. Remove `CLAUDE_CODE_OAUTH_TOKEN` from the repository secrets, because
   same-repository pull request workflows can read those.

Without the App secrets, the `post` job logs the result and succeeds.

## How a run works

1. `plan` holds no secret. It resolves the pull request and its head through
   the API, refuses closed, draft and non-`develop` pull requests, classifies
   the change, applies the rerun rules and writes a 0600 brief. A fork's
   soundness change gets a pending `fork: owner review` status and never
   reaches a reviewer job. A change with no soundness path gets `success`.
2. `select-N` picks the next reviewer from the tier's order, skipping the
   author's family, disabled reviewers, quota-exhausted pools and, when the
   diff exceeds the brief cap, any reviewer that is not hard read-only with
   `reads_files: true`.
3. `attempt-N-<harness>` runs that reviewer on a detached, credential-free
   checkout of the head. A Codex job first allows unprivileged user namespaces
   (`kernel.apparmor_restrict_unprivileged_userns=0`), which the Ubuntu 24.04
   runner restricts; without that, the Codex read-only sandbox cannot start and
   Codex reports its review incomplete. It never runs pull request code. The result is an
   artifact with the run ID, head SHA, reviewer and either a validated verdict
   or an absence.
4. `post` re-validates every artifact from this run, replays the selection,
   and posts the status and the findings. `findings_delivery` in
   `.github/oracle-reviewers.yml` chooses how:
   - `comment` posts one pull request comment, so findings never block a
     merge.
   - `review` posts one pull request review per result on the head commit.
     Under SHIP WITH FIXES, each defect that lands on a diff line opens a
     thread; a defect that spans lines gets a multi-line thread when every
     line of the span is in the diff, and a single-line thread at its first
     line otherwise. Every other defect is listed in the review body with the
     summary. DO NOT SHIP opens no thread. A run with no verdict still
     posts a review that explains the pending result, so the latest review
     always reflects the latest run. The ruleset requires every thread to be
     resolved, so these threads block merges. The policy has used `review`
     since the parity period, and the oracle signal requires it.

## Rounds

The oracle keeps its own history in its reviews. Each decisive review ends with
a hidden marker, `<!-- oracle-protocol: v1 ... -->`, written by code after the
sanitised model text, which cannot contain an HTML comment; defect file paths
are escaped the same way. It records the
cycle and round, the kind of round, the decision, the head, the base branch,
the reviewer, the tier, the strike count, the open defects with their ids, and
a hash of each changed file's patch (an 8-hex path hash and a 16-hex patch hash;
a marker over 4 KB keeps only the aggregate digest, which is computed from the
full-length patch hashes). The per-file map fits for about 200 changed files
when 10 defects are open, and about 210 when none are. Without the map, every
file counts as changed, so a larger pull request gets a follow-up on every
file after each push instead of one on the files that changed. `plan` reads
every review from the App's Bot account, page by page, and ignores dismissed reviews and
reviews without a marker. A review with more than one marker, or a marker that
does not decode, holds the result pending, as does a review list that cannot be
read. Pending results write no marker.

A file's patch hash ignores the `index` line and hunk line numbers, except for
binary files, so a rebase that leaves the pull request's changes alone keeps
every hash. Prose files outside soundness paths are left out.

| Situation | Action |
|---|---|
| No marker yet | first round: a full review |
| Three DO NOT SHIP decisions | refused: one review per head, no reviewer runs |
| The latest decision is on this head | nothing runs |
| Base branch or tier changed | new cycle with a full review; strikes are kept |
| Patch unchanged (a rebase or a prose-only change) | the decision is carried to the new head |
| Latest SHIP or SHIP WITH FIXES, patch changed | follow-up on the changed files |
| Latest DO NOT SHIP, patch changed | new cycle with a full review that quotes the previous summary |

A move of the base commit alone does not restart a cycle. A diff that cannot be
read, a file list that is truncated, or a file missing from the diff never
carries a decision, and neither does a file leaving the pull request: each
gives a follow-up on every file instead. A decision carries only when the
full-length digest matches; when it differs but the stored per-file hashes do
not show which file changed, the follow-up covers every file.

A follow-up brief lists the earlier defects with their ids (D1, D2, ...,
numbered by code within a cycle) and asks the reviewer to mark each one fixed,
not fixed or withdrawn, with evidence. An earlier defect left without a status
counts as not fixed. Unfixed earlier defects stay open, and a new
defect counts only in a file changed since the last review; new defects in
other files are listed, collapsed and capped at five, as not counted. More
than 10 open defects in total give DO NOT SHIP. The previous reviewer is tried
first. A follow-up runs at every tier, including very high.

The oracle never resolves a review thread. A follow-up lists the defects it
verified as fixed, and the author resolves those threads. Each new thread
carries a hidden `oracle-defect: c<cycle>-D<n>` marker before its
`oracle-finding` fingerprint, which stays last.

After three DO NOT SHIP decisions on distinct patches of one pull request, the
oracle posts a refusal (`failure`, `Decision: **REFUSED**.`) once per head and
reviews it no more. Carried and refused records do not count, and the strike
count also never drops below the one in the retry state artifact. Reopening
does not reset the count; a new pull request does, so the limit is friction
against review loops, not a security control. The stand-in attestation still
works on a refused pull request.

Before it posts, the `post` job reads the review list again and posts nothing
if the head already has a marker, so two runs for one head cannot both post.
It tries the read three times, about two seconds apart. If every try fails, it
posts anyway and says in the job summary that the post was not confirmed: the
retry state is already uploaded by then, so skipping would leave the head
recorded as decided with no review.

Before posting, `post` drops any finding that matches an open review thread
from this App, by file and normalized title, unless the new finding is more
severe than the open thread. Each new thread carries a hidden `oracle-finding`
marker with that fingerprint. A dropped finding still counts toward this run's
result and is listed in the review body.

A defect fails the review even when its line is outside the diff. Such a
defect cannot become a line comment, so it is listed in the review body with a
note that it counts like the others. Ignoring it would let a reviewer's report
of a real defect pass because it cited an unchanged line.

Reviewers run one at a time, so a decision stops the run and an absent
reviewer hands over to the next.

## Decisions

The reviewer answers with schema 2 of the verdict (`VERDICT_SCHEMA` in
`_project/scripts/oracle_reviewers/verdict.py`). Every property is required,
because Codex structured outputs reject optional keys: `status` (`complete` or
`incomplete`), `incomplete_reason`, `decision` (`SHIP`, `SHIP_WITH_FIXES`,
`DO_NOT_SHIP`, or `NONE` when incomplete), `summary`, `files_examined`,
`defects` and `prior_defects`. A reviewer that adds any other key, such as a
defect count, is invalid. The brief asks for must-fix defects only, at
most `protocol.max_defects` (10), and for `DO_NOT_SHIP` when the change needs
rework or more defects would have to be listed.

The code, not the model, decides, and the stricter outcome wins:

| Reviewer reports | Result |
|---|---|
| SHIP or SHIP_WITH_FIXES, no defects | SHIP |
| SHIP or SHIP_WITH_FIXES, 1 to 10 defects | SHIP WITH FIXES |
| more than 10 defects | DO NOT SHIP; the defects are dropped and the summary notes the count |
| DO_NOT_SHIP | DO NOT SHIP; the summary is kept, at most 1500 characters |
| `incomplete` | the reviewer is absent (`incomplete`) and the next one runs |

Severity orders the list and never gates: any listed defect, even a Low one,
makes the result SHIP WITH FIXES. SHIP posts `success`; SHIP WITH FIXES and
DO NOT SHIP post `failure`. The first line of the review body is
`### oracle-verdict: <state> for <sha>`, and a `Decision:` line follows it.
Reviews posted before 2026-10-09 open with `### oracle-review-shadow:` instead;
the required check still reads that line on an open pull request, and the
merge digest reads both for good, because it reads merged history. A list of more than 10 defects is not treated as
an invalid verdict, because every reviewer would then fail in turn and leave
the pull request pending.

## Evidence that a review happened

A verdict without a defect can be hollow: a reviewer that could not read the
files may still answer. The review job checks each verdict against the head
checkout before it records it; a failed check records the reviewer as absent
(`incomplete`), so the next reviewer runs and a hollow answer never passes.

- In every mode, each defect must cite a file in the head commit and a line
  within it. A defect that does not is kept, with a note that its citation was
  not found, so it still fails the change; discarding the verdict would hand the
  change to the next reviewer, which might pass it.
- When the brief lists files instead of carrying the diff (`file-list` mode),
  a verdict that would be judged SHIP must name, in `files_examined`, every
  soundness-path file under review that still exists at the head (in a
  follow-up, the files changed since the last review), and the reviewer's
  trace must show it read each one. That includes a follow-up whose
  only defects are in unchanged files: those defects are not counted, so the
  follow-up would ship and still needs the reads. A verdict that would fail
  (DO NOT SHIP, a defect counted in scope, or an earlier defect not fixed)
  needs no read evidence. Claude runs with
  `--output-format stream-json`, and each file needs a successful Read or Grep
  that names it; the structured-output call does not count. Codex runs with
  `--json`, and each file needs a successful content-reading command (`cat`,
  `sed`, `head`, `tail`, `nl`, `awk`, `grep` or `rg` on the file, or `git
  show`, `git diff` or `git blame`) with that exact path as an operand. A
  listing, an existence check, `echo`, a search of a directory, a path used as
  a search pattern and a read of the staged diff do not count.
- An inline brief carries the diff, so no read is required there.

The brief no longer forbids running commands. Its `<<read-rule>>` slot is
filled per harness when the review runs: Codex may run read-only shell
commands (`cat`, `sed -n`, `head`, `rg`, `git show` and similar) but nothing
that writes, builds, tests or uses the network, and its `--sandbox read-only`
still blocks writes; Claude reads with Read, Grep and Glob; muse uses its
workspace file tools with shell disabled. A file-list brief also gets the
whole pull request diff staged in the checkout.

muse and agy leave no trace of what they read, so for them only the citation
check and the reviewer's own `files_examined` apply. agy also cannot read files
in headless plan mode (its reads are denied), so it has `reads_files: false`
and never takes a file-list brief. The workflow-level group for the pull request
serializes runs on one pull request; the attempt jobs have no concurrency group
of their own.

## Commands

- `/oracle-review` at the start of a top-level pull request comment, from an
  owner, member or collaborator, reruns the review on the current head. A reply
  on a review thread or a line comment does not trigger it, because those are
  review comments, not issue comments.
- `gh workflow run oracle-verdict.yml --ref develop -f pr=<number>`
  does the same. A dispatch from any other ref is refused.
- An hourly schedule retries pull requests whose last result on the current
  head is pending.

A rerun on the same head needs a previous run in which every attempted
reviewer was absent. A run that stayed pending because its artifacts failed
validation, or because a selected reviewer never reported, is not rerun on the
same head; push a new head instead. Comment, dispatch and scheduled reruns
share a daily budget per pull request (`retry.daily_budget`), with backoff that
starts at one hour and doubles. The retry state is the
`oracle-verdict-state-<number>` artifact of the latest run of this workflow
whose commit is on `develop`; artifacts from any other workflow or branch are
ignored. When a pull request has no such artifact yet, `plan` reads the
`oracle-review-shadow-state-<number>` artifact from runs of the workflow's
former file, `.github/workflows/oracle-review-shadow.yml`, so the strike
count, retry budget and backoff carry over the rename.

A pull request whose brief exceeds the size cap even without the diff, because
it changes so many files that the file list alone is too long, cannot be
reviewed: every reviewer is skipped and the result is pending with the cause
`oversize`. Its review says the change is too large for the oracle and that the
ways forward are to split the pull request or ask the owner for a stand-in. The
same head is not retried; a new push plans again.

## Calibration

These checks must pass before the cut-over that makes the context required.

- **P2:** a `pull_request_target` run from a same-repository test pull request
  enters the `oracle` environment; a `pull_request` run from the pull request
  branch cannot.
- **P3:** each reviewer authenticates in CI. A reviewer that cannot is set to
  `enabled: false` with a `disabled_reason`. agy is enabled with its own
  job in every slot, and the workflow has five slots so the longest chain
  fits when every reviewer is enabled. The codex reviewers, sol and luna, are
  disabled because the OpenAI API project behind `OPENAI_API_KEY` has no
  quota; their jobs and tier places stay, so setting `enabled: true` on each
  re-enables them. While they are off, very high is reviewed by opus alone
  (diversity-exempt), and the lower tiers by sonnet, muse and agy, skipping the
  author's family.
- **P4:** capture each reviewer's quota, authentication and outage output from
  real runs. The attempt artifact's `diagnostic` field holds one redacted line
  of the output, at most 300 characters, with credentials and long tokens
  removed; raw output is never uploaded. Add the messages to the `CALIBRATED_*_PATTERNS` tables in
  `_project/scripts/oracle_reviewers/absence.py`; until then they count as
  errors, which never pass. A quota absence skips the reviewer's whole pool
  for the rest of the run, and in later runs until the reset time the CLI
  reported, or for one hour (`QUOTA_BACKOFF` in `selection.py`) when it
  reported none, such as Codex's `Quota exceeded. Check your plan and billing
  details.`

## Cut-over

The cut-over is done: `.github/oracle-reviewers.yml` sets `mode: enforce` and
`.github/workflows/oracle-review.yml` passes `--signal oracle`, the only
signal the checker accepts. The required `oracle-review` check requires, from
the `benchbox-oracle` App's Bot account:

- a review of the head, not pending or dismissed, submitted after any retarget,
  whose latest one opens with `### oracle-verdict: success for` (or, before
  2026-10-09, `### oracle-review-shadow: success for`) the head SHA, because
  the oracle posts every verdict as a comment review and a defect outside the
  diff has no thread;
- no unresolved review thread from the App.

The verdict is read from the pull request's own review, not from the commit
status, which any pull request with the same head commit could set. The
oracle signal needs `findings_delivery: review`, under which every run that
reaches a result, including a pending one, posts a review, so the latest
review is the latest result.

Each `oracle-review` run is its own check suite on the head, and a failed or
cancelled suite keeps the required check red until it is re-run. After the
`post` job of `oracle-verdict` posts its review, it re-runs the failed
`pull_request` runs for the head, at the same moment the review starts a
`pull_request_review` run. The workflow has no concurrency group, because
under `cancel-in-progress` one of those runs cancelled the other. Instead, a
run for a pull request event checks, before and after the checker, that the
head it was triggered for is still the pull request's head; a run for a
superseded head fails rather than report on a head it was not created for.

The stand-in attestation must be posted after the oracle's latest review of
the head, so overriding a failing verdict is a deliberate act; it never
overrides an open thread.

The Codex connector is no longer a signal. The daily soundness merge digest
still accepts a connector review or thumbs-up for merges before the cut-over
(2026-10-09 00:16 UTC), so its history stays accurate. A pull request opened
before the cut-over keeps check runs that required the connector; re-running
them does not help, because a re-run reuses the original base, so such a pull
request needs a new push, a fresh `pull_request` event, or a stand-in.

The checker runs from the base commit, so the workflow can pass `--signal`
only after `develop`'s copy of the script accepts it. A unit test fails if
`mode: enforce` and `--signal oracle` disagree or if delivery is not `review`.
Going back to the connector would need this cleanup reverted and the connector
App reinstalled.
