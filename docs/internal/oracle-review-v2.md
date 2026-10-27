<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Self-hosted oracle review

```{tags} contributor, operations, ci
```

`.github/workflows/oracle-verdict.yml` uses agent CLIs to review every pull
request except changes confined to inert paths (see Inert-path manifest),
and posts a non-required `oracle-verdict` status and a review
through the owner's GitHub App. The required `oracle-gate` check reads that
App's pull request reviews from `develop` code (see Required gate from trusted
code). The policy lives in
`.github/oracle-reviewers.yml`. The decision records are
`_project/decisions/oracle-review-v2-shadow-2026-10-05.md` and, for verdicts,
evidence and review rounds, `_project/decisions/oracle-review-protocol-2026-10-08.md`.

The soundness manifest explicitly lists `.github/workflows/oracle-verdict.yml`,
in addition to covering all workflows by directory prefix. A pull request changing
only this workflow is subject to the same required `oracle-gate` gate as other
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
   - `ORACLE_APP_ID` and `ORACLE_APP_PRIVATE_KEY`, used only by the `Mint the
     App token` step of the `review` job, which runs after the attempt loop
     and the redaction check;
   - `TYPESAFE_API_KEY`, held only by the classify step, the two disk scans
     and the redaction step of the `review` job; the classify step uses it to
     classify the change with TypeSafe's Jev model;
   - `ORACLE_GATEWAY_TOKEN` and `ORACLE_GATEWAY_URL`, held by the attempt loop
     and the redaction step: the reviewer's client token for the credential
     gateway (see Credential gateway) and the gateway's HTTPS URL. No
     provider key, broker URL or broker token is stored for the reviewer, and
     the owner's OMP config is not stored as a secret: the reviewer's model
     roles and fallback chains are committed in `.github/oracle-omp-config.yml`;
   - `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE`, used to join the tailnet.
4. Keep provider credentials, `CLAUDE_CODE_OAUTH_TOKEN` included, out of the
   repository secrets, because same-repository pull request workflows can
   read those.

The `review` job installs a pinned OMP Linux build once and checks its SHA-256
before running it.

### Credential gateway

The reviewer holds no provider credential. The attempt loop runs the pinned
OMP with one environment variable, `OMP_GATEWAY_TOKEN`, a client token for an
`omp auth-gateway` on the owner's Mac. The gateway sits in front of the
credential broker: it checks the token, limits the client to the listed models
and accounts, and forwards the request with the owner's real credential, so
neither the broker token nor a provider key reaches the runner. For every
attempt the loop writes the reviewer's OMP home into a fresh scratch `HOME`
(`write_reviewer_home`, the code behind `cli.py reviewer-home --home
<home> --gateway-url <url>`): a copy of
`.github/oracle-omp-config.yml` as `config.yml`, and a `models.yml` that
points the providers `anthropic`, `openai-codex`, `muse-code` and
`google-antigravity` at the gateway with `transport: pi-native` and
`apiKey: OMP_GATEWAY_TOKEN` (the variable name, not a value). The code
refuses an empty URL or one that is not `https://`.

Reviews reach the gateway only while the Mac is awake and online; otherwise
attempts are absent and the next sweep retries (see the triggers below). A run that
fails without posting a result is re-dispatched once by the sweep; when two runs on the same
head have failed without a result, the sweep posts a pending `oracle-verdict`
status that names the failed step.

**Accepted risk.** The reviewer is not confined to files. It can read any file
the runner user can read, and it can read its own client token, for example
from `/proc/self/environ`. This is accepted because the token is scoped to the
listed models, reachable only over the tailnet and quick to rotate. The
overlay and the prompt neutralization limit what a pull request can steer the
reviewer to read, but are not a boundary. Do not enable OMP's `secrets.enabled`
setting: it restores real values into URLs and output.

The `review` job joins the tailnet once, before the attempt loop, through
`tailscale/github-action` with
GitHub's OIDC token (`id-token: write`), so no long-lived Tailscale secret
is stored. The step runs only when `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE`
are set. A failed join does not end the review: the reviewer cannot reach the
gateway and its attempt is recorded as absent.

[`oracle-gateway-host.md`](oracle-gateway-host.md) builds, checks, rotates and
moves the gateway host, and lists the Tailscale OIDC credential and tailnet
policy the review job needs.

