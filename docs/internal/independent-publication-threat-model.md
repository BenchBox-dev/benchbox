# Independent publication threat model

> **Historical.** The publication control plane this page describes has been retired.
> benchbox.dev is built and deployed from `BenchBox-dev/benchbox-site`; see
> [`adr-site-repo-split.md`](adr/adr-site-repo-split.md). The workflows, scripts, keys and
> secrets named below no longer exist. The authority rules still apply: acceptance into
> `published-results` is not publication, and only a deploy receipt proves what is live.

This threat model implements the accepted authority contract in
[`adr/adr-independent-publication-authorities.md`](adr/adr-independent-publication-authorities.md).
It covers the static publication control plane, accepted corpus inputs, artifacts,
deployment, observation, rollback, and takedown. Contributor result data is untrusted
even after schema validation. Trusted code and policy live on `develop`; the accepted
archive lives on `published-results`.

## Assets and trust boundaries

| Asset or boundary | Trust rule |
|---|---|
| Accepted bundles and sidecars | Attacker-controlled data. Parse as data only, enforce size/schema/hash limits, and never execute embedded paths, markup, SQL, URLs, or commands. |
| Pull-request branch and merge commit | Untrusted until the protected target's checks and required human review complete. A PR must not gain secrets or production write credentials. |
| Explorer builder, validators, admission policy, manifest schema, and attestor | Trusted control-plane code pinned from an exact reviewed `develop` SHA. |
| `published-results` archive | Corpus acceptance authority. A manifest consumes one exact SHA, not a moving branch ref. Acceptance does not imply trust, ranking, visibility, or liveness. |
| Candidate bundle | Immutable artifact selected by ID. Its producer, source pins, manifest, unpacked tree, target, parent, and required route checksums are validated before approval. |
| Publication manifest | Desired-state content inside the candidate bundle. It pins source SHAs, policy version, builder identity, artifact expectations, target, and parent. |
| Build artifact and provenance | Immutable output bound to the manifest and builder. Artifact existence or build success is not proof of deployment. |
| Deployment credentials and GitHub tokens | Least-privilege secrets available only to trusted post-merge jobs. They must not be exposed to pull-request code or recursively trigger an equivalent privileged workflow. |
| Live receipt signing key | Isolated attestation credential. It signs only fresh public observations that match the intended manifest and artifact. |
| Transaction journal (`publication` ref) | Single state authority for monotonic generation reservations, durable transactions, and head advancement under non-forced Git CAS. |
| Recovery watchdog | Read-only observer. It reports aged or unresolved work; protected transaction workflows perform every write and restoration. |
| Withdrawal and ranking fields | Maintainer-controlled policy data. Contributor payloads cannot set or promote them. |
| Audit evidence | Append-only manifests, provenance, deploy acknowledgements, receipts, approvals, withdrawal events, and rollback events retained outside replaceable read models. |

## Required threat cases and controls

### Pull-request code execution and malicious data

**Threat:** A contributor modifies executable validators, build helpers, workflow-like
configuration, or crafted bundle content so validation executes attacker code or leaks a
secret.

**Controls:**

- Data-only corpus changes run without repository or deployment write tokens.
- Trusted executable code and admission-policy changes land on `develop`, require manual
  maintainer review, and may not auto-merge.
- The trusted build checks out code and accepted data by separate exact SHAs. It does not
  execute code from `published-results` or a contributor head branch.
- Bundle processing is non-executing and fail-closed for unknown fields that cross an
  executable boundary, path traversal, symlinks, archive expansion, active markup,
  network fetches, and resource-limit violations.
- A valid result merged to `published-results` becomes publication input by default, but
  visibility, trust, withdrawal, and ranking policy still govern presentation.

### GitHub token recursion and confused deputy behavior

**Threat:** A token-bearing promotion job causes another workflow or bot to run with
broader credentials, bypass review, recursively publish, or approve its own output.

**Controls:**

- Pull-request validation receives no publication secret.
- Promotion credentials are scoped to one target and cannot alter trust policy, branch
  protection, workflow definitions, or receipt-verification rules.
- Events created by a publication token do not constitute a fresh approval and cannot
  recursively invoke an equivalent privileged promotion.
- Every privileged action binds actor, reviewed manifest digest, target generation, and
  source SHAs. Reusable components receive explicit immutable inputs, not ambient branch
  names or event payload authority.
- Trust/workflow policy changes always require manual review and may not auto-merge.

### Provenance substitution and moving references

**Threat:** A branch moves after approval, an artifact is swapped, or a deployment uses
bytes not produced from the reviewed manifest.

**Controls:** The operator supplies one immutable artifact ID. Trusted preparation binds the
producer run, workflow path and revision, exact `develop` and `published-results` SHAs,
manifest digest, archive digest, unpacked site-tree digest, target, parent, and complete
required route checksums before approval. Deployment revalidates the same artifact after
approval. The attested live receipt binds it to public route observations. A branch name,
workflow run number, or mutable artifact label is never sufficient.

