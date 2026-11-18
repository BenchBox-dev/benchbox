# Vendor publication terms record

**Audience:** the project owner and maintainers who decide how published results relate to vendor terms.
**Scope:** per vendor in the published corpus, what the vendor's current terms say about publishing benchmark results, and what BenchBox does today. Internal, unpublished.

## Purpose and limits

This page records texts. It is not legal analysis. Nothing here says whether a clause applies to BenchBox, whether BenchBox or a submitter complies with it, or what the project should do. Every decision column is the owner's and reads `Owner decision: pending`.

Owner decision of 2026-11-13: static result pages include every published result in the meantime. Any withdrawal goes through the existing takedown process, not by editing pages. That process is documented in [`results-phase-3-runbook.md`](results-phase-3-runbook.md) (section "Takedown Process": `benchbox result withdraw`, `benchbox admin withdraw --reason ...`) and [`independent-publication-contract.md`](independent-publication-contract.md) (section "Emergency takedown").

Method: every quote below was read from the live vendor page on 2026-11-16. Quotes are verbatim except that typographic quotes and apostrophes are normalised to ASCII and `...` marks an omission. "Terms date" is the date the page itself shows, or `no date shown`. The 2026-10-22 comparison is against the "Terms check" table in `benchbox-site/docs/development/adr/adr-client-link-locality-disclosure.md`.

Corpus counts are `platform.name` in `results-data/bundles/*.json` on `develop` (4c848ffdd6), excluding `*.manifest.json` and `*.override.json`: 427 bundles.

## What BenchBox does (all vendors)

Source of the behaviour: `benchbox/validation/bundle.py` (`PLAN_TIERS`, `VENDOR_NOTICE_PLATFORMS`, `TIERED_PLATFORMS`, `SELF_RUN_PLATFORMS`, `vendor_platform_key`, `evaluate_vendor_conditions` at line 830, called during bundle validation at line 3258) and `benchbox/cli/commands/submit.py` (`_vendor_condition_fields`, `_enforce_vendor_conditions`). The user-facing description is `docs/vendor-publishing-checklist.md` in `benchbox-site`.

- Result schema 2.5 bundles can carry the same five fields in `provenance` (`benchbox run --plan-tier` and `--preview-feature` record the first two); the validator applies the same refusals to the bundle's own `provenance` and to a manifest, with the stricter declaration winning, and `benchbox submit` merges the bundle's fields with its options.
- `benchbox submit` writes `plan_tier`, `preview_features`, `vendor_permission`, `vendor_notice` and `self_run` into the manifest from CLI options. The validator reads the same fields.
- Refusal: `plan_tier` of `free` or `trial` (or `funding: free-trial`), or any `preview_features`, with no `vendor_permission`. On Databricks any `preview_features` entry refuses even with permission. `self_run: false` on BigQuery refuses. A malformed `vendor_notice` or an invalid `plan_tier` refuses.
- Warning: missing `vendor_notice` on Snowflake, Redshift, Athena, ClickHouse Cloud, MotherDuck; missing `self_run` on BigQuery; missing `plan_tier` on Snowflake, Redshift, Athena, ClickHouse Cloud, Databricks, MotherDuck.
- The checks read only what the submitter declares. They cannot see the real plan tier or features.
- Starburst Galaxy has no entry in any of the maps: no check. Platforms outside `_VENDOR_KEYWORDS` get no check.
- Replication information: bundles carry configuration and query text; the checklist treats that as the "disclosure includes what is needed to replicate" condition. Sending information to a vendor, notice in advance and reciprocity are manual steps with no check beyond the recorded `vendor_notice`.
- Published manifests in `results-data/bundles` for BigQuery (51), Snowflake (50) and Databricks (50): none records `plan_tier`, `preview_features`, `vendor_permission`, `vendor_notice` or `self_run`; all have `funding: unspecified`.
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

Owner decision: pending.

## 2. Terms differences since 2026-10-22

- Google Service Specific Terms: page shows "Last modified April 2, 2026"; the 2026-10-22 table recorded "modified 2026-10-12". Benchmarking clause substance matches the table. Not reconciled here.
- AWS Service Terms: now "Last Updated: November 5, 2026" (table: 2026-10-13). Clauses 1.8, 2.3, 2.5 as in the table in substance. AWS Customer Agreement now "Last Updated: October 29, 2026"; 11.9 and 11.2 as in the table in substance.
- ClickHouse Cloud Addendum: page shows "Effective Date: April 10, 2026"; a web search listing also showed a PDF "URL Version (v20260917)" with "Effective Date: September 17, 2026". The PDF was not read; which one is current is not established.
- Snowflake AUP, Snowflake Terms of Service, Databricks AUP, Free Edition AUP, ClickHouse AUP, MotherDuck MSA, Starburst Galaxy Terms: no change in dates or in the clauses recorded.

## 3. Managed platforms with no published result in the corpus

"No published results in corpus as of 2026-11-16." BenchBox supports each (see `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS` above; Starburst has no check).

