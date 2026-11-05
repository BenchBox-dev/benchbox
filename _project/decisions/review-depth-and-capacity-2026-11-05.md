# Decision: review depth before the Theseus rename, and a smaller consumer contract

Date: 2026-11-05
Status: Decided; takes effect when the re-lock to Theseus v0.1.7 merges.
Related: `_project/decisions/oracle-gate-relaxation-2026-11-04.md`,
`_project/decisions/batch-integration-loop-2026-11-01.md`,
`docs/internal/oracle-review-v2.md` (Theseus promotion trust),
`docs/agent/batch-feature-delivery.md`.

## Decision

The owner decided, in this order:

1. **Depth first.** Theseus v0.1.7 ships the review-depth fixes before the
   `.oracle-*` to `.review-*` rename (v0.2.0). BenchBox re-locks to v0.1.7 first
   and to v0.2.0 afterwards, so the code that decides BenchBox merges is
   genuinely reviewed under the depth rules instead of passing under a
   relaxation. If v0.1.7 is not promoted within five working days, the fallback
   is to relax the v0.2.0 promotion with a record, as the 2026-11-04 decision
   did for a repair.
2. **Promotion required reads are limited to the executed surface.** A
   promotion's required reads are the changed files that BenchBox executes: the
   promoted wheel's package source under `src/`, `pyproject.toml`, and the
   `review.yml` and `gate.yml` workflows its callers reference. Rename detection
   applies, and a byte-identical rename needs no read. The rest of the upstream
   delta is staged and listed as not required.
3. **Coverage for every clearing verdict.** A SHIP or SHIP WITH FIXES is
   accepted only when every changed hunk of every required file was seen. A
   SHIP recorded before the change carries no coverage mark and is re-reviewed
   on its next head; a carry or a follow-up round from such a record becomes a
   full first round. A same-head status already posted stands.
4. **A trimmed consumer configuration.** `.github/oracle-reviewers.yml` loses
   `brief_max_bytes` and the `promotion.bootstrap_baseline_commit`,
   `bootstrap_reviewers` and `bootstrap_path_map` keys. It keeps only BenchBox
   semantics: the gated base, attesters, path manifests, models and pools, retry
   and protocol limits, exempt paths, and the promotion `package`, `repository`
   and `dependencies`. Theseus v0.1.7 refuses unknown top-level keys, so
   `brief_max_bytes` must go with the re-lock.

## Narrowing of the BenchBox gate's promotion scope

Decision 2 narrows what the BenchBox gate reviews when it promotes Theseus.
Until now a promotion was reviewed over the whole delta between the locked and
the proposed source tree (with BenchBox's exempt globs applied to it). From
v0.1.7 the gate requires reads only of the executed surface. Theseus tests,
docs, tools and every workflow no caller references are no longer read by the
BenchBox promotion review.

The reason is that the whole delta cannot be reviewed genuinely. The v0.2.0
promotion under the old rules has 234 required files, 4.3 MB of file bytes and a
2.9 MB `upstream.diff` of about 1.28M tokens, which is more than any reviewer's
context. Rename detection alone halves the diff (49 renames, 18 of them
byte-identical). The executed surface of the same delta is 27 files, about
123 KB and 54K tokens, which any configured reviewer can read in full. What
BenchBox executes today is therefore read completely; what is dropped is
reading that the reviewers could only touch.

Consequences of the narrowing:

- Changes to Theseus tests, docs and tooling reach BenchBox with Theseus's own
  independent review and, once it is live, its self-gate, not with a BenchBox
  review.
- The executed surface is derived by Theseus from the tree and the verified
  wheel. BenchBox adds no key for it and encodes no Theseus path. In v0.1.7 it
  follows job-level `uses:` references to workflows in the Theseus repository.
  The Theseus repository holds only `ci.yml`, `gate.yml` and `review.yml` and no
  composite action, and the one other file the workflows check out from it,
  `src/theseus_reviewer/bootstrap.py`, is inside the package. A step-level
  action or another checked-out file added to the Theseus repository later is
  not followed, so a release that adds one needs a reviewer's explicit
  attention until Theseus follows it.
- A trust model that relies on Theseus's release evidence instead of any
  consumer reading is a separate, later item. It needs a live Theseus self-gate
  and a receipt format first.

## Evidence

- **Shallow SHIP on #3942.** Its final SHIP (hosted run 41858800966, the `huge`
  reviewer) had 304 required files. Replaying the trace against the real hunks,
  and crediting a bare read with the whole file, 150 of the 304 files had
  changed hunks that were never read; 36 to 44 files had no changed line read at
  all; 25 were credited by a grep alone. About a third of the changed lines were
  read (9,996 of 30,248). The old rule credited any read of a file, any grep with
  a match, and the whole file for a read that returned 300 lines.
