# PR triage runbook

Durable judgment rules for triaging open PRs against `develop` — the parts
of PR triage that are stable enough to write down once, rather than
rediscovered per-session. These are rules, not a walkthrough: they assume
familiarity with the dev-loop (`AGENTS.md`) and the auto-merge machinery
(`scripts/pr_arm.py`, `_project/scripts/soundness_paths.py`).

## Always confirm against the current head SHA

Webhook payloads and CI event data can reference a SHA that is no longer
the PR's head — a later push superseded it before (or while) the event was
being processed. Before acting on any webhook-delivered check-run or
comment payload, re-fetch the PR and confirm its head SHA still matches
what the event described. Acting on a stale SHA risks approving, merging,
or "fixing" a state the PR has already moved past.

## Platform outage vs. a real failure

A whole-run failure where every job dies at the `setup-python` or
`setup-uv` step, with GitHub's "Unicorn" HTML error page appearing in the
job logs instead of normal output, is a GitHub Actions infrastructure
outage — not a defect in the PR. Wait and re-run once the outage clears;
do not start debugging the PR's own code for a failure signature like this.

## Cross-branch comparison uses a worktree, never `checkout -- .`

To compare the current branch against another ref (develop, a sibling
branch, a tag), use `git worktree add <path> <ref>` and diff/read from
there. Never run `git checkout <ref> -- .` against the current working
tree to "borrow" another ref's files — that mutates the current branch's
tracked files in place, can stage unrelated changes, and can silently
discard uncommitted work with no undo path as clean as a worktree's.

## TODO-tracker modify/delete merge conflicts

When a merge conflict is a modify/delete on a TODO-tracker record (one
side archived or completed the item; the other side still has edits
against the live copy), check which side already archived/completed the
item and keep that side's resolution. Do not resurrect an archived item by
re-adding the modified-but-stale copy — that reintroduces work the other
side had already closed out.

## `mergeable_state` semantics

- `dirty` — a real merge conflict against the base branch; the PR needs a
  rebase/merge before anything else is worth checking.
- `blocked` — a required check or gate hasn't passed (or hasn't run) yet.
  Not a conflict; look at the check runs, not the tree.
- `unknown` — GitHub hasn't finished computing the field yet. Refetch
  after a short wait; never conclude anything (mergeable or not) from
  `unknown` itself.

## The two standing sweeps

Two automated, read-only sweeps cover complementary parts of the "PR is
green but stuck" problem; neither one enables auto-merge, merges, or edits
a PR's mergeability — both only alert.

- **Soundness-drain daily digest**
  (`_project/scripts/soundness_drain_report.py`, scheduled via a daily
  workflow) — for PRs that touch a soundness-critical path (or have the
  owner as a requested reviewer): flags ones parked more than 24h since
  their required lane went green, so they don't sit forgotten accumulating
  conflicts while waiting on the review that `oracle-gate` requires.
- **Green-unmerged nightly sweep**
  (`_project/scripts/green_unmerged_sweep.py`,
  `.github/workflows/nightly.yml`) — alerts only on **true stranding**:
  non-draft, non-soundness, non-hold-label PRs whose required lane is green,
  auto-merge is still off more than 2 hours after the head commit, **and**
  the PR timeline shows prior arm intent that was lost. "Required lane
  green" means **every** `develop-squash-only` required context is
  latest-success — the documented required contexts are
  `core`, `explorer`, `results-data`, `docs`, `landing`, and `tooling` — so a
  PR that is green on one and red or silent on another is not green and is not
  alerted.
  `--apply` only
  upserts the digest issue; it **never** enables auto-merge and must not
  re-arm a hold it did not set.

  After the auto-merge hold, **an intentional non-armed green PR is normal**,
  not a stuck state, and the sweep **excludes** it. Auto-merge is **not**
  armed by `opened`, `reopened`, or `synchronize` (a re-push will not flip it
  on). The intentional arm paths are:

  - `make pr-arm` on a finished branch (arms auto-merge on the exact local head
    after a live check of the PR)
  - `make pr-open READY=1` to open or reuse the PR and arm it in one step
  - `make pr-ready PR=<n> HEAD=<sha>`, which arms through `make pr-arm`; with
    `EVIDENCE=<path>` or `BATCH=1` it runs the evidence transaction instead,
    and the evidence file declares `delivery_mode` (`batch` evidence must
    include the complete batch binding)

  No workflow arms auto-merge: a draft→ready arm point never fired once and
  was deleted (see
  `_project/decisions/auto-merge-policy-consolidation-2026-08-06.md`, D2).
  The Makefile paths refuse while the PR carries the `no-auto-merge` label or
  is a draft. Nothing disarms a PR that is already armed when the label is
  added later: to hold an armed PR, disable auto-merge
  (`gh pr merge <n> --disable-auto`) or withdraw it with
  `make pr-landing-withdraw PR=<n> HEAD=<sha>`, then add the label. The
  evidence transaction also refuses for soundness paths.
  Soundness-path PRs require `oracle-gate` on the current head and resolved
  threads. After any push or refresh, rerun CI and wait for the oracle's
  result on that head (or the stand-in approval) before arming.
  Withdraw readiness before editing an armed PR.

  `oracle-gate` re-evaluates on its own when the oracle posts a verdict, when
  a stand-in comment lands, and on an hourly sweep. Resolving a review thread
  starts no run, so after resolving oracle threads, re-evaluate the PR from
  `develop`:

  ```bash
  gh workflow run oracle-gate.yml --repo BenchBox-dev/BenchBox --ref develop -f pr=<pr>
  ```

  The workflow reads the PR's current head itself, so this also works for a
  fork PR. Confirm the `oracle-gate` status on the still-current head before
  assessing readiness. A passing `oracle-gate` does not arm the PR.

  **Classifier (arm intent):** a PR is stranded only when auto-merge is off
  *and* the issue/PR timeline includes at least one of
  `ready_for_review`, `auto_squash_enabled` / `auto_merge_enabled`, or
  `auto_merge_disabled` (the last implies a prior enable that was dropped).
  Never-armed intentional holds never emit those events. Missing timeline
  data fail-closes to "no arm intent" (prefer a missed strand over
  false-positiveing holds). Durable holds (draft or `no-auto-merge`) are
  also excluded even if the timeline still shows older arm events.
  `--apply` only upserts the digest issue from the stranded set; it never
  enables auto-merge and therefore cannot re-arm holds.

  Do **not** remediate a green-unmerged alert by re-pushing "to re-trigger
  synchronize." That path no longer enables auto-merge. When the branch is
  final, arm via `make pr-arm` (or `READY=1`); if work continues, leave
  auto-merge off (or apply a durable hold — see below).

  A workflow conclusion is also not proof of the PR's `auto_merge` field
  either way — the sweep re-reads the PR field (and the timeline).

