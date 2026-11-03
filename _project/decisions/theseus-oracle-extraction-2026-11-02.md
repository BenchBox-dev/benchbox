# Theseus: extract the oracle development tool, not BenchBox policy

Date: 2026-11-02
Status: Separate-repository direction authorized; extraction contract recorded. Matcher
arrangement, evidence-record form and activation remain owner decisions or later
acceptance gates, not completed work.

## Decision and boundary

Move the generic oracle review engine, gate evaluator, local driver, cache, matcher,
reusable workflow implementation and OMP maintenance machinery to Theseus. BenchBox
remains the first consumer and owns its policy, manifests, read-index scanning, PR
lifecycle, trigger wrappers, schedules, environments, permissions, required-check/App
source binding, running gateway host and provider accounts. Theseus is a development-tool
dependency only: neither BenchBox's benchmark runtime nor its installed user package
acquires a reviewer dependency.

The companion [consumer inventory](theseus-oracle-extraction-inventory-2026-11-02.md)
records the source revision, counts, consumer citations, migration owners and proving
checks. Extraction is a coordinated replacement of those consumers, not a package rename.

Theseus must own its own GitHub App and repository governance. The owner supplies bootstrap
reviews until its gate is live; a broken bootstrap gate is not permission to self-approve.
The first live Theseus gate uses a separate owner-reviewed bootstrap source and a
trusted committed lock naming verified wheel/dependency hashes and fetch sources.
It must not depend on the final release whose publication it is meant to protect.
The repository lookup `gh api repos/BenchBox-dev/theseus` returned HTTP 404: it is unavailable
or private to the caller, not proven absent. The owner must create it or grant access.

Use distribution `theseus-reviewer`, import `theseus_reviewer`, CLI `theseus`.
Immutable GitHub Release assets are sufficient; PyPI is optional.
OMP redistribution rights and public availability are unestablished:
do not assume OMP can be bundled. Preserve the existing `~/.cache/benchbox/omp` cache.

### Explicit non-goals

No daemon, credential service, local-claim authority, model-policy redesign, benchmark
runtime extraction or external-adopter onboarding is authorized by this decision. Running
the existing host and managing provider accounts stay with BenchBox's owner. Distinct
client tokens do not isolate a shared provider quota. This record confers no production
activation or publication authority.

## Preserve prior decisions

This extraction preserves, rather than reopens:

- [Oracle protocol](oracle-review-protocol-2026-10-08.md): computed strict decisions,
  head-file evidence, read rules, follow-up rounds, stable finding IDs and stored state;
  authors, not the bot, resolve threads. Existing finding and dispute state must survive.
- [Gateway credentials](oracle-reviewer-gateway-2026-10-22.md): the reviewer holds only a
  scoped gateway client token, never raw provider credentials. Existing model policy stays
  consumer-owned. A new package is not evidence that the live gateway leg works.
- [One review job](oracle-single-review-job-2026-10-25.md): one attempt loop, not a chain
  of queue admissions; preserve phase-specific secrets, process isolation prerequisites,
  scans, redaction, fresh attempt homes and late App-token minting. Merely placing secrets
  in different steps is not isolation. Fork and candidate builds receive no secrets.
- [Required App-bound gate](oracle-gate-required-check-2026-10-21.md): trusted consumer
  code posts the required context through the bound App; a PR cannot judge itself.
- [Retired legacy check](oracle-review-check-retired-2026-10-24.md): `oracle-gate` is the
  only required oracle context. Keep the edited/dismissed notifier and narrowed trusted
  triggers until an equivalent replacement exists; do not resurrect `oracle-review` as a
  second required implementation.

The [future-state index](../../docs/design/future-state/index.md) (lines 5–39)
and [contract index](../../docs/design/future-state/contract-index.md) (lines 12–24)
discarded broad `_project` package extraction because it does not ship and has no
second consumer. The owner's separate open-source oracle-tool request supersedes that
narrow disposition only for this development tool; it is not a new second-consumer
claim or permission to extract the rest of `_project`.
The [extraction evidence baseline](future-state-extraction-evidence-2026-08-13.md)
(lines 5–21, 29–57) still gates other maintainer, experimental and monitoring
extractions. The [support-tier commitment](architecture-support-tier-commitment.md)
(lines 41–65) still forbids changing product registries or companion distributions
without product evidence and a separate decision. This move satisfies none of those
product extraction gates, and the cost sample below supplies neither complete CI-minute
nor release-cost evidence. The [property ledger](../../docs/internal/dev-loop-property-ledger.md)
(lines 399–410) also requires individual reclassification before deleting its broad
`_project/scripts` catch-all: drain reporting remains pure-process and merge digest
remains product-safety. Live product publishing is not maintainer tooling; preserve the
boundary recorded in
[`prune-publishing-subsystem`](../../docs/design/future-state/prune-publishing-subsystem/README.md)
(lines 7–19).

