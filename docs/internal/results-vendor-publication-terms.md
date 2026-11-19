# Vendor publication terms record

**Audience:** the project owner and maintainers who decide how published results relate to vendor terms.
**Scope:** per vendor in the published corpus, what the vendor's current terms say about publishing benchmark results, and what BenchBox does today. Internal, unpublished.

## Purpose and limits

This page records texts. It is not legal analysis. Nothing here says whether a clause applies to BenchBox, whether BenchBox or a submitter complies with it, or what the project should do. The owner's decisions of 2026-11-19 on the open questions are in [`vendor-publication-terms-2026-11-19.md`](../../_project/decisions/vendor-publication-terms-2026-11-19.md) (D1 to D6), and each section below names the one that applies. The owner owns this record and re-reads it before each minor release and when a vendor in the corpus announces new terms (D6).

Owner decision of 2026-11-13: static result pages include every published result in the meantime. Any withdrawal goes through the existing takedown process, not by editing pages. That process is documented in [`results-phase-3-runbook.md`](results-phase-3-runbook.md) (section "Takedown Process": `benchbox result withdraw`, `benchbox admin withdraw --reason ...`) and [`independent-publication-contract.md`](independent-publication-contract.md) (section "Emergency takedown").

Method: every quote below was read from the live vendor page on 2026-11-16. Quotes are verbatim except that typographic quotes and apostrophes are normalised to ASCII and `...` marks an omission. "Terms date" is the date the page itself shows, or `no date shown`. The 2026-10-22 comparison is against the "Terms check" table in `benchbox-site/docs/development/adr/adr-client-link-locality-disclosure.md`.

Corpus counts are `platform.name` in `results-data/bundles/*.json` on `develop` (4c848ffdd6), excluding `*.manifest.json` and `*.override.json`: 427 bundles.

## What BenchBox does (all vendors)

Source of the behaviour: `benchbox/validation/bundle.py` (`PLAN_TIERS`, `VENDOR_NOTICE_PLATFORMS`, `TIERED_PLATFORMS`, `SELF_RUN_PLATFORMS`, `vendor_platform_key`, `evaluate_vendor_conditions` at line 830, called during bundle validation at line 3258) and `benchbox/cli/commands/submit.py` (`_vendor_condition_fields`, `_enforce_vendor_conditions`). The user-facing description is `docs/vendor-publishing-checklist.md` in `benchbox-site`.

- Result schema 2.5 bundles can carry the same five fields in `provenance` (`benchbox run --plan-tier` and `--preview-feature` record the first two); the validator applies the same refusals to the bundle's own `provenance` and to a manifest, with the stricter declaration winning, and `benchbox submit` merges the bundle's fields with its options.
- `benchbox submit` writes `plan_tier`, `preview_features`, `vendor_permission`, `vendor_notice` and `self_run` into the manifest from CLI options. The validator reads the same fields.
- Refusal: `plan_tier` of `free` or `trial` (or `funding: free-trial`), or any `preview_features`, with no `vendor_permission`. On Databricks any `preview_features` entry refuses even with permission. `self_run: false` on BigQuery refuses. A malformed `vendor_notice` or an invalid `plan_tier` refuses. Since the owner decision of 2026-11-17 (section 6), BigQuery, Snowflake and Databricks manifests dated on or after `VENDOR_CONDITIONS_MANDATORY_FROM` (2026-11-18) are also refused when `plan_tier` (paid, free or trial), `vendor_notice` or `self_run: true` is missing.
- Refusal, owner decision D5 of 2026-11-19: Redshift, Athena, ClickHouse Cloud, MotherDuck and Starburst are in `UNSETTLED_VENDOR_PLATFORMS`, and every submission for them is refused until the owner settles that vendor's terms, which happens when its first result is submitted. The notice and `plan_tier` warnings below apply once a vendor is settled without mandatory fields.
- Warning: missing `vendor_notice` on Redshift, Athena, ClickHouse Cloud, MotherDuck; missing `plan_tier` on Redshift, Athena, ClickHouse Cloud, MotherDuck. For BigQuery, Snowflake and Databricks the warning is replaced by the mandatory-field refusal after the cutoff and by the `pre_policy` label before it.
- The checks read only what the submitter declares. They cannot see the real plan tier or features.
- Starburst matches `vendor_platform_key` only for the D5 refusal; it has no entry in the notice, tier or self-run maps. Platforms outside `_VENDOR_KEYWORDS` get no check.
- Replication information: bundles carry configuration and query text; the checklist treats that as the "disclosure includes what is needed to replicate" condition. Sending information to a vendor, notice in advance and reciprocity are manual steps with no check beyond the recorded `vendor_notice`.
- Published manifests in `results-data/bundles` for BigQuery (51), Snowflake (50) and Databricks (50): none records `plan_tier`, `preview_features`, `vendor_permission`, `vendor_notice` or `self_run`; all have `funding: unspecified`. Section 7 classifies all 151.
- Per the ADR, the owner decided on 2026-10-21 that public bundles carry `platform_timing` for every platform, confirmed 2026-10-22 for MotherDuck and Databricks, with the MotherDuck 13.1 and Databricks 7(b) risks recorded as accepted. This page does not revisit that.

## 1. Scope: vendors with published results

### Managed services (full rows in section 4)

| Platform | Bundles | Terms read (live) | Terms date shown | Section |
| --- | --- | --- | --- | --- |
| BigQuery | 51 | Google Cloud Service Specific Terms, General Service Terms 7 | Last modified April 2, 2026 | 4.1 |
| Snowflake | 50 | Acceptable Use Policy IV | Last Updated: February 3, 2025 | 4.2 |
| Databricks | 50 | Acceptable Use Policy; Free Edition AUP | Last modified: March 20, 2026; FE no date shown | 4.3 |

### Self-run engines, not managed services

These are software the submitter runs; there is no vendor service whose terms govern the run. Licence read from the project's official licence file on 2026-11-16. "Benchmark clause" means the fetched licence text was searched for "benchmark" and read for any restriction on publishing performance results.

| Platform (bundles) | Licence | URL read | Terms date | Benchmark / performance-publication clause |
| --- | --- | --- | --- | --- |
| CedarDB (1) | CedarDB Community Edition General Terms and Conditions (German binding, English for information, s.8); OSS notices page | https://cedardb.com/legal/agreements/community_tcs.pdf ; https://cedardb.com/docs/licensing ; https://cedardb.com/legal/oss_licenses | PDF: no date shown. Licensing page metadata: modified 2026-11-09 | None found in the Community terms (ss.1-8 read; extracted text is garbled bilingual layout and Exhibits 1 and 2 were not in it). OSS notices page: one match for "benchmark", "Some of the benchmark data in testdata/ is licensed differently", a third-party data notice. Enterprise and Enterprise Trial terms were not found and not read. Owner decision of 2026-11-17: publication of results from the CedarDB Community Edition is approved, on the terms at https://cedardb.com/legal/agreements/community_tcs.pdf. |
| StarRocks (1) | Apache-2.0 | https://raw.githubusercontent.com/StarRocks/starrocks/main/LICENSE.txt | none shown | None found |
| Apache Doris (3) | Apache-2.0 (file also bundles third-party licence texts, incl. MIT) | https://raw.githubusercontent.com/apache/doris/master/LICENSE.txt | none shown | None found |
| LakeSail (2) | Apache-2.0 | https://raw.githubusercontent.com/lakehq/sail/main/LICENSE | none shown | None found |
| DuckDB (51) | MIT, Copyright 2018-2026 Stichting DuckDB Foundation | https://raw.githubusercontent.com/duckdb/duckdb/main/LICENSE | none shown | None found |
| DuckLake (1) | MIT, Copyright 2018-2025 Stichting DuckDB Foundation | https://raw.githubusercontent.com/duckdb/ducklake/main/LICENSE | none shown | None found |
| ClickHouse Local (67), ClickHouse Server (1) | Apache-2.0, Copyright 2016-2026 ClickHouse, Inc. | https://raw.githubusercontent.com/ClickHouse/ClickHouse/master/LICENSE | none shown | None found |
| Spark (7), PySpark (2) | Apache-2.0 (file also lists third-party licences) | https://raw.githubusercontent.com/apache/spark/master/LICENSE | none shown | None found |
| DataFusion (63) | Apache-2.0 | https://raw.githubusercontent.com/apache/datafusion/main/LICENSE.txt | none shown | None found |
| Polars (25) | MIT, Copyright (c) 2025 Ritchie Vink | https://raw.githubusercontent.com/pola-rs/polars/main/LICENSE | none shown | None found |
| Pandas (17) | BSD-3-Clause | https://raw.githubusercontent.com/pandas-dev/pandas/main/LICENSE | none shown | None found |
| SQLite (35) | Public domain dedication | https://www.sqlite.org/copyright.html | none shown | None found. Text read: "Anyone is free to copy, modify, publish, use, compile, sell, or distribute the original SQLite code..." |