**Cut-over cleanup (one time).** After a hosted run passes on the gateway,
delete `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `META_API_KEY`,
`OMP_AUTH_BROKER_URL`, `OMP_AUTH_BROKER_TOKEN` and `OMP_AGENT_CONFIG` from the
`oracle` environment, remove the rule that still lets `tag:oracle-ci` reach
the broker on `tcp:8765`, and rotate those credentials once. If nothing else
reaches the broker over the tailnet, also stop publishing it
(`tailscale serve --https=8765 off`). Until then, reverting the cut-over
restores the old setup. `GEMINI_API_KEY` was referenced by the old workflow but
never set in the environment, so there is nothing to delete for it.

Without `ORACLE_APP_ID`, the job logs the result and succeeds.

## How a run works

1. The plan step holds only the job token. It resolves the pull request and its head through
   the API, refuses closed, draft and non-`develop` pull requests, classifies
   the change, applies the rerun rules and writes a 0600 brief. A fork's
   change outside the inert paths gets a pending `fork: owner review` status
   and never reaches a reviewer. A change touching only inert paths
   (see Inert-path manifest) gets `success`. The whole run is one job, `review`:
   every later step is skipped unless the plan asks for a review (decision
   `review`) or a post.
2. The classify step asks Jev, through a direct call to TypeSafe's
   `/v1/systemone` API, for the change's size bucket, review capability,
   review template and per-file priority. The bucket names the first reviewer
   in `.github/oracle-reviewers.yml`; later slots can move up the chain (see
   Rounds). A change to review machinery always gets the `gate` template and
   at least the `large` bucket. Later slots reuse that classification.
   Selection skips disabled reviewers, quota-exhausted pools and, when the
   diff exceeds the brief cap, any reviewer that is not hard read-only with
   `reads_files: true`.
3. The attempt loop (`cli.py attempts`) runs that reviewer, once per slot,
   through OMP on a detached,
   credential-free checkout of the head. OMP starts in an empty working
   directory with the checkout added as a read-only workspace, the tools
   limited to `read,grep,glob`, skills and rules off (`--no-skills`,
   `--no-rules`), and a trusted overlay. The overlay turns off project MCP
   config, the advisor, context promotion, prewalk and URL fetching
   (`fetch.enabled: false`). It also disables every discovery provider except
   OMP's built-in defaults, so the checkout's `AGENTS.md`, `.omp/AGENTS.md`,
   `CLAUDE.md`, editor rule files and plugins do not load. OMP reads any file
   named by an `@` mention in the prompt before a tool call, resolving relative
   paths from the working directory, so the brief puts a zero-width space
   before every `@` that is followed by a character other than whitespace or
   another `@` in the change data and the file priority. An `@` preceded by
   that character does not start a mention. These settings limit what the
   pull request can make the reviewer load. They do not confine its file
   tools: the reviewer can still read any file the runner user can. It never
   runs pull request code. The result is an artifact with the run ID, head
   SHA, reviewer and either a validated verdict or an absence. See Job
   structure for what holds which credential and the checks that guard the
   job.
4. After the loop, the same job re-validates every artifact from this run,
   replays the selection, and posts the status and the findings.
   `findings_delivery` in
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

## Job structure and controls

`oracle-verdict.yml` has two jobs. `sweep` dispatches due retries. `review`
does everything else for one pull request in one admission to the runner pool,
where earlier designs queued a chain of plan, select, attempt and post jobs
and waited for a runner at every step. Its `if:` admits a manual dispatch, a
`pull_request_target` event other than a plain edit, and a `/oracle-review`
comment from an owner, member or collaborator. The decision record
`_project/decisions/oracle-single-review-job-2026-10-25.md` holds the reasoning.

Steps, in order:

1. Refuse a dispatch from a ref other than `develop`.
2. Check out the trusted `develop` copy into `trusted`.
3. Plan the review (job token only). The plan's head SHA feeds the next step,
   so the pull request head is checked out after the plan.
4. Check out the pull request head into `workspace`, as data that is never
   executed.
5. Classify the change and select the reviewer for attempt 1 (the only step
   that sets `TYPESAFE_API_KEY` for classification).
6. Disk scan (C4).
7. Install and verify OMP, then join the tailnet once. A failure in either
   leaves the attempt absent instead of ending the run.
8. Run the attempt loop (`cli.py attempts`): for every slot up to
   `max_attempts`, select the reviewer with the existing selection logic,
   reset the checkout, give the reviewer a fresh scratch `HOME` with its OMP
   home, run it, confirm that it left nothing behind, delete the `HOME`, and
   write the slot's result file. A crash or timeout still leaves an absence
   result for the slot.
9. Disk scan again (C4), finalize, then the redaction check (C5).
10. Mint the App token, run the guard and post the status, comment and
    review, upload the retry state (only when the status was posted) and the
    attempt artifacts, and dispatch `oracle-gate`.

The reviewer is an untrusted model inside a trusted harness with read-only
tools. It can read what the runner user can read while it runs and can leak it
only through its published review text. Step environment scoping alone does
not hide secrets from the runner: the initial job message can contain the App
key and job token in Runner.Worker memory throughout the job. Immediately before
the loop, a permanent Linux prerequisite requires Yama `ptrace_scope` 1, 2 or 3,
one non-root UID, and no effective, permitted, inheritable or ambient
`CAP_SYS_PTRACE`. Yama prevents a child from reading ancestor or unrelated
process memory without an explicit tracing grant. This assumes the trusted
runner does not grant `PR_SET_PTRACER` access and the read-only harness does not
execute privilege-escalation tools or change UID/capabilities. It is not a
sandbox against arbitrary execution with the runner's sudo access. Parent step
environments remain readable, so the loop environment still holds only its
gateway values (plus the accepted OIDC request token). Each control below is
permanent, not a one-time inspection:

| Control | Enforced by |
|---|---|
| C2 total exit | `runner.execute` lists the harness process group and, when the loop runs on Linux, every orphan adopted by the loop (a child subreaper) after each reviewer exit. Survivors are killed and the run fails closed: the slot is recorded absent, the loop stops, the job fails and nothing posts. |
| C3 no secret in a script | The workflow test fails if any `run:` block names `secrets.*` or `github.token`; secrets reach steps through `env:` or `with:` only. |
| C4 disk scan | `cli.py scan-disk` searches `$HOME`, `$RUNNER_TEMP`, both checkouts, `/tmp`, `/var/tmp`, `/dev/shm` and `${TMPDIR:-/tmp}` for the exact TypeSafe key and job token before the reviewer and after the last exit. It deduplicates nested roots and does not follow file or directory symlinks. This covers the declared readable scratch roots, not every file the user can read. Values come from the scanning step's own `env:`. A hit fails the job. |
| C5 redaction completeness | `cli.py redact-check` runs after finalize in a step that holds every secret value the job held (gateway token, TypeSafe key, gateway URL, Tailscale client ID and audience, the OIDC request token and the job token). It redacts attempt, verdict, diagnostic, absence and result text, across control-character splits, and fails if a value remains. The loop step cannot hold those values, because a same-user child can read its parent's environment. |
| C6 fresh `HOME` | Every attempt gets a new directory from `mkdtemp`; the previous one is deleted before the next attempt starts, and the checkout is reset. |
| C7 token scope | The job's permissions are exactly `actions: write`, `contents: read`, `pull-requests: read` and `id-token: write`. `github.token` appears only in the `env:` of steps that run before or after the loop step, and both checkouts use `persist-credentials: false`. |
| C8 timeout budget | `timeout-minutes` is `max_attempts` times the longest reviewer `timeout_minutes` plus a setup allowance, under 360; the harness timeout still fires first and becomes an absence. A workflow test recomputes the number from the policy. |
| C9 cancellation | `cancel-in-progress` is true only for `pull_request_target`; comment, dispatch and sweep runs queue behind a run in flight. Every posting step has the default success condition, so a cancelled run posts nothing. |
| C10 App mint last | `ORACLE_APP_PRIVATE_KEY` is referenced by the review job's posting mint, which runs after the loop, the second disk scan and the redaction check, and by the sweep job's failure-status mint. The sweep job never runs a reviewer, so its mint cannot leak to one. |

The OIDC request token is in the loop step's environment, as the gateway token
is, and is accepted on the same terms: GitHub documents it as valid only while
the job runs, and its value is redacted. The gateway URL is sensitive and is
redacted too.

If any of C2 to C10 cannot be made to pass on GitHub-hosted runners, the
fallback is the same `review` job without the posting steps, plus a separate
`post` job that holds the App key and reads the uploaded artifacts.

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
a hash of each changed file's identity (an 8-hex path hash and a 16-hex identity hash;
a marker over 4 KB keeps only the aggregate digest, which is computed from the
full-length identity hashes). The per-file map fits for about 200 changed files
when 10 defects are open, and about 210 when none are. Without the map, every
file counts as changed, so a larger pull request gets a follow-up on every
file after each push instead of one on the files that changed. `plan` reads
every review from the App's Bot account, page by page, and ignores dismissed reviews and
reviews without a marker. A review with more than one marker, or a marker that
does not decode, holds the result pending, as does a review list that cannot be
read. Pending results write no marker.
A file's identity is its old path, new path, old and new mode, and the full
merge-base and head blob ids. In CI the files API supplies the paths and the
head blob id, and the recursive git trees of the merge base and the head
supply the modes and blob ids; locally `git diff --raw --full-index
--no-abbrev` against the merge base supplies the same six fields, so one pull
request has one digest in both places. A file added or removed has an absent
mode (`000000`) and blob id (forty zeros) on the missing side. The patch digest
covers every changed file, prose included, and is the one digest that keys
carry, strikes and forced re-review budgets. If any field cannot be read (a
truncated file list, a truncated tree, a head blob id that disagrees with the
tree, a merge base that cannot be resolved) the digest is `unread-<head>` and
nothing carries.

| Situation | Action |
|---|---|
| No marker yet | first round: a full review |
| Three DO NOT SHIP decisions | refused: one review per head, no reviewer runs |
| The latest decision is on this head | nothing runs |
| Base branch changed | new cycle with a full review; strikes are kept |
| Patch digest unchanged (the merge-base diff is byte-identical) | the decision is carried to the new head |
| Latest SHIP or SHIP WITH FIXES, patch changed | follow-up on the changed files |
| Latest DO NOT SHIP, patch changed | new cycle with a full review that quotes the previous summary |
| Author rebuttal (`/oracle-review` comment, or a push after a `Dispute:` thread reply), head unchanged | forced follow-up that rejudges the disputed defects, at most twice per patch |
A move of the base commit alone does not restart a cycle. A file list that is
truncated, or a file whose blob ids cannot be read, never carries a decision,
and neither does a file leaving the pull request: each gives a follow-up on
every file instead. A decision carries only when the full-length digest
matches, so a rebase that changes any file the pull request touches, a hunk
that moves, or a mode, rename or binary change gets a fresh review; when the
digest differs but the stored per-file hashes do not show which file changed,
the follow-up covers every file.

Because the digest keys strikes and forced re-review budgets as well, a rebase
onto a develop that changed one of the pull request's files is a new digest: it
can add a strike when that patch is reviewed and refused, and it starts a new
forced re-review allowance. A rebase that leaves the merge-base diff
byte-identical keeps the digest, the strike count and the allowance.

A follow-up brief lists the earlier defects with their ids (D1, D2, ...,
numbered by code within a cycle) and asks the reviewer to mark each one fixed,
not fixed or withdrawn, with evidence. An earlier defect left without a status
counts as not fixed. Unfixed earlier defects stay open, and a new
defect counts only in a file changed since the last review; new defects in
other files are listed, collapsed and capped at five, as not counted. More
than 10 open defects in total give DO NOT SHIP. The previous reviewer is tried
first unless the follow-up's own bucket names a larger reviewer, as it does for
a push that touches review machinery. A follow-up runs for every size bucket.

A forced follow-up rejudges rather than skips: its brief lists each disputed thread under "Disputed findings", keyed by the finding id from the thread's hidden marker, with each reply quoted as data. For every keyed disputed finding the verdict must give an explicit disposition: withdraw it with an examined head-commit file and line that refutes it, or maintain it as not fixed by restating one shown rebuttal reply exactly as shown, saying in concedes whether the rebuttal holds, and citing an examined head-commit file and line that answers it or an examined reproduction script as `repro: path`. Withdrawing grants the rebuttal, so a withdrawal needs concedes set; agreeing with the rebuttal yet keeping the defect not fixed is invalid, as is a missing disposition, a claim that matches no shown reply, or withdrawing without conceding: those make the attempt invalid and it is retried. A maintain or fixed disposition whose citation does not verify is not invalid: the defect stays open and its disposition is published with the evidence gap. A demoted withdrawal or fix is judged exactly as a maintain, so its concedes and claim values cannot invalidate it. A keep-open verdict is never discarded or rerolled; only the safe direction is taken. A withdrawal without a verified citation counts as not fixed, and a defect the reviewer marks fixed on an unchanged patch counts as not fixed too, so only evidenced dispositions leave the open list, and withdrawals are reported separately as never defects. Every disposition and its evidence is published under its finding in the review body; when the body would exceed the cap, finding details shrink first and no disposition line is ever dropped. On a forced round the judge counts a withdrawal without a file-and-line citation as not fixed; the head-commit existence check still happens when citations are marked. Only disputes bound to a current-cycle prior finding are enforced, shown and published; a stale or unknown dispute stays data.
A thread reply counts as a keyed dispute only when a reply from an owner, member or collaborator of the repository, other than the oracle bot, contains a line starting with `Dispute:` that states the case against the defect, and the thread carries the hidden finding marker. Replies from other accounts never reach the brief and never force a re-review. A `Dispute:` reply on a thread without a marker is shown as data and forces no re-review. A plain acknowledgement such as `done` is not a dispute.
A forced re-review runs only after a SHIP or SHIP WITH FIXES decision, never
after DO NOT SHIP. The third forced re-review of one patch is refused with a
message to change the patch or ask the owner for a stand-in: empty commits and
byte-identical rebases keep the patch digest, so they share the patch's budget instead of
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

Before it posts, the `review` job reads the review list again and posts nothing
if the head already has a marker, so two runs for one head cannot both post.
It tries the read three times, about two seconds apart. If every try fails, it
posts anyway and says in the job summary that the post was not confirmed: the
skipping would leave the head with no review. A run that skips posting, or
whose status post failed, uploads no retry state, so it adds no strike.

Before posting, `post` drops any finding that matches an open review thread
from this App, by file and normalized title, unless the new finding is more
severe than the open thread. Each new thread carries a hidden `oracle-finding`
marker with that fingerprint. A dropped finding still counts toward this run's
result and is listed in the review body.

A defect fails the review even when its line is outside the diff. Such a
defect cannot become a line comment, so it is listed in the review body with a
note that it counts like the others. Ignoring it would let a reviewer's report
of a real defect pass because it cited an unchanged line.

Reviewers run one at a time, so a decision stops the run. The chain starts at
the bucket's reviewer or, for a follow-up, at the previous reviewer if that one
belongs to a larger bucket, and then takes the reviewers of the larger buckets
in size order, leaving out any reviewer whose harness, model and effort match
one already in the chain: `trivial` and `small` share `@smol`, so a run never
spends slots on both. A reviewer whose attempt was absent as `invalid`,
`incomplete`, `empty` or `error` runs once more before the chain advances, so
a reviewer gets at most two attempts and the run at most `max_attempts`. A
decision is never retried, and neither are `quota`, `auth` or `timeout`
absences, a reviewer job that did not finish (`ensure-attempt` records it with
the fixed `JOB_FAILURE_DETAIL` detail) or a reviewer that selection would skip
(disabled, out of quota, or unable to take the brief). Selection and artifact
replay share this rule, so an artifact set with a third attempt of one
reviewer, or any attempt after a decision, is rejected. If every reviewer in
the chain is absent or skipped, the run stays pending. A SHIP from the first
reviewer that answers ends the run like any other decision.

An OMP run whose event stream ends with no final assistant text, for example
after a tool call, is absent as `empty` with the detail "the reviewer ended
without a final answer". Final text that is prose or malformed JSON, and OMP
output that is not an event stream, stays `invalid`. Both are retried alike.

The review body lists every absent attempt under "Reviewers not available" as
`- <reviewer> (attempt <slot>): absent (<kind>): <detail>`. The detail is the
one recorded in the attempt artifact, with credentials redacted, whitespace
collapsed and at most 160 characters; it is left out when the attempt has none.
Raw reviewer responses are never published there.

## Decisions

The canonical schema-2 response has seven reviewer-supplied properties:
`status` (`complete` or `incomplete`), `incomplete_reason`, `decision` (`SHIP`,
`SHIP_WITH_FIXES`, `DO_NOT_SHIP`, or `NONE` when incomplete), `summary`,
`files_examined`, `defects` and `prior_defects`. The oracle adds the computed
`defect_count` to stored verdicts; reviewers cannot set it. Stored artifacts
must match the canonical fields strictly.

Only reviewer ingress normalizes known harmless drift:

- Missing `incomplete_reason` becomes empty. Missing `summary` becomes empty
  except for DO_NOT_SHIP, whose summary is the published rationale.
- Finding metadata `line_note`, `end_line_note` and `detail_extra` is removed.
- Prior metadata (`severity`, `file`, `line`, `end_line`, `title`, `detail`) is
  not removed: a prior entry must hold exactly `id`, `status` and `evidence`,
  so a current finding filed under the priors cannot be read as a judgment.
- An object keyed by prior ID becomes a list. An omitted embedded ID comes
  from the key; an embedded ID must agree after the supported `#` prefix is
  removed. Duplicate normalized IDs remain invalid.

