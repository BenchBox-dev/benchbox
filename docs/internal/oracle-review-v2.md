<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Self-hosted oracle review

```{tags} contributor, operations, ci
```

`.github/workflows/oracle-verdict.yml` uses agent CLIs to review every pull
request except changes confined to inert paths (see Inert-path manifest),
and posts a non-required `oracle-verdict` status and a review
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
   - `TYPESAFE_API_KEY`, used only by `select-1` to classify the change with
     TypeSafe's Jev model;
   - the reviewer credentials, used only by the `attempt-N-omp` jobs. Either
     set `OMP_AUTH_BROKER_URL` and `OMP_AUTH_BROKER_TOKEN` for an OMP auth
     broker that holds the owner's subscription logins, or set provider
     credentials directly: `CLAUDE_CODE_OAUTH_TOKEN` (from
     `claude setup-token`, passed to OMP as `ANTHROPIC_OAUTH_TOKEN`),
     `OPENAI_API_KEY`, `META_API_KEY` and `GEMINI_API_KEY`. Unset secrets are
     not passed. Only providers with a credential can serve a fallback.
4. Remove `CLAUDE_CODE_OAUTH_TOKEN` from the repository secrets, because
   same-repository pull request workflows can read those.

Each attempt job installs a pinned OMP Linux build and checks its SHA-256
before running it.

### Credential broker

The broker runs on the owner's Mac, not in a container, so it shares the
owner's OMP credential store and is the only process that refreshes OAuth
tokens. A launchd agent (`~/Library/LaunchAgents/dev.omp.auth-broker.plist`)
runs `omp auth-broker serve --bind=127.0.0.1:8765 --trust-proxy-headers`, and
`tailscale serve --bg --https=8765 http://127.0.0.1:8765` publishes it to the
tailnet only, over HTTPS. Reviews reach it only while the Mac is awake and
online; otherwise attempts are absent and the hourly sweep retries.

Each attempt job joins the tailnet through `tailscale/github-action` with
GitHub's OIDC token (`id-token: write`), so no long-lived Tailscale secret
is stored. The step runs only when `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE`
are set. The tailnet policy must:

- own `tag:oracle-ci` (`tagOwners`), and limit the default allow-all rule's
  source to `autogroup:member`, so tagged nodes match nothing else;
- grant `tag:oracle-ci` only `tcp:8765` on the broker host.

The Tailscale OIDC credential carries the `auth_keys` write scope and
`tag:oracle-ci`. This repository uses GitHub's immutable OIDC subject, so
restrict the credential's subject to
`repo:BenchBox-dev@319264189/benchbox@1135770337:environment:oracle`; the
name-only form is rejected with a 403. Add `TS_OAUTH_CLIENT_ID`,
`TS_AUDIENCE`, `OMP_AUTH_BROKER_URL`
(`https://<host>.<tailnet>.ts.net:8765`) and `OMP_AUTH_BROKER_TOKEN` to the
`oracle` environment. Rotate the broker token with
`omp auth-broker token --regenerate`, restart the agent with
`launchctl kickstart -k gui/$(id -u)/dev.omp.auth-broker`, and update the
secret. Disable key expiry for the broker host in the Tailscale console.

The broker serves credentials, not model roles. Store the owner's
`~/.omp/agent/config.yml`, which maps each role alias to a model and its
fallback chain, as the `OMP_AGENT_CONFIG` secret, and refresh it after
changing roles:
`gh secret set OMP_AGENT_CONFIG --env oracle < ~/.omp/agent/config.yml`.
Attempt jobs fail without it, because role aliases would otherwise resolve
to OMP's built-in models.

Without the App secrets, the `post` job logs the result and succeeds.

## How a run works

1. `plan` holds no secret. It resolves the pull request and its head through
   the API, refuses closed, draft and non-`develop` pull requests, classifies
   the change, applies the rerun rules and writes a 0600 brief. A fork's
   change outside the inert paths gets a pending `fork: owner review` status
   and never reaches a reviewer job. A change touching only inert paths
   (see Inert-path manifest) gets `success`.
2. `select-1` asks Jev, through a direct call to TypeSafe's
   `/v1/systemone` API, for the change's size bucket, review capability,
   review template and per-file priority. The bucket names one reviewer in
   `.github/oracle-reviewers.yml`. A change to review machinery always gets
   the `gate` template and at least the `large` bucket. Later slots reuse
   that classification.
   Selection skips disabled reviewers, quota-exhausted pools and, when the
   diff exceeds the brief cap, any reviewer that is not hard read-only with
   `reads_files: true`.