Apache-2.0 reference text also read: https://www.apache.org/licenses/LICENSE-2.0.txt (no "benchmark" match). The same ClickHouse licence file covers the open-source server and local binary, not ClickHouse Cloud (section 5.3).

Owner decision D4 (2026-11-19): rely on the licence texts. The published CedarDB bundle records no edition; the owner confirms it was the Community Edition, and the Enterprise and Trial terms are pursued only if an Enterprise result is submitted.

## 2. Terms differences since 2026-10-22

- Google Service Specific Terms: page shows "Last modified April 2, 2026" in the reader copy read on 2026-11-16; the live HTML read on 2026-11-17 shows "Last modified November 11, 2026"; the 2026-10-22 table recorded "modified 2026-10-12". Benchmarking clause substance matches the table (section 8).
- AWS Service Terms: the page read on 2026-11-16 showed "Last Updated: November 5, 2026"; the live page on 2026-11-17 shows "Last Updated: November 17, 2026" (table: 2026-10-13). Clause 1.8 as in the table in substance (section 8). AWS Customer Agreement "Last Updated: October 29, 2026" (read 2026-11-16); 11.9 and 11.2 as in the table in substance.
- ClickHouse Cloud Addendum: page shows "Effective Date: April 10, 2026"; a PDF "URL Version v20260917" shows "Effective Date: September 17, 2026". Both were read on 2026-11-17 (section 8.3). Which one is current is not established by ClickHouse's own pages.
- Snowflake AUP, Snowflake Terms of Service, Databricks AUP, Free Edition AUP, ClickHouse AUP, MotherDuck MSA, Starburst Galaxy Terms: no change in dates or in the clauses recorded.

## 3. Managed platforms with no published result in the corpus

"No published results in corpus as of 2026-11-16." BenchBox supports each (see `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS` above; Starburst has no check).

| Platform | Clause (verbatim, short) | URL | Terms date | Read | Owner decision |
| --- | --- | --- | --- | --- | --- |
| Redshift, Athena (incl. Athena Spark) | AWS Service Terms 1.8: "You may perform benchmarks or comparative tests or evaluations ... If you perform or disclose ... any Benchmark of any of the Services, you (i) will include in any disclosure, and will disclose to us, all information necessary to replicate such Benchmark, and (ii) agree that we may perform and disclose the results of Benchmarks of your products or services..." | https://aws.amazon.com/service-terms/ | Last Updated: November 5, 2026 | 2026-11-16 | D5: settle on first result; refused until then |
| ClickHouse Cloud | AUP 2: see 5.3 | https://clickhouse.com/legal/agreements/acceptable-use-policy | Effective date: July 16, 2026 | 2026-11-16 | D5: settle on first result; refused until then |
| MotherDuck | MSA 1.5: see 5.4 | https://motherduck.com/terms-of-service/ | Last Updated: 2026-10-05 | 2026-11-16 | D5: settle on first result; refused until then |
| Starburst Galaxy | No benchmark clause; see 5.5 | https://www.starburst.io/terms/galaxy-terms-of-service | no date shown | 2026-11-16 | D5: settle on first result; refused until then |

## 4. Managed platforms with published results

### 4.1 BigQuery (51 bundles)

- **Clause** (Google Cloud Service Specific Terms, General Service Terms, 7 Benchmarking): "Customer may itself (but may not permit a third party to): (a) conduct benchmark tests of the Services (each a "Test"); and (b) publicly disclose the results of such Tests only if (i) the public disclosure includes all necessary information to replicate the Tests, and (ii) Customer allows Google to conduct benchmark tests of Customer's publicly available products or services and publicly disclose the results of such tests. Notwithstanding the foregoing, Customer may not do either of the following on behalf of a hyperscale public cloud provider without Google's prior written consent: (A) conduct (directly or through a third party) any Test or (B) disclose the results of any such Test."
- **Related text** (Google Cloud Terms of Service 14.19, Confidential Information): "information that one party (or an Affiliate) discloses to the other party under or in connection with this Agreement, and which is marked as confidential or would normally under the circumstances be considered confidential information ... Customer Data is considered Customer's Confidential Information." Pre-GA Offerings are defined in Service Specific Terms 5 (no benchmark clause read there).
- **Source:** https://cloud.google.com/terms/service-terms ; https://cloud.google.com/terms
- **Terms date shown:** Service Specific Terms "Last modified April 2, 2026"; Cloud Terms of Service "Last modified March 24, 2026".
- **Date read:** 2026-11-16.
- **Change since 2026-10-22:** none in clause substance; date shown differs (section 2).
- **BenchBox today:** `SELF_RUN_PLATFORMS["bigquery"]` in `benchbox/validation/bundle.py`; `--self-run` in `benchbox/cli/commands/submit.py`. `self_run: false` refuses, missing warns. No check for reciprocity, no notice, no tier or preview check. Replication information: bundle config and query text. The 51 published manifests record none of the vendor fields.
- **Flags** (looks restrictive - owner to assess): "may not permit a third party to" conduct tests; reciprocity ("Customer allows Google to conduct benchmark tests of Customer's publicly available products"); prior written consent where a Test is done on behalf of a hyperscale public cloud provider.
- Owner decision D1 (2026-11-19): the submitter, as the account holder, carries the replication and reciprocity conditions; publishing is not acceptance by the project.

### 4.2 Snowflake (50 bundles)

