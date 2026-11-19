# Vendor publication terms: owner decisions

Date: 2026-11-19
Status: Decided by the owner on 2026-11-19. Every decision took the
recommended option.

## Context

[`docs/internal/results-vendor-publication-terms.md`](../../docs/internal/results-vendor-publication-terms.md)
records what each vendor's terms say about publishing benchmark results. It
ends with nine ambiguous clauses and eleven questions for the owner. This
document turns them into six decisions. Each one gives the relevant text, what
BenchBox does today, the options and a recommendation.

The terms record is a reading of texts, not legal advice, and so is this
document. The decisions set project policy: who records what, and what the
validator and checklist do.

## Already decided

These two questions were settled on 2026-11-17 and need no new decision.

- **Question 2, the submit-time check.** For BigQuery, Snowflake and
  Databricks, `plan_tier`, `vendor_notice` and `self_run: true` are mandatory
  for manifests dated on or after 2026-11-18. The check reads only what the
  submitter declares; it cannot see the real plan tier or features.
- **Question 3, the 151 earlier bundles.** They stay published and are
  labelled `pre_policy` in validator output. A field is filled only where a
  run record proves it, and none does. Withdrawal goes through the takedown
  process.

Ambiguous clause 3 (Databricks Beta) is also settled: every declared
Databricks preview feature is refused, with or without permission.

## D1. Who carries the vendor conditions for a published result

Covers questions 1 and 6.

**What the terms say.** All three vendors with published results attach
conditions to disclosure:

- **Google:** disclosure must include the information needed to replicate the
  test, and the customer allows Google to benchmark the customer's public
  products (reciprocity).
- **Snowflake:** results go to Snowflake "reasonably in advance" of
  disclosure, and disclosure includes replication information.
- **Databricks:** replication information, plus reciprocity.

AWS, ClickHouse Cloud and MotherDuck have similar reciprocity and replication
clauses. Each condition binds the customer: the account holder who ran the
test.

**What BenchBox does.** The bundle carries configuration and query text, which
the checklist treats as the replication information. The submitter declares
the plan tier, the notice and self-run, and the validator refuses missing or
disqualifying declarations. Nothing records reciprocity.

**Options.**

1. *Submitter carries them.* Each submitter is the customer of record, and
   satisfying the conditions is their responsibility. BenchBox records their
   declarations and refuses what the declarations disqualify. Publishing a
   result is not acceptance of reciprocity by the project.
2. *The project carries them.* The project reviews each result against each
   vendor's clauses before publication, and accepts reciprocity as the
   publisher.
3. *Per vendor.* The project takes on specific clauses for specific vendors
   and leaves the rest to submitters.

**Recommendation: option 1.** It matches who holds the account and who is
bound, and it is what the validator already enforces. Option 2 needs a manual
review step that no tool supports and makes the project party to agreements it
did not sign. For the project's own runs, the project is the submitter, so
option 1 still covers them (see D2).

Decision: option 1. The submitter carries the vendor conditions.

## D2. Snowflake advance notice for the project's own results

Covers question 5.

**What the terms say.** Snowflake AUP IV: "Customer may publicly disclose ...
the results of the Test as long as (1) Customer shares the Test results with
Snowflake reasonably in advance of its disclosure". Results involving Preview
features are confidential.

**What BenchBox does.** `vendor_notice` (a `sent_on` date and a `reference`)
is mandatory for new Snowflake manifests. The check confirms that a dated
reference exists; it does not judge "reasonably in advance". The 50 published
Snowflake results were all run by the project and record no notice.

**Options.**

1. *The submitter sends the notice and records it in `vendor_notice`.* For
   project runs, the owner sends it. Earlier results stay `pre_policy`.
2. As option 1, and also send Snowflake the 50 earlier results now, recording
   the date in the terms record.
3. As option 1, and also set a minimum gap (for example 14 days) between
   `sent_on` and publication, enforced by the validator.

**Recommendation: option 1.** It is what the mandatory field already requires.
Option 2 is a goodwill step the owner can take at any time without a code
change. Option 3 turns "reasonably in advance" into a number the clause does
not set.

Decision: option 1. The submitter sends the notice and records it in
`vendor_notice`; for project runs, the owner sends it.

## D3. Earlier risk acceptances: MotherDuck 13.1 and Databricks 7(b)

Covers question 4.

**What the terms say.**

