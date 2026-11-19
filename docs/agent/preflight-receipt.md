# Preflight receipt

`make pr-preflight` leaves a receipt for the exact tree it ran on, and `make pr-open` will not push a tree
that has no passing receipt. The agent must run the checks before publication; the owner and the reviewer
can read what ran.

## Flow

1. Commit. The receipt is keyed by `git rev-parse HEAD^{tree}`; a dirty working tree, or a tree that changes
   during the run, produces no receipt.
2. `make pr-preflight` (never wrapped in `make heavy-run`, see `heavy-work.md`). Each step (`changelog-check`,
   `ruff-check`, `ruff-format`, the guards `docs-reference-inventory`, `project-references` and
   `docs-ownership`, `theseus-prefetch`, `pytest`, `linux-pytest`) is recorded with its command, exit code, outcome
   (`passed`, `failed`, `unverified`, `known-failure`), pytest counts, duration and the last 40 output
   lines, in `~/.cache/benchbox/preflight/<tree>.json` (`BENCHBOX_PREFLIGHT_RECEIPT_DIR` overrides the
   directory). A failed run still writes a receipt, and it blocks `pr-open`.
3. `make pr-open` verifies the receipt for the tree of `HEAD` before the review or the push. It refuses when the
   receipt is missing, for another tree, shows a failed step, lacks a step its recorded selection required
   (`ruff-check`/`ruff-format` for changed Python, each guard step that the changed paths between the receipt's merge base and head require (`verify` recomputes the list itself, so a receipt that skipped a guard is refused, but a recorded guard step proves only that it was run and passed), `pytest` for mapped tests, `linux-pytest` when selected), or was
   selected against a merge base that is not an ancestor-or-equal of the PR's merge base (`BASE=HEAD` never
   passes). Any new content commit, and any
   `pr-refresh` merge that changes the tree, needs a fresh `make pr-preflight`.
4. `make pr-open` renders a short "Preflight receipt" section into the PR body between
   `<!-- preflight-receipt: v1 begin tree=… -->` and `<!-- preflight-receipt: v1 end -->`, replacing the section
   from an earlier push of the same PR.

## Dry runs

GNU Make runs any recipe line that contains `$(MAKE)` even under `-n`, `-q` or `-t`, so the landing targets whose
recipes call `$(MAKE)` (`pr-preflight`, `pr-preflight-fast-tests`, `pr-content-guard`, `pr-open`, `pr-refresh`,
`pr-fanout`, `pr-ready`, `pr-arm-auto-merge`) check the first word of `MAKEFLAGS` themselves. Under a dry run they print
`dry-run (-n, -q or -t): skipping <target>` and run nothing: no fetch, push, merge, receipt, `gh` call or arming.
The recipe exits 0. Make itself exits 0 under `-n` and `-t`; under `-q` it reports its own question status (0 or 1,
depending on the Make version).

## Theseus install

The tests that need the verified Theseus install (`theseus_engine` in `tests/unit/scripts/conftest.py`) never touch the
network: their fixture points the bootstrap's download mirror at an unreachable loopback port and reads wheels only from
the install cache (`THESEUS_INSTALL_CACHE`, default `~/.cache/theseus/installs`). A re-lock PR names a wheel that cache
does not hold yet, so every one of those tests skipped with `Connection refused`.

Before pytest, `make pr-preflight` runs the `theseus-prefetch` step (`_project/scripts/theseus_prefetch.py`). It fetches
the locked wheels with `curl`, accepts a byte source only when its sha256 matches `.github/theseus-lock.json`, and
stores it in that cache; the bootstrap re-verifies it on install. A hash mismatch fails the step and the preflight. When
no source is reachable the step prints `WARNING: Theseus install not verified locally` and the receipt records it as
`unverified`. Whatever the cause, when the pytest step still skips Theseus tests the receipt marks it `unverified`,
records the count as `theseus_skipped`, and `pr-open` renders "N tests that need the verified Theseus install were
skipped; the Theseus group was not run locally". Skipped tests are never reported as passed.

## Local review

After the receipt check, `make pr-open` runs the local pre-PR review (`scripts/local_review.py`) on the committed
`HEAD` and refuses to push when it does not clear the head; running another reviewer first is unnecessary. To run the review alone
before pushing, commit and run:

```bash
python3 scripts/local_review.py --policy .github/review.yml --trusted-ref origin/develop
```

It reviews the diff against `origin/develop` (`--base-ref` changes that), never uncommitted changes, and posts nothing
to GitHub. Setup, the classifier key and exit codes are in `docs/internal/review-v2.md` under "Local pre-PR review".

## Known environment-only failures

`.github/preflight-known-failures.json` is the only way a failing step is accepted:

```json
{"schema": 1, "entries": [{"step": "linux-pytest", "test": "<pytest node id>", "reason": "...", "tracker": "<tracker id>"}]}
```

- Only the `linux-pytest` step can be allowlisted, and only when pytest exited 1. Every failed test in the step
  must have an entry, and the pytest summary count must equal the listed failures; an unlisted failure, a crash,
  an interrupted or killed run, or output that hides a failure fails the step.
- The file is a soundness path (`.github/soundness-paths.txt`), so an edit gets the full review and gate.
- The allowlist is read from the committed tree (`git show HEAD:`) at both preflight and `pr-open`, so an
  entry is a visible diff in the PR that relies on it and is reviewed like code. Removing an entry invalidates
  receipts that relied on it.
- Accepted failures appear in the receipt and the PR body with their reason and tracker; they are never hidden.
- An entry names a tracked environment defect and is removed when the defect is fixed.

## What it is not

The receipt is produced on the author's machine, so it is a claim. The hosted gate and Theseus never read it;
CI is the proof and `pr-arm` still needs the `core` verdict.