- **Clause** (Acceptable Use Policy IV Benchmarking): "Subject to the use restrictions herein, Customer may conduct benchmark tests of the Service, including the Snowflake AI Features (each a "Test"). With the exception of Tests involving Previews, Customer may publicly disclose, or allow third parties to publicly disclose, the results of the Test as long as (1) Customer shares the Test results with Snowflake reasonably in advance of its disclosure, and (2) the disclosure includes all information necessary to replicate the Test. Test results involving Preview features are confidential and cannot be publicly disclosed without Snowflake's prior written approval."
- **Related text:** Terms of Service 4.2 Usage Data: "Snowflake may collect and use Usage Data to develop, improve, support, and operate its products and services. Snowflake may not share any Usage Data that includes Customer's Confidential Information with a third party except..." Terms of Service 14: Confidential Information includes "All Snowflake Technology and the terms and conditions of this Agreement (including ongoing discussions regarding the parties' relationship under this Agreement)". AUP VI: "in the event of any conflict between the Agreement and this Acceptable Use Policy, this Acceptable Use Policy shall govern."
- **Source:** https://www.snowflake.com/en/legal/addenda/acceptable-use-policy ; https://www.snowflake.com/en/legal/terms-of-service
- **Terms date shown:** AUP "Last Updated: February 3, 2025"; Terms of Service "Last Updated: April 16, 2026".
- **Date read:** 2026-11-16.
- **Change since 2026-10-22:** none.
- **BenchBox today:** Snowflake is in `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS`. Missing `vendor_notice` warns; `plan_tier` free or trial, or `preview_features`, without `vendor_permission` refuses; missing `plan_tier` warns. The check confirms a dated reference exists; it does not judge "reasonably in advance". The 50 published manifests record none of the vendor fields.
- **Flags** (looks restrictive - owner to assess): sharing results with Snowflake "reasonably in advance" of disclosure; prior written approval for results involving Preview features (confidential); "Subject to the use restrictions herein" qualifier; Snowflake Technology is Confidential Information under Terms of Service 14.
- Owner decisions D1 and D2 (2026-11-19): the submitter carries the conditions, sends the advance notice and records it in `vendor_notice`; for project runs the owner sends it. Earlier results stay `pre_policy`.

### 4.3 Databricks (50 bundles)

- **Clause** (Acceptable Use Policy, paid including trial, benchmark paragraph after item 7): "You may perform benchmark tests ("Benchmark") of the Databricks Services. Except with respect to Beta Services, you may disclose the results of the Benchmark. If you disclose, or cause the results of any Benchmark to be disclosed, you will include in such disclosure (i) all information necessary to replicate such Benchmark, and (ii) agree that we may perform and disclose the results of Benchmarks of your products or services included in such disclosure, irrespective of any restrictions on Benchmarks in the terms governing your products or services."
- **Clause** (Free Edition AUP, final paragraphs): "You may perform benchmark tests of the Free Edition Services, provided that you will not disclose the results of such tests unless expressly permitted by us."
- **Related text** (AUP item 7(b)): "except as expressly authorized under the Agreement, ... (b) scrape, bulk or systematically export, or create persistent copies of any metadata generated by the Databricks Services, which prohibition includes, without limitation, exfiltration of audit logs or credentials, or any actions resulting in a denial of service." Free Edition AUP prohibited-use 11: "to develop or offer a product or service made available to any third party that could reasonably be considered a substitute for that third party's possible subscription to or purchase of any Databricks product or service". The Free Edition Terms and the Agreement were not read.
- **Source:** https://www.databricks.com/legal/acceptable-use-policy ; https://www.databricks.com/legal/acceptable-use-policy-fe (index: https://www.databricks.com/legal/aup)
- **Terms date shown:** AUP "Last modified: March 20, 2026"; Free Edition AUP no date shown in body (page metadata: modified 06/10/2025).
- **Date read:** 2026-11-16.
- **Change since 2026-10-22:** none.
- **BenchBox today:** Databricks is in `TIERED_PLATFORMS` only. `plan_tier` free/trial without `vendor_permission` refuses; any `preview_features` entry refuses even with permission (message cites the AUP Beta exclusion); missing `plan_tier` warns. No notice check, no reciprocity check. Variants such as Databricks DataFrame match via `vendor_platform_key`. Bundles carry `platform_timing`. The 50 published manifests record none of the vendor fields.
- **Flags** (looks restrictive - owner to assess): AUP 7(b) on bulk or systematic export or persistent copies of "metadata generated by the Databricks Services"; Beta Services excluded from disclosure; reciprocity; Free Edition results disclosable only if "expressly permitted by us"; whether results from a trial plan fall under "paid (including trial)" AUP.
- Owner decisions D1 and D3 (2026-11-19): the submitter carries the conditions; the 2026-10-22 acceptance of 7(b) stands.

## 5. Managed platforms with no published result in the corpus

### 5.1 Redshift

- **Clause:** AWS Service Terms 1.8 (quoted in section 3). Beta: 2.3 "You may provide AWS with information relating to your access, use, testing, or evaluation of Beta Services or Beta Regions, including observations or information regarding the performance, features, and functionality of Beta Services or Beta Regions ("Test Observations"). AWS will own and may use and evaluate all Test Observations for its own purposes. You will not use any Test Observations except for your internal evaluation purposes of any Beta Service or Beta Region." 2.5 "Test Observations, Suggestions concerning a Beta Service or Beta Region, and any other information about or involving (including the existence of) any Beta Service or Beta Region are considered AWS Confidential Information."
- **Related text:** AWS Customer Agreement 11.9 "You will not issue any press release or make any other public communication with respect to this Agreement or your use of the Services or AWS Content." 11.2 "...the Service Terms will control over this document."
- **Source:** https://aws.amazon.com/service-terms/ ; https://aws.amazon.com/agreement/
- **Terms date shown:** Service Terms "Last Updated: November 5, 2026"; Customer Agreement "Last Updated: October 29, 2026".
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none in substance; dates moved (section 2). No Redshift-specific benchmark clause found in the Service Terms; the only other "benchmark" clause there concerns Oracle Software.
- **BenchBox today:** in `UNSETTLED_VENDOR_PLATFORMS` (every submission refused, D5), `VENDOR_NOTICE_PLATFORMS` (notice warning) and `TIERED_PLATFORMS` (preview/free/trial refusal). No published results.
- **Flags** (looks restrictive - owner to assess): information must also go "to us" (AWS); reciprocity; Beta/Preview observations are AWS Confidential Information and limited to "internal evaluation purposes"; Customer Agreement 11.9 publicity clause (which 11.2 subordinates to Service Terms).
- Owner decision D5 (2026-11-19): settle on the first submitted result; refused until then.

### 5.2 Athena (including Athena Spark)

Same texts, URLs, dates and BenchBox behaviour as 5.1 (`vendor_platform_key` maps `athena` variants). No Athena-specific benchmark clause found. Flags as 5.1. Owner decision D5 (2026-11-19): settle on the first submitted result; refused until then.

### 5.3 ClickHouse Cloud

- **Clause** (Acceptable Use Policy 2 Benchmarking): "you may perform benchmark, evaluation or comparative tests of the Services (each, a "Benchmark"), provided, however, that if you publish any Benchmark, or direct and/or permit any third party to publish any Benchmark (whether performed by you or a third party), you (i) must also disclose ... to ClickHouse and include in any public disclosure of the results of any such Benchmark, all information necessary to replicate such Benchmark and (ii) agree that ClickHouse may perform and disclose ... the results of Benchmarks of your products or services, to the extent they are in any way competitive with the Services ... The foregoing right to publish Benchmarks does not apply to any beta, preview, or early-access features of the Services. Results of any Benchmark involving such features are confidential and may not be publicly disclosed without ClickHouse's prior written consent."
- **Related text:** AUP 1.4 bars using "automated means (e.g., bots, scrapers) to access the Services or scrape, harvest or extract data or metadata (including, without limitation, audit logs or user credentials) from the Services except for automated or agentic interactions that are expressly identified in the applicable Documentation as supported by the Services ... or otherwise with ClickHouse's prior written consent". Cloud Addendum 2.4.2: "ClickHouse owns all right, title and interest in and to Usage Data".
- **Source:** https://clickhouse.com/legal/agreements/acceptable-use-policy ; https://clickhouse.com/legal/clickhouse-cloud-addendum
- **Terms date shown:** AUP "Effective date: July 16, 2026"; Cloud Addendum "Effective Date: April 10, 2026" (see section 2 on a v20260917 PDF).
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none in the clauses read.
- **BenchBox today:** in `UNSETTLED_VENDOR_PLATFORMS` (every submission refused, D5), `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS` (`clickhouse-cloud`; "clickhouse" plus "cloud" in the platform name). ClickHouse Local and Server bundles in the corpus are not matched. No published results.
- **Flags** (looks restrictive - owner to assess): disclosure "to ClickHouse"; reciprocity; beta/preview/early-access results confidential without prior written consent; AUP 1.4 metadata extraction wording.
- Owner decision D5 (2026-11-19): settle on the first submitted result, including which Cloud Addendum version is current; refused until then.

