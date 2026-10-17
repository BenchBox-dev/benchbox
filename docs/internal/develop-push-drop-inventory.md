<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Develop push-drop coverage inventory

```{tags} contributor, operations, ci
```

Companion to [`develop-post-merge-gaps.md`](develop-post-merge-gaps.md).
That doc explains the **class**: GitHub can drop `push` delivery for
consecutive develop merges. Required PR checks run on the pull request head;
post-merge `trunk.yml` validation depends on delivery of the develop push event.
Keeping pending trunk runs prevents replacement after delivery, but does not
guarantee delivery. This inventory answers the remaining question: **which workflows still
fire only on a develop push**, and for each, is the residual risk accepted or
does it need a follow-up?

## Inventory method

1. Enumerate every workflow under `.github/workflows/` whose `on.push` can fire
   for a push that updates `refs/heads/develop` — either an explicit
   `branches: [develop]` (or a list containing `develop`), or a bare `push:`
   with no branch filter (all branches, including develop).
2. Record whether `on.schedule` and `on.workflow_dispatch` are present.
3. Classify **role**:
   - **Safety-critical** — failure or silence can leave a required input
     missing, the public corpus stale, or a required integrity signal absent.
   - **Advisory / metrics** — observability, hygiene, or secondary signals
     that do not alone bound merge safety or public publication safety.
4. For each safety-critical row **without** a schedule: either document
   **accepted risk** (with the compensating control) or mark **follow-up
   needed**. Prefer documentation over adding expensive scheduled suites.

Snapshot date: **2026-09-29** (workflow tree of the six-unit CI change).
Re-run the method when adding a new develop-push workflow.

## Inventory table

| Workflow | Push scope | Schedule | Dispatch | Role | Push-drop residual | Disposition |
| --- | --- | --- | --- | --- | --- | --- |
| `pricing-data-drift-check.yml` | `develop` + pricing generator/inputs path filter | weekly Mon `0 6 * * 1` | yes | Safety-critical integrity — regenerated pricing tables vs vendor APIs | Weekly schedule + dispatch bound drift even if a path-matched push is dropped | **Covered** — schedule present; path filter already limits push volume |
| `submission-validator-drift-check.yml` | `develop` + validator path filter | weekly Mon `0 6 * * 1` | yes | Safety-critical integrity — develop vs `published-results` validator copy | Weekly schedule + dispatch bound drift even if a path-matched push is dropped | **Covered** — schedule present; path filter already limits push volume |
| `sync-results-data-to-published.yml` | `develop` + `results-data/**` (and related validator paths) | **none** | yes | Safety-critical — only automated mirror of the develop corpus to `published-results` | A dropped path-matched push leaves the public corpus stale until human recovery | **Accepted risk** — the daily `corpus-drift-check.yml` canary detects develop-ahead drift and recommends `gh workflow run sync-results-data-to-published.yml`; the workflow keeps the write-heavy mirror on push and dispatch only (no scheduled mutation of a public branch) |

### Explicit non-entries (push, but not develop)

These fire on `push` but **not** for the develop tip, so they are out of this
inventory's risk class:

| Workflow | Why excluded |
| --- | --- |
| `lint.yml` / `test.yml` | `push.branches: [release]` only |
| `release.yml` | tag push `v*` only |

### Retired entries

The previous snapshot also listed `develop-post-merge.yml`,
`orphaned-commit-detector.yml`, `results-explorer-browser.yml`,
`publication-lane-explorer.yml` and `docs.yml`. The first three were retired
with the six-unit CI. `docs.yml` was removed when the public site moved to
`BenchBox-dev/benchbox-site`; the site's own CI now builds and checks it.

### Related scheduled canaries (no develop push)

Not push-drop *subjects*, but they **mitigate** the class for other workflows:

| Workflow | Cadence | Role relative to push-drop |
| --- | --- | --- |
| `corpus-drift-check.yml` | daily `37 6 * * *` | Detects develop-ahead / content-changed corpus drift when the mirror push path was silent (incident class of 2026-08-03) |

## Classification notes

### Safety-critical without schedule

One develop-push workflow lacks a schedule:

- **`sync-results-data-to-published.yml`** — The 2026-08-03 incident was
  exactly this failure mode: three consecutive develop merges got no push
  delivery, the mirror never opened, and `published-results` stayed stale
  with private path leaks until a human noticed. The deliberate design
  response was **not** to put the write-capable mirror on a cron (mutation of
  a public branch from schedule is a higher blast radius). Instead
  `corpus-drift-check.yml` is schedule-only, read-only, fails loud on
  develop-ahead content changes, and points maintainers at a one-shot
  `workflow_dispatch` of the mirror. That is an accepted residual risk with a
   bounded detection window (≤ ~1 day), not an untracked gap.

### Covered rows (schedule present)

- **`submission-validator-drift-check.yml`** — weekly is enough for a rarely
  changing dual-branch validator sync; the path-filtered push is an early
  signal.
- **`pricing-data-drift-check.yml`** — weekly schedule plus dispatch bound
  drift; the push path only adds an early signal for generator edits.

## Cost and policy constraints (standing)

When extending this inventory or adding backstops:

| Do | Do not |
| --- | --- |
| Prefer schedule + `workflow_dispatch` as additive coverage | Replace the `push` trigger |
| Keep schedule paths slim / read-only when possible | Add ~24× daily full test suites on tip |
| Keep write/mutation jobs push + dispatch only | Give scheduled jobs new permission classes without review |
| Document accepted risk with the compensating canary | Paper over drops with `pull_request` / `workflow_run` spam |

## Verification (local, offline)

Confirm this inventory still names the primary covered workflow and at least
one other develop-push subject:

```bash
test -f docs/internal/develop-push-drop-inventory.md
rg -q "sync-results-data-to-published|submission-validator-drift-check" \
  docs/internal/develop-push-drop-inventory.md
rg -q "push-drop|push gaps" docs/internal/develop-post-merge-gaps.md
```

Re-enumerate develop-push workflows (must match the table's subject set):

```bash
python3 - <<'PY'
from pathlib import Path
import yaml

subjects = []
for path in sorted(Path(".github/workflows").glob("*.yml")):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = doc.get(True) or doc.get("on")
    if not isinstance(on, dict) or "push" not in on:
        continue
    push = on["push"]
    hits = False
    if push is None:
        hits = True
    elif isinstance(push, dict):
        if "tags" in push and "branches" not in push:
            hits = False
        else:
            branches = push.get("branches")
            if branches is None and "branches-ignore" not in push:
                hits = "tags" not in push
            else:
                hits = branches is not None and "develop" in list(branches)
    if hits:
        sched = [e.get("cron") for e in (on.get("schedule") or [])]
        subjects.append((path.name, sched or None))
for name, sched in subjects:
    print(f"{name}\tschedule={sched}")
PY
```

Expected subject set (names only):
`pricing-data-drift-check.yml`,
`submission-validator-drift-check.yml`, `sync-results-data-to-published.yml`.

## Manual recovery cheatsheet

| If this is silent / red after a develop burst | Recover |
| --- | --- |
| Corpus mirror lag | `gh workflow run corpus-drift-check.yml` then, if develop-ahead, `gh workflow run sync-results-data-to-published.yml --ref develop` |
| Validator drift | `gh workflow run submission-validator-drift-check.yml --ref develop` |
| Pricing data drift | `gh workflow run pricing-data-drift-check.yml --ref develop` |
