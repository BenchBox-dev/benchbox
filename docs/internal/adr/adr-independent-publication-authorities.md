# ADR: Independent publication authorities and state contract

## Status

Accepted (2026-08-31). Superseded by
[`adr-site-repo-split.md`](adr-site-repo-split.md).

The publication control plane that implemented this contract has been retired from
core. benchbox.dev is built and deployed from `BenchBox-dev/benchbox-site`, which
records a receipt for every deploy and rolls back to a receipt's artifact. The authority
and state rules below remain the reference for acceptance, withdrawal and readmission.

The maintainer decision approving this ADR is recorded by tracker item
`independent-publication-a1-authority-and-threat-contract`. Publication control-plane
implementation may begin only after this accepted contract is present and its
architecture checks pass.

## Context

BenchBox historically coupled package releases, documentation, the Explorer
application, accepted result data, and GitHub Pages deployment through the protected
`release` branch. That path is the current implementation, but it does not define the
future authority model. In particular, a merge to `published-results` proves corpus
acceptance, not deployment, and a successful build proves neither deployment nor what
users can observe.

This ADR extends, rather than discards, the slim-branch decision in
[`adr-published-results-slim-corpus-branch.md`](adr-published-results-slim-corpus-branch.md).
It also makes the A0 desired, built, deployed, and observed distinction normative.
A `published-results` merge proves acceptance only; it does not prove that the
new publication is deployed or publicly observable.

## Decision

### 1. Seven authority surfaces

No branch, workflow, deployment provider, or status field is a universal publication
authority.

| Surface | Authority | Contract |
|---|---|---|
| Package releases | Protected `release` branch, validated `v*` tag workflow, exact built wheel, and package registry receipt | Package validation and publication remain independent of site and corpus promotion. |
| Prose and site content | `develop` source plus the publication manifest's pinned source SHA | The manifest selects exact reviewed content. A live receipt proves what was served. |
| Versioned API documentation | Exact tested release wheel plus its matching immutable tag/source commit, pinned by artifact digest and publication manifest | API docs document released package bytes. They do not import unreleased `develop` code or inherit corpus state. |
| Explorer application artifacts | Explorer code and build/admission policy on `develop`, pinned by exact SHA and artifact digest | Application code is trusted control-plane input. It never makes result data trusted merely by rendering it. |
| Accepted corpus archive | `published-results` | Every valid result merged to `published-results` is accepted production input by default. The complete accepted archive, including published-only paths, is preserved. |
| Publication desired state | Reviewed publication manifest in the trusted control plane | The manifest pins exact `develop` and `published-results` SHAs, builder identity, policy version, and intended target. It requests publication but does not prove it happened. |
| Observed live state | Attested live receipt | Only a receipt produced from post-deploy public probes proves publication. Git merges, workflow conclusions, artifacts, deployment API state, and provider dashboards are supporting evidence only. |

`develop` owns Explorer code and admission policy. `published-results` owns the accepted
corpus archive. These are complementary authorities, not competing corpus authorities.
A publication manifest MUST pin the exact `published-results` commit it consumes. By
default every validator-clean result reachable from that commit is publication input.
Presentation MAY change through orthogonal visibility, trust, withdrawal, and ranking
fields, but those fields MUST NOT create a second curated corpus whose membership
silently replaces the accepted archive.

### 2. Orthogonal result and deployment state

The following concepts change independently and MUST NOT be collapsed into one status:

- **Acceptance:** `accepted` means the bundle is in the validator-clean archive at the
  manifest's pinned `published-results` SHA.
- **Promotion:** `promotion_pending` means desired state names an accepted result or
  corpus SHA that has not yet received a matching live receipt.
- **Liveness:** `live` means a valid attested receipt observes the intended artifact and
  corpus at the public endpoint.
- **Failure:** `promotion_failed` means the requested build, deploy, or observation did
  not produce a matching receipt. Accepted input remains accepted.
- **Withdrawal:** `withdrawal_requested` records desired suppression. `withdrawn` means a
  matching live receipt confirms suppression from public presentation and ranking. Neither
  state rewrites the historical acceptance fact or audit record.
