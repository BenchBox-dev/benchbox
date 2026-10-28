# results-data/ Extraction Trigger

Forcing function for the question raised in
[`docs/development/benchbox-results-platform-strategy.md`][strategy]:

> A dedicated `benchbox-results` repository remains an optional future
> extraction if corpus size or contribution volume starts to overwhelm
> the main repo. It is not a Phase 1 or early Phase 2 requirement.

[strategy]: ../../docs/development/benchbox-results-platform-strategy.md

This document **does not pre-decide** extraction. It defines the
conditions that fire an evaluation, the procedure to run that
evaluation, and the ADR template the evaluation produces. Until a
trigger fires, the right answer is "stay in monorepo."

## Trigger Conditions

Any **one** quantitative trigger or **two** qualitative triggers fire
the evaluation.

### Quantitative

| # | Trigger                                              | Source                                              | Threshold              |
|---|------------------------------------------------------|-----------------------------------------------------|------------------------|
| Q1 | Total `results-data/` size (raw bytes)              | `du -sb results-data/`                              | > 250 MB               |
| Q2 | Sustained PR volume to `published-results` branch   | gh API; same data source as Phase 3 promotion M1    | ≥ 20/mo for 3 mo       |

Q1 is point-in-time (one snapshot is enough). Q2 needs three
consecutive review periods at-or-above 20/mo — one bursty quarter
isn't a trigger.

### Qualitative

| # | Trigger                                                   | Source                            |
|---|-----------------------------------------------------------|-----------------------------------|
| L1 | Contributor reports fork/clone time > 60s on broadband   | issue, Discord, email             |
| L2 | A maintainer formally requests evaluation citing operational pain | maintainer note in this file or an issue |
| L3 | Phase 3 design begins                                    | promotion of any Phase 3 design TODO to Active |

Note: contributor fork-time complaints are *expected* to be rare and
underreported. One credible report should be taken seriously, but two
are required to fire the evaluation by qualitative-only path.

### Why these specific triggers and not others

- **250 MB Q1**: GitHub treats >100 MB single files specially and
  warns at 50 MB; ~250 MB total is the band where shallow clones and
  default git settings start producing visible user friction. Below
  that, the monorepo overhead is invisible.
- **20/mo for 3 mo Q2**: half the Phase 3 promotion threshold (50/mo).
  At 20/mo we already have meaningful signal that the corpus is
  growing fast enough to justify the structural review, but Phase 3
  would still be premature. The two reviews are on the same cadence.
- **Fork-time L1**: this is the user-facing symptom of the structural
  problem. It's the only signal that captures "is the monorepo
  *actually* annoying contributors" rather than abstract size metrics.
- **Phase 3 begin L3**: when Phase 3 hosted ingest stands up, the
  question of where the canonical bundles live becomes acute (hosted
  API writes them; explorer reads them; what's the source of truth?).
  Pre-Phase-3 we genuinely don't have to decide.

### What we explicitly do NOT make a trigger

- **Number of files in `results-data/`**: file count tracks bundle
  count, which tracks Q2 (PR volume). Counting both is double counting.
- **CI minute consumption**: the cost is real but small relative to
  the engineering cost of an extraction. Not the right forcing
  function.
- **"It feels heavy"**: aesthetic discomfort isn't a trigger. If it's
  real pain, it should be expressible in fork time, build time, or
  contributor friction.

## Evaluation Procedure (run when triggered)

1. **Open** a new active TODO `evaluate-results-data-extraction-decision`
   under `_project/TODO/main/active/`. Owner = current maintainer
   rotation. Reference this checkpoint TODO.
2. **Copy** the template
   `docs/development/adr/TEMPLATE-results-data-extraction.md` to
   `docs/development/adr/YYYY-MM-DD-results-data-extraction.md`.
3. **Measure**: snapshot `results-data/` size, PR volume from the most
   recent two quarterly metrics reports (the former `phase2_metrics.py`
   script was retired, so gather them by hand), and any qualitative
   evidence cited.
4. **Score** the decision matrix in the ADR. Cite numbers, not vibes.
5. **Recommend** one of: (A) stay in monorepo, (B) extract to a new
   repo, (C) extract with selective sync.
6. **Review** with at least one other maintainer. If only a single
   maintainer exists, post the recommendation publicly (issue or
   discussion) and wait 7 days for community pushback before
   proceeding.
7. **Commit** the ADR. The commit history is the audit trail.
8. **If "extract"**: file a follow-up implementation TODO with explicit
   migration plan, including:
   - History-preservation strategy (`git filter-repo` recipe).
   - SHA stability impact statement (callers that pin specific
     commits in `results-data/`).
   - Rollback path (merging back into the monorepo if extraction
     proves wrong).
   - CI workflow migration (which workflows currently reference
     `results-data/` and how they move).
9. **Mark this checkpoint TODO Completed** only after the ADR is
   committed; the implementation TODO (if any) tracks the next steps.

## Current State

The `phase2_metrics.py` script that ran the Q1 + Q2 checks was retired;
the quarterly review evaluates them by hand. Qualitative triggers are tracked here in this file under
"Qualitative Trigger Log" (append-only).

## Qualitative Trigger Log

Append entries when a qualitative trigger fires.

_(none yet)_
