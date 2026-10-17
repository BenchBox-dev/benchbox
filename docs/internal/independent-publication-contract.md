# Independent publication operations contract

> **Historical.** The publication control plane this page describes has been retired.
> benchbox.dev is built and deployed from `BenchBox-dev/benchbox-site`; see
> [`adr-site-repo-split.md`](adr/adr-site-repo-split.md). The workflows, scripts, keys and
> secrets named below no longer exist. The authority rules still apply: acceptance into
> `published-results` is not publication, and only a deploy receipt proves what is live.

The governing architecture decision is
[`docs/internal/adr/adr-independent-publication-authorities.md`](adr/adr-independent-publication-authorities.md).
This runbook defines operator-visible states and evidence. It does not activate a new
workflow or modify the A0 freeze.

## Evidence hierarchy

| Claim | Minimum evidence |
|---|---|
| accepted | Bundle and sidecar are validator-clean and reachable from the pinned `published-results` SHA. |
| desired | One selected immutable candidate artifact is validated against exact `develop` and `published-results` SHAs, policy, builder, target, and parent. |
| built | The retained candidate artifact ID, manifest digest, unpacked site-tree digest, and provenance match the candidate contents. |
| deployed | Provider acknowledgement names the exact artifact, target, and transaction generation. |
| live | Fresh attested public probes match the candidate and the signed receipt binds those observations to it. |
| promotion_failed | The build, deployment, or observation lane ended without a valid matching live receipt. |
| withdrawal_requested | Authorized withdrawal event is present in desired state and excluded from candidate public and ranking read models. |
| withdrawn | A matching live receipt confirms that the deployed generation suppresses the result from public presentation and ranking. |

Only an attested live receipt proves publication. A merge, green workflow, uploaded
artifact, provider deployment object, or successful internal health check is supporting
evidence only.

## Promotion procedure

1. Dispatch the candidate builder from `develop`. It resolves current source pins, builds
   the site, validates privacy and lane inputs, and uploads one immutable candidate bundle.
2. The transaction writer validates the numeric artifact ID, producing run, workflow
   revision, manifest, target, parent, archive extraction, site-tree digest, and complete
   required route checksum set before the approval job.
3. One authorized maintainer reviews the generated summary and approves the protected
   `github-pages` environment. The permit digest and Git author metadata are audit fields;
   neither is a value the maintainer must type or paste.
4. After approval, the writer revalidates the same artifact, records intent with journal
   CAS, deploys the exact bytes, records provider acknowledgement, probes the required
   public routes, and signs the live receipt.
5. A failed or uncertain operation remains blocked. A retry starts a fresh transaction
   against the same immutable artifact and obtains fresh environment approval.

## Provider feasibility and fail-closed fallback

The writer records a unique write intent and requires a provider response before calling a
publication deployed. The Pages adapter passes the intent commit as `pages_build_version`.
The provider does not supply a documented late-activation fence, so an uncertain response
remains `recovery-required` and blocks new operations. The watchdog reports that state; it
does not compensate or guess what the provider did.



## Required live receipt fields

A receipt records, at minimum:

- schema version, receipt ID, target, generation, and observation timestamp;
- publication manifest digest and exact `develop` and `published-results` SHAs;
- artifact identity and digests;
- required route set and per-route status, content digest or semantic assertion, cache
  metadata where relevant, and observation origin;
- nonce and freshness window;
- attestor identity and signature; and
- the prior live receipt ID when this is a promotion or rollback.

Receipts are immutable and append-only. Partial route success, stale observations,
internal-only probes, or a signature over mismatched inputs fail closed.

## Reconciliation inputs

The `scripts/publication` canaries consume this evidence as files. They never
fabricate a state; a missing or malformed input fails closed.

- `reconciliation.py` requires `--manifest` (the desired manifest) and a
  `--receipts-dir` containing `assembly-receipt.json` (built), `deployment-receipt.json`
  (deployed), and `live-receipt.json` (the attested live receipt with the fields
  above). The four states must be distinct sources.
- `verify_independence_matrix.py` requires a `--receipts-dir` containing recorded
  lane transitions in `independence-matrix.json` or `lane-transitions.json`.
- `check_operational_receipts.py` requires a `--receipts-dir` containing
  `rollback-drill.json`, `takedown-drill.json`, `incident-drill.json`,
  `retention-policy.json` (which must cite its `source`), and either a
  `capacity-audit.json` or a `--pages-dir` to measure.

`scripts/publication/reconciliation.py` verifies every live receipt signature
against the repository public key at
`docs/operations/publication-attestor-public-key.pem`. It signs canonical JSON
(sorted keys, compact separators, UTF-8) with `signature` and
`attestor_signature` excluded. Receipts must declare
`signature_algorithm: ed25519`; missing, empty, malformed, or non-verifying
signatures fail closed. The matching private key is never stored in the
repository. Before dispatching the production deployer, a maintainer must set
the `PUBLICATION_ATTESTOR_PRIVATE_KEY` environment secret to the matching
Ed25519 PEM private key in the protected `publication-attestation` and
`github-pages` environments. There must be no repository-level copy.