- **Readmission:** `readmission_requested` records an authorized desired restoration after
  receipt-confirmed withdrawal. The result remains suppressed until a matching live receipt
  confirms the restoration generation, then presentation returns to `active`. A result that
  previously had a public ID retains its tombstone while readmission is pending; a never-public
  private result continues to have no public surface. Readmission does not rewrite the historical
  acceptance fact or prior withdrawal evidence.
- **Visibility:** controls discoverability and direct presentation independently of
  trust, ranking eligibility, and deployment state.
- **Trust:** describes provenance or review confidence. It is never supplied by
  contributor-controlled bundle data.
- **Ranking eligibility:** determines whether a visible result participates in ordered
  comparisons. Visibility alone does not imply eligibility.
- **Deployment state:** records desired, built, deployed, and observed transitions for
  a manifest and artifact, not for the truthfulness of an individual benchmark claim.

A result can therefore be accepted but hidden, visible but ranking-ineligible, trusted
but awaiting or under receipt-confirmed withdrawal, or included in a built artifact that
never became live.

### 3. Desired, built, deployed, and observed transitions

1. **Desired:** one selected immutable candidate artifact contains a manifest that pins exact source SHAs and policy inputs. The candidate is validated before approval.
2. **Built:** a trusted builder emits immutable artifacts and provenance tied to that
   manifest. Build success does not imply deployment.
3. **Deployed:** the deployment provider acknowledges the exact artifact for the target
   environment. Provider acknowledgement does not imply public reachability.
4. **Observed:** independent public probes verify required routes and digests, then an
   attestor signs an immutable receipt binding manifest, artifact, target, observations,
   time, and attestor identity.

Only the final transition permits the `live` claim. A later desired state does not erase
the last known-good observed state. Concurrent promotions are serialized or rejected by
compare-and-set against the target's current manifest generation. A late receipt for a
superseded generation cannot make that generation current.

### 4. Approval and review policy

One authorized maintainer may approve normal promotion. One authorized maintainer may
also order an emergency takedown. Separation of duties is desirable but is not a
liveness dependency for the current maintainer model.

The protected `github-pages` environment supplies the one normal publication approval.
The operator reviews the generated candidate summary; no permit digest, generation,
tracker claim, or author attribution is an approval input. Changes to trust policy,
admission policy, promotion workflow semantics, credential scope, receipt verification,
withdrawal rules, or ranking policy require manual maintainer review and MUST NOT auto-merge. Data-only submissions may use their separately
defined validation and merge policy, but no data PR may modify trusted executable code or
policy.

### 5. Rollback, takedown, retention, and audit invariants

- Rollback selects a previously attested manifest and artifact. Rebuilding an equivalent
  tree without matching provenance is not the same rollback object.
- Rollback is bounded by the last known-good attested receipt and retained exact artifact.
  It uses the normal protected writer and requires a fresh live receipt. The watchdog cannot
  initiate rollback or compensate an unknown provider outcome.
- Emergency takedown fails closed for the candidate presentation: an authorized maintainer
  may record `withdrawal_requested` and republish without waiting for a second approver.
  The result becomes `withdrawn` only when a matching live receipt confirms suppression.
- Reversing a withdrawal requires a new authorized `readmission_requested` event. Public
  presentation remains suppressed until a matching live receipt confirms restoration. A
  previously minted public ID retains its tombstone while pending; a never-public private
  result retains no public surface. A failed or pending restoration must not expose the result.
- Takedown does not rewrite Git history or destroy the accepted archive. During the A0
  freeze, privacy or legal incidents record an immediate withdrawal request and apply
  candidate/artifact-access suppression; any irreversible source-byte erasure requires a
  separately approved incident plan that inventories Git history, artifacts, caches, and
  preservation-law constraints. A branch deletion alone must never be described as erasure.
- Manifests, build provenance, deploy acknowledgements, live receipts, withdrawal events,
  and rollback events are append-only audit evidence and are retained independently of
  replaceable read models.
- Public artifacts and indexes are derived and rebuildable. The accepted archive and
  audit evidence are preservation floors.