- **MotherDuck MSA 13.1:** "MotherDuck's Confidential Information includes any
  technical or performance information about the Services."
- **Databricks AUP 7(b):** bars bulk or systematic export, or persistent
  copies, of "metadata generated by the Databricks Services".

On 2026-10-22 the owner accepted both risks so that public bundles carry
`platform_timing` for every platform. The texts read on 2026-11-16 have not
changed since.

**Options.**

1. Keep the 2026-10-22 acceptance.
2. Withdraw it for one or both: stop publishing `platform_timing` for that
   platform.

**Recommendation: option 1.** The texts are unchanged, so the basis for the
earlier decision is unchanged. No MotherDuck result is published yet; D5 also
covers MotherDuck.

Decision: option 1. The 2026-10-22 acceptance stands.

## D4. Self-run engines, including CedarDB

Covers questions 7 and 8, and ambiguous clause 9.

**What the terms say.** The licences of every self-run engine in the
corpus were read (Apache-2.0, MIT, BSD-3-Clause, the SQLite public-domain
dedication, and CedarDB Community Edition terms). None mentions benchmarks or
limits publishing performance results. CedarDB's Enterprise and Enterprise
Trial terms, and Exhibits 1 and 2 of its Community terms, could not be
reached.

**What BenchBox does.** No vendor check applies to self-run engines.

**Options.**

1. Rely on the licence texts. Record that the one published CedarDB result
   used the Community Edition, and do not pursue the Enterprise and Trial
   terms unless an Enterprise result is submitted.
2. As option 1, and also ask CedarDB for the unreachable terms now.

**Recommendation: option 1.** The licences contain no clause to act on. The
CedarDB edition needs one confirmation, from the bundle's run record or from
whoever ran it.

Decision: option 1. Rely on the licence texts. The published CedarDB bundle
(`tpch_sf001_cedardb_sql_20260825_095330_cf0d9e4d.json`) records a
self-hosted server on localhost with version `unknown` and no edition, so
the bundle alone does not show the edition; the owner confirms it.

## D5. Vendors with no published result yet

Covers questions 9 and 10, and ambiguous clauses 1, 2, 5, 6, 7 and 8.

**What the terms say.** Redshift, Athena, ClickHouse Cloud, MotherDuck and
Starburst Galaxy have no published result. Open points for them:

- Which ClickHouse Cloud Addendum is current: April 10 or September 17, 2026.
- The ClickHouse General Terms were not read.
- Whether the AWS and Google benchmark permissions reach Beta and Pre-GA
  features.
- Whether Starburst's confidentiality clauses reach performance results, and
  whether the Galaxy Early Access Agreement still applies.

**What BenchBox does.** Redshift, Athena, ClickHouse Cloud and MotherDuck get
warnings for a missing notice or plan tier, and refusals for free, trial or
preview use without permission. Starburst Galaxy has no check.

**Options.**

1. *Settle on arrival.* Settle each vendor when its first result is
   submitted: re-read its terms, resolve its open points, and decide whether
   its fields become mandatory as they did for BigQuery, Snowflake and
   Databricks. Until then, refuse validation for that vendor unless the owner
   has settled it.
2. *Settle now.* Resolve every open point now, before any result arrives.

**Recommendation: option 1.** The terms change (the AWS page changed twice in
November), so a reading done now would need repeating at arrival anyway.
Refusing an unsettled vendor makes sure the first result triggers the review.
That refusal is a small validator change, made only if this option is chosen.

Decision: option 1. Each vendor is settled when its first result is
submitted. Until then, `UNSETTLED_VENDOR_PLATFORMS` in
`benchbox/validation/bundle.py` refuses submissions for Redshift, Athena,
ClickHouse Cloud, MotherDuck and Starburst. Settling a vendor removes it
from that set.

## D6. Who owns the terms record, and how often it is re-read

Covers question 11.

**Options.**

1. The owner owns it. Re-read it before each minor release, and whenever a
   vendor in the corpus announces new terms.
2. The owner owns it. Re-read it every quarter.
3. No set cadence; re-read it only when a vendor is settled under D5.

**Recommendation: option 1.** Release preparation already has a checklist, so
the re-read gets a natural trigger and an owner. Re-reading quarterly would
not line up with when results are published.

Decision: option 1. The owner owns the record and re-reads it before each
minor release and when a vendor in the corpus announces new terms.