Protocol/schema, status-context, artifact and finding identities are stable contracts.
App identity, attesters and base branch are configurable consumer bindings, not renamed
protocol identities. Historical App IDs remain historical evidence, not portable defaults.

The dropped ship/no-ship direction supplies a design constraint, not an implementation:
there must not be two independently drifting matcher implementations. The recommended
exception is a byte-identical vendored matcher module with enforced parity, but the owner
has not approved that arrangement. Resolve it before implementation; do not silently
choose vendoring or a second embedded implementation.

## Trusted installation and promotion contract

The trust chain is **trusted consumer commit → reviewed lock → exact source, asset,
dependency and workflow hashes → byte verification → isolated install**. No `latest`,
ambient installation, missing-lock or different-version fallback is allowed. Validate
installed content as well as downloaded content. Cache identity includes source/asset/
workflow/dependency hashes, interpreter and platform; validate a cache hit before use.
Hashing a release label alone is insufficient.

A reviewer uses the lock from its trusted review base. A separate gate process uses the
current-develop lock, so an older review cannot replace the current trusted gate. Refuse
an incompatible branch/base with explicit rebase guidance rather than crossing versions
or falling back. Preserve one review job while keeping those trust bases separate.

Promotion protection must classify the **full staged delta** as required-read, non-inline
and gate-tier. Bind refusal to the head before considering a human stand-in; never carry
an earlier success onto a promotion head. This behavior must first land and be proved in
BenchBox; that merged BenchBox behavior is the completion boundary for the **BenchBox
promotion protection** deliverable. The **Final source and installed-package parity**
deliverable then owns the reviewed Theseus port commit and installed differential proof
before the freeze. An extraction that only adds protection to the new repository leaves
the source-of-truth gap open. The durable evidence-record format is still an owner choice;
no proposed schema is approved here.

## Sequence and acceptance gates

### Source parity and immutable publication

BenchBox remains the implementation source of truth until cutover. Maintain a source-sync
ledger for **every affected BenchBox commit**, mapping source revision and port revision,
including policy/interface effects and evidence disposition. Do not lose urgent changes
between a sampled baseline and release.

After the promotion behavior and generic port are proved, the owner bounds a source
freeze. Freeze named artifact bytes, prove parity against those bytes, then publish those
same bytes, not a rebuild under the same name. An urgent source fix breaks the freeze:
land it in BenchBox, port it, invalidate the prior candidate/evidence, and repeat parity.
Only one active implementation/writer may control each behavior; the owner-approved
byte-identical matcher arrangement is the only possible duplication exception. Old exports
become inert compatibility evidence, never an alternative runnable implementation.

### Consumer cutover and drift checks

The **Consumer cutover and guard reconciliation** deliverable must cover the complete
inventory, including these often-hidden obligations:

- `quality/comment-policy.json` symbols and digests; comment-policy enforcement;
- `quality/docs-reference-inventory.json` and `_project/inert-doc-readers.json`;
- `tests/unit/ledger_cutover_files.txt` and inert ledger/export readers;
- preflight target and scenario selection, heavy-tier/changelog/coverage selectors;
- Windows antipattern exclusions;
- mutation targets, copied inputs, test paths and mutation runner;
- soundness and gate paths, archive/sparse paths and workflow filters;
- `duplicate-check-delta`, inventory freshness, consumer scenarios and publication gates.

These are cutover acceptance checks, not permission to edit unrelated product behavior.
Path-only rewrites are insufficient where a digest, symbol scanner, copied fixture or
required check still targets removed code. Preserve the full finding/dispute history.

### Offline proof, live gateway and rollback

