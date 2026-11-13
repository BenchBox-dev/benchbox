---
develop_sha: 1e8cc3dee48a4c345bb96df2e59f47cdbf6dde5f
---
# PR-Review Sweep — Template

A reusable checklist for "PR-review follow-ups" TODOs. Built from the
`codex-pr-review-followups-week-2026-05-01` execution (historical TODO id;
the routine is now the reviewer-agnostic `pr-review-followups`) and extended
with the two axes the original sweep template missed (captured in blind-spot
`_project/blind-spots/2026-05-01-130000-codex-followups-todo-misses-coverage-downgrades.md`).

The default `--author` filter targets the oracle reviewer App, `benchbox-review[bot]`, but the
routine accepts any reviewer login (other bots, human reviewers, etc.) via
`--author` or `DEFAULT_REVIEW_AUTHORS`. The local executor is currently the
codex CLI, isolated behind `--executor-*` flags.

## When to use this template

Run a sweep whenever a backlog of bot/agent inline review threads has
accumulated on merged PRs. Cadence is operator-driven (no fixed schedule);
pick a sweep window appropriate to the volume of merged PRs and the size of
the unactioned queue (`make pr-review-followups-list` previews it). Each
sweep produces:

1. A new tracker item created through the `todo-db` skill with id
   `pr-review-followups-<window-tag>` (the window tag identifies the selected
   range; legacy entries used `week-YYYY-MM-DD`)
2. A rescan audit at
   `_project/audits/pr-review-thread-rescan-<window-tag>.md` listing
   resolved-by-this-TODO, already-fixed-by-earlier-merges, and
   still-actionable threads
3. Links from relevant in-window hosted findings or local finding drafts to the
   tracker item

At the start, record `SWEEP_HEAD=$(git rev-parse origin/develop)` and the exact
merged-PR set returned for the window. Every axis and the rescan audit must use
that same immutable head and PR set. The frontmatter SHA above records the head
at which this template was last validated; do not copy it into a later audit.

For routine single-pass cleanup, prefer the automated Make routine before
creating another manual inventory:

```bash
# Preview the candidate queue.
make pr-review-followups-list PR_REVIEW_SINCE=YYYY-MM-DD PR_REVIEW_UNTIL=YYYY-MM-DD

# In a feature worktree, action each queued comment, reply with the
# BenchBox action marker, run preflight, and open the batched PR.
make pr-review-followups PR_REVIEW_SINCE=YYYY-MM-DD PR_REVIEW_UNTIL=YYYY-MM-DD
```

The routine uses the same judgment rules below: it verifies current behavior
before editing, treats stale-but-fixed threads as no-current-action, preserves
historical DONE verification commands as executable documentation, and skips
resolved threads during a normal action pass. Use
`PR_REVIEW_INCLUDE_RESOLVED=1` for the separate phantom-resolution audit.
Unresolved threads are skipped on future passes only after a durable reply
contains the `benchbox-pr-review-followup-actioned` marker.

The routine also checks top-level PR timeline comments for Codex code-review
usage-limit failures. Those comments are not inline findings, so there is no
code change to action. Instead, `make pr-review-followups-list` reports merged
PRs whose latest usage-limit failure has no later `@codex review` trigger or
Codex review result, and `make pr-review-followups` posts the fresh
`@codex review` trigger for each PR that does not already have one. PRs with
a later trigger but no later Codex review result remain visible as
`awaiting-review-result`; they are not considered actually reviewed until the
bot posts a later Codex review result. Set `PR_REVIEW_USAGE_LIMIT_RETRY=0`
only when deliberately auditing inline review threads without re-requesting
missed Codex reviews.

Coverage boundary: this sweep operates over merged PRs returned by
`gh pr list --state merged --base <PR_REVIEW_BASE>` inside the selected
`PR_REVIEW_SINCE` / `PR_REVIEW_UNTIL` window. It is not the policy source for
which PRs Codex reviews. Codex review triggering is controlled by the GitHub
integration: PRs opened for review, drafts marked ready, and explicit
`@codex review` comments request review. Draft-only PRs, PRs outside the
configured base/window, and quota failures can therefore be absent from the
inline finding queue until they are made ready or explicitly retried.

If the routine crashes mid-sweep (transient `gh api` failures and pre-commit
hook auto-fixes will normally retry; anything else exits with a non-zero
status), re-drive it on the same worktree with `PR_REVIEW_RESUME=1`:

```bash
make pr-review-followups PR_REVIEW_RESUME=1 PR_REVIEW_SINCE=YYYY-MM-DD
```

