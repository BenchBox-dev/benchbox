<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Self-hosted oracle review

```{tags} contributor, operations, ci
```

`.github/workflows/oracle-verdict.yml` reviews every pull request (PR) with
agent CLIs, except changes confined to inert paths (see Inert-path manifest),
and posts a non-required `oracle-verdict` status and a review through the
owner's GitHub App. The required `oracle-gate` check reads that App's PR reviews
from `develop` code (see Required gate from trusted code). Policy:
`.github/oracle-reviewers.yml`. Decision records:
`_project/decisions/oracle-review-v2-shadow-2026-10-05.md` and, for verdicts,
evidence and review rounds,
`_project/decisions/oracle-review-protocol-2026-10-08.md`.

The soundness manifest lists `.github/workflows/oracle-verdict.yml` explicitly,
besides covering all workflows by directory prefix, so a PR changing only this
workflow faces the same required `oracle-gate` as other soundness changes; an
absent, pending or failed oracle decision does not pass on its own.

## Setup

1. Create a GitHub App owned by the repository owner with **Commit statuses:
   write** and **Pull requests: write**, installed on this repository only.
2. Before the workflow first runs, create the `oracle` environment with
   deployment branches limited to `develop`; GitHub creates a missing
   environment with no protection rules.
3. Add these `oracle` environment secrets:
   - `ORACLE_APP_ID`, `ORACLE_APP_PRIVATE_KEY`: only the `review` job's `Mint
     the App token` step, after the attempt loop and the redaction check.
   - `TYPESAFE_API_KEY`: only the classify step (TypeSafe's Jev model classifies
     the change), the two disk scans and the redaction step.
   - `ORACLE_GATEWAY_TOKEN`, `ORACLE_GATEWAY_URL`: the reviewer's client token
     for the credential gateway and the gateway's HTTPS URL, held by the attempt
     loop and the redaction step. No provider key, broker URL, broker token or
     owner OMP config is stored for the reviewer; its model roles and fallback
     chains are committed in `.github/oracle-omp-config.yml`.
   - `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`: the tailnet join.
4. Keep provider credentials, `CLAUDE_CODE_OAUTH_TOKEN` included, out of
   repository secrets: same-repository PR workflows can read those.

The `review` job installs a pinned OMP Linux build once and checks its SHA-256
before running it.

### Credential gateway

The reviewer holds no provider credential. The attempt loop runs the pinned OMP
with one environment variable, `OMP_GATEWAY_TOKEN`: a client token for an `omp
auth-gateway` on the owner's Mac. The gateway fronts the credential broker: it
checks the token, limits the client to the listed models and accounts, and
forwards with the owner's real credential, so neither the broker token nor a
provider key reaches the runner. Per attempt, the loop writes the reviewer's OMP
home into a fresh scratch `HOME` (`write_reviewer_home`, behind `cli.py
reviewer-home --home <home> --gateway-url <url>`):
`.github/oracle-omp-config.yml` as `config.yml`, and a `models.yml` pointing
providers `anthropic`, `openai-codex`, `muse-code` and `google-antigravity` at
the gateway with `transport: pi-native` and `apiKey: OMP_GATEWAY_TOKEN` (the
variable name, not a value). An empty or non-`https://` URL is refused.

The gateway answers only while the Mac is awake and online; otherwise attempts
are absent and the sweep retries (see Commands).

**Accepted risk.** The reviewer is not confined to files: it can read any file
the runner user can, including its own client token (for example from
`/proc/self/environ`). This is accepted because the token is scoped to the
listed models, reachable only over the tailnet and quick to rotate. The overlay
and prompt neutralization limit what a PR can steer it to read but are not a
boundary. Do not enable OMP's `secrets.enabled`: it restores real values into
URLs and output.

The `review` job joins the tailnet once, before the attempt loop, through
`tailscale/github-action` with GitHub's OIDC token (`id-token: write`), so no
long-lived Tailscale secret is stored. The step runs only when
`TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE` are set. A failed join does not end the
review; the attempt is absent.

[`oracle-gateway-host.md`](oracle-gateway-host.md) covers building, checking,
rotating and moving the gateway host, and the Tailscale OIDC credential and
tailnet policy the job needs.

