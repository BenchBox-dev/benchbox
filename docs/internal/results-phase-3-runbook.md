# BenchBox Results Platform - Operational Runbook

**Created:** 2026-04-01
**Phase scope:** Phase 3 (Hosted API at `api.benchbox.dev`)
**Prerequisite reading:** `docs/reference/threat-model.md` and the [hosted
results contract](https://benchbox.dev/docs/reference/hosted-results-contract.html)

Phase 1 and Phase 2 have no hosted services. This runbook applies exclusively to
Phase 3. Until Phase 3 launches, file GitHub issues for any anomalies found in
the static explorer or PR-based submission pipeline.

> **Not implemented yet.** The CLI surfaces described below - `benchbox admin
> ...`, `benchbox result withdraw`, and `benchbox setup --service` - are Phase 3
> designs, not part of the shipped CLI. Hosted submission credentials are
> managed today with `benchbox auth login --service <url>`; see
> `benchbox auth --help`.

---

## w4 - Auth Model

### Token Type: API Keys

Phase 3 uses API keys, not OAuth, for initial launch.

**Rationale:** BenchBox submissions originate from the CLI (`benchbox submit`),
not a browser. OAuth requires a redirect flow that has no natural place in a
terminal workflow. API keys are simpler to implement, sufficient for the
use-case volume anticipated at Phase 3 launch, and straightforward to revoke.
OAuth can be added later if organization/team features require browser-based
identity.

### Token Scopes

| Scope | Grants |
|---|---|
| `submit:write` | POST to `/submissions`; upload bundles; view own submission status |
| `result:manage` | Withdraw or update metadata on own results; cannot affect other actors' results |

Admin operations (trust promotion, forced withdrawal, user management) require
a separate admin token issued only to maintainers. Admin tokens are not
distributable via `benchbox setup`.

### Session Lifetime

API keys do not expire by default. However, a maximum key lifetime of 12 months
is recommended. After 12 months, the API returns `401 Unauthorized` with
`{"error": "token_expired", "message": "API key expired; run benchbox setup --service to provision a new token"}`.
A renewal prompt is shown in the CLI 30 days before expiry. This directly
mitigates the Med-High spoofing/impersonation risk identified in the threat
model by limiting the window of exposure for compromised keys.

Admins can revoke any token at any time via
the admin CLI (`benchbox admin revoke-token --actor-id X`). Revocation takes
effect immediately - the token is removed from the validation store.

Actors are responsible for rotating their own tokens. The provisioning flow
makes it easy to generate a replacement token without losing submission history.

### First-Time Provisioning

```
benchbox setup --service
```

1. Prompts: "Paste your BenchBox API token:" (token obtained from benchbox.dev/account)
2. Validates the token against the API (single lightweight `GET /me` call)
3. Stores the token under `~/.benchbox/credentials.yaml`:
   ```yaml
   service:
     token: bbx_<token>
     endpoint: https://api.benchbox.dev
   ```
4. Prints: "Token stored. Ready to submit with `benchbox submit`."

The credentials file must be chmod 600. `benchbox setup` enforces this on write
and warns if the file is world-readable on subsequent runs.

---

## w5 - Rate Limiting and Quota Model

### Per-Actor Limits

| Limit | Value | Scope |
|---|---|---|
| Burst rate | 10 submissions | Any rolling 60-second window |
| Daily cap | 50 submissions | Per actor, per calendar day (UTC) |
| Storage quota | 500 MB total | Raw bundles; cumulative across all submissions |
| Bundle size cap | 50 MB | Per single submission |

### Grace Period for New Actors

Actors in their first 7 days after token issuance receive:
- 3× burst budget (30 submissions per 60-second window)
- Elevated storage quota of 2 GB (instead of the standard 500 MB; facilitates
  initial corpus seeding while preventing unbounded abuse)
- Same daily cap (50/day) - the grace period is about burst flexibility, not
  unlimited daily volume

After 7 days the standard limits apply automatically with no action required
from the actor.

**Grace-period abuse prevention:** Auto-revoke any API key provisioned from
an IP address that has spawned ≥5 new accounts in 24 hours or ≥20 new accounts
in 7 days. This check is enforced at token provisioning time, not retroactively.

### Limit Breach Responses

| Limit Hit | HTTP Status | Response Body |
|---|---|---|
| Burst exceeded | 429 Too Many Requests | `{"error": "rate_limit_exceeded", "retry_after": <seconds>}` + `Retry-After` header |
| Daily cap hit | 429 Too Many Requests | `{"error": "daily_limit_exceeded", "message": "daily submission limit reached; resets at 00:00 UTC"}` |
| Storage quota exceeded | 413 Content Too Large | `{"error": "storage_quota_exceeded", "message": "storage quota exceeded; contact support to discuss an increase"}` |
| Bundle size cap exceeded | 413 Content Too Large | `{"error": "bundle_too_large", "max_bytes": 52428800}` |

The API does not queue submissions when limits are hit. Submissions are rejected
immediately so actors can see the error and retry at the correct time.

Applied-tuning receipt companions have two additional validation bounds:
8 MiB for the complete `.applied.json` file and 10,000 entries in its
`receipt.entries` list. Both limits are intentionally far above realistic runs.
Submissions that exceed either bound fail validation rather than being silently
truncated. As a defense for legacy or manually published inputs, the Explorer
pipeline caps the same fields and records `truncated`, `truncation_reason`, and
the original byte or entry count in the stored receipt.

---

## w6 - Moderation Workflow

### Trust Tiers

| Trust Label | Meaning | How Assigned |
|---|---|---|
| `self-reported` | Actor-submitted; not independently verified | Automatic on successful ingest |
| `public-curated` | Reviewed and approved by a maintainer | Manual promotion via admin CLI |
| `rejected` | Failed validation or maintainer review | Set by ingest pipeline or admin |

Withdrawal is an orthogonal presentation state, not a trust tier. The API or admin CLI
records `withdrawal_requested`; only a matching live receipt records `withdrawn`.

### Trust Promotion Path (self-reported → curated)

1. Maintainer identifies a candidate submission (manual discovery or triggered
   by `benchbox admin review --submission-id X`).
2. Runs the validation suite:
   - Schema conformance: bundle matches schema-v2 specification
   - Bundle integrity: server-stored hash matches bundle content
   - Cohort compatibility: benchmark, scale factor, and execution mode are
     consistent with existing cohort members
   - Sanity checks: no implausible timings (e.g., sub-millisecond TPC-DS),
     valid platform identifier, valid query count
3. If all checks pass, maintainer approves:
   ```
   benchbox admin promote --submission-id X --trust public-curated
   ```
   This updates the trust label in the metadata DB and triggers a read model
   rebuild so the result appears in the curated index.
4. If any check fails, maintainer rejects:
   ```
   benchbox admin reject --submission-id X --reason schema_invalid|cohort_mismatch|sanity_fail|manual_review
   ```
   Rejection is valid only before archive acceptance. Status is set to `rejected`
   with the reason code. The actor receives an email notification (if contact on
   file). Unaccepted staging data is retained for 30 days, then purged by staging
   lifecycle policy. Once a bundle is accepted, maintainers must use presentation
   withdrawal; rejection must never purge accepted source bytes.

### Takedown Process

**Actor-initiated withdrawal** (own results only):

```
benchbox result withdraw --submission-id X
```

- Records `withdrawal_requested` immediately and excludes the result from the candidate
  public and ranking read models
- Reserves, deploys, and probes a new generation (target: < 15 minutes); status becomes
  `withdrawn` only when a matching live receipt confirms public suppression
- Accepted source bytes remain in the accepted archive indefinitely. Withdrawal
  suppresses presentation and ranking only; it does not start a purge clock.
  Unaccepted staging data may be retained for 90 days in case the actor wants to
  resubmit after correcting an error, then purged by staging lifecycle policy.

**Admin-forced withdrawal** (any result; triggered by abuse, false data, or
legal request):

```
benchbox admin withdraw --submission-id X --reason abuse|false_data|legal|policy_violation
```

- Records `withdrawal_requested` with a force-withdrawal flag and reason code
- Triggers an immediate candidate-index rebuild and controlled deployment; status becomes
  `withdrawn` only after the matching live receipt confirms public suppression
- Actor is notified by email within 24 hours with the reason code
- Accepted source bytes remain in the accepted archive indefinitely for audit and
  lineage. A 180-day purge applies only to unaccepted staging data. Deleting an
  accepted bundle requires the separately approved erasure exception and evidence
  plan defined by the independent-publication operations contract.

### Audit Log Schema

Every state-changing event is appended to the audit log. The log is
append-only; no entries may be modified or deleted. Stored outside the
metadata DB (write-once object store or managed audit logging service).

```json
{
  "event_id": "evt_01J...",
  "timestamp_utc": "2026-04-01T12:34:56.789Z",
  "actor_id": "actor_abc123",
  "action": "submitted",
  "target_submission_id": "sub_xyz789",
  "target_bundle_hash": "sha256:deadbeef...",
  "reason_code": null,
  "metadata": {}
}
```

**Valid `action` values:**

| Action | Triggered By |
|---|---|
| `submitted` | Actor via submission API |
| `validated` | Ingest pipeline (automated) |
| `accepted` | Ingest pipeline commits validator-clean input to the accepted archive |
| `promotion-requested` | Publication controller reserves a target generation |
| `promotion-failed` | Build, deployment, or observation ends without a matching receipt |
| `live-receipt-issued` | Attestor confirms the target generation at the public endpoint |
| `rejected` | Ingest pipeline or admin |
| `withdrawal-requested` | Actor or admin records desired presentation suppression |
| `withdrawn` | Attestor confirms suppression in a matching live receipt |
| `readmission-requested` | Admin records authorized desired restoration after receipt-confirmed withdrawal |
| `trust-promoted` | Admin |
| `trust-revoked` | Admin |
| `redacted` | Admin (sensitive field removed; bundle hash invalidated) |
| `purged` | Lifecycle policy or admin (bundle deleted from object store) |

---

## w7 - Platform SLOs

| Metric | Target | Measurement Method | Alert Threshold |
|---|---|---|---|
| Submission API ACK latency p99 | < 5 seconds | Measured at API gateway (synthetic probe every 60s) | Page if p99 > 10s for any 5-minute window |
| Explorer page load p95 | < 500 ms | GitHub Pages / CDN; real-user monitoring via Performance API | Page if p95 > 1000ms for any 5-minute window |
| Explorer DuckDB-WASM init time | < 3 seconds (p95) | Real-user monitoring; sampled 1% of page loads | Page if p95 > 6s for any 5-minute window |
| CI build + deploy end-to-end | < 10 minutes | GitHub Actions job duration | Page if any build exceeds 20 minutes |
| Backup RPO | < 24 hours | Age of last successful metadata DB and object store backup | Page if last backup age > 24 hours |
| Backup RTO | < 4 hours | Restore drill results (run quarterly) | Flag if drill exceeds 4 hours; schedule remediation |

**Alert routing:**
- All page-level alerts route to the on-call maintainer via PagerDuty or
  equivalent.
- Backup RPO breach alerts to all maintainers immediately (not on-call only).
- SLO breaches that persist > 30 minutes trigger a P1 incident (Class A or B,
  depending on nature).

**Restore drills:** Run quarterly against a staging environment. Document
restore duration in the audit log under `metadata: {drill: true}`. Drill
failures trigger a remediation ticket within one week.

---

## w8 - Incident Response Runbook

### Incident Severity Levels

| Severity | Meaning | Response Target |
|---|---|---|
| P1 (Critical) | Data breach or service fully unavailable | Acknowledge < 15 minutes; mitigate < 1 hour |
| P2 (High) | Result integrity compromised; partial outage | Acknowledge < 1 hour; mitigate < 4 hours |
| P3 (Medium) | Abuse campaign; elevated error rate; SLO at risk | Acknowledge < 4 hours; mitigate < 24 hours |
| P4 (Low) | Degraded performance within SLO; cosmetic issue | Acknowledge < 24 hours; schedule fix |

---

### Class A: Result Integrity Incident

**Examples:** Tampered bundle reaches the public index; hash verification fails
post-publish; incorrect query timings attributed to wrong platform.

**Severity:** P2 (High)

**Detection signals:**
- Automated bundle-hash re-verification job fails for a receipt-confirmed live result
- Community report via GitHub issue or email to security@benchbox.dev
- Audit log shows `live-receipt-issued` for a known-bad hash or without matching
  `accepted`, `promotion-requested`, and receipt-evidence events

**Response steps:**

1. **Request withdrawal of affected result(s) immediately** (target: < 1 hour from detection)
   ```
   benchbox admin withdraw --submission-id X --reason false_data
   ```
   Confirm the request is recorded and the candidate public-index rebuild is triggered. Do
   not claim public removal until external probes and a matching live receipt confirm it.

2. **Re-verify all results published in the same time window** (± 2 hours around
   the affected result's publish timestamp). Use:
   ```
   benchbox admin verify-window --from <ts> --to <ts>
   ```
   Any result that fails hash verification receives `withdrawal_requested` pending
   investigation and becomes `withdrawn` only after matching receipt confirmation.

3. **Identify root cause.** Three categories:
   - Storage tampering: compare bundle in object store against original ingest
     hash in audit log
   - Pipeline bug: compare ingest pipeline version at publish time against
     current version; check for known regressions
   - Bad bundle submitted by actor: verify actor's submitted hash matches
     the stored bundle; if mismatch, the ingest server failed to reject

4. **Notify affected submitters within 24 hours.** Email the actor whose result
   was affected. Include: which result, what was found, what action was taken.

5. **Post public notice** if ≥10 results were affected OR if trust was
   materially misrepresented (e.g., a `public-curated` result was found to be
   tampered). Post to benchbox.dev/blog or GitHub Discussions. Include: what
   happened, which results were affected (by submission_id), and what was done.

6. **File post-incident report** within 72 hours covering: timeline, root cause,
   affected results, remediation, and prevention measures.

---

### Class B: Data Breach

**Examples:** Private bundle or actor contact records exposed; API key database
leaked; misconfigured bucket ACL made private bundles world-readable.

**Severity:** P1 (Critical)

**Detection signals:**
- Anomalous access patterns in storage or metadata DB access logs
- External security researcher report to security@benchbox.dev
- Automated ACL drift alert (bucket policy changed unexpectedly)
- Unexpected download spike for private-prefix objects

**Response steps:**

1. **Triage: confirm breach with a second independent signal before mass
   revocation.** Correlate the initial detection signal with at least one
   independent source (e.g., cross-reference anomalous access patterns with
   audit logs, verify the report with a second team member, or confirm via a
   different monitoring channel). This step must complete within 30 minutes of
   detection. If the second signal confirms the breach, proceed to step 2. If
   the breach cannot be confirmed but also cannot be ruled out, proceed anyway
   and treat the incident as real until proven otherwise.

2. **Revoke all API keys and rotate service credentials** (target: < 1 hour
   from detection). This is a broad action - err on the side of revoking too
   many rather than too few.
   ```
   benchbox admin revoke-all-tokens --reason security_incident
   ```
   Rotate object store credentials, metadata DB credentials, and any service
   account keys. Re-deploy services with new credentials before re-enabling
   access.

   **Rollback for false positives:** If post-triage investigation determines the
   incident was a false positive, restore actor access promptly: re-enable
   revoked API keys (or direct affected actors to re-provision via
   `benchbox setup --service`), post a brief explanation to affected actors, and
   file an internal report documenting the false positive to improve detection
   accuracy.

3. **Audit access logs to determine exposure scope.** For each access log entry
   in the affected time window, identify: which resources were accessed, by
   which IP or credential, and whether the access was authorized. Document the
   scope in the incident report.

4. **Notify affected actors within 72 hours.** Legal requirement in most
   jurisdictions. Notification must include: what data was exposed, approximate
   time window, what BenchBox has done, and what actors should do (e.g.,
   consider rotating passwords if email was exposed). Coordinate with legal
   counsel if actor volume is large or if regulated data (PII) was involved.

5. **File incident report with full timeline and remediation steps.** Report
   must be retained for a minimum of 2 years.

6. **Re-enable actor access only after root cause is fixed and a security
   review confirms the breach vector is closed.** Do not rush this step.
   Actors whose keys were revoked are notified and directed to re-provision
   via `benchbox setup --service`.

---

### Class C: Abuse / Spam Campaign

**Examples:** Actor flooding platform with fake or duplicated benchmark results;
coordinated campaign to manipulate the public index; automated scraping that
triggers rate limit exhaustion for legitimate actors.

**Severity:** P3 (Medium)

**Detection signals:**
- Rate limit alert: single actor triggering burst limit repeatedly over hours
- Moderation queue spike: large volume of new submissions from one or few actors
- Community report: users notice implausible results or identical results
  submitted under different names
- Storage quota approaching exhaustion faster than expected

**Response steps:**

1. **Block the offending actor** (target: < 30 minutes from detection).
   ```
   benchbox admin block-actor --actor-id X --reason abuse
   ```
   This revokes the actor's token and adds them to the deny list. Future token
   requests from the same email or IP range are rejected.

2. **Withdraw all results from the offending actor.** Results submitted during
   an active abuse campaign cannot be considered trustworthy regardless of
   technical validity.
   ```
   benchbox admin withdraw-all --actor-id X --reason abuse
   ```
   A read model rebuild is triggered automatically.

3. **Review and tighten rate limits or eligibility rules** if the campaign
   exploited a gap. Examples: lower burst limit, add CAPTCHA to token
   provisioning, require email verification before first submission.

4. **No public notice required** unless results from the offending actor
   reached the public index (i.e., were promoted to `public-curated`) before
   detection. If so, post a brief public notice following the Class A process:
   what happened, which results were affected (by submission_id), and what
   was done to remove them.

---

### Contact Directory (On-Call)

Keep this current. Store the authoritative version in the private ops runbook,
not in this document.

| Role | Responsibility |
|---|---|
| On-call maintainer | First responder for all incident classes |
| Secondary maintainer | Backup if primary is unavailable |
| Legal counsel | Required for Class B if PII is exposed |
| security@benchbox.dev | Public intake for external reports; acknowledge within 72 hours, triage within 7 days |