Duplicate JSON keys at any depth, unfilled response-template placeholders,
unknown top-level keys, reviewer-supplied defect counts and missing
decision-bearing fields remain invalid. No finding is discarded or given an
invented location. Applied rewrites are recorded in the diagnostic. Invalid
JSON retains a redacted final-message tail; parseable invalid output also
gets a bounded keys-and-types fingerprint, never field values.

`tests/fixtures/oracle_reviewers/pr3237_missing_text_fields.json` preserves
decoded payloads from the retained attempt diagnostics for runs `39662535698`
and `39669405558`. Their text is already redacted; these are not raw model
transcripts. Both responses retain the unfixed prior finding rather than
becoming approvals after normalization.

The brief asks for must-fix defects only, at most `protocol.max_defects` (10),
and for DO_NOT_SHIP when the change needs rework or more defects would have
to be listed.

The prompt ends, after the fenced change data, with a response template: JSON
to populate, not an example and not a schema. It carries the seven verdict keys
with placeholder strings from `verdict.RESPONSE_PLACEHOLDERS` (line `0`,
`end_line` null), and in a follow-up the actual prior defect ids, revalidated
against the defect id grammar, with unfilled `status` and `evidence`. Ingress
rejects a reply that leaves any placeholder in place. The three prompt
templates (`standard`, `gate`, `deep`) are at version 2. Only a plain,
line-ranged or raw read, or a single-file grep, supplies read evidence; a
computed read query (`?q=`) does not.

