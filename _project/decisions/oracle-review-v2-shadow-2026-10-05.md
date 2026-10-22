# Self-hosted oracle review, shadow mode

Date: 2026-10-05
Status: Decided. The shadow workflow is added; the required `oracle-review`
check and `_project/scripts/oracle_review_check.py` are unchanged.
The blocking severities rule is superseded by
`_project/decisions/oracle-review-protocol-2026-10-08.md`: severity orders
defects and never gates.
Related: `.github/workflows/oracle-verdict.yml` (formerly
`.github/workflows/oracle-review-shadow.yml`),
`.github/oracle-reviewers.yml`, `_project/scripts/oracle_reviewers/`,
`docs/operations/oracle-review-v2.md`.

## Context

Soundness-path pull requests merge only after the required `oracle-review`
check passes. Today that check needs the Codex connector's review or a
stand-in approval comment from an account that local automation also uses, so
the signal can be forged. The replacement runs agent reviewers from `develop`,
decides pass or fail in deterministic code, and posts the result through a
GitHub App that only the owner controls. No third-party GitHub app is
involved.

## Decision

One workflow, run from the `develop` copy, reviews each soundness-path pull
request with one reviewer chosen by complexity tier and by which reviewers
have usage left.

- **Tiers.** A deterministic classifier, read from `develop`, sorts the change
  into low–medium (data-only paths such as answer files, or a small
  single-area diff), medium–high (comparison, equivalence, validation or
  result-capture logic, or a larger diff) or very high (the gate itself, or a
  large multi-area diff). A label may raise the tier, never lower it. Files
  that make up the gate count as soundness paths even before the manifest
  lists them.
- **Reviewers.** Low–medium: codex `gpt-6-luna` at high effort, muse
  `muse-spark-1.3-contributor`, agy `gemini-3.8-flash-medium`, then the
  medium–high reviewers. Luna runs first because it has completed shadow
  reviews; muse uses the contributor model, the one the maintainer's Muse
  account provides. Medium–high: claude `claude-sonnet-5-5`, codex
  `gpt-6.1-sol`, then the low–medium reviewers. Very high: claude
  `claude-opus-5-5` at medium effort, then `gpt-6.1-sol` at medium effort,
  then pending. Opus never serves a lower tier.
- **Blocking severities.** Critical and High in the lower tiers; Critical,
  High and Medium in the very-high tier.
- **Diversity.** Reviewers from the author's family are skipped, including
  through fallback, except Opus in the very-high tier. The family comes from an
  `author-family:<family>` label; a missing or unknown label means Claude.
- **Quota pools.** Claude (Opus and Sonnet), Codex (Sol and Luna), muse and
  agy. A quota absence skips the rest of the pool for the run, and a reported
  reset time skips the pool until it passes. Any other absence lets pool peers
  try.
- **One reviewer per pull request.** A blocking verdict is final for the head;
  fallback happens only when a reviewer is absent. A blocking finding whose
  line is outside the diff still fails the review: it is posted in the summary
  rather than as a line comment, and dropping it would let a real defect pass
  on a citation detail. Unknown or failed outcomes
  are absent, which leaves the result pending, never passing.
- **Integrity.** Each reviewer job enters the `oracle` environment and loads
  only its own credential. Only the final job holds the App key, and it posts
  only artifacts from its own run that name the head the workflow checked out.
  Stored verdicts must match a strict schema; findings are cleaned of secrets,
  mentions and links before posting. The response-boundary rules in
  `oracle-review-protocol-2026-10-08.md` supersede blanket ingress rejection:
  only known harmless drift is normalized, while duplicate keys, placeholders
  and missing decision-bearing data remain invalid.
- **Usage limits.** A rerun needs a new head or a previous run in which every
  attempted reviewer was absent; a run left pending by invalid artifacts or a
  reviewer that never reported is not rerun on the same head. `/oracle-review`
  works only as a top-level pull request comment. `/oracle-review` comments and hourly scheduled retries
  share a daily budget per pull request, with backoff starting at one hour.

## What shadow mode does and does not do