### 6. Provider finality and quarantine

The Pages adapter records a durable write intent before submission and passes its unique
intent commit as `pages_build_version`. Workflow concurrency and journal CAS serialize the
normal writer, but neither is a provider activation fence. If a request may have been
submitted and its finality is unknown, the transaction enters `recovery-required` and blocks
new writes. The watchdog reports that state; it cannot compensate, finalize, or invent a
provider barrier. Restoration proceeds through a fresh protected transaction after finality
is established.

### 7. Single metadata journal authority

The publication state machine is recorded in a single durable, append-only Git journal on
the existing `publication` ref (`publication/transaction-state/`).
- State transitions use Git fast-forward commit CAS (no force-push, no merge).
- Trusted execution code is always loaded from a reviewed `develop` commit, never from the
  metadata ref.
- Journal writer tokens follow least privilege (`contents: write` only; no `pull_requests`
  or `workflows` permissions).
- The `publication` branch must be protected against force-pushes and deletions.

## Threat boundaries

The normative threat analysis is
[`../independent-publication-threat-model.md`](../independent-publication-threat-model.md).
Its minimum cases are attacker-controlled pull-request content and code execution, token
recursion, provenance substitution, concurrent promotion races, rollback confusion, and
emergency takedown abuse or delay.

## Consequences

- `published-results` becomes publication input by default rather than an archive that
  requires a separate corpus-membership promotion onto `develop`.
- Publication manifests must pin exact source commits, especially the accepted archive
  SHA, so later branch movement cannot alter a build retrospectively.
- A merge, successful workflow, uploaded artifact, deployment record, or DNS response
  must never be described as live without a matching attested live receipt.
- Explorer presentation remains policy-driven while archive authority stays singular.
- Package-release gates remain at least as strong as the current release process.
- Provider finality remains a safety condition for recovery; an unknown result is quarantined
  instead of being resolved by a timer, HTTP probe, or second approval.

## Reconciled prior decisions

| Prior decision or contract | Disposition |
|---|---|
| `adr-published-results-slim-corpus-branch.md` | Extended: slim archive ownership remains; the separate develop-side corpus-membership promotion is superseded by manifest-pinned default inclusion plus orthogonal presentation policy. |
| `_project/decisions/single-repo-migration.md` | Extended: its current release-driven preview describes the legacy implementation, not proof of future publication authority or live state. |
| `_project/decisions/independent-publication-a0-freeze-2026-08-31.md` | Reused: desired, built, deployed, and observed remain separate; A0 freezes destructive migration until its gates pass. |
| `docs/internal/results-phase-2-runbook.md` | Extended: accepted archive and takedown operations follow this authority model. |
| `adr-public-result-id-permanence.md` | Clarified: public serving creates the external link contract; the A0 observed baseline protects existing routes and later receipts identify subsequent generations. |
| `docs/reference/hosted-results-contract.md` | Extended: acceptance, promotion, liveness, visibility, and withdrawal are orthogonal; a merge never directly means live. |
| `docs/reference/threat-model.md` | Extended: Phase 3 adds hosted-service threats without replacing the cross-phase publication threat model or one-maintainer approval rule. |

## Alternatives rejected

| Alternative | Reason rejected |
|---|---|
| Treat merge to `published-results` as live publication | Acceptance and observable deployment are different facts. |
| Curate a second production corpus on `develop` | Creates competing corpus authorities and can silently omit valid accepted results. |
| Use one status for accepted, trusted, visible, ranked, and live | These dimensions change independently and require different authorization and rollback. |
| Require two maintainers for every promotion or takedown | Exceeds the approved current operating model and can delay emergency response. |
| Let policy or workflow changes auto-merge after tests | Tests cannot authorize changes to the trust boundary or credential-bearing control plane. |
| Assume GitHub Pages status implies an activation fence | GitHub Pages status/cancellation is not a documented no-late-activation contract; experiments alone cannot prove future non-activation. |
| Use workflow commit SHA for Pages write correlation | Repeated writes of different artifacts at the same workflow commit alias; unique write-intent commit OID is required. |
