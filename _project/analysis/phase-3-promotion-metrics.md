# Phase 3 Promotion Metrics

Tracks the indicators from
[`docs/development/benchbox-results-platform-strategy.md`][strategy] §
"Phase 3: Hosted Platform (Deferred)" so the Phase 2 → Phase 3 decision
is made on data, not frustration.

[strategy]: ../../docs/development/benchbox-results-platform-strategy.md

This document defines:

- **What we measure** — 6 concrete metrics with sources and thresholds.
- **How we measure** — manually; the former `phase2_metrics.py` script was retired and has no replacement.
- **When we look** — quarterly review cadence, owner, procedure.
- **What it triggers** — explicit promotion criteria for the dependent
  Phase 3 design TODOs.

## Metrics

Six metrics, four quantitative (gh-API derived) and two qualitative (a
flat notes file). Quantitative thresholds use the strategy doc's
language verbatim. Qualitative thresholds err generous: 3 distinct
requests is enough signal that the demand is real, not anecdotal.

| # | Metric                                            | Source                                                    | Threshold                  | Implication if breached                                     |
|---|---------------------------------------------------|-----------------------------------------------------------|----------------------------|-------------------------------------------------------------|
| 1 | Merged community submissions / month (90d window) | `gh pr list --base published-results --state merged`      | ≥ 50 sustained for 3 mo    | PR review at scale; hosted ingest reduces maintainer toil   |
| 2 | Median PR open→merge time, hours (proxy for review latency) | `gh pr list --base published-results --state merged --json` | > 72h median over 30d    | Reviewers are the bottleneck; async ingest would help       |
| 3 | PR backlog (open against published-results > 7d)  | `gh pr list --base published-results --state open --json` | ≥ 5 sustained for 30d      | Queue is growing; signals chronic capacity gap              |
| 4 | Distinct requests for private/unlisted results    | § Private/Unlisted, count distinct **Requester**          | ≥ 3 distinct requesters    | Public-only constraint blocks legitimate use cases          |
| 5 | Submissions blocked by maintainer-only constraints | § Blocked-Maintainer, count entries (one per **Date**)    | ≥ 5 entries                | PR-mediated trust model is gating real contributions        |
| 6 | Org-account / team-space requests                 | § Org-Spaces, count distinct **Organization**             | ≥ 3 distinct organizations | Multi-tenant identity needed; can't fake with PR labels     |

### Why these six and not others

- **Volume + latency together** (1, 2) catch both "too many PRs" and
  "few PRs but each takes forever" — either makes the model untenable.
- **Backlog (3)** is the leading indicator that complements the lagging
  median latency. A 30d sustained queue depth ≥ 5 means the system is
  not draining.
- **Qualitative signals (4, 5, 6)** capture demand the public-PR model
  fundamentally cannot serve (private results, anonymous submitters,
  org-isolated namespaces). Even a single such request is meaningful;
  the count rules deliberately differ — distinct *requesters* for
  private (one loud user shouldn't dominate), total *entries* for
  blocked submissions (one heavy contributor with multiple blocks is
  itself the signal), and distinct *organizations* for Org-Spaces
  (org-level demand, not individual employee asks).

### What we explicitly do NOT measure

- **Page views / explorer traffic** — interesting for product, but
  irrelevant to whether the *submission* model is at capacity.
- **Bundle file size** — covered by
  [`evaluate-results-data-repo-extraction-checkpoint`][repo-checkpoint]
  with its own thresholds.
- **CLI-side telemetry** — Phase 2 publishes via PR, not API; opt-in
  CLI telemetry is a Phase 3 concern, not a Phase 2 promotion signal.

[repo-checkpoint]: ../TODO/main/planning/evaluate-results-data-repo-extraction-checkpoint.yaml

### Threshold rationale

The strategy doc names "~50/month sustained" verbatim for volume; the
other quantitative thresholds are calibrated against that:

- **72h median latency**: at 50 PRs/mo (~12/wk), > 72h median means
  more than half the queue is older than the typical reviewer rotation
  — a clear bottleneck. M2 measures open→merge, so it includes draft
  and author-iteration time; treat it as a coarse proxy for review-only
  latency. If false-positive promotion is a concern, switch to
  first-non-author-comment → merge once gh's `--json reviews` field is
  the bottleneck check (not at calibration time).
- **Backlog ≥ 5 for 30d**: with 50/mo throughput, a steady-state
  backlog of 5 is ~3 days of in-flight work, which is healthy. Sustained
  ≥ 5 *for 30 days* means the queue is not draining at the arrival rate.

If the script is producing many false positives, **raise** thresholds
rather than promoting Phase 3 prematurely. False-positive promotion
wastes the largest engineering investment in the project.

## Review Cadence and Procedure

- **Cadence**: Quarterly, first Monday of each quarter (Q1: first Mon
  of January; Q2: April; Q3: July; Q4: October).
- **Owner**: BenchBox maintainer rotation — currently the single
  maintainer; revisit when a rotation exists.
- **Scheduler**: `.github/workflows/phase3-promotion-review.yml` fires the
  review automatically on the first Monday of each quarter (cron on days
  1-7 of Jan/Apr/Jul/Oct plus a Monday guard) and opens a PR with the
  generated report. The maintainer no longer has to remember the date.
  `workflow_dispatch` is the manual fallback if the cron is ever missed.

### Procedure (run each quarter)

1. Refresh the qualitative notes file
   `_project/notes/phase-2-requests.md` with anything captured from
   issues, Discord, email, or in-PR conversations since the last
   review.
2. Review the auto-generated PR titled `chore(phase-3): quarterly
   promotion review <date>` opened by
   `.github/workflows/phase3-promotion-review.yml`. If the cron was
   missed, regenerate it manually with `gh workflow run
   phase3-promotion-review.yml`. The `phase2_metrics.py` script that once
   generated the metrics report was retired; gather each metric by hand
   from its source.

3. In the PR (or the handoff file), for each breached threshold write a
   one-paragraph interpretation: is this a sustained signal or an
   artifact (e.g., a single contributor's bulk submission spike)? The
   "Trend vs <date>" section diffs each metric against the prior review
   so the two-consecutive-quarters signal is explicit.
4. **Decision rule**: if **two or more** quantitative thresholds are
   breached *for the second consecutive quarter*, OR any **two**
   qualitative thresholds are breached in a single review, promote the
   following TODOs from "Not Started" to active planning:
   - `design-results-ingest-storage-and-derived-read-model`
   - `integrate-benchbox-cli-submit-and-service-auth`
   - `operate-results-platform-security-observability-and-abuse-controls`
5. Merge the PR (or commit the handoff file, if generated manually).
   The commit history is the audit trail.

The "two thresholds, two consecutive quarters" rule is deliberate
hysteresis: it prevents one bursty quarter from triggering a
$40-225/month + significant engineering investment.

## Current State

See the most recent dated review in `_project/handoffs/phase-3-review-*.md`.
The baseline (zero PRs against `published-results`, no qualitative
requests yet) is recorded at the time this metrics system was set up.

## When This Document Should Be Updated

- A threshold is breached and the decision rule fires → record the
  decision and link to the next-action TODOs.
- The strategy doc updates the "Indicators that Phase 3 is needed"
  list → mirror the change here.
- A new metric becomes feasible (e.g., reviewer rotation exists,
  enabling per-reviewer load metrics) → add it explicitly with source,
  threshold, and rationale.

What this document should NOT track: per-quarter results — those go in
the dated handoff files, not here. Keep this stable.