The code, not the model, decides, and the stricter outcome wins:

| Reviewer reports | Result |
|---|---|
| SHIP, no counted or open prior defects | SHIP |
| SHIP_WITH_FIXES, no listed or open prior defects | absent (`invalid`), never promoted to SHIP |
| SHIP that still gives an `incomplete_reason` | absent (`invalid`); a reviewer that could not finish never ships |
| SHIP that marks an earlier defect fixed or withdrawn with empty evidence | absent (`invalid`); clearing a blocker needs evidence |
| SHIP_WITH_FIXES, only out-of-scope follow-up findings | SHIP; findings are disclosed as not counted |
| SHIP or SHIP_WITH_FIXES, 1 to 10 defects | SHIP WITH FIXES |
| more than 10 defects | DO NOT SHIP; the defects are dropped and the summary notes the count |
| DO_NOT_SHIP | DO NOT SHIP; the summary is kept, at most 1500 characters |
| `incomplete` | the reviewer is absent (`incomplete`); it runs once more, then the next one runs |

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
`large` uses `@review`, `huge` uses `@plan` and `max` uses `@slow`. The
committed `.github/oracle-omp-config.yml` maps each alias to a model
(`modelRoles`) and each primary model to its fallback chain
(`retry.fallbackChains`). Chains are keyed by the primary model id, not by
role: on the pinned OMP build a role-keyed chain makes every `--model @role`
walk the `default` chain, while a key containing `/` is matched by model and
restores each role's own chain. `retry.fallbackOn` is `usage-limit`, so only a
usage limit walks the chain; a revoked token, an out-of-scope model or an
outage fails the attempt instead of silently swapping the model family.
Changing a role or a chain is a pull request to that file, and a change to the
file must keep one chain per primary model and no role-name keys (a test
checks this). An empty `effort` takes the role's thinking level.

`@smol` maps to `openai-codex/gpt-6-luna:xhigh`, whose first usage-limit
fallback is `anthropic/claude-sonnet-5-5:medium`. `trivial` and `small` are in
the `codex` pool and every other reviewer is in the `claude` pool, so a quota
absence on one provider leaves the other provider's reviewers available. The
comparison behind this choice, and why Claude Haiku was dropped, is in
`_project/decisions/oracle-small-reviewer-model-2026-10-23.md`.