| Platform | Clause (verbatim, short) | URL | Terms date | Read | Owner decision |
| --- | --- | --- | --- | --- | --- |
| Redshift, Athena (incl. Athena Spark) | AWS Service Terms 1.8: "You may perform benchmarks or comparative tests or evaluations ... If you perform or disclose ... any Benchmark of any of the Services, you (i) will include in any disclosure, and will disclose to us, all information necessary to replicate such Benchmark, and (ii) agree that we may perform and disclose the results of Benchmarks of your products or services..." | https://aws.amazon.com/service-terms/ | Last Updated: November 5, 2026 | 2026-11-16 | pending |
| ClickHouse Cloud | AUP 2: see 5.3 | https://clickhouse.com/legal/agreements/acceptable-use-policy | Effective date: July 16, 2026 | 2026-11-16 | pending |
| MotherDuck | MSA 1.5: see 5.4 | https://motherduck.com/terms-of-service/ | Last Updated: 2026-10-05 | 2026-11-16 | pending |
| Starburst Galaxy | No benchmark clause; see 5.5 | https://www.starburst.io/terms/galaxy-terms-of-service | no date shown | 2026-11-16 | pending |

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
- Owner decision: pending.

### 4.2 Snowflake (50 bundles)

- **Clause** (Acceptable Use Policy IV Benchmarking): "Subject to the use restrictions herein, Customer may conduct benchmark tests of the Service, including the Snowflake AI Features (each a "Test"). With the exception of Tests involving Previews, Customer may publicly disclose, or allow third parties to publicly disclose, the results of the Test as long as (1) Customer shares the Test results with Snowflake reasonably in advance of its disclosure, and (2) the disclosure includes all information necessary to replicate the Test. Test results involving Preview features are confidential and cannot be publicly disclosed without Snowflake's prior written approval."
- **Related text:** Terms of Service 4.2 Usage Data: "Snowflake may collect and use Usage Data to develop, improve, support, and operate its products and services. Snowflake may not share any Usage Data that includes Customer's Confidential Information with a third party except..." Terms of Service 14: Confidential Information includes "All Snowflake Technology and the terms and conditions of this Agreement (including ongoing discussions regarding the parties' relationship under this Agreement)". AUP VI: "in the event of any conflict between the Agreement and this Acceptable Use Policy, this Acceptable Use Policy shall govern."
- **Source:** https://www.snowflake.com/en/legal/addenda/acceptable-use-policy ; https://www.snowflake.com/en/legal/terms-of-service
- **Terms date shown:** AUP "Last Updated: February 3, 2025"; Terms of Service "Last Updated: April 16, 2026".
- **Date read:** 2026-11-16.
- **Change since 2026-10-22:** none.
- **BenchBox today:** Snowflake is in `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS`. Missing `vendor_notice` warns; `plan_tier` free or trial, or `preview_features`, without `vendor_permission` refuses; missing `plan_tier` warns. The check confirms a dated reference exists; it does not judge "reasonably in advance". The 50 published manifests record none of the vendor fields.
- **Flags** (looks restrictive - owner to assess): sharing results with Snowflake "reasonably in advance" of disclosure; prior written approval for results involving Preview features (confidential); "Subject to the use restrictions herein" qualifier; Snowflake Technology is Confidential Information under Terms of Service 14.
- Owner decision: pending.

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
- Owner decision: pending.

## 5. Managed platforms with no published result in the corpus

### 5.1 Redshift

- **Clause:** AWS Service Terms 1.8 (quoted in section 3). Beta: 2.3 "You may provide AWS with information relating to your access, use, testing, or evaluation of Beta Services or Beta Regions, including observations or information regarding the performance, features, and functionality of Beta Services or Beta Regions ("Test Observations"). AWS will own and may use and evaluate all Test Observations for its own purposes. You will not use any Test Observations except for your internal evaluation purposes of any Beta Service or Beta Region." 2.5 "Test Observations, Suggestions concerning a Beta Service or Beta Region, and any other information about or involving (including the existence of) any Beta Service or Beta Region are considered AWS Confidential Information."
- **Related text:** AWS Customer Agreement 11.9 "You will not issue any press release or make any other public communication with respect to this Agreement or your use of the Services or AWS Content." 11.2 "...the Service Terms will control over this document."
- **Source:** https://aws.amazon.com/service-terms/ ; https://aws.amazon.com/agreement/
- **Terms date shown:** Service Terms "Last Updated: November 5, 2026"; Customer Agreement "Last Updated: October 29, 2026".
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none in substance; dates moved (section 2). No Redshift-specific benchmark clause found in the Service Terms; the only other "benchmark" clause there concerns Oracle Software.
- **BenchBox today:** in `VENDOR_NOTICE_PLATFORMS` (notice warning) and `TIERED_PLATFORMS` (preview/free/trial refusal). No published results.
- **Flags** (looks restrictive - owner to assess): information must also go "to us" (AWS); reciprocity; Beta/Preview observations are AWS Confidential Information and limited to "internal evaluation purposes"; Customer Agreement 11.9 publicity clause (which 11.2 subordinates to Service Terms).
- Owner decision: pending.