### 5.4 MotherDuck

- **Clause** (Master Subscription Agreement 1.5 Benchmarking): "If Customer performs or discloses, or directs or permits any third party to perform or disclose, any benchmarking of any of the Services, Customer (i) will include in any disclosure (including any public disclosure), and will disclose to MotherDuck, all data, software code, and other information necessary to enable MotherDuck or a third party to replicate such benchmark, and (ii) agrees that MotherDuck may perform and disclose the results of the benchmarks of Customer's products and services, irrespective of any restrictions on benchmarks in the terms governing Customer's products or services."
- **Related text:** 13.1 "MotherDuck's Confidential Information includes any technical or performance information about the Services." 15 Trials and Betas: "... their features and performance information are MotherDuck's Confidential Information." 3.2 Usage Data and 16 Publicity (MotherDuck may list the customer) read; no limit on the customer found there.
- **Source:** https://motherduck.com/terms-of-service/
- **Terms date shown:** "Last Updated: 2026-10-05".
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none.
- **BenchBox today:** in `UNSETTLED_VENDOR_PLATFORMS` (every submission refused, D5), `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS`. Free or trial `plan_tier` or any preview feature without `vendor_permission` refuses. The ADR records the owner's acceptance of 13.1 for public bundles. No published results.
- **Flags** (looks restrictive - owner to assess): 13.1 treating "any technical or performance information about the Services" as Confidential Information; 15 for Trials and Betas; disclosure of "all data, software code, and other information" to MotherDuck; reciprocity.
- Owner decisions D3 and D5 (2026-11-19): the 2026-10-22 acceptance of 13.1 stands; settle on the first submitted result; refused until then.

### 5.5 Starburst Galaxy

- **Clause:** no benchmark clause in the Galaxy Terms of Service (all eight sections read). Confidentiality, 4: "any information which relates to the other party's business affairs, trade secrets, technology, research, development, pricing or terms of this Agreement ("Confidential Information")"; 3: "The Service is the proprietary and confidential intellectual property of Starburst".
- **Source:** https://www.starburst.io/terms/galaxy-terms-of-service
- **Terms date shown:** no date shown.
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none.
- **BenchBox today:** in `UNSETTLED_VENDOR_PLATFORMS` (platform names containing "starburst"; every submission refused, D5); no entry in `VENDOR_NOTICE_PLATFORMS`, `TIERED_PLATFORMS` or `SELF_RUN_PLATFORMS`. No published results.
- **Flags** (looks restrictive - owner to assess): breadth of "relates to ... technology" in the Confidentiality clause (4); "proprietary and confidential" language about the Service in 3. Enterprise Software EULA and Galaxy Early Access Agreement not read.
- Owner decision D5 (2026-11-19): settle on the first submitted result, including the EULA and Early Access Agreement questions in section 9; refused until then.

## 6. Owner decisions of 2026-11-17 and how they are implemented

1. **Mandatory fields.** For BigQuery, Snowflake and Databricks, `plan_tier` (paid, free or trial; `unspecified` does not count), `vendor_notice` (valid `sent_on` and `reference`) and `self_run: true` are mandatory for a manifest dated on or after `VENDOR_CONDITIONS_MANDATORY_FROM = "2026-11-18"` in `benchbox/validation/bundle.py`. A missing field is refused, not warned. The constant is the date the change merges; it is a fixed date, not derived from the run.
2. **Manifest date.** The date is the later of the manifest `submitted_at` (written by `benchbox submit`) and the bundle's `run.timestamp` (read from the bundle at validation; a manifest cannot supply its own copy of it). A manifest with no date, or an unparseable one, is never exempt. A manifest that `benchbox submit` wrote (it carries `submission_tool_version` or `result_source: community`) must carry `submitted_at`; without it the manifest is never exempt, so deleting the field does not fall back to an older bundle date. A manifest without those markers is dated from the bundle only when it declares `result_source: internal` (maintainer-run, as all 151 corpus manifests do). The exemption is keyed on that date only, never on a missing field. Residual limit: the validator reads declarations, like every other vendor check, and `plan_tier`, `vendor_notice` and `self_run` can themselves be declared falsely, so a hand-written manifest that declares `internal` and states an old date is not detected.
3. **Existing corpus.** The 151 published results are labelled `pre_policy` ("published before vendor-condition checks") by `vendor_publication_status` and carry a single warning in validator output instead of a refusal. A field is filled only where a run record proves it. No bundle records a plan tier, vendor notice, permission or self-run attestation (`plan_tier`, `account_tier`, `vendor_notice`, `self_run`, `vendor_permission` appear in none of the 151 bundles), so no manifest was changed and no timing, digest or query result moved. The label lives in validator output, so no corpus change and no published-results mirror change was needed.
4. **Databricks Preview.** The live MCSA defines Beta Service with "preview" in the list (section 8.1). Every declared Databricks preview feature stays refused with or without `vendor_permission`; there is no validator or schema change. The ADR row and the site checklist now say Preview is refused too, citing the definition.
5. **New vendor conditions.** Default rule: refuse in the validator when the bundle already records the needed fact, otherwise add a checklist step; ambiguous clauses are listed for the owner and not resolved. Result of reading the documents in section 8: none of them contains a benchmark, performance-publication or reciprocity clause that is new to this record, so no validator condition was added. Documents with confidentiality language that may reach performance results are ambiguous and are listed in section 9.

## 7. Corpus status over the 151 published BigQuery, Snowflake and Databricks manifests

Command, from the repository root:

```bash
uv run -- python scripts/vendor_condition_corpus_report.py
```

The script reads every `results-data/bundles/*.manifest.json` whose bundle platform is BigQuery, Snowflake or Databricks, merges the bundle identity and run date exactly as the validator does, and classifies each manifest as `evidence_backed` (all three mandatory fields recorded), `pre_policy`, `withdrawn` (named in an optional `--withdrawn` list; none is, because a withdrawal is recorded by the Phase 3 service and not in the corpus) or `none` (neither). It exits 1 when any manifest is `none`. Result, run 2026-11-17 on the corpus of 151 manifests (BigQuery 51, Snowflake 50, Databricks 50): evidence_backed 0, pre_policy 151, withdrawn 0, none 0. The full per-manifest table is the appendix at the end of this page.

## 8. Terms read on 2026-11-17

Quotes are verbatim from the live vendor pages, retrieved 2026-11-17, with typographic quotes normalised to ASCII and `...` marking an omission. "No relevant clause" means the full text was read and searched for benchmark, performance, publish, disclos, compar, trial, free, evaluation, beta, preview, early access, experimental and confidential.

### 8.1 Databricks