`--resume` (or its env-var form) implies `--allow-dirty` so the prior
per-comment commits on the branch are accepted, and parses
`git log origin/<base>..HEAD` for the per-comment commit subject to skip
comments whose fix already landed locally. The GitHub-marker check still
catches threads where both the commit and the reply already succeeded, so
`--resume` only needs to short-circuit the executor re-run for half-completed
work. Use it after any unplanned exit; do not use it on a fresh branch.

## Trust model — review the resulting PR before merge

The routine runs the executor (currently `codex exec --sandbox workspace-write
-c approval_policy=never`) against the contents of comments authored by the
configured reviewer set (default: `benchbox-review[bot]`). That gives
the executor unattended write access to the repo for the duration of the
sweep. The trust boundary is the **author filter** (`--author`). Anyone with
repo write can change that flag to action comments from any author. Treat the
routine accordingly:

- The executor sandbox (`workspace-write`) permits the executor to read the
  whole repo and write any file under the worktree. It does not get network
  access by default, but it can run any local command (including `git`,
  `uv`, `make`).
- `approval_policy=never` means the executor never prompts before running a
  local command — including ones that mutate state. The routine is
  intentionally unattended, so this is required.
- Per-comment commits land **before** the GitHub marker reply is posted, and
  each commit message includes the source PR# + comment id (see
  `commit_message_for_result`). This means a crash mid-sweep leaves a
  reviewable diff on the branch and no phantom-actioned thread on GitHub.
- Per-comment commit SHAs are temporary review evidence on the feature branch.
  BenchBox squash-merges the batch, so the durable rescan record must link the
  remediation PR and resulting `develop` squash commit/tree.
- The reviewer of the resulting PR should: (a) skim each per-comment commit
  to confirm the change matches the source comment's intent, (b) reject any
  commit that pulled in unrelated edits, (c) verify that a "fixed"
  disposition actually corresponds to a change on disk (a stale executor run
  could in principle fabricate an evidence block while leaving the tree
  unchanged — the per-commit diff is the ground truth).
- Prompt-injection content inside a reviewer's comment body is treated as
  input, not instruction; the prompt template
  (`_project/scripts/prompts/pr_review_followup.md`) tells the executor to
  verify against the current tree and to make the smallest coherent fix.
  Reviewers should still be alert to PRs that touch suspiciously broad
  surface area for a single source comment.

## Required scope axes

The frame "unresolved bot/agent review threads from PRs #N–#M" is good at
catching what the configured reviewers flagged-and-was-never-resolved, but
blind to several adjacent failure modes. **All five axes below are
mandatory** — skip any one of them only with an explicit `out-of-scope:`
line citing why.

### Axis 1 — Reviewer inline threads (the canonical scan)

```bash
# Discover merged PRs in the review window.
START_DATE=YYYY-MM-DD  # the Monday before the sweep window
END_DATE=YYYY-MM-DD    # the Sunday closing the window
SWEEP_HEAD=$(git rev-parse origin/develop)

gh pr list --repo joeharris76/BenchBox --state merged --base develop --limit 1000 \
  --json number,mergedAt,mergeCommit,title,url

# The binding uses paginated GraphQL reviewThreads and retains thread ids,
# resolution, outdated state, anchors, comments, authors, and replies.
make pr-review-followups-list \
  PR_REVIEW_SINCE="$START_DATE" PR_REVIEW_UNTIL="$END_DATE" \
  PR_REVIEW_USAGE_LIMIT_RETRY=0
```

Bucket each comment as **Fixed (link the commit)**, **Already fixed by
earlier merge (link that merge)**, or **Still actionable (becomes a w-unit
in the TODO)**.

### Axis 2 — In-window findings

Findings may have been filed during the same review window by local review
paths. PR-review threads will not surface them. The tracked
`_project/blind-spots/` corpus is frozen; current authority is the hosted
findings domain plus unsynced drafts under `~/.benchbox/finding-drafts/`.

Both reads go through the `todo-db` skill. They are MCP tools, not shell commands:

- Hosted findings: `finding_list`, then filter the returned `created_at`
  timestamps to the exact inclusive window. The tool has no date arguments.
- Zero-credential local drafts not yet landed in the tracker: the drafts under
  `~/.benchbox/finding-drafts/`. Land them with the floor CLI:

```bash
uv run --project _project/scripts --locked -- todo-db finding sync
```

For each in-window finding:

- Cross-link it from the TODO's `description:` or relevant `w-unit notes:`.
- If the finding maps to a w-unit (e.g. a test was loosened in a PR a
  reviewer also commented on), add an `anti_patterns:` entry connecting them so a
  future agent picking up the w-unit cannot land the fix without
  addressing the blind-spot.
- If the finding is out-of-window or unrelated, mention it explicitly with its
  finding id and reason.