### 5.2 Athena (including Athena Spark)

Same texts, URLs, dates and BenchBox behaviour as 5.1 (`vendor_platform_key` maps `athena` variants). No Athena-specific benchmark clause found. Flags as 5.1. Owner decision: pending.

### 5.3 ClickHouse Cloud

- **Clause** (Acceptable Use Policy 2 Benchmarking): "you may perform benchmark, evaluation or comparative tests of the Services (each, a "Benchmark"), provided, however, that if you publish any Benchmark, or direct and/or permit any third party to publish any Benchmark (whether performed by you or a third party), you (i) must also disclose ... to ClickHouse and include in any public disclosure of the results of any such Benchmark, all information necessary to replicate such Benchmark and (ii) agree that ClickHouse may perform and disclose ... the results of Benchmarks of your products or services, to the extent they are in any way competitive with the Services ... The foregoing right to publish Benchmarks does not apply to any beta, preview, or early-access features of the Services. Results of any Benchmark involving such features are confidential and may not be publicly disclosed without ClickHouse's prior written consent."
- **Related text:** AUP 1.4 bars using "automated means (e.g., bots, scrapers) to access the Services or scrape, harvest or extract data or metadata (including, without limitation, audit logs or user credentials) from the Services except for automated or agentic interactions that are expressly identified in the applicable Documentation as supported by the Services ... or otherwise with ClickHouse's prior written consent". Cloud Addendum 2.4.2: "ClickHouse owns all right, title and interest in and to Usage Data".
- **Source:** https://clickhouse.com/legal/agreements/acceptable-use-policy ; https://clickhouse.com/legal/clickhouse-cloud-addendum
- **Terms date shown:** AUP "Effective date: July 16, 2026"; Cloud Addendum "Effective Date: April 10, 2026" (see section 2 on a v20260917 PDF).
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none in the clauses read.
- **BenchBox today:** in `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS` (`clickhouse-cloud`; "clickhouse" plus "cloud" in the platform name). ClickHouse Local and Server bundles in the corpus are not matched. No published results.
- **Flags** (looks restrictive - owner to assess): disclosure "to ClickHouse"; reciprocity; beta/preview/early-access results confidential without prior written consent; AUP 1.4 metadata extraction wording.
- Owner decision: pending.

### 5.4 MotherDuck

- **Clause** (Master Subscription Agreement 1.5 Benchmarking): "If Customer performs or discloses, or directs or permits any third party to perform or disclose, any benchmarking of any of the Services, Customer (i) will include in any disclosure (including any public disclosure), and will disclose to MotherDuck, all data, software code, and other information necessary to enable MotherDuck or a third party to replicate such benchmark, and (ii) agrees that MotherDuck may perform and disclose the results of the benchmarks of Customer's products and services, irrespective of any restrictions on benchmarks in the terms governing Customer's products or services."
- **Related text:** 13.1 "MotherDuck's Confidential Information includes any technical or performance information about the Services." 15 Trials and Betas: "... their features and performance information are MotherDuck's Confidential Information." 3.2 Usage Data and 16 Publicity (MotherDuck may list the customer) read; no limit on the customer found there.
- **Source:** https://motherduck.com/terms-of-service/
- **Terms date shown:** "Last Updated: 2026-10-05".
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none.
- **BenchBox today:** in `VENDOR_NOTICE_PLATFORMS` and `TIERED_PLATFORMS`. Free or trial `plan_tier` or any preview feature without `vendor_permission` refuses. The ADR records the owner's acceptance of 13.1 for public bundles. No published results.
- **Flags** (looks restrictive - owner to assess): 13.1 treating "any technical or performance information about the Services" as Confidential Information; 15 for Trials and Betas; disclosure of "all data, software code, and other information" to MotherDuck; reciprocity.
- Owner decision: pending.

### 5.5 Starburst Galaxy

- **Clause:** no benchmark clause in the Galaxy Terms of Service (all eight sections read). Confidentiality, 4: "any information which relates to the other party's business affairs, trade secrets, technology, research, development, pricing or terms of this Agreement ("Confidential Information")"; 3: "The Service is the proprietary and confidential intellectual property of Starburst".
- **Source:** https://www.starburst.io/terms/galaxy-terms-of-service
- **Terms date shown:** no date shown.
- **Date read:** 2026-11-16. **Change since 2026-10-22:** none.
- **BenchBox today:** no entry in `VENDOR_NOTICE_PLATFORMS`, `TIERED_PLATFORMS` or `SELF_RUN_PLATFORMS`; no check. No published results.
- **Flags** (looks restrictive - owner to assess): breadth of "relates to ... technology" in the Confidentiality clause (4); "proprietary and confidential" language about the Service in 3. Enterprise Software EULA and Galaxy Early Access Agreement not read.
- Owner decision: pending.

## For the owner

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