The workflow posts a separate, non-required `oracle-review-shadow` status and
its findings. Shadow mode first posted findings as one pull request comment
rather than review threads, because the ruleset requires every review thread
to be resolved and shadow findings were not to block merges.

Parity period (from 2026-10-07): the policy uses `findings_delivery: review`.
Each run posts a pull request review on the head, with a thread per finding
on a diff line, and those threads must be resolved before a merge, like the
connector's. The status stays non-required and the required check still
requires the connector; each `oracle-review` run logs the oracle's verdict on
`parity:` lines for comparison. Setting `findings_delivery: comment` ends the
period and makes findings advisory again.

It does not change the required `oracle-review` check, the ruleset, the digest
or the soundness manifest. It does not post a pending status when a run
starts, because only the final job holds the App key. Without the App secrets
it logs the result and succeeds.

## Disabled or not yet proven

- agy is disabled in the policy and has no job, because its CI authentication
  and quota output are not calibrated and its usage is exhausted.
- The quota and authentication messages of claude, codex and muse are not
  calibrated. Until they are, those failures are recorded as errors, which
  still never pass; the pattern tables in `absence.py` take the calibrated
  messages.
- codex and agy have no turn-cap flag, so their timeout is the only bound.
- muse is installed unpinned from Meta's documented installer,
  `https://dev.meta.ai/install.sh`, which has no version or published
  checksum: it fetches an unversioned launcher, and the launcher downloads the
  channel's latest binary each time it runs. The owner accepted this on
  2026-10-05. The residual risk is that a compromised download runs with
  `META_API_KEY`. It is contained as far as the workflow allows: the muse jobs
  load no other secret and never the App key, hold only `contents: read`, run
  the reviewer with `--disable-write --disable-shell`, and their output
  reaches the posting job only as a validated verdict or a redacted excerpt.
  2026-10-09: a SHA-256 pin of `install.sh` was added and then removed. It
  covered only the bootstrap script, not the launcher or the binary, which
  change with each release, about daily; and it would have stopped every muse
  install, with no alert, whenever Meta edited the script. muse runs the latest
  `muse-stable` release on every run, and each attempt writes the installed
  version to its job summary. The owner confirmed this on 2026-10-09.
- The policy's `max_attempts`, and so the number of reviewer slots in the
  workflow, must cover the longest chain of enabled reviewers (four today);
  enabling agy needs a fifth slot and an agy job.
- Retry state is read only from artifacts of this workflow's runs whose commit
  is on `develop`, so a pull request's own workflow cannot forge it. That a
  `pull_request_target` run records the base commit as its head SHA is
  assumed, not yet observed; if it does not, those runs' state is ignored, and
  a comment rerun after a decisive pull request event is not refused.

## Cut-over

A later change sets `mode: enforce` and passes `--signal oracle` to the
required check, retires the connector, adds the gate files to
`.github/soundness-paths.txt`, and pins the context to the App's integration
ID in the ruleset. Renaming the status context also needs `ORACLE_CONTEXT` in
`_project/scripts/oracle_review_check.py` changed to match; a test enforces
it. Findings already go to review threads from the parity period.

2026-10-07: the cut-over took effect. `.github/oracle-reviewers.yml` sets
`mode: enforce`, and `oracle-review` passes `--signal oracle`, so the check
requires the `benchbox-oracle` App's success review of the head and no
unresolved App thread. The connector's result is still logged on `parity:`
lines. The connector App is uninstalled separately; the soundness-path and
ruleset steps above remain separate changes.

2026-10-09: the oracle's name dropped "shadow", which read as advisory after
the cut-over. The workflow is `.github/workflows/oracle-verdict.yml`, the
status context and the first line of each review are `oracle-verdict`, and the
retry state artifact is `oracle-verdict-state-<number>`. The required check
also reads the old `### oracle-review-shadow:` line until no open pull
request's latest oracle review uses it; the merge digest reads both lines for
good. `plan` falls back to the old state artifact and workflow path when no
new state exists. This record keeps the old name where it describes history.