**Cut-over cleanup (one time).** The pre-gateway secrets
(`CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `META_API_KEY`,
`OMP_AUTH_BROKER_URL`, `OMP_AUTH_BROKER_TOKEN`, `OMP_AGENT_CONFIG`) are deleted
from `oracle`. Rotate those credentials once and remove the rule that still lets
`tag:oracle-ci` reach the broker on `tcp:8765`; if nothing else reaches the
broker over the tailnet, stop publishing it (`tailscale serve --https=8765
off`).

Without `ORACLE_APP_ID`, the job logs the result and succeeds.

## How a run works

`oracle-verdict.yml` has two jobs: `sweep` dispatches due retries; `review` does
everything else for one PR in one runner-pool admission
(`_project/decisions/oracle-single-review-job-2026-10-25.md`). Its `if:` admits
a manual dispatch, a `pull_request_target` event other than a plain edit, and a
`/oracle-review` comment from an owner, member or collaborator. Steps:

1. Refuse a dispatch from a ref other than `develop`, and check out trusted
   `develop` into `trusted`.
2. **Plan** (job token only) resolves the PR and head through the API, refuses
   closed, draft and non-`develop` PRs, classifies the change, applies the rerun
   rules and writes a 0600 brief. A fork's change outside the inert paths gets a
   pending `fork: owner review` status and never reaches a reviewer; an
   inert-only change gets `success`. Every later step is skipped unless the plan
   decides `review` or a post. The plan's head SHA feeds the next step.
3. Check out the PR head into `workspace`, as data never executed.
4. **Classify** (the only step setting `TYPESAFE_API_KEY` for classification)
   asks Jev, through TypeSafe's `/v1/systemone` API, for the size bucket, review
   capability, review template and per-file priority, and selects the attempt-1
   reviewer. The bucket names the first reviewer in
   `.github/oracle-reviewers.yml`; later slots reuse the classification and can
   move up the chain (see Rounds). Review-machinery changes always get the
   `gate` template and at least the `large` bucket. Selection skips disabled
   reviewers, quota-exhausted pools and, when the diff exceeds the brief cap,
   any reviewer that is not hard read-only with `reads_files: true`.
5. Disk scan (C4).
6. Install and verify OMP, then join the tailnet; either failing leaves the
   attempt absent instead of ending the run.
7. **Attempt loop** (`cli.py attempts`): per slot up to `max_attempts` (five),
   select the reviewer, reset the checkout, create a fresh scratch `HOME` with
   its OMP home, run the reviewer, confirm nothing was left behind, delete the
   `HOME`, and write the slot's result file: an artifact with run ID, head SHA,
   reviewer, and a validated verdict or an absence. A crash or timeout still
   leaves an absence. OMP runs on a detached, credential-free head checkout: an
   empty working directory, the checkout as a read-only workspace, tools
   `read,grep,glob`, `--no-skills`, `--no-rules`, and a trusted overlay. The
   overlay turns off project MCP config, the advisor, context promotion,
   prewalk, URL fetching (`fetch.enabled: false`) and every discovery provider
   except OMP's built-in defaults, so the checkout's `AGENTS.md`,
   `.omp/AGENTS.md`, `CLAUDE.md`, editor rule files and plugins do not load. OMP
   reads any file named by an `@` mention in the prompt before a tool call
   (relative paths from the working directory), so in the change data and file
   priority the brief puts a zero-width space before every `@` followed by a
   character other than whitespace or another `@`; an `@` after that character
   starts no mention. None of this confines the file tools (see Accepted risk).
   PR code never runs.
8. Disk scan (C4); finalize, which re-validates this run's artifacts and replays
   selection; redaction check (C5).
9. **Post.** Mint the App token, run the guard, post the status and findings,
   upload the retry state (only if the status posted) and attempt artifacts, and
   dispatch `oracle-gate`. `findings_delivery` in `.github/oracle-reviewers.yml`
   chooses the findings form:
   - `comment`: one PR comment, so findings never block a merge.
   - `review` (the policy value, required by the oracle signal): one PR review
     per result on the head commit. Under SHIP WITH FIXES, a defect on a diff
     line opens a thread: multi-line when every line of its span is in the diff,
     else single-line at its first line. Other defects go in the review body
     with the summary. DO NOT SHIP opens no thread. A run with no verdict still
     posts a review explaining the pending result, so the latest review reflects
     the latest run. The ruleset requires every thread resolved, so threads
     block merges.

## Job structure and controls

The reviewer is an untrusted model in a trusted read-only harness: it can read
what the runner user can and leak it only through its published review text.
Step environment scoping alone does not hide secrets from the runner: the
initial job message can keep the App key and job token in Runner.Worker memory
all job. So, immediately before the loop, a permanent Linux prerequisite
requires Yama `ptrace_scope` 1, 2 or 3, one non-root UID, and no effective,
permitted, inheritable or ambient `CAP_SYS_PTRACE`. Yama stops a child reading
ancestor or unrelated process memory without an explicit tracing grant. This
assumes the runner grants no `PR_SET_PTRACER` access and the harness neither
runs privilege-escalation tools nor changes UID or capabilities; it is no
sandbox against arbitrary execution with the runner's sudo access. Parent step
environments stay readable, so the loop environment holds only its gateway
values (plus the accepted OIDC request token). Every control is permanent, not a
one-time inspection:

| Control | Enforced by |
|---|---|
| C2 total exit | After each reviewer exit, `runner.execute` lists the harness process group and, on Linux, every orphan the loop adopts as child subreaper. Survivors are killed and the run fails closed: slot absent, loop stops, job fails, nothing posts. |
| C3 no secret in a script | The workflow test fails if any `run:` block names `secrets.*` or `github.token`; secrets reach steps only via `env:` or `with:`. |
| C4 disk scan | Before the reviewer and after the last exit, `cli.py scan-disk` searches `$HOME`, `$RUNNER_TEMP`, both checkouts, `/tmp`, `/var/tmp`, `/dev/shm` and `${TMPDIR:-/tmp}` for the exact TypeSafe key and job token, deduplicating nested roots and following no file or directory symlinks. It covers the declared readable scratch roots, not every readable file. Values come from the step's own `env:`. A hit fails the job. |
| C5 redaction completeness | After finalize, `cli.py redact-check` runs in a step holding every secret the job held (gateway token, TypeSafe key, gateway URL, Tailscale client ID and audience, OIDC request token, job token), redacts attempt, verdict, diagnostic, absence and result text across control-character splits, and fails if a value remains. The loop step cannot hold these: a same-user child can read its parent's environment. |
| C6 fresh `HOME` | Each attempt gets a new `mkdtemp` directory; the previous one is deleted first and the checkout reset. |
| C7 token scope | Job permissions are exactly `actions: write`, `contents: read`, `pull-requests: read`, `id-token: write`. `github.token` appears only in the `env:` of steps before or after the loop; both checkouts use `persist-credentials: false`. |
| C8 timeout budget | `timeout-minutes` is `max_attempts` times the longest reviewer `timeout_minutes` plus setup, under 360; the harness timeout fires first and becomes an absence. A workflow test recomputes it from the policy. |
| C9 cancellation | `cancel-in-progress` is true only for `pull_request_target`; comment, dispatch and sweep runs queue behind a run in flight. Posting steps keep the default success condition, so a cancelled run posts nothing. |
| C10 App mint last | `ORACLE_APP_PRIVATE_KEY` is referenced by the review job's posting mint, after the loop, second disk scan and redaction check, and by the sweep job's failure-status mint; the sweep job runs no reviewer, so its mint cannot leak to one. |

The OIDC request token shares the gateway token's terms in the loop environment:
GitHub documents it as valid only while the job runs, and it is redacted. The
gateway URL is sensitive and redacted too.

If any of C2 to C10 cannot pass on GitHub-hosted runners, the fallback is the
same `review` job without posting steps, plus a separate `post` job that holds
the App key and reads the uploaded artifacts.

## Inert-path manifest

`.github/inert-paths.txt` is the source of truth for code-driven review and
coverage scope: `glob` lines exempt broad prose and `except` lines keep
machine-read documents in scope (exemptions minus machine-read exceptions). The
`classifier.exempt_paths` policy key is gone and parses as empty. The Python
consumers (`oracle_review_check.py`, the reviewer classifier,
`coverage_test_selection.py`) read the manifest through
`_project/scripts/inert_paths.py`; soundness and gate paths take precedence.
`.github/path-filters.yml` does not read it: its `code-ci` list hand-maintains
the machine-read documents routed to code CI.

Add an `except` line to keep a document in review scope; remove it to return the
document to inert. Every `except` line must match a tracked path
(`test_every_exception_matches_a_tracked_path`), which is why the assignment
seed `scripts/*.md` was dropped. The guard also fails when live scanning finds
an inert-path reference without an exception
(`test_guard_reports_no_unexcepted_inert_references`).

`_project/inert-doc-readers.json` maps each machine-read document to the tests
that read it, so a docs-only change selects its readers and escalates only when
nothing reads the document. It is checked in so the mapping is reviewed like
code and CI verifies freshness
(`test_checked_in_readers_match_the_rebuilt_index`). Regenerate with `python3
_project/scripts/inert_paths.py --regenerate-readers` and commit.

## Rounds

The oracle keeps its history in its reviews. Each decisive review ends with a
hidden marker, `<!-- oracle-protocol: v1 ... -->`, written by code after the
sanitised model text (which cannot contain an HTML comment; defect file paths
are escaped likewise). It records cycle and round, round kind, decision, head,
base branch, reviewer, size bucket (`tier` field), strike count, open defects
with ids, and each changed file's identity hash (8-hex path hash, 16-hex
identity hash). A marker over 4 KB keeps only the aggregate digest, computed
from the full-length identity hashes; the per-file map fits about 200 changed
files with 10 open defects, about 210 with none. Without the map every file
counts as changed, so a larger PR gets a follow-up on every file after each
push. `plan` reads every review from the App's Bot account, all pages, ignoring
dismissed and markerless reviews. More than one marker in a review, an
undecodable marker, or an unreadable review list holds the result pending.
Pending results write no marker.

A file's identity is its old and new path, old and new mode, and full merge-base
and head blob ids. In CI the files API supplies paths and head blob id and the
recursive git trees of merge base and head supply modes and blob ids; locally
`git diff --raw --full-index --no-abbrev` against the merge base supplies the
same six fields, so a PR has one digest in both places. An added or removed file
has mode `000000` and blob id forty zeros on the missing side. The patch digest
covers every changed file, prose included, and alone keys carries, strikes and
forced re-review budgets. If any field is unreadable (truncated file list or
tree, a head blob id disagreeing with the tree, an unresolvable merge base) the
digest is `unread-<head>` and nothing carries.

The marker is not signed, so an account with write access could rewrite it by
editing the review. The planner, the post guard, the merge digest, finding
deduplication and dispute binding therefore fail closed on edited App objects.
`plan` and the guard read every App review's `lastEditedAt` and
`includesCreatedEdit` in one batched GraphQL call (the `review` job's
`pull-requests: read` token is enough). An edited App review, marked or not (so
a stripped marker cannot hide a record), holds the result pending, and no marker
from it ever drives a carry, a follow-up scope or a strike count. If the edit
state cannot be read (a GraphQL error, a review without a node id, a missing
field) the `plan` step fails and posts nothing, so a decided head keeps its
latest status. The guard lets a planned hold post its pending status, but fails
the run when the history it re-reads has an error the plan did not see (an edit
that landed after the plan), and when it cannot re-read the history after its
three tries, instead of posting unconfirmed.

Before a carry is planned, the recorded decision is checked against the App's
own `oracle-verdict` status for the reviewed head: only statuses created by the
App's Bot account count, the one whose description carries the record's
`[run <uuid>]` is chosen, and when several qualify the one created nearest the
review's submission wins (a record with no run uuid considers every App success
or failure, and a non-`SHIP` record falls back to the App statuses without a run
uuid, because only success statuses carry one). The status must be `success`
exactly when the decision is `SHIP`; `SHIP WITH FIXES`, `DO NOT SHIP` and
`REFUSED` require `failure`. A mismatch or a missing status holds the result
pending, and a status list that cannot be read fails the `plan` step.

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

A base-commit move alone does not restart a cycle. A decision carries only on a
full-length digest match, so a rebase changing any touched file, a moved hunk,
or a mode, rename or binary change gets a fresh review. A truncated file list,
unreadable blob ids or a file leaving the PR never carries; each, like a changed
digest whose per-file hashes do not show which file changed, gives a follow-up
on every file. Since the digest also keys strikes and forced re-review budgets,
a rebase onto a `develop` that changed one of the PR's files is a new digest: it
can add a strike if refused and starts a new forced re-review allowance. A
rebase leaving the merge-base diff byte-identical keeps digest, strikes and
allowance.

A follow-up brief lists earlier defects by id (D1, D2, ..., numbered by code
within a cycle) and asks for each to be marked fixed, not fixed or withdrawn,
with evidence; no status means not fixed. Unfixed earlier defects stay open. A
new defect counts only in a file changed since the last review; new defects
elsewhere are listed as not counted, collapsed and capped at five. More than 10
open defects give DO NOT SHIP. The previous reviewer goes first unless the
follow-up's own bucket names a larger one, as for a push touching review
machinery. Follow-ups run for every size bucket.

Follow-up scope preserves every exact changed-path match and additionally
compares Unicode NFC forms and symlink aliases resolved inside the head checkout.
A case-only citation matching one changed path uses that path, unless the cited
path exists in the head checkout as its own file, which is never folded; an ambiguous
case-only match still counts against the decision rather than disappearing.
Canonical matching can only bring findings into scope. Findings on genuinely
unchanged files remain not counted, and aliases resolving outside the checkout
do not establish a match.

A forced follow-up rejudges rather than skips. Its brief lists each disputed
thread under "Disputed findings", keyed by the finding id from the thread's
hidden marker, with replies quoted as data. Only disputes bound to a
current-cycle prior finding are enforced, shown and published; stale or unknown
disputes stay data. Each keyed disputed finding needs an explicit disposition:

- **Withdraw**, citing an examined head-commit file and line that refutes it.
  Withdrawing grants the rebuttal, so concedes must be set; withdrawing without
  conceding is invalid.
- **Maintain** as not fixed: restate one shown rebuttal reply exactly, set
  concedes to the JSON boolean true or false according to whether the rebuttal
  holds, and cite an examined head-commit file and line that answers it, or an
  examined reproduction script as `repro: path`. Conceding while keeping the
  defect not fixed is invalid.

The response template shows concedes as null, correct for an undisputed defect;
a disputed defect left null is invalid, as is a missing disposition or a claim
matching no shown reply. Invalid attempts are retried. A maintain or fixed
disposition whose citation does not verify is not invalid: the defect stays open
and the disposition is published with the evidence gap. A demoted withdrawal or
fix is judged exactly as a maintain, so its concedes and claim cannot invalidate
it. A keep-open verdict is never discarded or rerolled; only the safe direction
is taken. A withdrawal without a verified file-and-line citation counts as not
fixed (the head-commit existence check still runs when citations are marked), as
does a defect marked fixed on an unchanged patch, so only evidenced dispositions
leave the open list; withdrawals are reported separately as never defects. Every
disposition and its evidence is published under its finding in the review body;
if the body would exceed the cap, finding details shrink first and no
disposition line is dropped.

A thread reply is a keyed dispute only if it is from an owner, member or
collaborator other than the oracle bot, has a line starting `Dispute:` stating
the case against the defect, and sits on a thread carrying the hidden finding
marker. Other accounts' replies never reach the brief or force a re-review. A
`Dispute:` reply on a markerless thread is shown as data and forces nothing. A
plain acknowledgement such as `done` is not a dispute.

Forced re-reviews run only after SHIP or SHIP WITH FIXES, never after DO NOT
SHIP. The third for one patch is refused with a message to change the patch or
ask the owner for a stand-in; empty commits and byte-identical rebases keep the
digest and so share its budget. Ordinary follow-ups never consume it, and a push
disputing nothing is carried, not rejudged.

The oracle never resolves a thread: a follow-up lists defects it verified fixed,
and the author resolves those. Each new thread carries a hidden `oracle-defect:
c<cycle>-D<n>` marker before its `oracle-finding` fingerprint, which stays last.

After three DO NOT SHIP decisions on distinct patches of one PR, the oracle
posts a refusal (`failure`, `Decision: **REFUSED**.`) once per head and reviews
no more. Carried and refused records do not count, and the strike count never
drops below the retry state artifact's. Reopening does not reset it; a new PR
does, so the limit is friction against review loops, not a security control. A
stand-in attestation still works on a refused PR.

Before posting, the `review` job re-reads the review list and posts nothing if
the head already has a marker, so two runs cannot both post for one head. It
tries three times about two seconds apart; if every try fails, or an App review
was edited, the guard step fails and nothing is posted: the head keeps its latest
status and a later run decides it. A run that skips posting, or whose status post
failed, uploads no retry state and adds no strike.

`post` drops any finding matching an open review thread from this App by file
and normalized title, unless the new one is more severe. Each new thread carries
a hidden `oracle-finding` marker with that fingerprint. A dropped finding still
counts toward the result and is listed in the review body. An open thread whose
first comment was edited is ignored for this: its fingerprint never suppresses a
finding. A dispute under an edited App first comment is never bound. If a
thread's edit state cannot be read, nothing is deduplicated and no dispute is
bound.

A defect on a line outside the diff still fails the review. It cannot be a line
comment, so the body lists it with a note that it counts like the others;
otherwise a real defect citing an unchanged line would pass.

Reviewers run one at a time; a decision stops the run. The chain starts at the
bucket's reviewer (for a follow-up, the previous reviewer if it belongs to a
larger bucket), then takes larger buckets' reviewers in size order, skipping any
whose harness, model and effort match one already in the chain: `trivial` and
`small` share `@smol`, so a run never spends slots on both. A reviewer absent as
`invalid`, `incomplete`, `empty` or `error` runs once more before the chain
advances, so at most two attempts per reviewer and `max_attempts` per run. Never
retried: a decision; `quota`, `auth` or `timeout` absences; a reviewer job that
did not finish (`ensure-attempt` records it with the fixed `JOB_FAILURE_DETAIL`
detail); a reviewer selection would skip (disabled, out of quota, or unable to
take the brief). Selection and artifact replay share this rule, so a third
attempt of one reviewer, or any attempt after a decision, is rejected. If every
reviewer is absent or skipped, the run stays pending. A SHIP from the first
reviewer that answers ends the run like any decision.

An OMP event stream ending with no final assistant text (for example after a
tool call) is absent as `empty`, detail "the reviewer ended without a final
answer". Prose or malformed-JSON final text, and OMP output that is not an event
stream, is `invalid`. Both are retried alike.

The review body lists every absent attempt under "Reviewers not available" as `-
<reviewer> (attempt <slot>): absent (<kind>): <detail>`. The detail is the
attempt artifact's, credentials redacted, whitespace collapsed, at most 160
characters, and omitted when absent. Raw reviewer responses are never published
there.

## Decisions

The canonical schema-2 response has seven reviewer-supplied properties: `status`
(`complete` or `incomplete`), `incomplete_reason`, `decision` (`SHIP`,
`SHIP_WITH_FIXES`, `DO_NOT_SHIP`, or `NONE` when incomplete), `summary`,
`files_examined`, `defects` and `prior_defects`. The oracle adds the computed
`defect_count` to stored verdicts; reviewers cannot set it. Stored artifacts
must match the canonical fields strictly.

Only reviewer ingress normalizes known harmless drift:

- Missing `incomplete_reason` becomes empty; missing `summary` becomes empty
  except for DO_NOT_SHIP, whose summary is the published rationale.
- Finding metadata `line_note`, `end_line_note` and `detail_extra` is removed.
- Prior metadata (`severity`, `file`, `line`, `end_line`, `title`, `detail`) is
  not removed: a prior entry must hold exactly `id`, `status` and `evidence`, so
  a current finding filed under the priors cannot pass as a judgment.
- An object keyed by prior ID becomes a list. An omitted embedded ID comes from
  the key; an embedded ID must agree once the supported `#` prefix is removed.
  Duplicate normalized IDs stay invalid.

Duplicate JSON keys at any depth, unfilled template placeholders, unknown
top-level keys, reviewer-supplied defect counts and missing decision-bearing
fields stay invalid. No finding is discarded or given an invented location.
Applied rewrites are recorded in the diagnostic. Invalid JSON keeps a redacted
final-message tail; parseable invalid output also gets a bounded keys-and-types
fingerprint, never field values.

`tests/fixtures/oracle_reviewers/pr3237_missing_text_fields.json` preserves
already-redacted decoded payloads (not raw transcripts) from the attempt
diagnostics of runs `39662535698` and `39669405558`. Both keep the unfixed prior
finding rather than becoming approvals after normalization.

The brief asks for must-fix defects only, at most `protocol.max_defects` (10),
and for DO_NOT_SHIP when the change needs rework or more defects would be
needed.

The prompt ends, after the fenced change data, with a response template: JSON to
populate, not an example or schema. It holds the seven verdict keys with
placeholders from `verdict.RESPONSE_PLACEHOLDERS` (line `0`, `end_line` null)
and, in a follow-up, the actual prior defect ids, revalidated against the id
grammar, with unfilled `status` and `evidence`. Ingress rejects any remaining
placeholder. The three prompt templates (`standard`, `gate`, `deep`) are at
version 2.

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

Severity orders the list and never gates: any listed defect, even Low, makes
SHIP WITH FIXES. SHIP posts `success`; SHIP WITH FIXES and DO NOT SHIP post
`failure`. The body opens `### oracle-verdict: <state> for <sha>`, then a
`Decision:` line. Reviews before 2026-10-09 open with `###
oracle-review-shadow:`; the required check still reads that on open PRs, and the
merge digest reads both permanently since it reads merged history. More than 10
defects is not an invalid verdict, because every reviewer would then fail in
turn and leave the PR pending.

## Model selection

Each reviewer in `.github/oracle-reviewers.yml` names an OMP role alias, not a
model: `trivial` and `small` use `@smol`, `medium` `@default`, `large`
`@review`, `huge` `@plan`, `max` `@slow`. The committed
`.github/oracle-omp-config.yml` maps each alias to a model (`modelRoles`) and
each primary model to its fallback chain (`retry.fallbackChains`). Chains are
keyed by primary model id, not role: on the pinned OMP build a role-keyed chain
makes every `--model @role` walk the `default` chain, while a key containing `/`
matches by model and restores each role's chain. `retry.fallbackOn` is
`usage-limit`, so only a usage limit walks the chain; a revoked token,
out-of-scope model or outage fails the attempt rather than silently switching
model family. Roles and chains change by PR to that file, which must keep one
chain per primary model and no role-name keys (a test checks). An empty `effort`
takes the role's thinking level.

`@smol` maps to `openai-codex/gpt-6-luna:xhigh`, first usage-limit fallback
`anthropic/claude-sonnet-5-5:medium`. `trivial` and `small` are in the `codex`
pool, all other reviewers in `claude`, so a quota absence on one provider leaves
the other's reviewers available. Rationale, including dropping Claude Haiku:
`_project/decisions/oracle-small-reviewer-model-2026-10-23.md`.

Reviews record the policy's reviewer name, not the answering model. They do not
exclude the author's model family: the owner chose role-based selection with OMP
fallback over family exclusion.

## Evidence that a review happened

A defect-free verdict can be hollow: a reviewer that could not read the files
may still answer. The job checks each verdict against the head checkout before
recording it; a failure records the reviewer absent (`incomplete`), so it runs
once more before the chain advances, and with no verdict the run stays pending.
A hollow answer never passes.

- Every defect must cite a head-commit file and a line within it. One that does
  not is kept, noted as citation not found, so it still fails the change;
  discarding the verdict would hand the change to a reviewer that might pass it.
- In `file-list` mode (the brief lists files instead of the diff), a verdict
  that would be SHIP must name in `files_examined` every soundness-path file
  under review still present at the head (in a follow-up, those changed since
  the last review), and OMP's event stream must show a successful `read` of each
  or a `grep` whose requested path is that single file. This includes a
  follow-up whose only defects are in unchanged files, since those are not
  counted and it would ship. A failing verdict (DO NOT SHIP, a counted in-scope
  defect, or an earlier defect not fixed) needs neither. A multi-path or
  directory `grep` credits nothing, and `details.files` is ignored: it lists
  matches, not reads. The recorded transcript
  (`tests/fixtures/oracle_reviewers/omp_multi_turn_review.jsonl`) keeps grep
  match metadata (`files`, `fileMatches`, `matchCount`, `fileCount`) apart from
  the rendered `displayContent` with its own `truncated` flag, and its only grep
  is untruncated, so `files` is unproven under truncation.
- An inline brief carries the diff; no read is required.
- Read credit is exact-path equality after normalisation, never prefix or
  sibling. `read` and single-file `grep` accept the documented selectors:
  `:raw`, `:N`, `:LN`, `:N-`, `:N..`, `:A-B`, `:A..B`, `:LA-LB`, `:A+C`, `:-N`,
  comma lists such as `:1-12,-15`, and `:raw` with a range in either order. A
  computed `?q=` read, `:conflicts` and `:img` credit nothing: a query returns
  whatever it computes (one character was observed). A committed file named as a
  selector-shaped variant of another (`a.py:raw`, `a.py:conflicts`, `a.py?q=.x`
  or `x.whl:pkg/m.py` beside committed `a.py` or `x.whl`) is ambiguous, since
  OMP's resolution order is unverified, so neither is credited from that read.
- A successful read of an archive listing or member (registered archive
  extension, inside the checkout) credits only the archive; a member path never
  credits a repository file. An error, binary-entry notice, `?q=` or `..`
  member, no match, directory, `;`-joined read including an archive member,
  sibling or outside path credits nothing. Binary and unreviewable content,
  out-of-bounds line reads and notice-only reads stay with
  `oracle-verdict-labels-unreviewed-bytes` and
  `oracle-read-evidence-tightening-20261008`; no file is exempted.
- Partial reads keep credit: exposure, not whole-file or semantic proof. The
  overlay sets `read.summarize.enabled: false` so a large file's plain read is
  not replaced by a structural summary; the trace records each credited read's
  `details.summary` elision counts (not rejected). Without summarisation an
  open-ended read is still capped by `read.defaultLimit` (300 lines), undetected
  by the gate. The real path must stay inside the checkout, so a committed
  symlink to an outside file credits nothing.
- Every parsed outcome keeps a bounded, redacted `trace:` line in the attempt
  diagnostic: read, grep and failed-call counts, up to five credited, required
  and missing paths, and elision totals, so elided credited reads stay
  measurable. It never carries the raw event stream, and OMP stdout is no longer
  excerpted.
- Credentials: `commands.omp_env()` forwards only the base environment and the
  gateway client token (`GATEWAY_ENV`, `OMP_GATEWAY_TOKEN`). `credentials.py`
  `NAMES` lists that token, the broker and provider variables the loop no longer
  receives, the runner-held values (gateway URL, Tailscale client ID and
  audience, OIDC request token) and legacy extras, so a local run still holding
  them is redacted too. Values of eight or more characters are removed by exact
  match before and after every sanitising transform and before truncation,
  across control-character splits and overlaps, in verdict text, absence
  details, diagnostics and error snippets. A known value in a structural field
  (file, `files_examined`, prior id, object key) invalidates the verdict with a
  fixed message instead of moving a finding. This is known-value hygiene, not a
  sandbox: dynamically brokered values, values only inside `OMP_AGENT_CONFIG`,
  and values under eight characters cannot be matched. Local fake-sentinel
  probes showed `read` returns a committed symlink target, an absolute outside
  path and a `..` path (the checkout is not a read boundary) and reaches
  `/proc/self/environ` and the home configuration, which is why the reviewer
  holds only the scoped gateway token
  (`oracle-response-read-boundary-prerequisite-20261021`).

The verdict is the reviewer's final assistant message, from its `message_end`
event; remarks between tool calls are ignored. A final message that is not
exactly one JSON verdict is invalid.

The brief no longer forbids commands, but OMP's tools are only `read`, `grep`
and `glob`, so the reviewer cannot write or run anything. A file-list brief also
stages the whole PR diff in the checkout. The workflow-level concurrency group
serializes runs per PR; the `review` job has none of its own, so a new push
cancels a run in flight, which then posts nothing.

## Commands

- `/oracle-review` at the start of a top-level PR comment from an owner, member
  or collaborator reruns the review on the current head. Thread replies and line
  comments do not trigger it (they are review comments, not issue comments). A
  manual rerun may re-review a head after a decisive result, within the daily
  retry budget and doubling backoff, so an author can dispute a finding with a
  `Dispute:` thread reply without a new commit; with no `Dispute:` reply the
  decision stands. The brief carries unresolved thread replies as disputed
  findings keyed by finding id (see Rounds), and the reviewer checks any claimed
  grammar or documented behaviour.
- `gh workflow run oracle-verdict.yml --ref develop -f pr=<number>` does the
  same; other refs are refused.
- The sweep retries PRs whose last result on the current head is pending and
  due. It runs on every push to `develop` and hourly; because GitHub starts the
  schedule irregularly, the first push after a head becomes due is the usual
  trigger. The target is a dispatch within 25 minutes of that push, or at the
  next scheduled sweep without one. Push and schedule sweeps share one
  concurrency group that never cancels a running sweep. It lists seven days of
  `oracle-verdict` runs, all pages, and skips a PR with a run queued, in
  progress, waiting, pending or requested, or started in the last two minutes,
  so it never cancels a retry in flight. It also re-dispatches a head whose
  latest run failed, timed out or was cancelled without leaving retry state,
  unless the head has a decisive App verdict status; the dispatch carries a
  marker the plan verifies against the head's failed runs, so it reviews instead
  of skipping on backoff. This happens once: when the two latest runs on the
  head failed without a result (ignoring runs that never reviewed it, such as a
  skipped plain-comment run), the sweep stops dispatching and posts a pending
  `oracle-verdict` status naming the failed step, with an App token minted in
  the reviewer-free sweep job. An existing pending status is left alone; without
  the App the sweep reports the gap instead of failing. A failure in one PR's
  lookup, dispatch or surfacing is reported, the others continue, then the job
  fails. Daily budget, backoff and attempt limits are unchanged.
- `make oracle-backoff-reset PR=<number> HEAD=<sha> REASON='<why>'` lets the
  owner clear one PR head's backoff and retry window once. It needs a prior
  `Owner instruction` comment from an attester naming the head, as for a
  stand-in. It posts `Oracle backoff reset: <sha>` and the reason, which `plan`
  and the sweep honour only from an attester, unedited, after that instruction,
  until the next verdict run reviewing the head spends it. It applies only when
  the head's recorded result is a pending reviewer outage; it never re-rolls a
  decisive verdict or restores budget.

A same-head rerun needs a previous run where every attempted reviewer was
absent. A run pending because its artifacts failed validation, or a selected
reviewer never reported, is not rerun on that head; push a new one. Comment,
dispatch and scheduled reruns share a per-PR daily budget
(`retry.daily_budget`), with backoff starting at one hour and doubling. Retry
state is the `oracle-verdict-state-<number>` artifact of this workflow's latest
run whose commit is on `develop`; other workflows' and branches' artifacts are
ignored. Without one, `plan` reads the `oracle-review-shadow-state-<number>`
artifact from runs of the former `.github/workflows/oracle-review-shadow.yml`,
so strikes, budget and backoff survive the rename.

A PR whose brief exceeds the size cap even without the diff (its file list alone
is too long) cannot be reviewed: every reviewer is skipped and the result is
pending with cause `oversize`. Its review says the change is too large and
suggests splitting the PR or asking the owner for a stand-in. The head is not
retried; a new push plans again.

## Local pre-PR review

`make pr-open` reviews the committed `HEAD` before pushing, catching Critical or
High defects before a PR exists. The command is `cli.py local-review`; `make
trunk-revert` opens its PR through `gh pr create` without it. The review is
advisory, labelled as possibly differing from the CI oracle, and posts nothing
to GitHub.

- **Scope.** `cli.py plan --local` builds the plan from git: base is the merge
  base of `origin/develop` and the pinned head; no PR number, prior reviews or
  retry state. Uncommitted and untracked changes are never reviewed: the
  reviewer works in a clean clone at that head. The policy
  (`.github/oracle-reviewers.yml`), soundness manifest
  (`.github/soundness-paths.txt`) and workflow pin come from `origin/develop`,
  as the hosted job reads them from the base branch, so a branch cannot widen
  its own exempt paths. Reviewer code and templates come from the checked-out
  tree. Soundness scope, chain, brief and templates are CI's (the template calls
  the change "unopened"), run through the same `cli.py attempts` loop and
  redaction as the `review` job. The hosted Linux isolation check and disk scans
  do not run locally.
- **Classifier key.** `TYPESAFE_API_KEY` comes from the environment or, on
  macOS, keychain service `TYPESAFE_API_KEY`, added once with `security
  add-generic-password -s TYPESAFE_API_KEY -a "$USER" -w` (it prompts for the
  value). Without a key, including in cloud sessions, the review says it uses a
  fixed default: the policy's `medium` bucket reviewer with the `standard`
  template, or `gate` when review-machinery files change.
- **Credentials.** Local sessions use the `oracle-local` gateway client at
  `http://127.0.0.1:4000` with the token in
  `~/.omp/oracle-gateway/local-client.token`. Cloud sessions
  (`ORACLE_CLOUD_GATEWAY_TOKEN` set or `BENCHBOX_EPHEMERAL_CLONE=1`) use the
  `oracle-cloud` client from `ORACLE_GATEWAY_URL` and
  `ORACLE_CLOUD_GATEWAY_TOKEN`, refusing if either is missing.
  `write_reviewer_home` accepts `http://` on a loopback host only for local
  review; the CI loop still requires `https://`. Token, key and URL values are
  never printed: every line passes the redaction that covers the stored result.
  The hosted accepted risk applies: the reviewer can read the user's files,
  including that token.
- **Harness pin.** The `omp:` section of `.github/oracle-reviewers.yml` pins the
  version and each platform's URL and SHA-256. CI reads it from its trusted
  checkout via `cli.py install-omp` under isolated `uv` with `pyyaml==6.0.3`;
  YAML is imported only to parse the policy, so other bare-Python CLI commands
  need no PyYAML. The shared `omp_pin.py` installer downloads into
  `~/.cache/benchbox/omp/<version>/<platform>/omp` (`BENCHBOX_OMP_CACHE`
  overrides the root), checks SHA-256 before making it executable and its
  reported version before caching. Unpinned platforms refuse by name. The
  workflow's legacy `OMP_URL`, `OMP_SHA256` and `OMP_VERSION` values must equal
  the policy pin: CI's installer refuses otherwise, and the workflow test
  enforces it.
- **Blocking.** A Critical or High finding, or DO NOT SHIP, makes `pr-open`
  refuse before pushing (exit 1). A `Local oracle override: <reason>` line with
  a non-empty reason in the PR body file (`PR_BODY_FILE`) lets it proceed and
  stays in the body; an empty or `<placeholder>` reason, or a line inside an
  HTML comment or code fence, is refused. Nothing else overrides, not even a
  commit message. An unreachable gateway, timeout, quota exhaustion, no verdict,
  missing credential or `omp` version mismatch also refuses (exit 2), and the
  override does not apply.
- **Pinned head.** `pr-open` records `HEAD` before the review, passes it as
  `--head`, and refuses to push if `HEAD` moved during the review or lock wait,
  so the pushed head is the reviewed one.
- **Cache and lock.** Results are cached under `~/.cache/benchbox/local-oracle`
  (`BENCHBOX_LOCAL_ORACLE_DIR` overrides), keyed by patch digest, base commit,
  head tree, policy hash, `omp` version, whether a classifier key was present,
  and a hash of the review machinery (the `oracle_reviewers` package, its
  templates, the OMP config). An unchanged patch reuses the result, even after
  an override; a keyless default result is not reused once a key exists, and
  machinery changes are reviewed again. Only verdicts are cached. A lock in the
  same directory allows one local review per machine; a second waits up to an
  hour, then refuses.

## Calibration

The workflow has five attempt slots. Each reviewer authenticates in CI through
the credential gateway with the `ORACLE_GATEWAY_TOKEN` client token; one that
cannot is set `enabled: false` with a `disabled_reason`.

Capture each reviewer's quota, authentication and outage output from real runs.
The attempt artifact's `diagnostic` holds one redacted output line of at most
300 characters or, for an invalid verdict, the last 2,000 characters of the
final message, both stripped of credentials and long tokens; raw output is never
uploaded. For OMP only stderr is excerpted, never stdout. A `trace:` line of at
most 700 characters follows every outcome that reached verdict parsing (valid,
invalid, incomplete or failed evidence); its paths are redacted by known value
and secret pattern, not the generic long-token filter. Add the messages to the
`CALIBRATED_*_PATTERNS` tables in
`_project/scripts/oracle_reviewers/absence.py`; until then they count as errors,
which never pass. A quota is the terminal event's structured `errorStatus` 429
and an authentication failure is `errorStatus` 401; the message text of that
event never classifies a failure, and the `CALIBRATED_*_PATTERNS` tables apply
to stderr only. A quota absence records the `provider` of the terminal event,
which may be a fallback provider. It skips the reviewer's whole pool for the rest
of the run. In later runs the block lasts one hour (`QUOTA_BACKOFF` in
`selection.py`) unless the provider is the pool's own primary provider
(`POOL_PROVIDERS`) and the reset time was parsed from that error's own message;
such a block lasts until the reset but at most 24 hours (`PRIMARY_BLOCK_CAP`),
long enough to cover a daily or session limit without letting one stale reset
hide a pool for days. A new head carries only primary-provider blocks, and an
owner backoff reset carries none. A pending reason names the provider that
returned the quota error and when the block was recorded.

## Changing the reviewer harness

The harness boundary is OMP's command line, JSON event stream and configuration,
which unit tests cannot check alone. A change to `commands.py`, `verdict.py`,
`evidence.py`, `runner.py`, the reviewer policy or the attempt loop must also:

- keep `tests/fixtures/oracle_reviewers/omp_multi_turn_review.jsonl` a real
  recording. When OMP output changes, re-record with `omp --print --mode json`
  on a review that writes text, calls `read` and `grep`, then answers; replace
  machine paths and check for credentials. Never hand-edit it to fit the code.
- be smoke-tested before merge by running `runner.review` on a file-list brief
  with the reviewer and model CI will select and the committed
  `.github/oracle-omp-config.yml` (written into a scratch `HOME` with
  `reviewer-home`). The reviewer must take at least two turns and return a
  verdict that passes the evidence check. After merge, dispatch a run on a
  soundness PR to confirm it in CI. Repeat both after any routing or
  bucket-floor change.
- record, beside the CI-pinned proof, a separately labelled local proof: local
  OMP build and version, the reviewer overlay, a plain read of a 100+ line file
  with no `details.summary` and no `elided; re-read needed ranges` footer, and a
  single-file `grep`. A local macOS OMP 18.12.6 run does not prove the pinned
  Linux build or the secret `OMP_AGENT_CONFIG`; only an observed CI run does.
- be self-reviewed across every module at that boundary and its tests, not a
  subset.

## Cut-over

Done: `.github/oracle-reviewers.yml` sets `mode: enforce`, and the checker
(`_project/scripts/oracle_review_check.py`), reported by the required
`oracle-gate` check, accepts only the oracle signal:

- a latest `oracle-verdict` commit status on the head from the `benchbox-oracle`
  App's Bot account, state `success`, posted after any retarget;
- no unresolved review thread from the App.

The verdict comes from the App's commit status, never a review body: the account
every agent uses can edit or delete reviews, while a status cannot be edited or
deleted and records its creator. Statuses from other accounts or contexts,
including legacy `oracle-review-shadow`, are ignored. A status belongs to a
commit, not a PR, so the gate binds it by run: each verdict run mints a run
uuid, writes it into the review marker and appends `[run <uuid>]` to the success
status description (within the 140-character limit). The gate passes on a
success status only when this PR has its own unedited oracle review of that head
whose marker carries the same uuid and whose header agrees with the status.
Unedited is verified through the review API; an unverifiable edit state never
authorizes. A status from another PR at the same head, or an earlier policy,
never authorizes: any success status without a run uuid, including evidence
predating the requirement, fails closed and waits for a fresh verdict run.
Merged PRs predate this and never re-run the gate. The oracle signal needs
`findings_delivery: review`, under which every run reaching a result, pending
included, posts a review then a status in the same state.

The `oracle-review` workflow is a notification, not a check: it triggers only on
`pull_request_review` submitted, edited and dismissed, holds no token
permissions, and runs one `review-event` job with a single no-op step (no
checkout, secrets or API calls), producing no check run named `oracle-review`.
`edited` keeps edits to oracle reviews visible in run history; the gate never
reads review bodies for its decision but rejects edited receipts when binding a
status.

`pull_request_review` runs the PR's own workflow file, so the PR controls the
notification and can delete or alter it. The verdict path does not depend on it:
after posting a head's status, comment and review, a final `review` step reads
the PR base through the API and, if `develop`, dispatches `oracle-gate.yml` from
`develop` with `pr` and the reviewed `head_sha`. This covers comment, dispatch
and scheduled verdict runs, whose completion carries `develop`'s head and cannot
be mapped to the PR by the gate's bridge. The step runs only with the App
configured and the head not skipped by the guard, so never before the post; it
uses the job's `actions: write` for this dispatch alone and posts no status; the
gate re-reads everything. Remaining trusted fallbacks: the `oracle-gate` sweep
(every push to `develop`, plus schedule) and the gate's own triggers
(`pull_request_target`, stand-in comments, dispatch).

GitHub dismisses only approvals and change requests, and the oracle posts
comment reviews, so `dismissed` covers only human reviews, which are not
`oracle-gate` inputs.

Resolving a thread has no Actions event, so resolving the oracle's last thread
starts nothing. `make pr-arm` and `make pr-arm-remote` dispatch `oracle-gate`
for the PR and head when its `oracle-gate` status is not `success`. For an
already-armed PR, run `gh workflow run oracle-gate.yml --ref develop -f
pr=<number>` or wait for the next sweep (next push to `develop`, or the
schedule). `make pr-rerun` re-runs failed CI jobs and does not refresh
`oracle-gate`.

A stand-in attestation must follow the oracle's latest decisive verdict
(`success` or `failure`) on the head, so overriding a failure is deliberate; a
later non-decisive review such as `pending` does not void it, and it never
overrides an open thread.

The merge digest applies the same window to merged history and files a
`soundness-review-gap` issue for any commit it cannot account for. An attester's
comment closes such an issue if it starts with `External review: codex`, `muse`
or `agy`, has a line beginning `Verified clean`, and lists no `**Critical**: `,
`**High**: `, `**Medium**: ` or `**Low**: ` finding. A later digest run
self-closes only an issue opened because a commit had no review signal, once it
finds one; issues opened for a reviewer thread, or several reasons, close only
on an attester's review.

`oracle-gate` runs `develop`'s gate and checker, both accepting only the oracle
signal. `tests/unit/scripts/oracle_reviewers/test_policy.py` fails if the live
policy leaves `mode: enforce` or delivery is not `review`.

## Required gate from trusted code

A check run from the PR's workflow file lets a PR editing `.github/workflows/`
change the check that judges it, and any PR sharing the head commit can set the
status it reads. So the required check is `oracle-gate`:
`_project/scripts/oracle_gate.py` imports the decision and helpers from
`_project/scripts/oracle_review_check.py` rather than reimplementing them, and
posts `oracle-gate` on the PR head with the oracle App token. The pass rule is
the checker's: only exempt paths changed, the App's latest review of the head
reports success with no unresolved oracle thread, or a valid stand-in covers the
exact head; otherwise failure. The script re-reads review state just before
posting, so a stale evaluation posts the fresh decision rather than overwriting
a newer one. It posts only for PRs based on `develop`, the one branch whose
ruleset requires the gate: the status is the commit's, so a same-head PR into
another branch, where the diff might be exempt-only, must not replace a
`develop` PR's failure with a success.

A same-repo PR could still add a workflow with `statuses: write` posting a
forged `oracle-gate` success to its own head, so the workflow alone does not
make the context unforgeable. The unit test fails on any step outside
`oracle-gate.yml` that could post the context, but a generated file or new
workflow could still land one. The ruleset closes this: the required
`oracle-gate` check is scoped to the `benchbox-oracle` App's integration ID
(5201041), so only that App's statuses satisfy it. Without that binding a forged
success would count and the gate property would not hold.

`.github/workflows/oracle-gate.yml` runs `develop` code only. Triggers:

- `pull_request_target` (opened, synchronize, reopened, ready_for_review,
  edited);
- created, edited and deleted issue comments whose body (or, for an edit,
  previous body) contains the stand-in marker anywhere, so leading whitespace,
  which the checker strips, still triggers; the checker decides on the exact
  text;
- completion of `oracle-verdict` and `oracle-review` (the bridge for review and
  thread events, which have no trusted trigger);
- `workflow_dispatch` with a `pr` input;
- a `schedule` sweep over open PRs targeting `develop`;
- every `push` to `develop`, so a merge re-evaluates open PRs without waiting
  for the schedule.

GitHub delays and drops scheduled runs, so pushes bound latency while `develop`
is active and the schedule covers quiet hours. Only the sweep job runs on
`push`; the gate and bridge jobs exclude it. Push sweeps share one concurrency
group, separate from every PR's, and queue rather than cancel, so a running
sweep finishes before the next starts.

A completion run evaluates nothing: its `bridge` job finds the open `develop` PR
whose head is the completed run's head commit (not the payload's first PR, which
can target another branch or be stacked on that head) and dispatches the gate
for it from `develop`. Gate and sweep jobs check out `develop`, never the PR,
sparsely (gate, predicate, reviewers policy), run the script with pinned
`pyyaml` as `oracle-verdict` does, and mint the App token as `oracle-verdict`
does, from the same `oracle` environment and secrets. If the reviewers policy
cannot load, the script errors rather than deciding with empty rules; the sweep
records every per-PR error and fails if any PR errored. Runs evaluating a PR
share one concurrency group keyed by its number, with cancel-in-progress, so a
newer run cancels an older one instead of racing.

When evaluation fails after the head SHA is known, whatever the cause (API,
network, malformed payload, policy), the script posts `failure` for that head
with a short error description and exits with the error code, so an earlier
`success` cannot outlive a gate that can no longer evaluate. This is
deliberately fail-closed: an API outage turns every touched PR to `failure`
until its next successful evaluation. The PR read is retried three times,
waiting 2 then 4 seconds. If it still fails, the script posts `failure` to the
optional `--head-sha` if given; otherwise it makes one GraphQL read of the PR's
`headRefOid` and posts there if it is a 40-character lowercase hex SHA. A live
REST read always wins over both. Sweep and bridge dispatches pass a head;
`pull_request_target` runs pass the event payload's head, validated by the
resolve step; `issue_comment` payloads carry none, so those runs rely on
GraphQL. The remaining gap: if REST and GraphQL both fail with no head supplied,
nothing posts and the prior status stands until the next sweep carries the head.
PRs not based on `develop` and `--no-post` runs never post, errors included. The
sweep lists open `develop` PRs with `gh api --paginate`, with a 45-minute
timeout.

`oracle-gate` became required on 2026-10-21, scoped to the `benchbox-oracle`
App's integration ID, alongside `oracle-review`, which stopped being required on
2026-10-24 after a side-by-side comparison
(`_project/decisions/oracle-review-check-retired-2026-10-24.md`). Its workflow
now only notifies; its completion after a review event is how `oracle-gate`
re-evaluates without a trusted trigger of its own.