- **Master Cloud Services Agreement** (https://www.databricks.com/legal/mcsa; "Last Updated February 20, 2026"). Definition 11.5: "'Beta Service' means any feature of the Databricks Services that is designated as "beta," "experimental," "preview," or similar, and that Databricks at its sole discretion offers to Customer, and Customer at its sole discretion elects to use. For clarity, Beta Services are not deemed generally available under the Agreement." No separate definition of Preview exists in the MCSA, the AUP, the Service Specific Terms or the Free Edition Terms. No benchmark clause in the MCSA body. 1.2.2(a): "Customer and its Authorized Users will not: violate the Acceptable Use Policy or use the Databricks Services in violation of applicable laws ...". 4.2: "NOTWITHSTANDING ANYTHING TO THE CONTRARY HEREIN, BETA SERVICES AND ANY DATABRICKS SERVICES PROVIDED UNDER A FREE TRIAL PERIOD ARE PROVIDED 'AS-IS' AND WITHOUT WARRANTY OF ANY KIND." Confidential Information 11.7: "any non-public business or technical information disclosed by or on behalf of either party ... that is designated as confidential at the time of disclosure or that, under the circumstances, a reasonable person would understand to be confidential or proprietary, which includes but is not limited to the Databricks Services, Customer Content, and the terms of the Agreement." Nothing in the MCSA mentions performance results.
- **Acceptable Use Policy** (https://www.databricks.com/legal/acceptable-use-policy; "Last modified: March 20, 2026"): "You may perform benchmark tests ("Benchmark") of the Databricks Services. Except with respect to Beta Services, you may disclose the results of the Benchmark. If you disclose, or cause the results of any Benchmark to be disclosed, you will include in such disclosure (i) all information necessary to replicate such Benchmark, and (ii) agree that we may perform and disclose the results of Benchmarks of your products or services included in such disclosure, irrespective of any restrictions on Benchmarks in the terms governing your products or services." The AUP defines no Beta Services and says "Any capitalized terms used but not defined herein shall have the meaning set forth in the applicable agreement between you and Databricks."
- **Service Specific Terms** (https://www.databricks.com/legal/service-specific-terms; "Last Updated July 14, 2026"): no benchmark, Beta, preview or free-trial clause.
- **Free Edition Terms of Service** (https://www.databricks.com/legal/databricks-free-edition; no date shown): no benchmark clause. 1: "You also agree that the Free Edition Acceptable Use Policy ("AUP") applies to your use of the Free Edition Services and that you will only use the Free Edition Services in accordance with the AUP." 3.1: "You may not modify, copy, distribute, transmit, display, perform, reproduce, publish, license, create derivative works from, transfer, or sell any information, software, products, or services obtained from the Free Edition Services." No Beta or preview clause. The Free Edition AUP (https://www.databricks.com/legal/acceptable-use-policy-fe; no date shown) says: "You may perform benchmark tests of the Free Edition Services, provided that you will not disclose the results of such tests unless expressly permitted by us."

### 8.2 Google and AWS

- **Google Cloud Service Specific Terms** (https://cloud.google.com/terms/service-terms; live HTML "Last modified November 11, 2026"). General Service Terms 7 reads as quoted in 4.1. 5.a: "Google may make available to Customer pre-general availability Google Cloud Platform features, models, tools, services or software that are either not yet listed at https://cloud.google.com/terms/services or identified as "Early Access," "Alpha," "Beta," "Preview," "Experimental," or a similar designation in related documentation or materials (collectively, "Pre-GA Offerings")." 8: "Certain Services may be made available to Customer on a trial basis ("Free Trial") subject to parameters and any Scope of Use, as presented in a Fees URL, Admin Console, Documentation, or otherwise." Section 9 bars disclosing benchmark or performance tests of Cloud NGFW Enterprise and Cloud IDS.
- **AWS Service Terms** (https://aws.amazon.com/service-terms/; "Last Updated: November 17, 2026"). 1.8 reads as quoted in section 3. 2.3: "You will not use any Test Observations except for your internal evaluation purposes of any Beta Service or Beta Region." 2.5: "Test Observations, Suggestions concerning a Beta Service or Beta Region, and any other information about or involving (including the existence of) any Beta Service or Beta Region are considered AWS Confidential Information." 10.3.1 (Amazon RDS for Oracle) bars "publish any results of benchmark tests run on the Oracle Software."

### 8.3 ClickHouse Cloud Addendum

- Current page https://clickhouse.com/legal/clickhouse-cloud-addendum shows "Effective Date: April 10, 2026"; its PDF is https://clickhouse.com/uploads/Click_House_Cloud_Addendum_URL_Version_v20260410_e_docx_a7f098f073.pdf ("URL Version v20260410").
- PDF https://clickhouse.com/uploads/Click_House_Cloud_Addendum_URL_Version_v20260917_cc1fe2fcec.pdf shows "Effective Date: September 17, 2026" ("URL Version v20260917"). It was found through web search; ClickHouse's legal pages and its product-addenda page link only the April PDF.
- Sections 1 to 6 of both versions: no relevant clause. No benchmark, publication, trial, free, beta, preview or early-access clause. Differences between the versions are in 1.6, 2.1 ("at the applicable tier purchased by Customer"), 2.3 (a prior-notice exception for security risk), 2.4.2 (Usage Data wording), 3.1.2 (credit rollover) and 4.2 (adds NON-INFRINGEMENT to the disclaimer); none concerns publication. 2.4.2 (September): "Usage Data does not contain Content, and will not be shared with third parties except (a) to Affiliates, contractors, or service providers in accordance with Section 6 (Confidential Information) of this Agreement, or (b) in a form that omits Customer Confidential Information and does not identify Customer or an individual."
- The addendum is incorporated into the ClickHouse General Terms and Conditions, which were not read. A separate "ClickHouse Security Cloud Preview Addendum" (https://clickhouse.com/legal/clickHouse-security-cloud-preview-addendum) exists and was not read.

### 8.4 Starburst

- **Enterprise Software EULA** (https://www.starburst.io/terms/enterprise-software-eula/; no date shown): no benchmark, performance, trial, beta, preview or early-access clause. Section 4: "Each party shall maintain as confidential and shall not disclose ... any information which relates to the other party's business affairs, trade secrets, technology, research, development, pricing or terms of this Agreement ("Confidential Information")". Section 3: "You shall not: (i) decompile, disassemble, reverse engineer or attempt to reconstruct, identify or discover any source code, underlying ideas, user interface techniques or algorithms of the Software or disclose any of the foregoing;..."
- **Galaxy Early Access Agreement** (https://www.starburst.io/terms/galaxy-early-access-agreement/; no date shown): preamble: "STARBURST HAS AGREED TO MAKE ITS NEW GALAXY SOFTWARE PRODUCT ("GALAXY") AND RELATED USER DOCUMENTATION ("DOCUMENTATION") AVAILABLE TO EARLY ACCESS PARTICIPANTS AT NO CHARGE FOR EVALUATION AND FEEDBACK PURPOSES ONLY (THE "EARLY ACCESS PROGRAM")." 1: "Galaxy and related information and documentation are the Confidential Information of Starburst." 3: "...all Confidential Information shall for a period of five (5) years from the Effective Date or from the date of disclosure, whichever is later, be held in confidence by the receiving Party and not be used except for the Early Access Program, without the prior written approval of the disclosing Party..." No benchmark or publication clause is stated.

### 8.5 CedarDB

- **Enterprise and Enterprise Trial terms: unreachable.** https://cedardb.com/legal/ lists only the open-source licence index; https://cedardb.com/legal/agreements/ returns HTTP 403; guessed agreement paths return 404; web search finds only the Community PDF. https://cedardb.com/docs/licensing (modified 2026-11-17) describes Enterprise as "Full-featured CedarDB with all enterprise capabilities. License must be renewed as negotiated." and Enterprise Trial as "Time-limited trial of CedarDB Enterprise with all features." with trial licences obtained from the self-service console (login not tried). No public text of either set of terms was reached.
- **Community Edition terms** (https://cedardb.com/legal/agreements/community_tcs.pdf; no date shown): the 4-page body has no benchmark, publication, trial, beta or confidentiality clause. 3.1: "CedarDB does not grant the User any further rights." 3.4: "The Community Edition contains open source components which are licensed under different license terms (see Exhibit 2)." **Exhibits 1 and 2 are not part of the PDF**: it ends at section 8 and has no embedded files; no HTML or English-only copy was found. https://cedardb.com/legal/oss_licenses/ (20 Aug 2025) is the likely counterpart of Exhibit 2; whether it is the same document is not established. The exhibits are therefore unreachable.

## 9. Ambiguous clauses for the owner

1. ClickHouse Cloud Addendum: April 10 and September 17, 2026 versions; ClickHouse's own pages still link April. Which one governs is not stated.
2. ClickHouse General Terms and Conditions (the Addendum's parent agreement) and the Security Cloud Preview Addendum were not read.
3. Databricks: the AUP says "Except with respect to Beta Services, you may disclose the results of the Benchmark" without saying whether disclosure of Beta results is banned or needs permission; the AUP does not define Beta Services and the link to MCSA 11.5 is by the AUP's reference to defined terms in "the applicable agreement". The MCSA says a feature is "designated" Beta without saying by whom.
4. Databricks Free Edition: Terms 3.1 ("publish ... any information ... obtained from the Free Edition Services") may or may not reach benchmark results; the Free Edition AUP requires that disclosure be "expressly permitted by us" without saying how permission is requested; the AUP-FE page links the Terms at a different URL (databricks-community-edition) from the one read.
5. Google: the benchmark clause (7) does not say whether it applies to Pre-GA Offerings (5.a).
6. AWS: 1.8 does not say whether the benchmark permission applies to Beta Services; 2.3 and 2.5 restrict Test Observations and the existence of Beta Services; the text does not link the clauses.
7. Starburst Enterprise EULA: whether performance results a licensee produces are "Confidential Information" (section 4) or an "underlying idea" or "algorithm" (section 3) is not stated; the EULA may be overridden by a signed agreement or Order Form.
8. Starburst Galaxy Early Access Agreement: Galaxy and "information and documentation related thereto" are Starburst Confidential Information for five years; whether results of testing Galaxy are "related" information is not stated; "Effective Date" is used but not defined; the page shows no date, and a search listing aged it at about 71 months, so whether it still applies to current Galaxy use is not established.
9. CedarDB: Enterprise and Enterprise Trial terms and Community Exhibits 1 and 2 are unreachable; whether any contains a benchmark or publication condition is unknown.

No validator refusal and no checklist step was added for these: none is a benchmark-publication clause whose required fact a manifest records. Clauses 1, 2, 5, 6, 7 and 8 concern vendors with no published result and are resolved when each is settled (D5); clause 3 is settled by refusing Databricks preview features; clause 4 is covered by the free-tier refusal without `vendor_permission`; clause 9 is covered by D4.

## For the owner

All eleven questions below are decided: questions 2 and 3 on 2026-11-17 (section 6), the rest on 2026-11-19 in [`vendor-publication-terms-2026-11-19.md`](../../_project/decisions/vendor-publication-terms-2026-11-19.md): questions 1 and 6 by D1, 5 by D2, 4 by D3, 7 and 8 by D4, 9 and 10 by D5, and 11 by D6.

1. For BigQuery, Snowflake and Databricks, which clauses (if any) does the project treat as applying to published results, and who records that?
2. Does the submit-time check, which reads only submitter declarations, stand as the project's handling of plan tier, Preview/Beta use, notice and permission, or does the project want something else recorded?
3. Should the existing 151 BigQuery, Snowflake and Databricks bundles, whose manifests record none of the vendor fields, be left as they are, or reviewed one by one through the takedown process?
4. Is the 2026-10-22 acceptance of MotherDuck 13.1 and Databricks 7(b) still the position, given the texts above?
5. For Snowflake, who sends results to Snowflake "reasonably in advance", and how is that recorded for results the project itself publishes?
6. For the reciprocity clauses (Google, Databricks, and the AWS, ClickHouse and MotherDuck ones), does the project treat publishing as acceptance, or leave that to submitters?
7. For CedarDB, does the project want the Enterprise and Trial terms read? (Answered 2026-11-17 for the Community Edition: publication approved; see the CedarDB row.)
8. Is the project content to rely on each engine's open-source licence text for the self-run engines, given none of the texts read mention benchmarks?
9. Should Redshift, Athena, ClickHouse Cloud, MotherDuck and Starburst Galaxy be settled before their first published result, or when a result arrives?
10. Which ClickHouse Cloud Addendum version (April 10 or September 17, 2026) is current, and should it be re-read?
11. How often should this record be re-read, and who owns it?

## Appendix: per-manifest corpus status

Output of `uv run -- python scripts/vendor_condition_corpus_report.py`, run 2026-11-17.

```text
cutoff: 2026-11-18
manifests: 151
evidence_backed: 0
pre_policy: 151
withdrawn: 0
none: 0

| Platform | Run date | Fields present | Run-record evidence | Status | Bundle |
| --- | --- | --- | --- | --- | --- |
| BigQuery | 2026-09-19 | - | none | pre_policy | clickbench_sf01_bigquery_sql_20260919_185021_2730e66a.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | clickbench_sf10_bigquery_sql_20260919_211134_6c9c5414.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | clickbench_sf1_bigquery_sql_20260919_201018_dd899402.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | coffeeshop_sf01_bigquery_sql_20260919_215952_d90b07cc.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | coffeeshop_sf1_bigquery_sql_20260919_220733_e34b9d5d.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | h2odb_sf01_bigquery_sql_20260919_221927_c38c03f2.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | h2odb_sf1_bigquery_sql_20260919_222849_eae9a72b.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | ssb_sf01_bigquery_sql_20260919_224527_9cb04d2f.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | ssb_sf10_bigquery_sql_20260919_231739_a394ecad.json |
| BigQuery | 2026-09-19 | - | none | pre_policy | ssb_sf1_bigquery_sql_20260919_225336_2580eecf.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | amplab_sf01_bigquery_sql_20260920_000055_35490af7.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | amplab_sf1_bigquery_sql_20260920_000619_033ba1cd.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | flightdata_sf01_bigquery_sql_20260920_115330_5dd56bc3.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | joinorder_sf1_bigquery_sql_20260920_004131_ebcd21b8.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | tpcdi_sf01_bigquery_sql_20260920_062227_ca696883.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | tpcdi_sf10_bigquery_sql_20260920_070853_5d042935.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | tpcdi_sf1_bigquery_sql_20260920_064220_bc5850d1.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | tpcds_sf01_bigquery_sql_20260920_015238_a9f40f37.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | tpcds_sf10_bigquery_sql_20260920_050846_0fbf6500.json |
| BigQuery | 2026-09-20 | - | none | pre_policy | tpcds_sf1_bigquery_sql_20260920_034529_dab15f22.json |
| BigQuery | 2026-09-23 | - | none | pre_policy | nyctaxi_sf1_bigquery_sql_20260923_091616_1f78ef99.json |
| BigQuery | 2026-09-23 | - | none | pre_policy | tpcds_obt_sf1_bigquery_sql_20260923_152357_480ffcea.json |
| BigQuery | 2026-09-25 | - | none | pre_policy | nyctaxi_sf01_bigquery_sql_20260925_221622_67c2e5e9.json |
| BigQuery | 2026-09-25 | - | none | pre_policy | tpch_skew_sf01_bigquery_sql_20260925_224018_ec5b9708.json |
| BigQuery | 2026-09-25 | - | none | pre_policy | tpch_skew_sf1_bigquery_sql_20260925_225451_d1e9d2fd.json |
| BigQuery | 2026-09-27 | - | none | pre_policy | datavault_sf001_bigquery_sql_20260927_100530_5ec9efa0.json |
| BigQuery | 2026-09-27 | - | none | pre_policy | flightdata_sf10_bigquery_sql_20260927_150859_7146e9c3.json |
| BigQuery | 2026-09-27 | - | none | pre_policy | flightdata_sf1_bigquery_sql_20260927_123112_15864c7e.json |
| BigQuery | 2026-09-27 | - | none | pre_policy | nyctaxi_sf10_bigquery_sql_20260927_120407_fec02550.json |
| BigQuery | 2026-09-27 | - | none | pre_policy | tpch_skew_sf10_bigquery_sql_20260927_104923_b3fc1da6.json |
| BigQuery | 2026-09-29 | - | none | pre_policy | metadata_primitives_sf1_bigquery_sql_20260929_204456_e435b418.json |
| BigQuery | 2026-09-29 | - | none | pre_policy | read_primitives_sf01_bigquery_sql_20260929_184751_5bfa3d9b.json |
| BigQuery | 2026-09-29 | - | none | pre_policy | tpchavoc_sf01_bigquery_sql_20260929_141245_143d6f5e.json |
| BigQuery | 2026-09-29 | - | none | pre_policy | tsbs_devops_sf01_bigquery_sql_20260929_192405_78305c52.json |
| BigQuery | 2026-09-29 | - | none | pre_policy | tsbs_devops_sf1_bigquery_sql_20260929_194803_b2f76dae.json |
| BigQuery | 2026-09-30 | - | none | pre_policy | datavault_sf01_bigquery_sql_20260930_072535_d291326d.json |
| BigQuery | 2026-09-30 | - | none | pre_policy | read_primitives_sf001_bigquery_sql_20260930_081905_1a102885.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | datavault_sf10_bigquery_sql_20261001_110327_d1328d05.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | datavault_sf1_bigquery_sql_20261001_091125_26fe04bf.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | tpchavoc_sf10_bigquery_sql_20261001_190554_ad5f29c8.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | tpchavoc_sf1_bigquery_sql_20261001_113358_53fc08cb.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf001_bigquery_sql_20261001_205304_209908db.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf01_bigquery_sql_20261001_151409_dc7eeac0.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf1_bigquery_sql_20261001_154316_52129540.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | tsbs_devops_sf10_bigquery_sql_20261001_085654_76754d66.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | write_primitives_sf001_bigquery_sql_20261001_202950_88736ac2.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | write_primitives_sf01_bigquery_sql_20261001_131148_b2d946ee.json |
| BigQuery | 2026-10-01 | - | none | pre_policy | write_primitives_sf1_bigquery_sql_20261001_144917_922446ec.json |
| BigQuery | 2026-10-03 | - | none | pre_policy | tpch_sf01_bigquery_sql_20261003_114214_37cecbba.json |
| BigQuery | 2026-10-03 | - | none | pre_policy | tpch_sf10_bigquery_sql_20261003_120614_73b816d9.json |
| BigQuery | 2026-10-03 | - | none | pre_policy | tpch_sf1_bigquery_sql_20261003_114700_6e0ec489.json |
| Databricks | 2026-09-30 | - | none | pre_policy | datavault_sf01_databricks_sql_20260930_175402_7225e491.json |
| Databricks | 2026-09-30 | - | none | pre_policy | datavault_sf1_databricks_sql_20260930_180331_e7b23724.json |
| Databricks | 2026-09-30 | - | none | pre_policy | tsbs_devops_sf01_databricks_sql_20260930_173454_741f616c.json |
| Databricks | 2026-09-30 | - | none | pre_policy | tsbs_devops_sf10_databricks_sql_20260930_174638_397e236a.json |
| Databricks | 2026-09-30 | - | none | pre_policy | tsbs_devops_sf1_databricks_sql_20260930_173958_176e523b.json |
| Databricks | 2026-10-01 | - | none | pre_policy | datavault_sf10_databricks_sql_20261001_195104_c9a232ef.json |
| Databricks | 2026-10-01 | - | none | pre_policy | flightdata_sf01_databricks_sql_20261001_102859_b0afbbe8.json |
| Databricks | 2026-10-01 | - | none | pre_policy | flightdata_sf10_databricks_sql_20261001_151316_6644c94b.json |
| Databricks | 2026-10-01 | - | none | pre_policy | flightdata_sf1_databricks_sql_20261001_143909_6326e239.json |
| Databricks | 2026-10-01 | - | none | pre_policy | metadata_primitives_sf1_databricks_sql_20261001_191204_8daddf3b.json |
| Databricks | 2026-10-01 | - | none | pre_policy | nyctaxi_sf01_databricks_sql_20261001_101301_84fd0b4c.json |
| Databricks | 2026-10-01 | - | none | pre_policy | nyctaxi_sf10_databricks_sql_20261001_102629_e82aa7d2.json |
| Databricks | 2026-10-01 | - | none | pre_policy | nyctaxi_sf1_databricks_sql_20261001_101715_c8b8cad2.json |
| Databricks | 2026-10-01 | - | none | pre_policy | read_primitives_sf001_databricks_sql_20261001_152239_14b1d8d2.json |
| Databricks | 2026-10-01 | - | none | pre_policy | read_primitives_sf01_databricks_sql_20261001_143234_f068492b.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpcds_obt_sf1_databricks_sql_20261001_192224_d11b3297.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpch_skew_sf01_databricks_sql_20261001_103252_6c8936f3.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpch_skew_sf10_databricks_sql_20261001_104602_9f1d9a8b.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpch_skew_sf1_databricks_sql_20261001_103719_61f8cbae.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpchavoc_sf01_databricks_sql_20261001_073813_8d9b08b5.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpchavoc_sf10_databricks_sql_20261001_084206_6d92a08e.json |
| Databricks | 2026-10-01 | - | none | pre_policy | tpchavoc_sf1_databricks_sql_20261001_075954_ffaa37ca.json |
| Databricks | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf001_databricks_sql_20261001_165710_25cbf2c1.json |
| Databricks | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf01_databricks_sql_20261001_175853_773a7d29.json |
| Databricks | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf1_databricks_sql_20261001_190409_576d4969.json |
| Databricks | 2026-10-01 | - | none | pre_policy | write_primitives_sf001_databricks_sql_20261001_164226_94cc487b.json |
| Databricks | 2026-10-01 | - | none | pre_policy | write_primitives_sf01_databricks_sql_20261001_174439_9055e7de.json |
| Databricks | 2026-10-01 | - | none | pre_policy | write_primitives_sf1_databricks_sql_20261001_184834_88ba12ba.json |
| Databricks | 2026-10-03 | - | none | pre_policy | amplab_sf01_databricks_sql_20261003_214137_2a7a0cdb.json |
| Databricks | 2026-10-03 | - | none | pre_policy | amplab_sf1_databricks_sql_20261003_214318_4d74e05e.json |
| Databricks | 2026-10-03 | - | none | pre_policy | clickbench_sf01_databricks_sql_20261003_220608_70c9438e.json |
| Databricks | 2026-10-03 | - | none | pre_policy | clickbench_sf10_databricks_sql_20261003_231900_2437b487.json |
| Databricks | 2026-10-03 | - | none | pre_policy | clickbench_sf1_databricks_sql_20261003_230712_3a9e1d61.json |
| Databricks | 2026-10-03 | - | none | pre_policy | coffeeshop_sf01_databricks_sql_20261003_232025_9d8852ef.json |
| Databricks | 2026-10-03 | - | none | pre_policy | coffeeshop_sf1_databricks_sql_20261003_232342_074127de.json |
| Databricks | 2026-10-03 | - | none | pre_policy | h2odb_sf01_databricks_sql_20261003_232445_0768c815.json |
| Databricks | 2026-10-03 | - | none | pre_policy | h2odb_sf1_databricks_sql_20261003_232810_1c91848b.json |
| Databricks | 2026-10-03 | - | none | pre_policy | joinorder_sf1_databricks_sql_20261003_220424_a39ae9d6.json |
| Databricks | 2026-10-03 | - | none | pre_policy | ssb_sf01_databricks_sql_20261003_212600_d10c5be1.json |
| Databricks | 2026-10-03 | - | none | pre_policy | ssb_sf10_databricks_sql_20261003_214042_3ee6078e.json |
| Databricks | 2026-10-03 | - | none | pre_policy | ssb_sf1_databricks_sql_20261003_212847_179f2636.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpcdi_sf01_databricks_sql_20261003_201241_01329d67.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpcdi_sf10_databricks_sql_20261003_202752_92040a00.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpcdi_sf1_databricks_sql_20261003_201821_dbfb95fb.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpcds_sf01_databricks_sql_20261003_204005_294ed3a2.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpcds_sf10_databricks_sql_20261003_212424_15e05c0c.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpcds_sf1_databricks_sql_20261003_205449_cf4f01d0.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpch_sf01_databricks_sql_20261003_114031_fbaeb62c.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpch_sf10_databricks_sql_20261003_120202_e83ea509.json |
| Databricks | 2026-10-03 | - | none | pre_policy | tpch_sf1_databricks_sql_20261003_114841_ab447f73.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | clickbench_sf10_snowflake_sql_20260919_213657_c9c32b6f.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | coffeeshop_sf01_snowflake_sql_20260919_220303_a0040982.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | coffeeshop_sf1_snowflake_sql_20260919_221140_a33dc417.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | h2odb_sf01_snowflake_sql_20260919_222222_58b4e7b0.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | h2odb_sf1_snowflake_sql_20260919_223457_01856170.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | ssb_sf01_snowflake_sql_20260919_224858_fb7ff357.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | ssb_sf10_snowflake_sql_20260919_233632_2df658e9.json |
| Snowflake | 2026-09-19 | - | none | pre_policy | ssb_sf1_snowflake_sql_20260919_225814_ec4b3865.json |
| Snowflake | 2026-09-20 | - | none | pre_policy | amplab_sf01_snowflake_sql_20260920_000319_c0a92e84.json |
| Snowflake | 2026-09-20 | - | none | pre_policy | amplab_sf1_snowflake_sql_20260920_000911_e1dbbcf8.json |
| Snowflake | 2026-09-20 | - | none | pre_policy | clickbench_sf01_snowflake_sql_20260920_024607_616ff980.json |
| Snowflake | 2026-09-20 | - | none | pre_policy | clickbench_sf1_snowflake_sql_20260920_025738_7ac21810.json |
| Snowflake | 2026-09-20 | - | none | pre_policy | joinorder_sf1_snowflake_sql_20260920_012237_b41d3b54.json |
| Snowflake | 2026-09-25 | - | none | pre_policy | datavault_sf1_snowflake_sql_20260925_224900_77db76a8.json |
| Snowflake | 2026-09-25 | - | none | pre_policy | nyctaxi_sf1_snowflake_sql_20260925_092203_0cd1a463.json |
| Snowflake | 2026-09-25 | - | none | pre_policy | tpch_skew_sf01_snowflake_sql_20260925_220834_4a9526c3.json |
| Snowflake | 2026-09-25 | - | none | pre_policy | tpch_skew_sf1_snowflake_sql_20260925_221217_97d77ab8.json |
| Snowflake | 2026-09-26 | - | none | pre_policy | datavault_sf01_snowflake_sql_20260926_082823_c709896c.json |
| Snowflake | 2026-09-29 | - | none | pre_policy | tpch_skew_sf10_snowflake_sql_20260929_124322_e9d6506c.json |
| Snowflake | 2026-09-29 | - | none | pre_policy | tsbs_devops_sf01_snowflake_sql_20260929_192425_ba04d0d6.json |
| Snowflake | 2026-09-29 | - | none | pre_policy | tsbs_devops_sf10_snowflake_sql_20260929_224733_a340c633.json |
| Snowflake | 2026-09-29 | - | none | pre_policy | tsbs_devops_sf1_snowflake_sql_20260929_192925_18bb194f.json |
| Snowflake | 2026-09-30 | - | none | pre_policy | datavault_sf10_snowflake_sql_20260930_180120_db643832.json |
| Snowflake | 2026-09-30 | - | none | pre_policy | tpchavoc_sf01_snowflake_sql_20260930_194734_9da55014.json |
| Snowflake | 2026-09-30 | - | none | pre_policy | tpchavoc_sf10_snowflake_sql_20260930_193124_48bb07c7.json |
| Snowflake | 2026-09-30 | - | none | pre_policy | tpchavoc_sf1_snowflake_sql_20260930_200344_2761bdbc.json |
| Snowflake | 2026-09-30 | - | none | pre_policy | write_primitives_sf001_snowflake_sql_20260930_212005_0fab9883.json |
| Snowflake | 2026-09-30 | - | none | pre_policy | write_primitives_sf01_snowflake_sql_20260930_222159_5d085279.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | flightdata_sf01_snowflake_sql_20261001_102703_bb18d2c8.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | flightdata_sf10_snowflake_sql_20261001_113621_e22fa5a3.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | flightdata_sf1_snowflake_sql_20261001_111501_3a83168a.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | metadata_primitives_sf1_snowflake_sql_20261001_111119_1ee8b07d.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | nyctaxi_sf01_snowflake_sql_20261001_101251_06190f1d.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | nyctaxi_sf10_snowflake_sql_20261001_102124_4c662b94.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | read_primitives_sf001_snowflake_sql_20261001_103832_16df8896.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | read_primitives_sf01_snowflake_sql_20261001_105057_79bd90a5.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | tpcds_obt_sf1_snowflake_sql_20261001_173028_d35974f6.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf001_snowflake_sql_20261001_084848_c3e017eb.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf01_snowflake_sql_20261001_090534_90566a67.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | transaction_primitives_sf1_snowflake_sql_20261001_092311_b4c0d6da.json |
| Snowflake | 2026-10-01 | - | none | pre_policy | write_primitives_sf1_snowflake_sql_20261001_083039_3c313d6f.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpcdi_sf01_snowflake_sql_20261003_134247_5dd95d51.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpcdi_sf10_snowflake_sql_20261003_135304_9ec08998.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpcdi_sf1_snowflake_sql_20261003_134837_b45dfad3.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpcds_sf01_snowflake_sql_20261003_140200_f5fd99c7.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpcds_sf10_snowflake_sql_20261003_143203_b8820a1d.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpcds_sf1_snowflake_sql_20261003_141435_27bd9f9c.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpch_sf01_snowflake_sql_20261003_113557_241439cc.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpch_sf10_snowflake_sql_20261003_114454_2454b319.json |
| Snowflake | 2026-10-03 | - | none | pre_policy | tpch_sf1_snowflake_sql_20261003_113923_e545c1c5.json |
```
