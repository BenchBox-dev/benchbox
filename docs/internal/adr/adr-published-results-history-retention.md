<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ADR: Retain `published-results` git history after the 2026-08-04 corpus leak

```{tags} adr, privacy, operations
```

**Status:** Accepted — 2026-08-04; re-examined against pseudonym reversibility — 2026-08-05
**Scope:** the `published-results` branch only

## Context

Between 2026-08-03 and 2026-08-04 the public `published-results` branch served
the pre-#1467 corpus. Measured with the repository's own canonical detector
(`find_public_path_leaks`):

| | value |
|---|---|
| primary bundles carrying private absolute paths | **183 of 207** |
| bundles exposing the raw 16-hex internal `machine_id` | **206 of 207** |
| repository visibility | public |

The leaked material is **machine-local absolute paths** — home directories
containing a username, working directories, and Python executable paths — plus
the capture-side machine identifier, which is itself a salted SHA-256 and not a
hardware ID. No credentials, tokens, keys, connection strings, or customer data
were exposed; those are redacted on a separate path that was working correctly.

The tip was corrected by #1535, which mirrored develop's sanitized corpus.
Verified after merge: 0 leaking files across 400, and all 206 `machine_id`
values are 12-hex public pseudonyms.

The commits carrying the old content remain reachable in that branch's history.

## Decision

**Retain the history. Do not rewrite `published-results`.**

## Rationale

1. **Rewriting does not retract.** The content has been public and cloneable.
   A force-push cannot recall clones, forks, GitHub's cached commit views, or
   anything already fetched or indexed. It converts a known exposure into an
   exposure plus a false belief that it was undone.
2. **The exposure class is low severity.** Usernames and directory layout, not
   secrets. The realistic harm is minor deanonymization of contributors whose
   bundles are in the corpus — and the corpus is authored by the maintainer.
3. **The cost is real and borne by others.** A force-push breaks every existing
   clone and fork of a branch whose entire purpose is to be consumed by
   outside contributors, and by the Explorer build pipeline.
4. **Rotation is available and cheaper.** Pseudonym identity can be rotated by
   re-deriving the corpus under a new `machine_id_salt`, which changes every
   published pseudonym without touching history. See the "Salt rotation"
   section of `docs/reference/result-formats.md`.

## Consequences

- The pre-#1467 values stay retrievable by commit SHA on a public branch. This
  is accepted, not overlooked.
- If material of a **higher** severity class (a credential, a token, customer
  data) is ever found on that branch, this decision does not apply: that is a
  rotate-the-secret-and-then-decide situation, and rewriting may be justified
  because the secret can actually be invalidated.
- The detection gap that allowed this is addressed separately by
  `.github/workflows/corpus-drift-check.yml`, a **scheduled** canary. It is
  scheduled rather than push-triggered because the root cause was lost push
  events, not a bad path filter — see that workflow's header for the evidence.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| `git filter-repo` + force-push | Does not retract already-public content; breaks all clones and forks; disproportionate for path/username exposure |
| Delete and recreate the branch | Same non-retraction problem, plus loses the mirror audit trail linking each corpus state to a develop SHA |
| Make the repository private | Defeats the purpose of a public results corpus |

## Amendment 2026-08-05: re-examination after confirmation-oracle finding

### Premise gap

The 2026-08-04 decision treated the public pseudonyms as irreversible
identifiers (a salted SHA-256 of machine-local material). After that decision
was recorded, dictionary recovery established that the empty default salt makes
those pseudonyms a **confirmation oracle**: a candidate value can be matched
against the corpus with certainty, no server involved. The original retention
rationale therefore understated the residual exposure class for values that
remain reachable in `published-results` history.

This amendment records that re-examination. It does **not** rewrite the
2026-08-04 rationale retroactively; what was known at decision time stays as
written above.

### Re-examination outcome

**Retention still holds. Do not rewrite `published-results`.**