## Durable auto-merge holds

`gh pr merge --disable-auto` alone is **not** a durable product signal: a later
intentional arm path could still enable auto-merge. The arm paths and the
sweep honour these durable holds (and nothing re-arms on push or nightly
`--apply`):

| Hold | Who honours it | Effect |
| --- | --- | --- |
| **Draft** | `make pr-arm` / `pr-ready` (refuse to arm), green-unmerged sweep | Not ready; no arm, not stranded |
| **Label `no-auto-merge`** | Required `hold` check (`pr-hold.yml`), `make pr-arm-auto-merge` / `pr-arm` / `pr-ready` (refuse to arm), green-unmerged sweep | Intentional hold; blocks arming and merging, even through a raw `gh pr merge`; not stranded |
| **Never armed** | green-unmerged sweep (timeline arm-intent classifier) | Green + auto-merge OFF with no prior arm events is normal after the post-#1592 policy; not stranded |

Use draft while the branch is incomplete. Use `no-auto-merge` when the PR
should stay non-draft (CI/review as ready) but must not auto-merge — for
example waiting on another PR, or after an explicit disable that must survive
a later re-arm attempt. Remove the label before arming with `make pr-ready`
(which refuses to arm while the label is present, and fails closed when the
label list cannot be read). The label is checked only when arming. It does
not disarm a PR that is already armed: disable auto-merge
(`gh pr merge <n> --disable-auto`) or run
`make pr-landing-withdraw PR=<n> HEAD=<sha>` first, then add the label.

Both sweeps upsert a single marker-tagged tracking issue while their
respective queue is non-empty, and patch it to the empty state exactly
once when it drains — never a per-run flood of new issues.

The green-unmerged sweep finds its issue by exact title and the
`<!-- green-unmerged-sweep -->` body marker, without labels. Search requests
oldest-created matches first, so a newer duplicate does not replace the original.
Unavailable, malformed, incomplete, or empty search results fall back to a
label-free, oldest-first scan with no page cap. Only a valid exhausted scan
permits creation; malformed pages (including a missing/404 response) and scan
errors abort before any issue write. Closed matches are reopened on digest upsert.
The general-purpose API pagination cap is unchanged.

Run `uv run -- python _project/scripts/green_unmerged_sweep.py --repo BenchBox-dev/benchbox`
without `--apply` for a read-only process smoke. Stderr records `search` or
`oldest_first_scan` and the existing issue number (or that creation would be
needed); neither this mode nor `--json` writes issues. `--apply` records the same
lookup mechanism before updating the digest.

## Red develop tip

`.github/workflows/trunk.yml` tests `develop` on pushes (fast lane,
four-shard medium tier, correctness gate and required-local-cases), and builds
the verified release distribution artifact. Pending runs are retained with
`concurrency.queue: max`, so results can lag during merge bursts; a missing
push event still needs separate recovery. A red develop tip is a trunk
failure that the PR's own checks did not catch. Revert first: run
`make trunk-revert PR=<n>` for the culprit and fix it afterwards. While trunk has
been red for more than 30 minutes, `make pr-open` refuses any PR that is not a
revert, because every PR opened on a red tip inherits the breakage. The
green-unmerged sweep does not report it.
See [Merge and trunk governance](merge-queue-governance.md).