## Forward reconciliation of post-send failures

When a promotion deployment write encounters a post-send failure (for example, a polling timeout or network disruption after the request was dispatched), the transaction transitions to `recovery-required` with failure stage `post_send`.

If the provider deployment actually landed and the live routes serve the approved candidate, the transaction may be reconciled forward to `externally-verified` and `durable`:

1. **Deployment identity binding:** The provider deployment status is fetched live via `GET /repos/{owner}/{repo}/pages/deployments/{deployment_id}` and must return `status: "succeed"`. Because the deploy step submits `pages_build_version: '${{ github.sha }}'`, the provider binds the deployment identity to the controller's workflow SHA. The journal and reconciliation engine require the deployment ID to match the transaction's recorded `controller.workflow_sha` (a CAS-protected journal field).
2. **Attested live receipt:** External public probes must independently verify all required routes against the candidate desired manifest, and the live receipt must be signed with `PUBLICATION_ATTESTOR_PRIVATE_KEY` in the `publication-attestation` environment.
3. **Execution paths:**
   - **In-band:** In the retired publication transaction workflow, the `verify` job executes with a failure override when `deploy` fails on a promotion transaction, verifying live endpoints and recording verification if the deployment succeeded.
   - **Operator dispatch:** For an active transaction already in `recovery-required`, an authorized operator could dispatch that workflow with `reconcile_transaction_id=<transaction_id>`. This bypasses candidate materialization and the deploy job, probes live routes, checks the bound Pages deployment status via the API, signs the live receipt, records verification in the journal, and finalizes the durable head.

## Rollback

Rollback targets the last known-good attested manifest and retained exact artifact. It uses
the same protected transaction writer, reserves a new generation, deploys the known bytes,
runs fresh public probes, and issues a successor receipt. It never rebuilds from a branch or
uses an artifact name as a substitute for identity.

The watchdog is observational. It reports an unresolved provider outcome or stale operation
and does not finalize, compensate, or select a rollback. Once provider finality is established,
an authorized maintainer starts a fresh protected transaction for restoration.

During cutover, legacy writers are drained and normal admission is disabled. The historical
fallback remains documented until the replacement has deployed and the outcome-based cutover
evidence is complete; it is not a second normal authority.

## Emergency takedown

One authorized maintainer may initiate emergency takedown.

1. Record actor, reason, time, affected public IDs or archive paths, and whether the
   action is presentation withdrawal or a request for source-byte erasure.
2. For presentation withdrawal, add `withdrawal_requested` to desired state. Exclude
   affected rows from the candidate public and ranking read models, reserve a generation,
   deploy, and obtain a fresh live receipt. Keep desired state at `withdrawal_requested`
   while deployment or external probes are pending or failed. Record `withdrawn` only in
   observed live state after the matching receipt confirms suppression. Ordinary promotion
   must carry the withdrawal request or receipt-confirmed withdrawal forward.
3. During the A0 migration freeze, do not delete accepted source bytes. For a privacy or
   legal incident, record the withdrawal request immediately, suppress candidate output and
   accessible artifacts where the provider permits, and open a separately approved erasure
   plan covering Git history, workflow artifacts, caches, mirrors, and inventory
   consequences. A branch deletion is not proof of erasure.
4. Preserve non-sensitive audit evidence. Do not claim public removal until external
   probes and a live receipt confirm it.

## Authorized readmission

One authorized maintainer may reverse a mistaken or temporary withdrawal, but the policy
event alone does not restore public presentation.

1. Record actor, reason, time, affected public IDs, prior withdrawal receipt, and
   `readmission_requested` in desired state.
2. Reserve a new generation, restore the result only in the candidate public and ranking
   read models allowed by current visibility, trust, and ranking policy, then deploy and
   run fresh external probes.
3. Keep observed presentation `withdrawn` while deployment or probes are pending or failed.
   Retain the public tombstone when a public ID previously existed; a never-public private
   result continues to have no public route or tombstone. Record presentation `active` only
   after a matching live receipt confirms the restoration generation.
4. Preserve the readmission event, deployment evidence, probe observations, and receipt in
   the append-only audit record. Readmission does not erase prior withdrawal evidence.

## Retention and audit

Retain manifests, approvals, provenance, artifact digests, deployment acknowledgements,
live receipts, failures, withdrawal events, and rollback events independently of derived
indexes. The accepted archive remains recoverable under the A0 preservation floor. Any
later privacy/legal erasure exception must explicitly supersede that freeze through an
audited incident decision. Derived Explorer databases, pages, indexes, and caches are
replaceable.

The operational record must allow a reviewer to answer who approved which exact source
SHAs, what was built, what was deployed, what was observed publicly, which policy was in
force, and how rollback or takedown changed presentation.