| Factor | Effect on retention |
|---|---|
| Confirmation oracle / recoverability | Raises residual risk for history commits that still carry empty-salt pseudonyms and pre-#1467 plaintext paths |
| Retractability | Unchanged: force-push still does not un-publish clones, forks, or cached views |
| Blast radius of rewrite | Unchanged: breaks every consumer of the public corpus branch |
| Severity class | Still path/username/low-entropy product identifiers for maintainer-authored seed content — not credentials or customer data |
| Forward mitigation already landed | Unread identifier fields dropped at the publication boundary (#1578 / tip mirror #1583); residual oracle on retained fields documented (`adr-published-identifier-field-set` salt decision 2026-08-05); scheduled directional drift canary + published-tip privacy scan |

The oracle finding changes *what history contains* (recoverable confirmations,
not just opaque digests), not *whether rewriting undoes publication*. Because
rewriting still fails the retraction test and still taxes every external
consumer, history remains retained. Forward exposure is reduced by omitting
unread fields and by requiring operators who publish community submissions to
configure a non-empty deployment-private salt.

### If this were reversed

A decision to rewrite would be a **separate change** with its own blast radius:
`git filter-repo` (or branch recreate), coordinated consumer re-clones, Explorer
pipeline pin updates, and an explicit higher-severity justification. That plan
is out of scope for this amendment; open a dedicated item if severity class
escalates.

### Verification of this amendment

- This section names reversibility / recovery as weighed factors.
- The original severity-class scoping sentence remains above (regression rung).

## Amendment 2026-10-31: the non-empty private salt is automatic

### Decision

The forward requirement above, that operators who publish community
submissions use a non-empty deployment-private salt, is now met by default.
`benchbox submit`, `benchbox export` and `benchbox publish run` resolve the
salt in this order: an explicit value passed in code, the
`BENCHBOX_MACHINE_ID_SALT` environment variable, an existing
`~/.benchbox/submission_salt`, and otherwise generate 64 random hex characters,
persist them there with mode `0600`, and reuse them. The variable is now only an
override: to share one salt across machines, to match the CI secret, or to rotate.

This supersedes the keep-empty decision of the retained-field salt amendment
(#1585) for the publication commands. It does not reintroduce a repository-baked
salt, which stays rejected: every installation generates its own value, so the
value is never public in git. The empty default remains only for library callers
that never publish (`ResultExporter` defaults, run capture, the Explorer
pipeline and the CPU attestation backfill); those paths read neither the file
nor generate one, which keeps corpus publication digests deterministic.

### Storage location

`~/.benchbox/submission_salt` is a durable choice. It lives beside the other
per-user BenchBox state, and it is the name an existing maintainer installation
already uses, so that installation's value becomes its automatic salt without a
move. The location follows `HOME`; there is no separate path variable.

### Corpus tools never generate

`scripts/stage_seed_corpus_bundle.py` and
`_project/scripts/results_explorer_corpus_migrate.py` use the variable, then an
existing salt file, and otherwise refuse. The salt feeds the permanent
`result_id` of staged bundles (a digest of the staged bytes), so a silently
generated salt would fork corpus identity. The CI seed-corpus lane keeps passing
its secret; a runner has no salt file, so a missing secret still fails closed.

### Trade-offs

- Per-install salts mean the same raw path or host yields different pseudonyms
  on different machines. No code compares pseudonyms across bundles or requires
  the CI and maintainer salts to match.
- An ephemeral `HOME` (a fresh container) generates a new salt each run, which
  changes the bundle bytes and therefore the hosted-submission idempotency key.
- Rotating the salt changes the `result_id` of every re-staged result. Tokens
  already in published bundles pass through unchanged under any salt, so the
  existing corpus is not rewritten.
- The residual confirmation oracle on history that already carries empty-salt
  pseudonyms is unchanged by this amendment; history remains retained.