The review records the reviewer name from the policy, not the model that
answered. Reviews do not exclude the author's model family: the owner chose
role-based selection with OMP fallback over family exclusion, so a change
written by one model family can be reviewed by the same family.

## Evidence that a review happened

A verdict without a defect can be hollow: a reviewer that could not read the
files may still answer. The review job checks each verdict against the head
checkout before it records it; a failed check records the reviewer as absent
(`incomplete`). That reviewer can run once more before the chain advances;
if no reviewer returns a verdict, the run stays pending. A hollow answer
never passes.

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
- Read credit is exact-path equality after normalisation, never a prefix or a
  sibling. The documented selector grammar is accepted on `read` and on a
  single-file `grep`: `:raw`, `:N`, `:LN`, `:N-`, `:N..`, `:A-B`, `:A..B`,
  `:LA-LB`, `:A+C`, `:-N`, comma lists such as `:1-12,-15`, and `:raw` with a
  range in either order. A computed `?q=` read, `:conflicts` and `:img` credit
  nothing: a query returns whatever its expression computes (one character was
  observed), unlike a verbatim range or grep. A plain, ranged or raw `read`,
  or a `grep` of the single file, supplies evidence. A committed file whose
  name is a selector-shaped variant of another committed file (`a.py:raw`,
  `a.py:conflicts`, `a.py?q=.x` or `x.whl:pkg/m.py` beside the committed
  `a.py` or `x.whl`) is ambiguous, because OMP's resolution order is not verified,
  so neither path is credited from that read.
- A successful read of an archive listing or member (registered archive
  extension, inside the checkout) credits the archive container only; the
  member path never credits a repository file. An error, a binary-entry
  notice, a `?q=` or `..` member, no match, a directory, a read of several
  `;`-joined paths that includes an archive member, a
  sibling or a path outside the checkout credits nothing. Binary and
  unreviewable content, out-of-bounds line reads and notice-only reads are
  not solved here and stay with `oracle-verdict-labels-unreviewed-bytes` and
  `oracle-read-evidence-tightening-20261008`; no file is exempted.
- Partial reads keep their credit; this is exposure, not whole-file or
  semantic proof. The reviewer overlay sets `read.summarize.enabled: false` so
  a plain read of a large file is not replaced by a structural summary, and
  the trace records each credited read's `details.summary` elision counts
  (the gate does not reject them). Without summarisation an open-ended read
  is still capped by `read.defaultLimit` (300 lines), which the gate does not
  detect.
- Every parsed outcome, successful or not, keeps a bounded, redacted `trace:`
  line in the attempt diagnostic: read, grep and failed-call counts, up to
  five credited, required and missing paths, and elision totals, so elided
  credited reads stay measurable. It never carries the raw event stream, and
  the OMP stdout stream is no longer excerpted into diagnostics. Read credit
  also requires the real path to stay inside the checkout, so a committed
  symlink to an outside file credits nothing.
- Credentials: `commands.omp_env()` forwards only the base environment and
  the gateway client token (`GATEWAY_ENV`, `OMP_GATEWAY_TOKEN`). One list
  (`credentials.py` `NAMES`) names that token, the broker and provider
  variables the attempt loop no longer receives, the runner-held values (the
  gateway URL, the Tailscale client ID and audience and the OIDC request
  token), and the legacy extras, so a
  local run that still holds them is redacted too. Values of at least eight
  characters are removed by exact match before and after every sanitising
  transform and before any truncation, across control-character splits and
  overlaps, in verdict text, absence details, diagnostics and error
  snippets. A known value inside a structural field (file, `files_examined`,
  prior id or an object key) invalidates the verdict with a fixed message
  instead of moving a finding. This is known-value hygiene, not a sandbox:
  values brokered dynamically or held only inside `OMP_AGENT_CONFIG`, and any
  value under eight characters, cannot be matched. A local fake-sentinel probe
  showed the reviewer's `read` returns a committed symlink target, an absolute
  outside path and a `..` path, so the checkout is not a read boundary. The reach
  probes also reached `/proc/self/environ` and the home configuration, which
  is why the reviewer holds only the scoped gateway client token
  (`oracle-response-read-boundary-prerequisite-20261021`).

The verdict is the text of the reviewer's final assistant message, taken from
its `message_end` event; remarks the reviewer makes between tool calls are
ignored. A final message that is not exactly one JSON verdict is invalid.

The brief no longer forbids running commands, but OMP's tool list holds only
`read`, `grep` and `glob`, so the reviewer cannot write or run anything. A
file-list brief also gets the whole pull request diff staged in the checkout.
The workflow-level group for the pull request serializes runs on one pull
request; the `review` job has no concurrency group of its own, so a new push
cancels a run in flight and a cancelled run posts nothing.

## Commands

- `/oracle-review` at the start of a top-level pull request comment, from an
  owner, member or collaborator, reruns the review on the current head. A reply
  on a review thread or a line comment does not trigger it, because those are
  review comments, not issue comments. A manual rerun may re-review the same
  head after a decisive result, bounded by the daily retry budget and the
  doubling backoff, so an author can dispute a finding with a `Dispute:` reply
  on its thread without a new commit. A manual rerun with no `Dispute:` reply
  leaves the head's decision in place instead of forcing another re-review.
  The new brief carries unresolved thread replies as disputed findings keyed by finding id and
  asks the reviewer to withdraw or maintain each keyed disputed defect with an explicit disposition
  and its evidence, published under the finding, checking any claimed grammar or documented behaviour.
- `gh workflow run oracle-verdict.yml --ref develop -f pr=<number>`
  does the same. A dispatch from any other ref is refused.
- A sweep retries pull requests whose last result on the current head is
  pending and due. It runs on every push to `develop` and on an hourly
  schedule; GitHub starts the schedule irregularly (2.0 to 6.1 hours apart
  between 2026-10-20 and 2026-10-25), so the first push after a head becomes
  due is the usual trigger. The target is a dispatch within 25 minutes of that
  push, or at the next scheduled sweep when no push comes. Push and
  schedule sweeps share one concurrency group that never cancels a running
  sweep. The sweep lists the last seven days of `oracle-verdict` runs, all
  pages, and skips a pull request that has a run that is queued, in progress,
  waiting, pending or requested, or started in the last two minutes, so it
  never cancels a retry in flight. Beyond due pending heads, it re-dispatches
  a head whose latest run failed, timed out or was cancelled without leaving a
  retry state, unless the head already carries a decisive App verdict status.
  The stateless dispatch carries a marker the plan verifies against the head's
  failed runs, so the redispatched run reviews instead of skipping on backoff. That happens once: when the two latest runs on the head both
  failed without a result (runs that never reviewed the head, such as a
  skipped run from a plain comment, are ignored), the sweep stops dispatching
  and posts a pending `oracle-verdict` status naming the failed step, using an
  App token minted in the reviewer-free sweep job. A status that is already
  pending on the head is left in place, and a sweep without the App configured
  reports the gap instead of failing. A failure inside one pull
  request's lookup, dispatch or surfacing is reported and the sweep continues with the
  others, then fails the job. The daily budget, backoff and attempt limits are
  unchanged.
