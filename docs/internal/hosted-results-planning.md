# Hosted results: Phase 3 planning notes

Maintainer planning that accompanies the [hosted results contract](https://benchbox.dev/docs/reference/hosted-results-contract.html).
The hosted API and self-service submission (Phase 3) are built only if Phase 2
pull-request volume exceeds maintainer capacity.

## Open questions (Phase 3)

The following questions are explicitly deferred to Phase 3 design and are not
resolved by this contract:

- **`public_result_id` collision resolution (Phase 3 only):** Concurrent mint
  strategy when two API submissions race to the same content-addressed id.
  Phase 1 static publication already fails closed on distinct content and
  skips identical content (see §1.1 and the [public result id permanence ADR](https://benchbox.dev/docs/development/adr/adr-public-result-id-permanence.html)).
- **Idempotency semantics:** Re-submitting a bundle that is already in
  `pending` or `validated` state - return `200 OK` or `409 Conflict`?
- **Cross-visibility cohort comparisons:** Whether results in different
  visibility states (e.g., `private` and `public-curated`) may be included in
  the same compare cohort.
- **Private result access control model:** Submitter-only access, or
  organisation-based sharing (e.g., all members of the submitter's GitHub org)?
- **Ranking eligibility rules for mixed-visibility cohorts:** Whether a cohort
  containing both `public-self-reported` and `public-curated` results is
  eligible for ranked display, and which rules govern it.

## Phase assignment summary

| Feature / Rule | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| `bundle_hash` computed and tracked | No | Yes (CI check) | Yes (API) |
| `submission_id` issued | No | No | Yes |
| `public_result_id` minted | Yes | Yes | Yes, except never-public `private` results |
| Acceptance state: `pending` / `validated` | No | Yes (PR states) | Yes |
| Acceptance state: `accepted` | Yes | Yes | Yes |
| Promotion state: `promotion_pending` / `live` / `promotion_failed` | Yes | Yes | Yes |
| Acceptance state: `rejected` | Admin only | Yes | Yes |
| Presentation state: `withdrawal_requested` / `withdrawn` / `readmission_requested` | Admin only | Yes | Yes |
| Visibility: `public-curated` | Yes | Yes | Yes |
| Visibility: `public-self-reported` | No | Yes | Yes |
| Visibility: `private` / `unlisted` | No | No | Yes |
| Trust label: Maintainer Run | Yes | Yes | Yes |
| Trust label: Community Submission | No | Yes | Yes |
| Trust label: Verified | No | No | Reserved |
| Hard block cohort enforcement | Yes (client-side) | Yes | Yes |
| Soft warning cohort display | Yes | Yes | Yes |
| Ranking eligibility rules | Yes (all curated) | Yes | Yes |
| Redactable fields supported | No | Partial (no auth) | Yes |
| Presentation withdrawal request | Admin policy operation | Admin or PR close before acceptance | Self-service API |
| Stable tombstone on withdrawal | Yes | Yes | Yes for minted public IDs; no for never-public `private` results |
| `benchbox submit --output` | No | Yes | Yes |
| `benchbox submit` → API | No | No | Yes |
| Status polling | No | No | Yes |