### Promotion races and stale completion

**Threat:** Two promotions race, a slower older deployment completes last, or a stale
receipt marks superseded content current.

**Controls:** Each target has a monotonic generation and compare-and-set transition.
Promotion reserves the expected generation before deploy. Observation accepts a receipt
only for the still-current desired generation. Late deployments or receipts are recorded
for audit but cannot replace the current generation. Only one authorized maintainer is
required to approve a promotion, but the state machine prevents self-racing updates.

### Rollback confusion and downgrade

**Threat:** An operator rolls back to unverified bytes, rebuilds with changed dependencies,
or labels a provider acknowledgement as recovered service.

**Controls:** Rollback chooses a previously attested manifest and exact artifact digest.
The rollback operation emits a new event referencing the prior live receipt and requires
fresh public probes plus a new live receipt. The previous receipt remains immutable.
Rollback is bounded to the last known-good receipt and cannot change policy, accept new
corpus input, or select an unattested artifact. The watchdog cannot initiate rollback.

### Takedown abuse, delay, and resurrection

**Threat:** Malicious or mistaken takedown hides valid results, a two-person dependency
delays urgent privacy response, or a later promotion resurrects withdrawn data.

**Controls:** One authorized maintainer may order emergency takedown. The event records
actor, reason, scope, timestamp, and affected identifiers. Candidate presentation fails
closed: `withdrawal_requested` results are excluded from the candidate visible and ranking
views before republish. Desired state carries the withdrawal request forward, so ordinary
promotions cannot resurrect it. The result is recorded as `withdrawn` only after a matching
live receipt confirms suppression at the public endpoint.
The accepted archive and audit history are not rewritten by a visibility takedown.
During the A0 migration freeze, a privacy or legal incident triggers an immediate
withdrawal request and candidate/artifact-access suppression, not accepted-source deletion.
Any later irreversible erasure requires a separately approved incident plan covering Git
history, workflow artifacts, caches, mirrors, inventory effects, and non-sensitive audit
retention.

### Receipt forgery and false liveness

**Threat:** Internal build or deploy state is presented as proof that users can reach the
new publication, or an attacker replays an old observation.

**Controls:** Only an attested live receipt proves publication. The receipt includes the
manifest and artifact digests, target, generation, required route observations, response
digests or semantic checks, observation time, freshness window, nonce, and attestor
identity. It is issued only after public-network probes. Expired, mismatched, replayed,
partial, or internally sourced observations cannot transition state to `live`.

### Lost-request ambiguity and late provider activation

**Threat:** A provider request POST succeeds or remains queued on the hosting service,
but the HTTP response is dropped or the initiating runner crashes. If compensation
executes without knowing provider state, the unresolved write may activate late,
overwriting newer content.

**Controls:** Durable intent is recorded before submission and the unique write-intent commit
OID is passed as `pages_build_version` for provider correlation. On indeterminate request
results, the transaction enters `recovery-required` and blocks new work. The watchdog reports
the state; it cannot compensate without a proven provider finality condition.

### Stale-writer fencing and concurrency displacement

**Threat:** A delayed or partitioned runner wakes up and executes an obsolete deployment or
advances the journal after a newer run has taken ownership.

**Controls:** Pages deployment jobs serialize through `pages-deploy` concurrency with
`cancel-in-progress: false`. Every state transition validates current journal state and epoch
under non-forced Git CAS. This coordinates the workflow but is not a provider activation fence.
Stale worker acknowledgements are recorded as audit evidence only and cannot move the durable head.

### Archive extraction and container confinement

**Threat:** Candidate build archives contain malicious path traversals, symlinks, or
unexpected files that escape build directories or contaminate other publication lanes.

**Controls:** Build candidate extraction verifies archive SHA-256 and unpacked tree digests,
strictly rejecting path traversals, symlinks, and oversized files. Lane builds execute within
materialized source closures with other lane inputs inaccessible. Candidate archives are
never executed as code.

### Watchdog privilege boundaries and runner loss

**Threat:** An out-of-band recovery workflow (`publication-recover.yml`) has broad privileges
that could be exploited by untrusted PR events or forge maintainer approvals.

**Controls:** Watchdog scanning is strictly read-only (`contents: read, actions: read`). It
authenticates workflow provenance, ignores pull-request events, reports operations older than
ten minutes, and cannot fabricate approvals, journal writes, Pages writes, or compensation.

## Security invariants

1. No untrusted pull-request code receives a production or receipt-signing credential.
2. No merge, build, artifact, provider deployment record, or branch tip is called live.
3. No publication consumes an unpinned `published-results` or `develop` reference.
4. No presentation policy creates a second accepted-corpus authority.
5. No trust, workflow, credential, receipt, withdrawal, or ranking-policy change
   auto-merges.
6. One authorized maintainer can promote and can perform emergency takedown.
7. Rollback and recovery end only after a fresh attested live receipt.
8. Accepted archive objects and append-only audit evidence remain recoverable under the
   A0 preservation floor.