- `make oracle-backoff-reset PR=<number> HEAD=<sha> REASON='<why>'` lets the
  owner clear the backoff and retry window of one pull request head once. It
  needs an `Owner instruction` comment from an attester that names the head,
  posted first, as for a stand-in. It posts `Oracle backoff reset: <sha>` and
  the reason, which `plan` and the sweep honour only from an attester, unedited,
  after that instruction, until the next verdict run that reviews the head spends it.
  The reset applies only when the head's recorded result is a pending reviewer-outage
  result; it never re-rolls a decisive verdict and never restores budget.

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

## Local pre-PR review

`make pr-open` reviews the committed `HEAD` before it pushes, so a Critical or
High defect is caught before a pull request exists. The command is
`cli.py local-review`; `make trunk-revert` opens its pull request through
`gh pr create` and does not run it. The review is advisory: its output is
labelled a local advisory review that may differ from the CI oracle, and it
posts nothing to GitHub.

- **What it reviews.** `cli.py plan --local` builds the plan from git: the
  merge base of `origin/develop` and the pinned head as the base and head
  SHAs, no pull request number, no prior reviews and no retry state.
  Uncommitted and untracked changes are never reviewed: the reviewer works in
  a clean clone checked out at that head. The policy
  (`.github/oracle-reviewers.yml`), the soundness manifest
  (`.github/soundness-paths.txt`) and the workflow pin are read from
  `origin/develop`, as the hosted job reads them from the base branch, so a
  branch cannot widen its own exempt paths. The reviewer code and templates
  are those of the checked-out tree. Soundness scope, reviewer chain, brief
  and templates are the CI ones (the template names the change "unopened"),
  and the review runs the same `cli.py attempts` loop as the `review` job,
  with the same redaction pass over the result. The hosted job's Linux
  isolation check and disk scans do not run locally.
- **Classifier key.** `TYPESAFE_API_KEY` comes from the environment or, on
  macOS, from the keychain service `TYPESAFE_API_KEY`. The owner adds it once:
  `security add-generic-password -s TYPESAFE_API_KEY -a "$USER" -w` (the
  command prompts for the value). Without a key, including in a cloud session,
  the review uses a fixed default and prints that it did: the policy's
  `medium` bucket reviewer with the `standard` template, or the `gate`
  template when review-machinery files change.
- **Credentials.** A local session uses the `oracle-local` gateway client at
  `http://127.0.0.1:4000` with the token in
  `~/.omp/oracle-gateway/local-client.token`. A cloud session (the
  environment sets `ORACLE_CLOUD_GATEWAY_TOKEN` or
  `BENCHBOX_EPHEMERAL_CLONE=1`) uses the `oracle-cloud` client from
  `ORACLE_GATEWAY_URL` and `ORACLE_CLOUD_GATEWAY_TOKEN`, and refuses when
  either is missing. The loop writes the reviewer home with that gateway, and
  `write_reviewer_home` accepts `http://` on a loopback host only when the
  local review asks; the CI loop still requires `https://`. Token, key
  and URL values are never printed: every line goes through the redaction
  that also covers the stored result. The accepted risk of the hosted job
  applies: the reviewer can read files the user can read, including that
  token.
- **Harness pin.** The `omp:` section of `.github/oracle-reviewers.yml` pins
  the version and each platform's URL and SHA-256. CI reads that section from
  its trusted checkout through `cli.py install-omp`, running under isolated
  `uv` with `pyyaml==6.0.3`. YAML is imported only when parsing the policy;
  the other bare-Python CLI commands do not require PyYAML. The shared `omp_pin.py`
  installer downloads into `~/.cache/benchbox/omp/<version>/<platform>/omp`
  (`BENCHBOX_OMP_CACHE` overrides the cache root), verifies SHA-256 before
  making the download executable, and verifies its reported version before
  publishing it in the cache. Unpinned platforms refuse by name. The build
  host publishes no checksum files; these hashes were computed from fresh
  TLS downloads, as was the original linux-x64 workflow pin. The darwin-arm64
  download matches the existing cached build and reports `omp/18.12.1`.
  During the consumer migration, CI adds the verified cache directory to its
  job PATH. Local review still checks PATH omp against the workflow's
  `OMP_VERSION` read from `origin/develop`; cloud bootstrap still reads
  `OMP_URL` and `OMP_SHA256` from the branch workflow. Those legacy values
  remain solely for these consumers. CI's installer refuses if any legacy
  value differs from the policy pin, and the workflow test enforces equality.
  Change both sets together until local and cloud consume the policy pin.
  That cutover removes the legacy values and equality check and invokes the
  cached binary by explicit path rather than through PATH.
- **Blocking.** A Critical or High finding, or a reviewer decision of DO NOT
  SHIP, makes `pr-open` refuse before any push (exit 1). A
  `Local oracle override: <reason>` line with a non-empty reason in the PR
  body file (`PR_BODY_FILE`) lets it proceed and stays in the body. An empty
  reason, a `<placeholder>` reason, and a line inside an HTML comment or code
  fence are refused. Nothing else supplies an override: a commit message does
  not. A gateway that is unreachable, a timeout, quota exhaustion, a reviewer
  that produced no verdict, a missing credential or an `omp` version mismatch
  also refuses (exit 2), and the override does not cover it.
- **Pinned head.** `pr-open` records `HEAD` before the review, passes it as
  `--head`, and refuses before pushing if `HEAD` moved while the review ran
  or waited for the lock, so the pushed head is the reviewed one.
- **Cache and lock.** The result is cached under
  `~/.cache/benchbox/local-oracle` (`BENCHBOX_LOCAL_ORACLE_DIR` overrides it)
  by the patch digest, the base commit, the head tree, the policy hash and the
  `omp` version, so an
  unchanged patch reuses it, including after the findings were overridden. The
  key also holds whether a classifier key was present and a hash of the review
  machinery (the `oracle_reviewers` package, its templates and the OMP
  config), so a keyless default result is not reused once a key exists, and a
  change to the machinery is reviewed again. Only reviews that produced a
  verdict are cached. One lock in the same directory allows one local review
  at a time per machine; a second waits up to an hour, then refuses.

