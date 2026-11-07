# Auto-merge policy: adversarial evaluation and consolidation

> Partly superseded on 2026-10-03 by [merge-queue-retirement-2026-10-03](merge-queue-retirement-2026-10-03.md): D5's soundness-path revocation and D7's advisory-only review policy are replaced by required `oracle-review`; the PR-body attestation is gone. Explicit arming and durable holds remain.

**Date**: 2026-08-06
**Status**: historical evaluation; D1–D4 and D6 were later implemented.
D5 and D7 remain recommendations pending explicit authorization. At the time
of this evaluation, no mechanism had yet been modified.
**Related**: PRs #1567, #1592, #1622, #1623, #1624 (policy lineage);
\#1568/#1569 (live arm-on-open failure); #1503/#1521/#1531 (stranded review
fixes); #1512 (anonymization auto-merge escape); #1543 (red-develop revert);
UAT batch #1616–#1631. Evidence window: 150 merged PRs (#1459–#1630,
2026-08-02 → 2026-08-06).

## Executive summary

The objective optimized here is **merged, correct work per unit of human and
agent attention**. Against that objective the current policy is *roughly
right and cheap* — post-#1592 the per-PR ceremony is one `make pr-ready` when
final — but it carries four pieces of scar tissue: (1) the workflow's arm
step is dead code (zero `ready_for_review` events in 150 PRs; drafts are
never used); (2) the "durable" `no-auto-merge` hold is not durable against
the only live arm path (`make pr-arm-auto-merge` never checks it — proven on
\#1626, armed 52 s after being labeled); (3) two files still document the
code-owner rule as RETIRED when it is live again; (4) the hook-regenerated
LOC block in `_project/specs/uat-framework.md` makes any two LOC-changing UAT
PRs conflict by construction, which is what actually serializes concurrent
agent batches. Recommendation: keep arm-when-final and the tiered soundness
lane unchanged; fix (2) and (4); delete (1); correct (3). Do **not** add
review gates.

## The objective, named and defended

Merge latency alone is the wrong target: median ready→merge is already 27
minutes and nothing human-visible waits on it. Defect count alone is the
wrong target: the measured escape rate is ~2% with same-day fix-forward SLA
and post-merge auto-revert, so marginal defect suppression is cheap to buy
back after the fact. The scarce resource in a solo-maintainer,
33-merges/day, agent-fleet workflow is **attention**: every per-PR ceremony
minute multiplies by ~33/day forever, while every escaped defect costs a
bounded, observed ~minutes-to-2h once. Hence: merged correct work per unit
attention, with a tier exception where blast radius makes a defect *not*
cheaply reversible (published results, release path — see D5).

## Reframe verdict — is auto-merge policy the constraint?

**Partially. Two distinct problems have been conflated; only one of them is
merge policy.**

1. **The arm-when-final policy is not scar tissue.** Its originating failure
   (auto-merge firing before the author's own review fixes landed —
   #1503/#1521/#1531, and escape commit `16a5a1432e` "re-land review fixes
   stranded by auto-merge on #1521 and #1531") is a real, observed defect
   class, and the fix costs one command per PR. It stays.
2. **The concurrent-batch serialization is not caused by merge policy at
   all.** It is caused by `_project/scripts/uat_loc_table.py` +
   `.pre-commit-config.yaml:32-38`, which force every net-LOC-changing UAT
   change to rewrite a shared generated block in
   `_project/specs/uat-framework.md:127-154` (module table) and `:174-190`
   (bucket summary). The summary block ends in a single grand-total line
   (`**Total: 11,043 production LOC across 26 modules.**`), so **any two
   LOC-changing UAT PRs textually conflict regardless of which modules they
   touch**. Measured: since 2026-07-15, 14 of 39 commits touching
   `tests/uat/*.py` also rewrote the spec — including all four substantive
   PRs of the 08-06 remediation batch. The 08-06 batch (#1618 armed at 00:28,
   still open and unmerged 13+ hours later while #1616/#1628/#1630 landed
   through the same file) is the arming-is-inert case: for *concurrent* UAT
   PRs, arming state is theatre because `mergeable_state: dirty` gates the
   merge, not the arm.

   Caveat the other way: in the 150-PR window at large, this did not bind —
   the 5 spec-touching merged PRs had **zero overlapping open windows**
   (sub-hour PR lifetimes serialize naturally), and 141/150 PRs merged while
   armed. The generated file is the binding constraint **only when multiple
   UAT sessions run concurrently — which is precisely the workflow the repo
   is moving toward.** Fix the file (D6); do not redesign merge policy
   around it.

Note: GitHub's server-side merge does not run custom merge drivers, so a
`.gitattributes` driver cannot fix mergeability on github.com; `merge=union`
(the `fast_lane_ceiling_log.md` precedent) would merge cleanly but produce
wrong totals that `--check` then flags on develop. The only real fixes are
derive-at-read-time (stop committing the volatile numbers) or coarsening the
committed numbers so most PRs don't move them.

## Prior art — every mechanism, with verdicts

| # | Mechanism | Where | Originating incident | What it prevents | Failure mode still reachable? | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Workflow arm on `ready_for_review` | `.github/workflows/auto-merge-on-open.yml:153-169` | #1567/#1592 (was: arm on `opened`) | n/a (it is an *enabling* path) | Dead in practice: 0 `ready_for_review` events in 150 PRs; drafts unused; 0 bot arms since #1592 (last: 85 s before its merge) | **supersede** — delete the arm step; workflow becomes revoke-only (D2) |
| 2 | Workflow soundness revocation | `auto-merge-on-open.yml:105-151,171-183`; `_project/scripts/auto_merge_soundness_paths.py` | #1512 (anonymization change auto-merged into every published byte) | Hands-free merge of correctness-/privacy-defining paths | Yes — reachable and firing: disable step executed in 20/100 recent runs (upper bound; `\|\| true` masks no-ops) | **extend** — keep; it is the tier boundary (D5) |
| 3 | Makefile withhold/arm (`pr-open` withholds, `pr-ready`/`READY=1` arms) | `Makefile:1582-1643` | #1503/#1521/#1531 (stranded review fixes) | Merge before the author is finished | The defect class recurs whenever arming precedes finality; observed escapes `16a5a1432e`, #1543 | **keep unchanged** (D1) |
| 4 | `no-auto-merge` durable hold label | `auto-merge-on-open.yml:87-103,185-194`; sweep `green_unmerged_sweep.py:119-123`; #1622 | Two sessions fighting over gating; no hold for a non-draft PR | Re-arming of an intentionally held PR | **Hold is bypassable today**: `pr-arm-auto-merge` (Makefile:1617-1630) never checks the label — #1626 was armed 2026-08-06T13:48:46Z *while labeled* (labeled 13:47:54, unlabeled 13:48:54, all actor `joeharris76`). Workflow disable-for-hold path: 0 executions in last 100 runs | **extend** — close the Makefile gap (D3); it is the only cross-session coordination primitive and costs one label check |
| 5 | Nightly green-unmerged sweep | `_project/scripts/green_unmerged_sweep.py`; `nightly.yml:736-775`; #1624 | Post-#1592, green+unarmed is normal; sweep falsely flagged intentional holds | Silently stranded finished work | Report-only (one tracking issue; never arms/merges/labels) — cannot cause a bad merge | **keep** — correct shape for a backstop: alerting, not acting |
| 6 | `pr-conflict-scan` (`git merge-tree --write-tree`) | `Makefile:1696-1709`, warn-only inside `pr-open` | concurrent-PR sessions | Surprise `dirty` states | n/a (advisory) | **keep** — cheap, right altitude |
| 7 | `pr-base-guard` + zero-CI-for-stacked-PRs | `.github/workflows/pr-base-guard.yml`; `docs/development/pr-base-branch-policy.md` | stacked PRs silently get zero CI | Unvalidated content landing via parent squash | Yes, guard is load-bearing | **keep** — out of scope here but interacts: it is why "wait for the parent PR" is not a coordination option |
| 8 | Ruleset: required CI only (`ci-required-result` + browser gate), no reviews, + code-owner rule on CODEOWNERS(=soundness) paths | `docs/operations/repo-admin-settings.md:47-91,117-186` | — | The actual merge gate | Live. **Doc contradiction**: `auto-merge-on-open.yml:50-63` and `auto_merge_soundness_paths.py:53-69` still say the code-owner rule was RETIRED 2026-07-18; `repo-admin-settings.md:119-124` records it re-enabled (verified 2026-07-21) | **extend** — fix the stale comments (D4) |

## Quantified findings

Window: full population of 150 merged PRs to `develop`, #1459–#1630,
2026-08-02T01:09Z → 2026-08-06T13:45Z (~4.5 days, ~33 merges/day; every day
exceeded 5 merges, peak 61). Classification: a PR is "auto-merged" if an
`auto_squash_enabled` event was in force at merge time (`mergedBy` is the
*enabler* identity under native auto-merge — `app/github-actions` means the
pre-#1592 workflow token armed it, not that any workflow merged it).

**Ready → merged latency** (no drafts exist, so created ≈ ready):

| Class | n | p50 | p90 | mean | max |
| --- | --- | --- | --- | --- | --- |
| Auto-merged (armed at merge) | 141 (94%) | 0.45 h | 3.5 h | 2.85 h | 37.7 h |
| — bot-armed (pre-#1592 arm-on-open) | 66 | 0.49 h | 3.0 h | 2.59 h | 37.7 h |
| Manual (never armed) | 9 (6%) | 0.61 h | 12.1 h | 3.03 h | 12.1 h |

Latency is statistically indistinguishable at the median. **The gate is not
buying latency and manual merging is not costing much latency — at this
scale the arming question is not a throughput question.**

**Escaped defects** (the central number):

| Signal | Auto (n=141) | Manual (n=9) |
| --- | --- | --- |
| Direct `#N`-referencing fix commit ≤7 d | 2 (1.4%) | 0 |
| Red-develop revert (#1543) | 1 | 0 |
| File-overlap ≥75% follow-up "fix" PR ≤7 d | 33 (23.4%) | 2 (22.2%) |

The overlap proxy is dominated by *planned* iterative hardening series
(~half of all PRs in the window are themselves `fix(...)`) and is
right-censored (mean observation 2.1 d of 7); read the honest escape rate as
**~2% (3/150), and note both direct-reference escapes are the
stranded-review-fix class that #1567/#1592 has since closed**. Manual n=9 is
too small to support any auto-vs-manual defectiveness claim. **There is no
evidence auto-merge causes defects; the one incident class it caused
(premature merge of unfinished work) was an *arming-time* bug, fixed by
arm-when-final, not by gating merges.**

**Arming mechanics:**

- 153 arm events: 84 by `joeharris76`, 69 by `github-actions[bot]`, 0 other.
  Bot arms stop at exactly #1592's merge (last: 2026-08-06T00:39:43Z, 85 s
  prior). Zero since → **the workflow arm step has never fired via
  `ready_for_review`; `make pr-ready` is the only live arm path.**
- Armed-but-never-fired: 0 among merged PRs (7 PRs saw 12 disable events,
  every one re-armed and merged armed); 4 among the 6 closed-unmerged PRs
  (closed/superseded, not conflict-wedged).
- Soundness revocation: 20/100 recent workflow runs executed the disable
  step (upper bound — the `\|\| true` makes already-off indistinguishable).
  Explicit-hold disable step: **0/100 executions ever**.
- `no-auto-merge` label lifetime usage: exactly 2 PRs (#1626: on for 60 s;
  #1631: still held). It has not yet prevented any merge — #1626's arm was
  stopped 9 minutes later by a session disarming it, not by the label.
- Hands-free rate: 13/150 (9%) merged with zero activity of any kind;
  105/150 (70%) with zero *human* review or comment. During the 08-06 UAT
  batch, every Codex review attempt failed on usage limits — the entire
  batch had zero completed external reviews, and develop did not break.

**UAT batch claims from the session, verified:** #1568/#1569 hands-free
merges 18 s/32 s after opening — confirmed. #1630 merged manually 36 min
after open with zero completed reviews; cost ≈ 0 — confirmed (its follow-up,
\#1631, is open and held, so "landed later" is not yet true). The 60-second
label fight — confirmed to the second on #1626, all four events actor
`joeharris76`, no attribution possible. The #1626 trap/cleanup regression and
the mutation-survival findings are session-reported and **not independently
verified here**; note #1626 is unmerged, so nothing about it is "shipped".

**Unanswerable from available data:** historical `mergeable_state` (GitHub
doesn't retain it) — armed-but-dirty inertness is established structurally,
not measured; CI-minutes burned on conflict-driven re-runs (not extracted);
defects *prevented* by withholding (counterfactual; one candidate save: the
review of unmerged #1626 while it sat unarmed).

## Decision matrix

Scored 1 (bad) – 5 (good) against: **A** attention cost/PR, **L** merge
latency, **C** defect containment (weighted by tier blast radius), **M**
multi-agent safety (indistinguishable actors), **S** comprehension cost of
the mechanism set.

| Policy | A | L | C | M | S | Total | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (a) No gating: arm on `opened`, merge when green | 5 | 5 | 1 | 1 | 5 | 17 | The pre-#1567 world. Reproduces the only observed incident class (3 stranded-fix incidents in one session; #1512 privacy escape reached published bytes). Fails exactly where reverts are not cheap. |
| (b) Full manual: no auto-merge anywhere | 2 | 3 | 3 | 2 | 4 | 14 | 141 armed merges become 141 human polls (~33/day). Manual cohort shows no measurable correctness gain (n=9, same defect proxy). Buys nothing the data can see; costs the scarce resource. |
| (c) Status quo, untouched | 4 | 4 | 4 | 2 | 2 | 16 | Works, but: dead arm path, hold label bypassable by the live arm path, contradictory docs, conflict-by-construction spec file. M=2 because the one coordination primitive doesn't bind. |
| (d) **Recommended**: status quo − dead code + label made real + generated-file fix; tiers unchanged | 4 | 4 | 4 | 4 | 4 | 20 | Same ceremony as (c); removes the multi-agent failure modes actually observed. |
| (e) Tighten: require completed bot review before arm | 2 | 2 | 4 | 3 | 3 | 14 | The 08-06 batch shows this deadlocks on third-party outages (every Codex review failed on limits); couples merge availability to an external quota. |

## Decisions and disposition (D1–D7)

The recommendations below are not all accepted. The merge of PR #1636
authorized and implemented D1–D4; PR #1634 implemented D6. D5 and D7 remain
proposed policy recommendations and are not authorized by this record. This record
preserves the evaluation and rationale; the current source and operational
runbook are the operative implementation record.

| ID | Decision | Choice |
| --- | --- | --- |
| D1 | Arm-when-final | **Keep unchanged.** `make pr-open` withholds; `make pr-ready` / `READY=1` arms. Its incident class is real and its cost is one command. |
| D2 | Workflow arm step | **Delete** (`auto-merge-on-open.yml:153-169` and the `hold`-gated enable condition). The workflow becomes revoke-only: soundness revocation + label revocation. Update the header policy comment; keep `ready_for_review` in the trigger list only if the draft path is ever adopted — otherwise drop it. Rationale: 0 firings ever; every line of a dead enabling path is comprehension cost on a security-relevant file. |
| D3 | Make the durable hold durable | **Extend** `pr-arm-auto-merge` (Makefile:1617-1630) to refuse when the PR carries `no-auto-merge` (one `gh pr view --json labels` check, exact match, same semantics as the workflow's `grep -qxF`). Add a pinning test beside `tests/unit/test_auto_merge_hold_is_durable.py`. This is the multi-agent coordination primitive: with indistinguishable actors, the label is the only cross-session signal; today the only live arm path ignores it (#1626, armed while labeled). |
| D4 | Doc truth | **Correct** the stale "RETIRED 2026-07-18 / no repo-layer backstop" text in `auto-merge-on-open.yml:50-63` and `auto_merge_soundness_paths.py:53-69` to match `repo-admin-settings.md:117-186` (code-owner rule live again, verified 2026-07-21). A safety comment that understates the actual protection invites wrong risk decisions in both directions. |
| D5 | Tiering | **Keep the tiered policy; do not unify.** UAT/test infrastructure: fix-forward tier (auto-merge on green; escapes cost ~2h bounded). Soundness paths (equivalence oracles, anonymization, result capture, resolver core) + release path: manual-merge tier (workflow revocation + CODEOWNERS + owner merge) — #1512 is the proof that this tier's escapes are *not* cheaply reversible (published bytes). A single uniform policy across these tiers is the error, in either direction. |
| D6 | The reframe fix | **Stop committing the volatile LOC numbers.** Preferred: `uat_loc_table.py` keeps the module/bucket *definitions* in the spec but moves the generated numbers out of Git (emit to an untracked artifact, or demote the pre-commit `--check` to a scheduled/nightly drift report). Fallback if inline numbers must stay: round to coarse bands (nearest 250) so typical PRs don't move them, and drop the single grand-total line — it alone makes all pairs conflict. This, not merge policy, is what serializes concurrent UAT batches. |
| D7 | Review gates | **Do not add any.** 70% of merges had zero human review activity and the escape rate is ~2% with same-day fix-forward + auto-revert backstops. Reviews are valuable (the #1626 catch) — fund them by keeping PRs unarmed until reviewed *when the author chooses*, not by a merge-blocking gate that deadlocks on reviewer outages. |

**Migration path** (each independently landable, smallest first): D4 (comment
edits + no behavior change) → D3 (Makefile guard + test) → D2 (workflow
deletion + update `test_auto_merge_enablement_point.py`) → D6 (script + spec
\+ `.pre-commit-config.yaml`; verify `guards-fix` and CI drift check follow).
Leave alone: sweep, soundness predicate/CODEOWNERS lockstep, pr-base-guard,
conflict-scan, ruleset.

## What safety is given up — explicitly

- **D2** gives up the draft→ready UI arming affordance. Accepted: it has
  never been used (0 events / 150 PRs), and `make pr-ready` or the merge-box
  button covers the need.
- **D6** gives up always-current LOC numbers inside the committed spec.
  Accepted: the numbers are decorative for correctness; drift becomes a
  nightly report instead of a per-commit invariant. The failure mode "spec
  quotes stale totals for up to a day" is affordable; the failure mode it
  buys off — concurrent agent batches serially conflicting on a generated
  total line — is the observed one.
- **D7 (standing)** accepts that ~2% of merges will need a same-day fix or
  revert, and that a defective UAT-tier change can sit on develop for up to
  ~2 h before fix-forward. Accepted *for that tier only* because: squash
  merges make reverts one commit; `develop-post-merge` auto-revert and the
  same-day SLA exist; and #1630's premature merge measurably cost ≈ 0. Not
  accepted for soundness/release tiers (D5 keeps them manual).
- **Residual, unchanged**: a PR editing `auto-merge-on-open.yml` runs its own
  workflow copy and can delete the self-protection in the same commit
  (documented in the header). The live code-owner rule narrows this; the
  residue is recorded in `repo-admin-settings.md` and accepted. D2 shrinks
  the file but does not change this class.
- **Actor attribution remains impossible.** Everything acts as
  `joeharris76`; the 60-second label fight cannot be attributed. D3 makes
  the label binding but cannot make it *auditable*. Any future policy that
  needs "who did this" requires a session-identity convention (e.g., a
  session tag in the label-event-adjacent PR comment) — costed at one
  comment per hold; not required for D1–D7 and deliberately not proposed.

## The case against this recommendation

The strongest honest argument: **this window cannot show the counterfactual,
and it was an anomalous window.** 4.5 days of burst remediation (61 merges
on Aug 4) with the policy already in flux mid-window; every "the gate buys
nothing" number is measured *under the gate*. The 2% escape rate is an
escape rate *of gated merges* — under policy (a) the #1512 class recurs, and
we know that because it did. If the fleet scales to N concurrent sessions,
the serialized sub-hour lifetimes that made arming look clean disappear, and
this ADR's own data (the stuck #1618) shows what that looks like. D6 removes
the *textual* conflicts but not the *semantic* ones: two UAT PRs editing the
same module still conflict, and with mutation-weak test suites
(session-reported: weak mutants survived fully green suites) green CI is a
softer oracle than this ADR's fix-forward economics assume. If the mutation
finding generalizes, the correct response is not "merge faster" but
"strengthen the oracle" — and until that is measured, deleting *any*
friction is a bet that CI green means what we act like it means.
Counter-position accepted as the reason D5 keeps the soundness tier manual
and D7 funds reviews rather than abolishing them; it does not, however,
justify per-PR ceremony on the fix-forward tier, where the observed cost of
being wrong stayed under the cost of one day of polling.

## Blind-spot audit (L2, per review-protocol §3/§4)

What class of issue does this evaluation's framework miss?

1. **Counterfactual blindness**: every measurement conditions on the policy
   that was active; prevented incidents are invisible. The framework treats
   absence of evidence (manual cohort n=9) as weak evidence of absence.
2. **Oracle strength is assumed, not audited**: the whole fix-forward
   economics rests on "green CI ≈ correct". The mutation-survival report is
   exactly the kind of signal this framework has no lane for — it evaluated
   merge *gating* while the load-bearing assumption is test *strength*. A
   dedicated mutation-score baseline for `tests/uat/` would convert this
   from anecdote to gate-able number.
3. **Single-window sampling**: 4.5 days, mid-incident, policy changing
   within the window (three regimes: arm-on-open → #1592 → #1622). Rates
   were not stratified by regime except for arming actors; latency and
   escape numbers mix regimes.
4. **Attention was never directly measured**: the objective is per-unit
   attention, but attention is proxied (ceremony steps, polling) rather than
   measured (no session-time accounting exists). The recommendation would
   survive a 2× error in the proxy, but the framework couldn't detect one.
5. **Third-party dependency risk entered sideways**: the Codex usage-limit
   outage materially changed the batch's review coverage and was discovered
   incidentally. No mechanism inventories external dependencies of the merge
   path (review bots, GitHub auto-merge semantics such as `mergedBy` =
   enabler) or alerts when one degrades.

> Addendum 2026-11-05: the SQLGlot delivery workflow (`.github/workflows/sqlglot-delivery.yml`) is a deliberate automated arm path for its own adoption PRs; it uses the ordinary `scripts/pr_arm.py` rules, so D2's removal of the draft-to-ready arm step is unchanged.