- **Compaction on #3902.** Three `huge` attempts compacted their context
  mid-turn, and one of those SHIPs was accepted (run 41696376860, peak context
  861,911 tokens). The old evidence check could not see compaction. Theseus
  v0.1.7 disables compaction in the reviewer's overlay and refuses any attempt
  whose output contains a compaction event.
- **The 406 limit.** GitHub's pull request diff endpoint answers 406 above
  20,000 diff lines (#3942) or 300 files (#3902, 353 files). The reviewers then
  ran with no diff, and one said the base and deletion comparison was
  "blocked". Theseus v0.1.7 builds the diff from Git in the trusted job and
  holds the plan if it cannot.
- **Copied constants.** `scripts/batch_integration.py` carried its own copy of
  the brief cap, header and mark sizes, and a file-list size estimate that
  duplicated Theseus's rendering and knew nothing of its capacity or promotion
  rules. The script now asks
  `theseus preflight` at land, at advance (fix and develop-merge), at status and
  before final evidence, and prints Theseus's reasons verbatim. Lock changes and
  oversize changes are refused there with the reasons Theseus gives.

## The v0.1.7 promotion itself

A promotion is judged by the rules of the base lock, so this promotion is
judged by v0.1.6. It cannot be reviewed under the rules it introduces. To
keep that from being a relaxation by default:

1. The promotion is the smallest rules-changing delta: one release (111 changed
   files, 31 of them on the executed surface), with the rename left for the next.
2. After the hosted verdict, `theseus audit-coverage` from v0.1.7 is run on the
   hosted review capture of the PR head. The promotion is accepted only if every
   required hunk is covered. The result is recorded in a PR comment. The hosted
   capture is written by v0.1.6, so it holds the requested read selectors and not
   the lines omp returned; the audit therefore gives an upper bound on coverage,
   and a full result is necessary evidence, not sufficient evidence.
3. A local review with the v0.1.7 wheel against the same head corroborates it.
   It runs under the strict rules and records the lines it actually read.
4. If the audit shows incomplete coverage after the retry budget, or the hosted
   review is absent or shallow, the owner decides. The promotion then qualifies
   for the repair-class relaxation of the 2026-11-04 decision: a base-pinned gate
   that cannot pass its own repair, which is what v0.1.7 is. It is recorded
   there.

Both the audit and the local review read the trusted policy at the base ref, and
v0.1.7 refuses the `brief_max_bytes` key that `origin/develop` still carries
until this change merges. They therefore run with `--base-ref origin/develop`
and `--trusted-ref` set to a local commit that is `origin/develop` with that one
key removed and nothing else changed. The author's own policy never decides what
counts as required.

Every later rule change, starting with the v0.2.0 promotion, is judged by the
strict rules and gets no relaxation.

## Consequences

- More hosted reviews end `absent (incomplete)` until the reviewers read the
  staged diff; the retry note names the uncovered spans. Total review spend is
  expected to rise by about 10 to 20 percent, offset by refused oversize changes
  and the much smaller promotion reviews.
- A change too large for any reviewer's capacity goes pending with an
  instruction to split, before any tokens are spent. BenchBox batches see that
  at landing through the preflight.
- A v0.1.6 reader rejects records that carry the new coverage mark, so rollback
  is a fix-forward, not a re-lock backwards.