## Calibration

These checks must pass before the cut-over that makes the context required.

- **P2:** a `pull_request_target` run from a same-repository test pull request
  enters the `oracle` environment; a `pull_request` run from the pull request
  branch cannot.
- **P3:** each reviewer authenticates in CI through the credential gateway
  with the `ORACLE_GATEWAY_TOKEN` client token. A reviewer that cannot is set
  to `enabled: false` with a `disabled_reason`. The workflow has five slots.
- **P4:** capture each reviewer's quota, authentication and outage output from
  real runs. The attempt artifact's `diagnostic` field holds one redacted line
  of the output, at most 300 characters. When the answer is not a valid
  verdict, it holds instead the last 2,000 characters of the reviewer's final
  message. Both have credentials and long tokens removed; raw output is never
  uploaded. For OMP, stdout is never excerpted; only stderr is. A `trace:` line
  of at most 700 characters follows on every outcome that reached verdict
  parsing (valid, invalid, incomplete or failed evidence). Its paths are
  redacted by known value and secret pattern, not by the generic long-token
  filter. Add the messages to the `CALIBRATED_*_PATTERNS` tables in
  `_project/scripts/oracle_reviewers/absence.py`; until then they count as
  errors, which never pass. A quota absence means OMP's fallback chain was
  also exhausted. It skips the reviewer's whole pool for the rest of the run,
  and in later runs for one hour (`QUOTA_BACKOFF` in `selection.py`).

## Changing the reviewer harness

The harness boundary is OMP's command line, its JSON event stream and its
configuration, none of which the unit tests can check by themselves. A change
to `commands.py`, `verdict.py`, `evidence.py`, `runner.py`, the reviewer
policy or the attempt loop must also:

- keep `tests/fixtures/oracle_reviewers/omp_multi_turn_review.jsonl` a real
  recording. When OMP's output changes, record a new one with
  `omp --print --mode json` on a review that writes text, calls `read` and
  `grep`, and then answers. Replace machine paths and check it for
  credentials. Never hand-edit it into the shape the code expects.
- be smoke-tested before merge by running `runner.review` on a file-list
  brief, with the reviewer and model that CI will select and the committed
  `.github/oracle-omp-config.yml` (written into a scratch `HOME` with
  `reviewer-home`). The reviewer must take at least two turns, and the result
  must be a verdict that passes the evidence check.
  After merge, dispatch a run on a soundness pull request to confirm it in
  CI. Repeat both after any change to routing or bucket floors.
- record, beside the CI-pinned proof, a separately labelled local proof: the
  local OMP build and version, the reviewer overlay, a plain read of a file
  over 100 lines with no `details.summary` and no `elided; re-read needed
  ranges` footer, and a single-file `grep`. A local macOS OMP 18.12.6 run does
  not prove the pinned Linux build or the secret `OMP_AGENT_CONFIG`; only an
  observed CI run does.
- be self-reviewed across every module at the harness boundary listed above
  and its tests, not a chosen subset.

## Cut-over

The cut-over is done: `.github/oracle-reviewers.yml` sets `mode: enforce`, and
the checker (`_project/scripts/oracle_review_check.py`), whose decision the
required `oracle-gate` check reports, accepts only the oracle signal. It
requires:

- a latest `oracle-verdict` commit status on the head, created by the
  `benchbox-oracle` App's Bot account, in state `success`, posted after any
  retarget;
- no unresolved review thread from the App.

The verdict is read from the App's commit status, not from review bodies. A
review can be edited or deleted by the account every agent uses, while a
commit status cannot be edited or deleted and records its creator, so the
gate ignores a status from any other account and any other context, including
the legacy `oracle-review-shadow` context, and never reads a review body for
its decision. A commit status belongs to a head commit, not to a pull
request, so the gate also binds the verdict to this pull request with an
explicit same-run binding: every verdict run mints a run uuid that it writes
into the review marker and appends to the success status description
(`[run <uuid>]`, capped to the 140-character status limit), and the gate
passes on a success status only when this pull request has its own unedited
oracle review of that head whose marker carries the same run uuid and whose
header agrees with the status. Unedited is verified through the review API;
a receipt whose edit state cannot be verified never authorizes. A status left
by another pull request at the same head, or by an earlier policy, never
authorizes this pull request: any success status without a run uuid fails closed
(waiting for a fresh verdict run), so an unbound status can never authorize an
open pull request. Evidence predating the run uuid requirement carries no uuid
and fails closed (waiting for a fresh verdict run); merged pull requests predate
this cutover and never re-run the gate. The oracle signal needs `findings_delivery:
review`, under which every run that reaches a result, including a pending
one, posts a review and then a status in the same state.

The `oracle-review` workflow is a review-event notification, not a check. It
triggers only on `pull_request_review` submitted, edited and dismissed, holds no
token permissions, and runs one `review-event` job with a single no-op step: no
checkout, no secrets and no API calls. It produces no check run named
`oracle-review`. The `edited` type keeps edits to oracle reviews visible in the run history; the
gate does not read review bodies for its decision, but it rejects edited receipts when binding
a status to this pull request.

Because `pull_request_review` runs the pull request's own workflow file, the
notification is controlled by the pull request and can be deleted or altered by
it. The verdict path does not rely on it: after the `review` job has posted the
commit status, comment and review for a head, a final step reads the pull
request's base through the API and, when it is `develop`, dispatches
`oracle-gate.yml` from `develop` with `pr` and the reviewed `head_sha`. This
covers comment, dispatch and scheduled verdict runs, whose completion carries
`develop`'s head and so cannot be mapped to the pull request by the gate's
bridge. The step runs only when the App is configured and the guard did not skip
the head, so it never precedes the post. It uses the job's `actions: write`
permission for this dispatch alone, posts no status, and the gate re-reads everything
when it runs. The remaining trusted fallbacks are the `oracle-gate` sweep (every push to
`develop`, plus a scheduled run)
and the gate's own triggers: `pull_request_target`, stand-in comments and
dispatch.

GitHub dismisses only approvals and change requests, and the oracle posts
comment reviews, so the notification's `dismissed` trigger covers human reviews,
which are not `oracle-gate` inputs.