3. `attempt-N-omp` runs that reviewer through OMP on a detached,
   credential-free checkout of the head. OMP starts in an empty working
   directory with the checkout added as a read-only workspace, the tools
   limited to `read,grep,glob`, and a trusted overlay that turns off project
   MCP config, the advisor, context promotion and prewalk. It never runs pull
   request code. The result is an artifact with the run ID, head SHA, reviewer
   and either a validated verdict or an absence.
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

## Inert-path manifest

`.github/inert-paths.txt` is the source of truth for code-driven review and
coverage scope. `glob` lines exempt broad prose; `except` lines keep
machine-read documents in scope, so the model is exemptions minus
machine-read exceptions. The `classifier.exempt_paths` policy key is gone
and the parser defaults it to empty; the Python consumers
(`oracle_review_check.py`, the reviewer classifier, `coverage_test_selection.py`)
read the manifest through `_project/scripts/inert_paths.py`, with soundness
and gate paths taking precedence over it. `.github/path-filters.yml` does not
read the manifest: its `code-ci` list hand-maintains the machine-read documents
routed to code CI.

To keep a document in review scope, add an `except` line for it. To return a
document to inert, remove its `except` line. Every `except` line must match
a tracked path: `test_every_exception_matches_a_tracked_path` fails
otherwise, which is why the assignment seed `scripts/*.md` was dropped
instead of carried (it matches nothing tracked). The guard also fails when
live scanning finds an inert-path reference without an exception
(`test_guard_reports_no_unexcepted_inert_references`).

`_project/inert-doc-readers.json` maps each machine-read document to the
tests that read it, so a docs-only change selects its readers instead of
escalating, and escalates only when nothing reads the document. The index
is checked in so the mapping is reviewed like code and CI can verify it is
fresh (`test_checked_in_readers_match_the_rebuilt_index`). Regenerate it with
`python3 _project/scripts/inert_paths.py --regenerate-readers` and commit
the result.

## Rounds

The oracle keeps its own history in its reviews. Each decisive review ends with
a hidden marker, `<!-- oracle-protocol: v1 ... -->`, written by code after the
sanitised model text, which cannot contain an HTML comment; defect file paths
are escaped the same way. It records the
cycle and round, the kind of round, the decision, the head, the base branch,
the reviewer, the size bucket (in the marker's `tier` field), the strike count, the open defects with their ids, and
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
every hash. Prose files outside the review scope are left out.

| Situation | Action |
|---|---|
| No marker yet | first round: a full review |
| Three DO NOT SHIP decisions | refused: one review per head, no reviewer runs |
| The latest decision is on this head | nothing runs |
| Base branch changed | new cycle with a full review; strikes are kept |
| Patch unchanged (a rebase or a prose-only change) | the decision is carried to the new head |
| Latest SHIP or SHIP WITH FIXES, patch changed | follow-up on the changed files |
| Latest DO NOT SHIP, patch changed | new cycle with a full review that quotes the previous summary |
| Author rebuttal (`/oracle-review` comment, or a push after a `Dispute:` thread reply), head unchanged | forced follow-up that rejudges the disputed defects, at most twice per patch |
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
first. A follow-up runs for every size bucket.

A forced follow-up rejudges rather than skips: its brief adds the disputed
threads under "Disputed findings", with each reply quoted as data, and tells
the reviewer to confirm each disputed defect as not fixed or withdraw it with
an examined head-commit file and line that refutes it. A withdrawal without
such a citation counts as not fixed, and a defect the reviewer marks fixed on
an unchanged patch counts as not fixed too, so only evidenced withdrawals
leave the open list, and they are reported separately as never defects. On a
forced round the judge counts a withdrawal without a file-and-line citation
as not fixed; the head-commit existence check still happens when citations
are marked.
A thread reply counts as a dispute only when a reply from someone other than
the oracle bot contains a line starting with `Dispute:` that states the case
against the defect. A plain acknowledgement such as `done` is not a dispute.
A forced re-review runs only after a SHIP or SHIP WITH FIXES decision, never
after DO NOT SHIP. The third forced re-review of one patch is refused with a
message to change the patch or ask the owner for a stand-in: empty commits and
rebases keep the patch digest, so they share the patch's budget instead of
resetting it. Ordinary follow-ups never consume this budget, and a push that
disputes nothing is still carried, not rejudged.

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

## Model selection

Each reviewer in `.github/oracle-reviewers.yml` names an OMP role alias,
not a model: `trivial` and `small` use `@smol`, `medium` uses `@default`,
`large` uses `@review`, `huge` uses `@plan` and `max` uses `@slow`. OMP
resolves the role to a model and, when that model hits a usage limit or an
outage, walks the role's own fallback chain. The oracle does not restate
models or chains, and its overlay leaves OMP's retry settings alone. An
empty `effort` takes the role's thinking level.

The review records the reviewer name from the policy, not the model that
answered. Reviews do not exclude the author's model family: the owner chose
role-based selection with OMP fallback over family exclusion, so a change
written by one model family can be reviewed by the same family.

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
  follow-up, the files changed since the last review), and OMP's event stream
  must show a successful `read` of each of those files, or a `grep` whose
  requested path is that single file. A `grep` over several paths or a
  directory credits nothing, and the reported `details.files` match list is
  ignored: it records files that matched, not files the reviewer read. The
  recorded transcript (`tests/fixtures/oracle_reviewers/
  omp_multi_turn_review.jsonl`) shows a grep result keeping match metadata
  (`files`, `fileMatches`, `matchCount`, `fileCount`) separate from the
  rendered `displayContent` behind its own `truncated` flag, and its only
  grep example is untruncated, so nothing proves `files` stays complete once
  output is truncated. That
  includes a follow-up whose only defects are in unchanged files: those
  defects are not counted, so the follow-up would ship and still needs the
  files named and read. A verdict that would fail (DO NOT SHIP, a defect
  counted in scope, or an earlier defect not fixed) needs neither.
