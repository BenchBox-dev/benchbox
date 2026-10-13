# CI dev-loop follow-up handoff (2026-10-13)

For a new agent session. Self-contained. Read `AGENTS.md` first, then the
Todo skill's batch guide. This handoff is the authority for all remaining
CI/dev-loop follow-up work. It supersedes
`_project/handoffs/2026-10-09-ci-devloop-batch-handoff.md` (revisions 1–6,
landed via #2929) for everything not yet done, and folds in and supersedes
the uncommitted `2026-10-11-ci-devloop-followups-batch-handoff.md` draft:
do not act on that draft; its two items are re-homed in Phase 9 below, and
the staged file should be dropped from its worktree so two handoffs never
compete. Settled design decisions live in the original handoff's section 7
and stay binding; they are referenced, not repeated, except where new
evidence changes them. Where this file and the tracker disagree, the tracker
plus the item acceptance text governs; this file's order and boundaries
still bind.

## 1. Objective and item set

Finish the 78 tracker items below in serial mode (Todo skill `batch`
action, not prepared/feature delivery). Code ships as ordinary pull requests
to `develop`. Some work is dispatches or GitHub administration with no PR.

Done means: every non-waiting item is `done` (or `dropped` with a recorded
reason), each with the evidence its acceptance names; every gated item in
section 5 is `waiting` with the exact resume condition in the ledger and,
where supported, a `not_before` hold. One session cannot finish everything:
canary 05/06 need first-run + 14 days, oracle first-line cleanup and the
merge-ceremony review are held to 2026-10-23, and dlv2-84 needs +30 days.
The terminal state of one session is a checkpoint: all else done, gated
items parked with holds. Report it as a checkpoint, not completion.

| Phase | Items (state on 2026-10-13: 10 done, 3 active, 60 open, 5 blocked) |
|---|---|
| 1 CI signal | `oracle-verdict-workflow-not-soundness-path-20261009` (done), `ci-docker-hub-unauthenticated-pulls-20261009` (active, other worker), `public-site-visual-baseline-missing-for-base-20261009` (done), `nightly-windows-pr-visual-approval-20261009` (done), `t3-drift-corpus-mismatch-20261009` (open), `t3-drift-dispatch-verify-20261008` (blocked), `t3-perf-aggregate-gate-tolerance-decision-20261009` (open), `t3-durations-refresh-failure-20261009` (done), `flaky-bigquery-credential-defaults-confirm-prompt` (blocked), `heavy-tier-merge-group-dead-code-20261009` (done) |
| 2 Windows | `nightly-v2-liveness-port-20261008` (done), `nightly-windows-verification-run-20261009` (open), `nightly-windows-unquarantine-20261008` (done), `nightly-windows-utf8-reads-20261008` (open), `agent-write-preflight-quoted-interpreter-20261009` (open), `windows-session-lock-holder-readable-20261009` (open), `windows-iceberg-vacuum-local-paths-20261009` (open), `windows-iceberg-file-uri-locations-20261009` (open), `windows-pytest-xdist-teardown-hang-20261009` (open), `nightly-v2-windows-unit-leg-20261009` (blocked) |
| 3 Nightly T3 | `ruleset-drift-check-home-20261009` (open), `nightly-v2-throughput-uat-port-20261009` (open), `cross-surface-mutation-sensitivity-not-enforced-20261009` (open), `polars-2-compat-post-merge-coverage-20261009` (open), `nightly-v2-unported-jobs-decision-20261009` (open), `dlv2-22-nightly-t3-shadow` (open), `monitor-nightly-cedardb-throughput-gate-and-floor` (open), `dlv2-70-retire-scheduled-workflows` (open) |
| 4 Oracle | `oracle-review-stale-check-runs-block-merge-20261008` (done), `oracle-muse-invalid-verdict-output-20261009` (open), `oracle-self-attested-file-list-reviewers-20261008` (open), `oracle-author-family-labels-never-applied-20261009` (open), `oracle-tier-reason-lists-every-file-20261009` (open), `oracle-strike-count-distinct-patches-20261008` (open), `oracle-reviewer-checkout-has-no-base-commit-20261009` (open), `oracle-read-evidence-tightening-20261008` (open), `oracle-absence-classification-lows-20261008` (active, PR #2911), `oracle-patch-hash-hunk-positions-20261008` (open), `oracle-runner-integrity-lows-20261008` (open), `pr-landing-soundness-refusal-unreachable-20261009` (open), `unattributed-update-branch-commits-block-merge-20261008` (open), `actions-runner-queue-oracle-latency-20261009` (open), `pr-2761-hollow-success-post-merge-review-20261008` (open), `oracle-checker-drop-legacy-first-line-20261023` (open), `review-soundness-merge-ceremony` (open) |
| 5 Comment policy | `comment-policy-strict-scope-mismatch` (active, PR #2915, other worker), `comment-policy-digest-and-base-exception-catch22-20261008` (open), `comment-policy-pinned-notice-drift-guard` (done), `comment-policy-hook-saturates-shared-host` (open), `comment-scanner-stdin-piped-interpreter-adapter` (open), `comment-scanner-mdx-jsx-support` (open), `comment-scanner-unresolved-runner-commands` (done), `policy-remove-fossil-fixture-digests` (open, new in this handoff) |
| 6 Canary | `canary-impact-03-replay-and-go-decision` (open), `canary-impact-04-shadow-job` (open), `canary-impact-05-shadow-watch` (open), `canary-impact-06-required-gate` (open) |
| 7 Release and cleanup | `v-tag-ruleset-allows-tag-moves-20261009` (open), `dlv2-51-release-yml-v2` (open), `repo-admin-settings-actions-pr-permission-doc-20261009` (open), `sync-workflow-stale-branch-cleanup` (open), `dlv2-80a-github-cleanup-queue-era` (open), `dlv2-44-precommit-trim` (open), `dlv2-defer-tpch-digest-darwin-arm64` (open), `dlv2-52-release-rehearsal-and-first-release` (open), `dlv2-53-retire-legacy-release-path` (open), `dlv2-42-retire-dev-loop-scripts-tests` (open) |
| 8 External-gated | `dlv2-62-publication-cutover-and-retirement` (open), `dlv2-80-github-cleanup` (open), `dlv2-81-config-cleanup` (open), `dlv2-82-makefile-rewrite` (open), `dlv2-83-docs-cleanup` (open), `dlv2-84-final-audit-and-metrics` (open), `site-split-52-ruleset-landing-context` (open), `tuning-liveness-e2e-gates-20261001` (open), `exasol-docker-ci-smoke` (blocked) |
| 9 Logged follow-ups | `comment-policy-shell-payload-coverage-20261011` (open), `native-task-model-tier-selection-20261011` (blocked) |

`policy-remove-fossil-fixture-digests` (open, ready) is new in this handoff:
`quality/comment-policy.json` carries digest-pinned fixture entries whose
digests match no current file state; acceptance is their removal with the
gate still green. It rides the catch-22 fixture-regeneration window
(section 4, step 2) so the registry is rewritten once, not twice.

## 2. Tracker preflight and ledgers

- Call the todo-db MCP `get_instructions` before any tracker write. State
  remote `https://github.com/BenchBox-dev/BenchBox`, state branch
  `todo-state`.
- `list_items(ready_only=true)` shows claimability only; it never overrides
  section 4 order, item acceptance, or section 6 boundaries. Do not `take`
  an item out of section 4 order because it is ready. Do not `finish` an
  item whose acceptance needs samples, runs, or approvals the tracker does
  not encode (CedarDB floor, canary windows, release approvals); park it
  `waiting` with a `not_before` hold instead.
- One live claim per worker: `take` one item, `finish` with the `take`
  generation before taking the next. One PR may cover several items: take
  the first, implement all on one branch, and after merge take and finish
  each in turn with the merge evidence. Merged-but-complete items need
  `take` plus merge-SHA and acceptance evidence, then `finish` — no new
  code. A merged-but-partial PR (section 3, #2908) never finishes its item.
- `update_item` replaces whole fields; append context by passing existing
  text plus a new paragraph. On `E_CONFLICT`, re-read and re-apply.
- Ledgers (outside every Git repository, never staged or copied into one):
  `/Users/joe/Developer/.todo-batch/BenchBox-ci-devloop-20261009.txt`
  (original 75) and
  `/Users/joe/Developer/.todo-batch/BenchBox-ci-devloop-followups-20261011.txt`
  (follow-ups). Refresh envelopes from live tracker state before any claim
  or closeout.

## 3. Live snapshot and PR evidence (2026-10-13, develop `77048bfb9`)

Counts: **10 done, 3 active, 60 open (39 ready, 21 not ready), 5 blocked**
of 78. The comment-policy required gate is green on develop. Do not take
over live claims; before touching any PR or worktree below, check the
tracker claim and the PR's last update — other sessions are active
(tuning, site-split, explorer, comment-scanner).

| PR | State | Relevance and next action |
|---|---|---|
| #2915 `fix/comment-policy-strict-scope` at `6e1e4342e2` | Open; `oracle-review` failing, muse verdict SHIP WITH FIXES (5 defects). Other worker's live claim. | Fix the oracle defects in scope, land, then `finish` strict-scope with the merge SHA. Unblocks catch-22. |
| #2911 `fix/oracle-absence-classification` at `3d432d3c61` | Open; `oracle-review` failing, verdict pending (reviewers erroring). | Land, then `finish` with merge SHA plus the item's post-merge evidence. |
| #2908 `fix/bigquery-credential-confirm-flake` → merge `8be916ea8` (2026-10-12) | Merged partial: title and tree show only xdist-trace instrumentation, not the root-cause fix. Its item stays blocked. | No take-finish. Next action is evidence-driven: wait for a failing trace from instrumented CI runs, prove the cause, then fix and run the 20-run series. |
| #2929 handoff status | Merged (`bc6dc7d22`). | Predecessor checkpoint; no action. |
| #2928 `fix/comment-policy-fixture-mirror-test` at `9f08e5ce29` | Open (other session); `core`, `medium-test (shard 0)`, `oracle-review` failing. Pre-registers a fixture digest. | Coordinate only. Catch-22/fossil work must rebase past it or agree merge order; never take it over. |

Muse invalid-output evidence: 1 of 2 required invalid answers captured
locally with provenance at `~/.todo-batch/muse-invalid-20261012/`; the
reliable path to the second is the diagnostic CI sidecar in the original
ledger. Attempt artifacts expire **2026-10-16** — treat section 4 step 5
as urgent.

## 4. Order and runbook

Phase order from the original handoff's section 4 still binds, including
its batching rules (phase 4/5 may interleave with 2/3 while those wait on
CI; one combined `nightly-v2` dispatch with `run_drift=true` serves the
Docker, drift-corpus, and perf-floor items together). Work phases in order;
inside a phase follow the listed order unless an item is waiting. The
immediate sequence for a new session:

1. Land #2915 (other worker's claim; assist only if asked), `finish`
   `comment-policy-strict-scope-mismatch` with the merge SHA.
2. One PR window: `comment-policy-digest-and-base-exception-catch22-20261008`
   (regenerates the digest fixtures it invalidates in the same PR) plus
   `policy-remove-fossil-fixture-digests` (removes dead digest entries in
   the same registry rewrite). Then `finish` both. Coordinate with #2928
   merge order first. Never rebase a gated PR onto exception-dependent
   heads to get around the gate.
3. `comment-policy-shell-payload-coverage-20261011`: first verify whether
   the ten coverage errors still reproduce on current develop (post-#2908,
   gate green); if gone, `take` and `finish` with that evidence. Its model-tier
   need constrains delegation only (section 8): implement directly if the
   selector is still unproven. Then `comment-policy-hook-saturates-shared-host`
   (baseline first), then the stdin and MDX scanner items.
4. Land #2911, then the oracle batches in original §4.4 order with the
   post-merge evidence rule: no `finish` on merge alone — each item names
   its post-merge check (run id, head, verdict) and needs it on the item
   first. Muse evidence capture before 2026-10-16 comes before the parser
   change it informs.
5. `flaky-bigquery-credential-defaults-confirm-prompt`: monitor only. No
   new PR until an instrumented run yields a failing trace that proves the
   cause; then fix plus the 20-run series per the item.
6. Phase-1 verification: one combined `nightly-v2` dispatch with
   `run_drift=true` after the Docker-pull and perf-floor changes merge;
   `take`/`finish` the Docker, drift-corpus, and perf items from its
   evidence. `t3-drift-dispatch-verify-20261008` unblocks on the corpus item.
7. Phase 2: one `nightly.yml` dispatch from `develop` for
   `nightly-windows-verification-run-20261009`; while it runs, prep the
   UTF-8/preflight/lock-holder PR in a fresh worktree (one PR), then the
   vacuum PR (own PR, deletes files), the file-URI PR (own PR), the xdist
   investigation (faulthandler first), and `nightly-v2-windows-unit-leg`
   last (needs items 4–7 plus three no-stall Windows runs).
8. Phase 3: ruleset-drift home (lands before dlv2-53 and dlv2-70),
   throughput/mutation/polars/unported-jobs per original D-T1–D-T6, then
   dlv2-22 (two green dispatches, non-cloud domains; `t3:cloud` stays off),
   then dlv2-70. CedarDB floor item: count scheduled same-runner-class
   samples in the floor log; park below five.
9. Phase 6 canary serially: 03 replay + go decision, then 04 shadow, then
   05 (+14 days from 04's first live run), then 06. Promotion rule D-K2
   still binds (zero misses, ≥20 runs, cost within limits; else extend
   once, restart once, then drop with evidence).
10. Phase 7: dlv2-51 (`release-v2.yml`, publish-time tag check under
    environment protection, TestPyPI end-to-end; missing trusted publisher
    is a stop), then rehearsal `0.5.0rc1` on TestPyPI after dlv2-22, then
    52 (waits on owner `pypi` approval) → 53 → 42. Cleanup items
    (v-tag ruleset, repo-admin doc, sync cleanup, 80a, precommit trim,
    darwin-arm64 digest) in any order once unblocked.
11. Phase 8 waits untouched (section 5).

Rebase every worktree onto the latest `origin/develop` before any push.
While a PR waits on CI or merge, do claim-free prep for the next step;
when everything waits on CI, checkpoint the ledger and end the invocation.
Check CI on a schedule, never in a tight loop. One dispatch per
verification need.

## 5. Resume conditions (gates that park items)

| Item | Waits for | Resume when |
|---|---|---|
| `canary-impact-05-shadow-watch`, `-06-required-gate` | 14 days of shadow runs after 04's first live run | first-run date + 14 days (`not_before` on 05 when 04 records the date) |
| `monitor-nightly-cedardb-throughput-gate-and-floor` | ≥5 same-runner-class scheduled samples | floor log shows 5 of 5; until then unclaimed, count on the item |
| `oracle-checker-drop-legacy-first-line-20261023`, `review-soundness-merge-ceremony` | `not_before` 2026-10-23 | that date |
| `dlv2-52-release-rehearsal-and-first-release` (final release) | owner approval of the `pypi` deployment | owner approves; then verify per the item |
| `dlv2-53`, `dlv2-42`, `dlv2-70` | chains above | their `needs` done |
| `dlv2-62`, `dlv2-80`–`-84`, `site-split-52` | site-split programme (-50, -51, -53, -54, -99) | those done; never work that programme in this batch |
| `tuning-liveness-e2e-gates-20261001` | tuning programme needs | both needs done; no partial status matrix |
| `exasol-docker-ci-smoke` | owner priority for Exasol | stays `blocked` until given |
| `native-task-model-tier-selection-20261011` | native runtime owner / upstream selector | stays `blocked`; section 8 governs |
| `dlv2-84` 30-day metrics | 30 days after last cleanup merges | follow-up `not_before` |

## 6. Rules and boundaries

- Worktrees only: `make worktree-create BRANCH=<prefix>/<slug>
  WORKTREE_PATH=../BenchBox.wt-<slug>` (`fix/`, `feat/`, `chore/`,
  `docs/`), `cd` there, `make agent-write-preflight`. Never write in the
  primary clone. Remove each worktree after its PR merges. The old
  `docs/ci-devloop-followups-20261011` worktree owns the superseded draft;
  leave it alone except to drop the draft file.
- Git identity Joe Harris <joeharris76@gmail.com> (check before the first
  commit). No attribution lines in commits, PR text, or comments. Stage
  explicit paths; never `git add -A`. `git mv` for renames.
- Python through `uv run -- ...`. Tests: `uv run -- pytest <paths>` (never
  `-o addopts=""`; it breaks `session_home()` tests).
- COMMENT-POLICY-001: no explanatory comments or docstrings in code, tests,
  or workflow `run:` blocks; run `make comment-policy-check`.
- Durable text (commits, PR titles/bodies, docs, issue comments) is for a
  reader who never saw the session: no phase/work-item labels, plan or
  handoff references, or run-local state. Give the durable reason.
- Per PR: `code` review self-review with Critical/Required fixed; `make
  pr-preflight` once; `make pr-open`. Never weaken `oracle-review`, its
  stand-in rule, the soundness path list, or the comment-policy/tooling
  gates. Never use GitHub's "Update branch"; rebase and push
  `--force-with-lease`.
- Never approve a `pypi` deployment, never run live cloud tests
  (`BENCHBOX_T3_CLOUD_ENABLED` stays unset), never substitute an external
  agent for native dispatch, never run `agy --dangerously-skip-permissions`.
- Do not work items outside section 1, and do not take over PRs or claims
  owned by other sessions except through the dead-session takeover rule
  (name the holder, prove it dead).
- **Stop and report** (ledger path + exact next action): denied permission;
  owner-only action (trusted publisher, secret, paid plan); oracle
  Critical/High unfixable in scope; anything weakening a required check;
  outside failing check blocking merge after one retry; destructive admin
  whose before-state differs from the item.

## 7. Verification gates

- Per item: every acceptance criterion met with the evidence the item names
  (run ids, PR numbers, SHAs, sample counts), recorded in context before
  `finish`. An open or green PR is not proof of merge.
- Per PR: review action clean, `pr-preflight` once, required checks green,
  oracle success review on soundness PRs, merged; phase-4 items add their
  post-merge evidence before `finish`.
- Tests for touched areas, e.g.
  `uv run -- pytest tests/unit/scripts/oracle_reviewers tests/unit/scripts/test_oracle_review_check.py tests/unit/workflows`,
  `uv run -- pytest tests/unit/scripts/test_comment_policy.py`,
  `make comment-policy-check`.
- Nightly T3: dispatched `nightly-v2` run with every non-cloud domain
  green, recorded on dlv2-22 and the drift/perf items.
- Final report (Todo batch format): one row per item with tracker state,
  PR and state, head, verification, blocker, next action, plus ledger
  paths. Every `waiting` item lists its section-5 resume condition.

## 8. Open decisions (not settled; do not invent owner answers)

- **Model-tier delegation.** The native dispatch interface exposes roles
  but no proven Sol/Luna selector, and the user forbids external agents.
  Do not claim tier-specific dispatch without a native run proving it.
  This constrains *delegation*, not authorship: High-complexity items
  (shell-payload coverage) may be implemented directly by the session.
  Keep `native-task-model-tier-selection-20261011` blocked until a native
  run proves explicit selection or the runtime owner accepts an upstream
  issue; a role mapping is not proof.
- **Fossil digests vs #2928.** If #2928 merges first, rebase the
  catch-22/fossil window onto it and re-verify the registry; if it stays
  red, land catch-22/fossil first and let #2928 rebase. Either way one
  registry rewrite, coordinated — never a competing second rewrite.
- **Exasol priority, `pypi` approval, site-split/tuning programmes.**
  Unchanged from the original handoff: out of this batch until the named
  owner or programme unblocks them.
- Everything else disputed goes to the owner with evidence and a
  recommendation; default to the safer parked state (`waiting`/`blocked`
  with a resume condition) over a weakened gate.

## 9. Pointers

- Original batch handoff (settled decisions §7, failure modes §6):
  `_project/handoffs/2026-10-09-ci-devloop-batch-handoff.md` (merged).
  Superseded draft (ignore; drop the file):
  `2026-10-11-ci-devloop-followups-batch-handoff.md` (staged only, in
  `../BenchBox.wt-ci-devloop-followups-20261011`).
- Operations: `docs/operations/oracle-review-v2.md`,
  `docs/operations/soundness-drain.md`, `docs/operations/nightly-t3.md`,
  `docs/operations/release-artifacts.md`,
  `docs/internal/public-site-visual-baseline.md`.
- Decisions: `_project/decisions/oracle-review-protocol-2026-10-08.md`,
  `_project/decisions/merge-queue-retirement-2026-10-03.md`,
  `docs/development/adr/adr-dev-loop-v2.md` (D4, D5, D7).