The 2026-05-01 sweep added the `do not fix Home.tsx without restoring
strict cold-load row counts` rule to its `must_not_do` list precisely
because cross-linking the blind-spot caught the regression-coverage
downgrade the reviewer itself never flagged.

### Axis 3 — Tests weakened in the window

Reviewers flag individual lines, not coverage downgrades. A PR can land a fix
that simultaneously loosens an assertion that was guarding the same
failure mode. The bug class is "assertion-loosening alongside fix lands
without a guard"; the original scan template had no axis for it.

```bash
# Source: the same merge range as Axis 1. Match Axis 1's inclusive end-of-day
# bound — bare `--until=$END_DATE` resolves to midnight at the *start* of
# END_DATE, which excludes test changes merged later on the closing day
# (precisely the changes this axis exists to catch).
git log --since="$START_DATE 00:00:00 -0400" --until="$END_DATE 23:59:59 -0400" -p \
  --diff-filter=MDR --find-renames --pretty='format:%H %s' "$SWEEP_HEAD" \
  -- '*.spec.ts' '*.spec.tsx' '*test*.py' 'tests/' \
  | rg -B 5 -A 5 'toHaveCount\(.*\) ->|\.length\) ?=>|count.*>\s*0|expect\(.*\)\.toBeGreaterThan\(0\)|assert.*> 0'
```

Patterns to flag (non-exhaustive — extend as new patterns surface):

- Replacing `expect(X).toHaveCount(N)` / `assertEqual(len(...), N)` with
  `count > 0` / `length > 0` / `assertGreater(..., 0)`.
- Removing strict-equal mocks/fixtures and replacing them with
  fixture-DB polling (loses the byte-for-byte regression guarantee).
- Deleting a regression test entirely with no replacement (search for
  `removed test` / `unused test` / `flake` justification on the PR).
- Loosening `expect(..., {timeout: T1})` to `expect(..., {timeout: T2 > T1})`
  by an order of magnitude (often masks intermittent regressions).
- Injecting CSS or DOM state before a visual capture so the test repairs the
  production behavior it is meant to observe.
- Expanding an accepted exception whitelist to include a raw internal error
  instead of asserting a supported failure contract and valid output state.

For each match: if no reviewer flagged it, file a w-unit OR a blind-spot
finding pointing at the diff. Do not silently accept "the team
simplified" without confirming the regression mode the original test
guarded is still covered.

### Axis 4 — Reviewer threads that were "marked resolved" without a fix

A reviewer's resolved-state in GitHub is unreliable: a contributor can resolve
a thread by replying or by clicking the resolve button, even if the code
was never changed. A thread that GitHub reports as resolved but for which
no commit in the window matches the fix description is a candidate for a
w-unit. The 2026-05-01 sweep used a manual cross-check; the next sweep
should script it:

```bash
make pr-review-followups-list \
  PR_REVIEW_SINCE="$START_DATE" PR_REVIEW_UNTIL="$END_DATE" \
  PR_REVIEW_INCLUDE_RESOLVED=1 PR_REVIEW_USAGE_LIMIT_RETRY=0
```

For every resolved/current row, compare the thread body, path, anchor, replies,
merged PR diff, and later `develop` history. Resolution alone is not fixing
evidence; classify it as fixed, already fixed, deferred, or rejected.

### Axis 5 — DONE-item verification commands that drifted

If the review window includes commits that touched a path or tool an
older DONE-item used in its `verification:` block (e.g. a script that
got renamed or an API that changed shape), the verification command may
silently no-op or fail. The 2026-05-01 policy decision below is
inherited:

> Historical DONE-item verification commands are kept executable. If a
> reviewer finding identifies a portability or correctness defect in a
> DONE-item verification block, fix it in place. Rationale: those
> commands serve as runnable documentation of how the policy was
> confirmed, so anyone re-verifying the historical decision (forks,
> recreated rulesets, future audits) needs them to work.

### Axis 5b — Verification-command antipatterns (silent false-positives)

