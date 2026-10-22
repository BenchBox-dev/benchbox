<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Soundness-PR drain digest

```{tags} contributor, operations, ci
```

## The signal, and what it is not

Soundness-path PRs (paths in `.github/soundness-paths.txt`) merge only after
the required `oracle-review` check passes, which needs a success review of the
current head from the `benchbox-oracle` App. CI cannot catch a change that
redefines the oracle it validates against, so such a PR can sit green but
unmergeable while it waits for that review. The oracle reviews each push on its own; to rerun it,
post a top-level `/oracle-review` comment or run
`gh workflow run oracle-verdict.yml --ref develop -f pr=<number>`.

When the oracle cannot review, or its verdict must be overruled, a
listed attester in `STANDIN_ATTESTERS` (`_project/scripts/oracle_review_check.py`)
can substitute an independent stand-in review. After that review of the
current head, the attester posts a PR comment whose whole text is the line
`Stand-in oracle review: APPROVE <full head SHA>`; the review itself goes in a
separate comment. The check accepts it only for
that exact head, only if posted after any retarget and after the oracle's
latest review of that head, and never while a review thread from the oracle
is unresolved. It is not compared with the push time, but a later oracle
review of the head supersedes it. An edited comment
does not count, and neither does one with any other text, because Markdown
around the line can hide or quote it.
Posting the stand-in comment does not rerun the check. After the Oracle or
stand-in review is complete for the current head, run
`make oracle-review-recover PR=<number>` if an unsuccessful `oracle-review`
run is blocking the PR. The target verifies each run belongs to the current
PR head and workflow, waits for each rerun serially, then closes and reopens
the PR to fire a fresh `pull_request` event. The `reopened` action reruns CI
and any other workflows subscribed to that action. A PR with auto-merge armed
is left open, because closing a PR turns auto-merge off; the target reports
that it skipped the close and reopen.

The `pull_request_review` submitted and dismissed triggers stay enabled so the
required Actions check `oracle-review` is reevaluated after the Oracle App
submits or dismisses a review. A stand-in uses the recovery command above.
Keeping this trigger means each submitted thread reply can enqueue an
`oracle-review` run; per-PR concurrency cancels superseded runs but still
consumes runner slots.

The develop ruleset requires the Actions check `oracle-review` without binding
it to a producer, and the separate `oracle-verdict` status cannot satisfy that
requirement. Do not remove the review trigger until a source-bound,
one-result-per-head replacement passes the early-failure / later-success
canary without a skipped passing check.

The attester account is also the one local automation uses, so the
attestation records who vouched for the review, not that a human read it.

What the gate does not do on its own is tell anyone a PR is *waiting*. Two
PRs (#1116, #1142) sat parked for days, accumulating merge conflicts,
before anyone noticed. **This digest is purely observational** — it adds a
daily "someone should look at this" signal on top of the unchanged gate. It
never merges, approves, or otherwise changes a PR's mergeability. The only
mutations it performs are:

1. adding/removing the `awaiting-owner` label to match the current queue, and
2. creating/updating a single pinned tracking issue with the digest text —
   and only when the queue is non-empty.

## How a PR qualifies for the queue

`_project/scripts/soundness_drain_report.py` lists every OPEN PR targeting
`develop` and includes a PR in the digest when **all** of the following
hold:

- **(a) required-lane green** — **every** required status context of the
  `develop-squash-only` ruleset has its latest check run on the PR's head
  SHA completed with `conclusion: success`. Today that is
  the five unit results defined in `.github/workflows/ci.yml` (`core`,
  `explorer`, `results-data`, `docs` and `tooling`; see
  [`repo-admin-settings.md`](repo-admin-settings.md)). Partial green — one
  context green while another is red or has never reported — is not green;
  a missing run is fail-closed, not an absent requirement.
- **(b) awaiting the owner** — auto-merge is currently OFF, **and** either
  the diff touches a soundness-critical path (reused via
  `any_soundness_path` imported from `soundness_paths.py` —
  never re-derived or edited), or the owner (`joeharris76`) is a requested
  reviewer.
- **(c) parked > 24h** — more than 24 hours of park time (see below).
  The gate deliberately does NOT use `updated_at`: the script's own label
  writes and ordinary human comments bump `updated_at`, so an idle-based
  gate would flap a genuinely parked PR out of the queue every time the
  signal fires.

Draft PRs are always excluded regardless of the above.

### Park time

Each qualifying PR also reports **park time**: hours since the PR became
ready-and-green, anchored on the `completed_at` of the **last** required
context to finish (falling back to `updated_at` if any required context
lacks that timestamp). The lane is only green once every context has
finished, so anchoring on a single unit alone would overstate park
time whenever another unit completes later — and trip the 24h gate
early. This is emitted per PR in both the text digest and `--json` output,
and is the intended input for park-time re-measurement work (the WS9
re-measure references this field rather than recomputing it).

## Running locally

The first command prints the human digest and is read-only. `--json` is machine-readable and read-only. `--apply` also
syncs the label and the pinned issue. `--self-test` is fixture-only and uses no network.

```bash
uv run -- python _project/scripts/soundness_drain_report.py
uv run -- python _project/scripts/soundness_drain_report.py --json
uv run -- python _project/scripts/soundness_drain_report.py --apply
uv run -- python _project/scripts/soundness_drain_report.py --self-test
```

Auth is a short token-source chain, never a long-lived PAT: `GITHUB_TOKEN`
or `GH_TOKEN` from the environment first; if neither is set and the `gh`
CLI is on `PATH`, its own token (`gh auth token`) is used. Without `--apply`
the script only reads — no labels or issues are touched.

## The `awaiting-owner` label

The label is fully owned by this script under `--apply`: it is added to
every currently-qualifying PR and removed from any evaluated PR that no
longer qualifies (check went red, auto-merge got re-enabled, idle dropped
back under 24h on a fresh push, etc.). Do not hand-manage it — the next
`--apply` run will reconcile it back to the computed set.

## The digest issue

No workflow runs the report on a schedule any more: the daily
`soundness-drain.yml` was retired with the six-unit CI; the `benchbox-oracle`
App now reviews soundness-path PRs. Run
`make soundness-drain-report` for the read-only view, or the script with
`--apply` when you want the label and issue updated. The digest is posted to a single pinned issue titled
**"Soundness-PR drain queue"** — found by exact title plus a body marker
(`<!-- soundness-drain-digest -->`, so a human issue reusing the title is
never adopted or clobbered) and updated in place (or created if it doesn't
exist yet), never as per-PR or per-event comments. When the queue drains,
an existing digest is patched to the empty state exactly once; after that
an empty queue produces no create, no update, no notification. This keeps
the signal to at most one digest per run, silent on a clean queue, and never
leaves a stale "parked" list showing after the queue empties.