- An inline brief carries the diff, so no read is required there.

The verdict is the text of the reviewer's final assistant message, taken from
its `message_end` event; remarks the reviewer makes between tool calls are
ignored. A final message that is not exactly one JSON verdict is invalid.

The brief no longer forbids running commands, but OMP's tool list holds only
`read`, `grep` and `glob`, so the reviewer cannot write or run anything. A
file-list brief also gets the whole pull request diff staged in the checkout.
The workflow-level group for the pull request serializes runs on one pull
request; the attempt jobs have no concurrency group of their own.

## Commands

- `/oracle-review` at the start of a top-level pull request comment, from an
  owner, member or collaborator, reruns the review on the current head. A reply
  on a review thread or a line comment does not trigger it, because those are
  review comments, not issue comments. A manual rerun may re-review the same
  head after a decisive result, bounded by the daily retry budget and the
  doubling backoff, so an author can dispute a finding with a `Dispute:` reply
  on its thread without a new commit. A manual rerun with no `Dispute:` reply
  leaves the head's decision in place instead of forcing another re-review.
  The new brief carries unresolved thread replies as disputed findings and
  asks the reviewer to confirm or withdraw each disputed defect with a reason,
  checking any claimed grammar or documented behaviour.
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
- **P3:** each reviewer authenticates in CI through the configured broker or
  provider secrets. A reviewer that cannot is set to `enabled: false` with a
  `disabled_reason`. The workflow has five slots.
- **P4:** capture each reviewer's quota, authentication and outage output from
  real runs. The attempt artifact's `diagnostic` field holds one redacted line
  of the output, at most 300 characters. When the answer is not a valid
  verdict, it holds instead the last 2,000 characters of the reviewer's final
  message. Both have credentials and long tokens removed; raw output is never
  uploaded. Add the messages to the `CALIBRATED_*_PATTERNS` tables in
  `_project/scripts/oracle_reviewers/absence.py`; until then they count as
  errors, which never pass. A quota absence means OMP's fallback chain was
  also exhausted. It skips the reviewer's whole pool for the rest of the run,
  and in later runs for one hour (`QUOTA_BACKOFF` in `selection.py`).

## Changing the reviewer harness

The harness boundary is OMP's command line, its JSON event stream and its
configuration, none of which the unit tests can check by themselves. A change
to `commands.py`, `verdict.py`, `evidence.py`, `runner.py`, the reviewer
policy or the attempt jobs must also:

- keep `tests/fixtures/oracle_reviewers/omp_multi_turn_review.jsonl` a real
  recording. When OMP's output changes, record a new one with
  `omp --print --mode json` on a review that writes text, calls `read` and
  `grep`, and then answers. Replace machine paths and check it for
  credentials. Never hand-edit it into the shape the code expects.
- be smoke-tested before merge by running `runner.review` on a file-list
  brief, with the reviewer and model that CI will select and the
  configuration stored in `OMP_AGENT_CONFIG`. The reviewer must take at least
  two turns, and the result must be a verdict that passes the evidence check.
  After merge, dispatch a run on a soundness pull request to confirm it in
  CI. Repeat both after any change to routing or bucket floors.
- be self-reviewed across every module at the harness boundary listed above
  and its tests, not a chosen subset.

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

The stand-in attestation must be posted after the oracle's latest decisive
verdict (`success` or `failure`) on the head, so overriding a failing verdict
is a deliberate act; a later review that carries no decisive verdict, such as
`pending`, does not void it, and it never overrides an open thread.

The merge digest applies the same window to merged history, and it files a
`soundness-review-gap` issue for a commit it cannot account for. An attester's
comment closes any such issue: it must start with `External review: codex`,
`muse` or `agy`, have a line beginning `Verified clean`, and list no
`**Critical**: `, `**High**: `, `**Medium**: ` or `**Low**: ` finding. A later
digest run closes by itself only an issue opened because a commit had no
review signal, and only once the digest finds one; an issue opened for a
reviewer thread, or for several reasons, closes only on an attester's review.

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

## Required gate from trusted code

`oracle-review` runs its workflow file from the pull request, so a pull request
that edits `.github/workflows/` can change the check that judges it, and the
commit status it reads can be set by any pull request sharing the head commit.
The `oracle-gate` context answers the first half: `_project/scripts/oracle_gate.py`
imports the decision and its helpers from `_project/scripts/oracle_review_check.py`
instead of reimplementing them, and posts `oracle-gate` for the pull request
head through the oracle App token. The pass rule is the checker's rule
unchanged: only exempt paths changed, the App's latest review of the head
reports success with no unresolved oracle thread, or a valid stand-in covers
the exact head; anything else posts failure. The script re-reads the review
state immediately before posting, so a run that evaluated stale data posts the
fresh decision instead of overwriting a newer one.

A same-repo pull request can still add a workflow with `statuses: write` that
posts the `oracle-gate` context to its own head with a forged success, so the
workflow alone does not make the context unforgeable. The unit test scans every
workflow for steps that could post the context and fails on any outside
`oracle-gate.yml`, but a status posted through a generated file or a new
workflow would land the same way. The binding that closes this is in the
ruleset: when the owner adds `oracle-gate` to the required checks, the check
must be scoped to the `benchbox-oracle` App's integration ID, so only statuses
posted with that App's identity satisfy it. Without that binding a forged
success counts and the gate property does not hold.

`.github/workflows/oracle-gate.yml` runs `develop` code only. It triggers on
`pull_request_target` (opened, synchronize, reopened, ready_for_review, edited),
on created issue comments that open with the stand-in marker, on completion of
`oracle-verdict` and `oracle-review` (the bridge for review and thread events,
which have no trusted trigger of their own), on `workflow_dispatch` with a `pr`
input, and on an hourly `schedule` sweep over open pull requests targeting
`develop`. Every job checks out `develop`, never the pull request, with a sparse
checkout of the gate, its predicate and the reviewers policy, and runs the
script with pinned `pyyaml` the same way `oracle-verdict` does; the App token
is minted the same way `oracle-verdict` mints it, from the same `oracle`
environment and secrets. When the reviewers policy cannot load, the script
errors instead of deciding with empty rules, and the sweep records every
per-PR error and fails the run when any pull request errored. Concurrency is a
single group keyed by pull request number for every trigger, with
cancel-in-progress, so a newer run for a pull request cancels the earlier one
instead of racing it.

The cutover keeps `oracle-review` required while `oracle-gate` runs
alongside it. Once production runs show the two contexts agreeing, the owner
adds `oracle-gate` to the required checks in `repo-admin-settings.md` and the
live ruleset, scoped to the `benchbox-oracle` App's integration ID, and only
then retires the `pull_request`-triggered check.