Some `verification:` commands *pass* while proving nothing — they exit 0
regardless of whether the behaviour they claim to check holds. These are
worse than a command that drifted and fails loudly (Axis 5): a green
false-positive lets a defective TODO land as "verified". A weekly sweep
flags these patterns in any TODO whose `verification.command` blocks were
added or touched in the window. The four below were caught retroactively
by Codex on already-merged TODOs (PRs #110, #117, #121, #122); the
generator bug they document is fixed, but the *class* recurs, so the sweep
checks for it going forward:

- **Wildcard `--queries`** — a `--queries '*'` / `--queries` glob (rather
  than explicit query names) can match zero queries and still exit 0, so an
  empty run reads as a pass. Require explicit query names in the command.
  (Embodied by `read-primitives-approximate-aggregate-queries.yaml`.)
- **`find -newer` with >2 positional args** — `find <dir> -newer A B` is an
  arity bug: `find` treats the extra path as another search root, not a
  second reference file, so the freshness comparison it appears to make is
  not the one that runs. (Embodied by
  `write-primitives-sketch-persistence-category.yaml`.)
- **DuckDB filename glob in a command string** — a `*.duckdb` (or similar)
  shell glob that expands to zero files, or to a stale file, lets the query
  run against the wrong (or no) database and still exit 0. Name the exact
  database path. (Embodied by
  `read-primitives-approximate-aggregates-dataframe-coverage.yaml`.)
- **`$(date)` / `$(date +...)` inside a verification command** — a date
  recomputed at run time is not the date the TODO was verified against;
  a command that embeds `$(date)` can pass on one side of midnight and
  fail on the other, so it verifies nothing stable. Pin the date literal.
  (Embodied by `results-explorer-uat-multi-scale-corpus-sweep.yaml`.)

The current tracker writer validates command structure at authoring time. The
sweep still owns the semantic checks above, including exit-status masking,
which a schema validator cannot judge.

Run the deterministic project lint over current hosted items through the `todo-db`
skill: `lint` for each in-window item, and `verify_list` for its verification
records. Both are MCP tools in the default profile.

Classify `lint` findings against the pinned window instead of claiming the
historical corpus is green.

The window-scoped verification lint this step used to run
(`_project/scripts/todo_verification_lint.py --since/--until`) was removed with
the shim and has no successor. Until one exists, check verification records for
the window by reading `verify_list` output per item, and treat the absence of a
window-wide gate as a known gap rather than a pass.

Then manually assess context-dependent exit masking such as `; echo $?`,
`|| true`, and pipelines ending in `head`, `tail`, or `wc`; their validity
depends on the expected result and cannot be decided from syntax alone.

## Tracker item shape

Create and update the item only through the `todo-db` skill. The
conventional fields for a sweep item are:

- `id: pr-review-followups-<window-tag>` (e.g. `pr-review-followups-2026-05-01-to-05-07`
  or `pr-review-followups-since-2026-05-01`; the window tag should be readable
  enough to identify the sweep range without opening the file)
- `title: "Address unresolved PR review follow-ups for <window>"`
- `worktree: main`
- `priority: High`
- `category: Testing and Quality Assurance`
- `description:` — sweep window (start/end or "since"), number of merged PRs
  scanned, number of reviewer comments found, the policy decision quoted from
  Axis 5, AND a paragraph noting which in-window blind-spots were folded in
  vs out-of-scope (Axis 2).
- `work:` — one w-unit per still-actionable thread; one
  `summary: "Re-run the PR-review thread scan and produce the rescan audit"`
  unit at the end whose `notes:` block lists the required audit sections.
- `anti_patterns:` — must include cross-links from folded-in findings so the
  rule travels with the work.
- `verification:` — must include checks for each w-unit AND a check that
  the rescan audit file exists with the three required sections.

## Rescan audit shape

`_project/audits/pr-review-thread-rescan-<window-tag>.md` is the per-sweep
public record (legacy entries used `codex-thread-rescan-week-YYYY-MM-DD.md`).
Required sections:

1. `## Resolved By w1-wN` — one row per fixed thread, link to the
   commit that fixed it.
2. `## Already-Fixed By Earlier Merges` — one row per stale-but-OK
   thread, link to the merge that already fixed it.
3. `## Still Actionable` — one row per thread that needs a w-unit
   (should be empty after the sweep TODO completes).

## Closing the loop

When the TODO completes:

- Triage the cross-linked findings through the `finding_triage` MCP tool with a
  one-line reason citing the tracker item id.
- Record the rescan audit path in the final work-unit evidence before running
  the floor CLI completion, which stays a human step:

  ```bash
  uv run --project _project/scripts --locked -- todo-db complete --pr <N> <item-id>
  ```
- If new patterns surfaced for Axis 3 (test-weakening) that this
  template did not list, add them here so the next sweep starts wider.

Run the authoritative final rescan before close-out:

```bash
make pr-review-followups-list \
  PR_REVIEW_SINCE="$START_DATE" PR_REVIEW_UNTIL="$END_DATE" \
  PR_REVIEW_FAIL_ON_PENDING=1 PR_REVIEW_USAGE_LIMIT_RETRY=0
```

`make pr-open` opens or reuses the remediation PR with auto-merge withheld.
After the final self-review and required checks, `make pr-ready` may arm squash
auto-merge unless the soundness-path guard requires maintainer review.