The **Offline package and workflow parity** deliverable proves exact-byte installation,
cache validation, protocol/gate/consumer parity and secret-free fork/candidate execution.
The **Live gateway and cutover observation** deliverable separately proves the real gateway
leg under owner-authorized credentials and host access. Record each observation as passed,
failed, unavailable or not exercised, with provenance; offline passes do not imply live
passes. Neither deliverable grants production activation authority.

Drain queued and running workflows before replacing trusted executors, and prevent new
old-pin admissions across the switch. No old-pin run may publish afterward; do not
manufacture bypass statuses to accomplish the drain. Retain the exact same-hash source
required for recovery. First-cutover rollback uses a named known-good revert, not an
arbitrary older package. Owner-only broken-gate recovery follows
[`docs/agent/emergency-merge.md`](../../docs/agent/emergency-merge.md); it must not become a
routine alternate merge path or a bypass available to the implementation agent.

After activation, collect an **unpooled** window of at least **7 days AND 60 attempts**,
with source/lock/artifact/workflow hashes, host/platform/interpreter, attempt outcomes and
latency provenance. Separate latency and cadence epochs when configuration changes;
do not pool pre-activation or incompatible versions to reach the threshold. Observe
natural failures and lifecycle events rather than provoking production events. Reliability
acceptance must receive an explicit evidence-backed disposition; it cannot remain open
indefinitely merely because a natural event did not occur. If the owner chooses to wait
for the original observation, add that observation to the disposition task's enforced
dependencies before finishing the decision. Optional external-adopter onboarding
is excluded from this first-consumer acceptance.

## Cost baseline and limits

Source baseline: `b64f28fc73001bcd9273e80c2ff51a5e43a6b8ca`. The following observations
were captured on 2026-11-02; they measure operational exposure, not extraction savings.

| Observation | Result | What it does not establish |
|---|---|---|
| Completed `oracle-verdict` workflow query, created since 2026-10-03, first page of 100 | API reported `total_count: 2500`; sampled 100 rows spanning 2026-11-01T02:55:02Z–2026-11-02T13:51:20Z | Not 2,500 inspected runs; not a complete 30-day population |
| Conclusions in that page | 79 success, 9 cancelled, 12 skipped | No model-attempt count or per-day rate |
| Sum of sampled `updated_at - created_at` | 442.4167 minutes, including queue, skipped and cancelled lifecycle | Not billed runner time, model time, review time or saved minutes |
| Successful run `41456585676`, review job `125314691031` | Job 2026-11-02T13:51:26Z–13:53:19Z = 113 seconds | One job is not a representative runtime distribution |
| Same workflow lifecycle | 2026-11-02T13:51:20Z–13:53:20Z = 120 seconds | Includes setup/queue/finalization; not an attempt count |
| Source commits touching reviewer engine and gates in the bounded window | 129 | Not independent features, engineering hours or review attempts |

Reproduce the source churn count with the exact revision and bounds:

```bash
git rev-list --count --since=2026-10-03T00:00:00Z \
  --until=2026-11-02T13:43:00Z b64f28fc73001bcd9273e80c2ff51a5e43a6b8ca \
  -- _project/scripts/oracle_reviewers _project/scripts/oracle_gate.py \
  _project/scripts/oracle_review_check.py
```

API sources (pagination and later API state can change results):

```bash
gh api 'repos/BenchBox-dev/BenchBox/actions/workflows/oracle-verdict.yml/runs?status=completed&created=>=2026-10-03&per_page=100'
gh api repos/BenchBox-dev/BenchBox/actions/runs/41456585676/jobs
gh api repos/BenchBox-dev/BenchBox/actions/runs/41456585676
```

For that first page, group `.workflow_runs[].conclusion`; sum each row's UTC
`updated_at - created_at` and divide seconds by 60. Do not infer total daily review/runner
rates from a single truncated page. Actual model attempts, provider usage/cost, billed
runner minutes, cadence distribution and before/after savings are unmeasured. Extraction
adds release, lock, governance and sync costs; no saved-minute claim is supported yet.

## Open owner choices and obligations

Resolve matcher arrangement and evidence-record form before freezing the interface.
Create or grant repository access; configure Theseus's App/governance and
bootstrap reviews; explicitly authorize live credentials, release publication and
activation when their prerequisites are met. Consumer wrappers, App bindings, host and
provider accounts remain consumer-owned. None of those choices is silently approved by
a documentation change.