GitHub Actions has no event for resolving a review thread, so resolving the
oracle's last thread starts no run of either workflow. `make pr-arm` and
`make pr-arm-remote` dispatch `oracle-gate` for the PR and head when the head's
`oracle-gate` status is not `success`. For a PR that is already armed, run
`gh workflow run oracle-gate.yml --ref develop -f pr=<number>`, or wait for the
next sweep (the next push to `develop`, or the schedule). `make pr-rerun`
re-runs failed CI jobs and does not refresh `oracle-gate`. The checker's decision is
unchanged: it passes only with a success verdict on the head and no
unresolved oracle thread.

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

`oracle-gate` runs `develop`'s copy of the gate and the checker, and both
accept only the oracle signal. `tests/unit/scripts/oracle_reviewers/test_policy.py`
fails if the live policy leaves `mode: enforce` or if delivery is not `review`.
Going back to the connector would need this cleanup reverted and the connector
App reinstalled.

## Required gate from trusted code

A check run from the pull request's workflow file lets a pull request that
edits `.github/workflows/` change the check that judges it, and the commit
status it reads can be set by any pull request sharing the head commit.
That is why `oracle-gate`, not the pull request's workflow, is the required check:
`_project/scripts/oracle_gate.py`
imports the decision and its helpers from `_project/scripts/oracle_review_check.py`
instead of reimplementing them, and posts `oracle-gate` for the pull request
head through the oracle App token. The pass rule is the checker's rule
unchanged: only exempt paths changed, the App's latest review of the head
reports success with no unresolved oracle thread, or a valid stand-in covers
the exact head; anything else posts failure. The script re-reads the review
state immediately before posting, so a run that evaluated stale data posts the
fresh decision instead of overwriting a newer one. It posts only for a pull
request whose base is `develop`, the one branch whose ruleset requires the
gate. The status belongs to the head commit, not the pull request, so a pull
request from the same head into another branch, where the diff might be
exempt-only, must not be able to replace a `develop` pull request's failure
with a success.

A same-repo pull request can still add a workflow with `statuses: write` that
posts the `oracle-gate` context to its own head with a forged success, so the
workflow alone does not make the context unforgeable. The unit test scans every
workflow for steps that could post the context and fails on any outside
`oracle-gate.yml`, but a status posted through a generated file or a new
workflow would land the same way. The binding that closes this is in the
ruleset: the required `oracle-gate` check is scoped to the `benchbox-oracle`
App's integration ID (5201041), so only statuses posted with that App's
identity satisfy it. Without that binding a forged success would count and
the gate property would not hold.

`.github/workflows/oracle-gate.yml` runs `develop` code only. It triggers on
`pull_request_target` (opened, synchronize, reopened, ready_for_review, edited),
on created, edited and deleted issue comments whose body, or for an edit its
previous body, contains the stand-in marker anywhere (so leading whitespace,
which the checker strips, still triggers a run; the checker then decides on the
exact text), on completion of
`oracle-verdict` and `oracle-review` (the bridge for review and thread events,
which have no trusted trigger of their own), on `workflow_dispatch` with a `pr`
input, on a `schedule` sweep over open pull requests targeting `develop`, and on
every `push` to `develop`, so a merged change re-evaluates open pull requests
without waiting for the schedule. GitHub delays and drops scheduled runs (the
observed starts were 3 to 6 hours apart), so the push is the bound while
`develop` is active and the schedule is only the fallback for hours without
pushes. Sweeps measured since 2026-10-24 ran a median of 1.7 minutes and a
90th percentile of 7.2 minutes, and `develop` received about 36 pushes a day,
about one runner-hour a day. Only the sweep job runs on `push`; the
gate and bridge jobs exclude it. Push sweeps share one concurrency group,
separate from every pull request's, and queue rather than cancel, so a running
sweep finishes before the next starts. A completion run evaluates nothing itself: its `bridge` job finds the
open `develop` pull request whose head is the completed run's head commit, not
the first pull request in the event payload, which can target another branch or
be stacked on that head, and dispatches the gate for it from `develop`. The
gate and sweep jobs check out `develop`, never the pull request, with a sparse
checkout of the gate, its predicate and the reviewers policy, and run the
script with pinned `pyyaml` the same way `oracle-verdict` does; the App token
is minted the same way `oracle-verdict` mints it, from the same `oracle`
environment and secrets. When the reviewers policy cannot load, the script
errors instead of deciding with empty rules, and the sweep records every
per-PR error and fails the run when any pull request errored. Every run that
evaluates a pull request is in one concurrency group keyed by its number, with
cancel-in-progress, so a newer run for a pull request cancels the earlier one
instead of racing it.

When evaluation fails after the head SHA is known, whatever the cause (an API,
network, malformed-payload or policy error), the script posts `failure` for
that head with a short error description and exits with the error code, so an
earlier `success` on the head cannot outlive a gate that can no longer
evaluate it. This is deliberately fail-closed: an API outage turns every pull
request the gate touches to `failure` until its next successful evaluation.
The pull request read is retried three times, waiting 2 seconds and then 4
seconds. If it still fails there is no live head, so the script posts `failure` to the
optional `--head-sha` when one was given. Without one it makes a single GraphQL
read of the pull request's `headRefOid` and posts `failure` to that head when it
is a 40-character lowercase hex SHA; a live REST read always wins over both. The
sweep and the bridge dispatch pass a head, and `pull_request_target`
runs pass the head from the event payload, which the resolve step validates
before outputting it. An `issue_comment` payload carries no head, so those runs
rely on the GraphQL read. The one remaining limit is when the REST read and the
GraphQL read both fail with no head supplied: nothing is posted, and the prior
status on the head stands until the next sweep carries the head.
A pull request whose base is not `develop`, and any `--no-post` run, never
posts, errors included. The sweep lists open `develop` pull requests
with `gh api --paginate`, so every one is covered, and its 45-minute timeout is
sized from a measured sweep of 16 pull requests in about 75 seconds (about 5
seconds each), which leaves room for several hundred.

`oracle-gate` became required on 2026-10-21, scoped to the `benchbox-oracle`
App's integration ID, while `oracle-review` stayed required beside it. Across
the 198 pull request heads reviewed from 2026-10-22 the two agreed on 195; the
one disagreement was an `oracle-review` run that went stale while
`oracle-gate` correctly passed, and two heads were replaced before the gate ran.
On 2026-10-24 `oracle-review` stopped being required
(`_project/decisions/oracle-review-check-retired-2026-10-24.md`). Its
workflow now only notifies: its completion after a review event is how
`oracle-gate` re-evaluates without a trusted trigger of its own.
