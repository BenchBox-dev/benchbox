# ADR: Client-to-Platform Locality Disclosure and Statement Overhead Probe

- Status: Accepted. Amended 2026-10-17: an opt-in, labeled estimate with the measured round trip removed, and platform timings in result bundles (see [Amendment: Round Trip Removed and Platform Timings](#amendment-round-trip-removed-and-platform-timings)). Revised 2026-10-21: public bundles carry `platform_timing` for every platform; the terms check no longer gates publication. Revised 2026-10-22: every platform's terms read from the live pages, with the MotherDuck and Databricks telemetry risks recorded and accepted.
- Date: 2026-09-03
- Constrains: Result schema `environment.client_link`, CLI `--client-cloud` and `--client-region` options, post-benchmark statement overhead probe, Results Explorer read model v10, and hosted result submission validation. The amendment also constrains result schema 2.4 (`platform_timing`, `request_count`, `run.timing_method`, `run.net_estimate_status`, `client_link.probed_at`) and the Results Explorer Compare round-trip view.

## Context and Motivation

When benchmarking remote and cloud data warehouses (e.g., Snowflake, Databricks, BigQuery, Redshift, ClickHouse Cloud, or remote-backed DuckLake), measured query duration consists of two distinct components:

1. **Engine execution time** on the remote database or warehouse compute cluster; and
2. **Client-to-platform round-trip overhead**, including client serialization, network transport across cloud backbones or the public Internet, ingress gateway/proxy routing, and result set deserialization.

For long-running analytical queries (taking tens of seconds or minutes), network round-trip overhead is negligible. However, for interactive workloads, short queries, and smaller scale factors (e.g., SF0.01–SF1 power tests, metadata queries, point lookups, and lightweight aggregations executing under 100 milliseconds), client-to-platform latency can easily dominate the overall measured time.

A benchmark executed from an AWS EC2 instance in `us-east-1` against an AWS `us-east-1` Snowflake warehouse experiences ~1 ms of network latency. The exact same benchmark run against the same warehouse from a developer laptop over residential WiFi in Europe or South America experiences 50–150 ms of network latency per query. Across a 22-query sequence, this discrepancy introduces several seconds of non-engine latency.

Without client-link locality disclosure:

- Observers may misattribute network transit time to platform query engine performance.
- Results collected under vastly different network topologies are compared as if they were identical setups.
- Community and maintainer runs cannot be reliably reproduced without guessing client runner placement.

## Reconciliation with the DuckLake Remote-Backed Results Decision

BenchBox encountered a similar architectural challenge in ADR [`adr-ducklake-maturity-and-publishability.md`](adr-ducklake-maturity-and-publishability.md), where the decision on remote-backed results addressed whether DuckLake runs backed by remote S3 storage and PostgreSQL metadata catalogs could be published and ranked alongside runs on local NVMe disk:

> "The honest framing is that DuckLake's catalog backend and storage location are **part of the configuration under test**, exactly like a tuning profile or a scale factor — not a defect in the run.
>
> The real hazard is not publication, it is *comparison*: a DuckLake-on-S3 number partly measures object-store latency, so ranking it against DuckLake-on-local-disk as though they were the same system is the error. That is a comparison-grouping concern, addressed by recording the backing, not by suppressing the result."

The governing principle that decision establishes is: **"Disclose the topology; do not infer the distance."**

BenchBox rejects attempting to mathematically adjust, subtract, or model network latency out of reported query timings. Latency subtraction is inherently speculative and risks masking engine-level stalls, connection teardowns, and driver inefficiencies. Instead, BenchBox discloses the empirical topology and network overhead directly:

1. **Disclose the client runner's location** (cloud provider and region).
2. **Measure the baseline statement round-trip floor** empirically via post-benchmark probes.
3. **Equip consumers and the Results Explorer** to group like-with-like topologies and flag cross-topology comparisons.

The [amendment](#amendment-round-trip-removed-and-platform-timings) allows one exception: an opt-in Compare view that removes the round trip BenchBox measured, labeled as an estimate and always shown with end-to-end time.

## Rejected Alternatives

### 1. Transport TCP Connect Probes and ICMP Pings

We rejected measuring network distance using ICMP `ping` or TCP SYN/ACK connection probes to target platform hostnames:

- **Ingress gateway and CDN distortion:** Modern managed data warehouses front compute clusters with multi-tenant API gateways, edge load balancers, CDN points-of-presence (e.g., Cloudflare, CloudFront), and Envoy proxies. An ICMP ping or TCP connect to `account.snowflakecomputing.com` or `bigquery.googleapis.com` terminates at the closest edge ingress proxy, which may be geographically near the client even when the backing compute warehouse is located across an ocean.
- **Protocol unrepresentativeness:** A TCP handshake does not traverse the application protocol stack (TLS session negotiation, HTTP/2 multiplexing, JDBC/ODBC session authentication, and database query dispatch). It fails to reflect the actual overhead experienced by database statements.
- **Network and permission restrictions:** ICMP is frequently disabled or filtered by cloud security groups, corporate firewalls, and container runtimes. Raw sockets require elevated privileges (`CAP_NET_RAW` or root), which BenchBox explicitly avoids requiring.
- **Internal / unmapped hostnames:** Cloud platforms often use dynamic, unmapped, or private endpoints inaccessible to standard network utility tools.

### 2. Named Qualitative Latency Bands (e.g., "local", "near", "metro", "cross-region")

We rejected categorizing runs into subjective latency bands:

- **Lossy and subjective:** Latency categories create arbitrary boundaries. For example, cloud cross-connects between AWS `us-east-1` (North Virginia) and GCP `us-east4` (Northern Virginia) often exhibit <2 ms latency, whereas two hosts in the same geographic metropolitan area over separate commercial ISPs may suffer >30 ms latency. Labeling cross-cloud as "cross-region" and same-metro as "near" is objectively inaccurate.
- **Maintenance fragility:** Cloud network backbones, direct connects, and peering agreements constantly evolve. Heuristic band assignments quickly become obsolete and invite endless debates over category boundaries.
- **Superiority of empirical data:** Storing objective facts (client cloud, client region, and measured statement overhead in milliseconds) provides ground truth that remains valid indefinitely.

## The Decision

We introduce `environment.client_link` as an optional nested metadata block in schema-v2 result bundles.

### 1. Schema Contract

```json
{
  "client_link": {
    "collection_status": "available",
    "source": "observed",
    "client_cloud": "aws",
    "client_region": "us-east-1",
    "statement_overhead_ms": {
      "samples": 5,
      "min": 1.42,
      "median": 1.68
    },
    "collection_error_class": null,
    "collection_error_message": null
  }
}
```

Fields:

- `collection_status` (`"available" | "partial" | "unavailable" | "error" | "not_requested"`): Overall collection outcome.
- `source` (`"observed" | "cli_option" | "unavailable"`): Origin of client cloud and region metadata.
- `client_cloud` (`string | null`): Cloud provider of the client runner (`"aws"`, `"gcp"`, `"azure"`).
- `client_region` (`string | null`): Cloud region identifier of the client runner (e.g., `"us-east-1"`, `"europe-west1"`).
- `statement_overhead_ms` (`object`): Empirical overhead measurements:
  - `samples` (`int`): Count of repeated probe executions (standard: `5`).
  - `min` (`float`): Minimum observed statement duration in milliseconds floor, representing the protocol and network baseline floor.
  - `median` (`float`): Median observed statement duration in milliseconds.
- `collection_error_class` (`string | null`): Name of error/exception class if collection failed.
- `collection_error_message` (`string | null`): Descriptive diagnostic error message if collection failed.

### 2. Client Locality Discovery (`observed` vs `cli_option`)

- **Automated IMDS Discovery (`observed`):** BenchBox attempts non-blocking, link-local discovery via standard cloud Instance Metadata Services:
  - AWS: IMDSv2 token request (`PUT http://169.254.169.254/latest/api/token`) followed by document fetch (`GET http://169.254.169.254/latest/dynamic/instance-identity/document`).
  - GCP: Metadata fetch (`GET http://169.254.169.254/computeMetadata/v1/instance/zone`) with `Metadata-Flavor: Google`.
  - Azure: Instance metadata fetch (`GET http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text`) with `Metadata: true`.
  Discovery uses short timeouts (100–250 ms) to ensure that running on developer laptops or non-cloud servers terminates immediately without slowing startup.
- **CLI Overrides (`cli_option`):** When running in air-gapped environments, on bare-metal servers, or where IMDS access is disabled, submitters can provide `--client-cloud` and `--client-region` on the CLI. The result records `source: "cli_option"`.
- **Non-Cloud / Laptop Runs:** When IMDS is unreachable and no CLI override is provided, `client_cloud` and `client_region` remain `null`, and `source` is set to `"unavailable"`.
- **Known limits:** Container runtimes with IMDS hop-limit 1 and ECS/Fargate tasks (metadata at `169.254.170.2` / `$ECS_CONTAINER_METADATA_URI_V4`, not EC2 IMDS) fail closed to `unavailable`; attest with `--client-region` there. IMDS requests bypass proxies and only tight region tokens (`^[a-z0-9][a-z0-9-]{0,63}$`) are accepted, so a middlebox error page can never become a published region.

### 3. Post-Benchmark Statement Overhead Probe (`statement_overhead_ms`)

- **Execution timing:** The overhead probe executes **post-benchmark**, immediately after all benchmark workload queries have finished and before database disconnection or container teardown.
- **No cache or timing contamination:** Executing post-benchmark ensures the probe queries never warm buffer caches, interfere with query stream sequencing, or affect benchmark timing measurements.
- **Protocol-level fidelity:** The probe executes 5 consecutive lightweight queries (typically `SELECT 1` or platform equivalent) through the active database adapter connection. This measures the complete round trip: client runtime → client driver serialization → network transport → server session parsing → execution → result retrieval.
- **Metrics:**
  - `min`: The lowest latency sample across the 5 iterations. This establishes the theoretical latency floor of the connection.
  - `median`: The median latency sample across the 5 iterations, capturing typical round-trip overhead.

### 4. Results Explorer Read Model v10

- Results Explorer read model v10 ingests `environment.client_link`.
- Locality disclosures appear as informative badges on result cards and benchmark detail views (e.g., `Client: AWS us-east-1 (1.4ms floor)`).
- Compare views evaluate client↔platform locality alignment:
  - When comparing runs with similar client-link characteristics, comparisons proceed normally.
  - When comparing a co-located run (e.g., intra-region cloud VM) against a high-overhead run (e.g., remote laptop over WAN), the comparison view displays an informative soft warning indicating that round-trip overhead differences may account for small query performance variance.

## Amendment: Round Trip Removed and Platform Timings

- Date: 2026-10-17

### Why

The disclosure above tells readers that a DuckDB-versus-Snowflake compare at a small scale factor partly measures the network. It does not let them answer the question many of them ask: how do the two compare once the client round trip is taken out? BenchBox already measures that round trip with the statement overhead probe, so the system under test does not grade itself. Separately, the platform-reported timings that adapters already read (for example Snowflake `QUERY_HISTORY`) never reach the result bundle, so nobody can check one against the other.

### What changes

1. **End-to-end time stays the only default.** Run pages, leaderboards and Compare default to the client's end-to-end time for every cohort. No other view is chosen automatically.
2. **An opt-in estimate on Compare.** Compare may offer a view labeled *round trip removed (estimated)*. For each networked query execution it removes `request_count × statement_overhead_ms.min`, where `request_count` is the number of requests the adapter sent inside the query's timer. It is always shown in one sentence with the end-to-end figure, never in place of it. Leaderboards stay end-to-end.
3. **Each adapter must earn the view.** `SELECT 1` on most cloud services runs through parse, compile and admission, so the floor can include server work. An adapter is eligible only after conformance runs show that its probe's platform-reported time is a small share of the floor and that the removed amount stays below the query's time outside the platform. Every adapter starts ineligible. Core publishes the outcome per run as `run.net_estimate_status`.
4. **Embedded runs remove nothing.** With the client and engine in one process there is no network round trip, so their figure in the view equals their end-to-end time.
5. **A reliability floor inside the view.** A query whose estimate leaves under 10% of its end-to-end time for any networked run in the cohort is *round-trip dominated*. It is left out of the view's geomean and win counts for every run, and shown as such, never as a number. The rule is fixed, the same for every platform, and shown on the comparability receipt. It labels no run and ranks nothing, so it is not a latency band.
6. **Timers measure only the query.** Before the view ships, adapters move tag setup, stats lookups, validation, plan capture and power-test query generation out of the measured timer. Runs record `run.timing_method`; the estimate needs method 2 or later, and runs are compared and ranked within one method.
7. **Platform timings in result bundles.** Schema 2.4 records, per query execution, the platform's own timing fields for each request, captured by platform statement ID and never by tag, with a derived `platform_ms` and a `valid`, `suspect` or `unavailable` status. Values are marked, never deleted. The probe's own statements are captured the same way, and `client_link` gains `probed_at`. These timings stay out of the Results Explorer until a page needs them.

### What stays rejected

- Removing the round trip by default, or ranking leaderboards on the estimate.
- Named or fixed latency bands that label or rank runs.
- Subtracting the median floor: the median can exceed the round trip a query paid. The median only bounds the range shown.
- ICMP and TCP connect probes, as above.

### Terms check

Result bundles are public downloads, so a platform's current terms were read for two things: conditions on publishing benchmarks, and any limit on republishing the query telemetry a customer retrieves.

Outcome as of 2026-10-22, every row read in full from the live pages:

| Platform | Benchmark terms | Telemetry republication | Read from |
| --- | --- | --- | --- |
| BigQuery | Allowed for tests the customer runs itself, if the disclosure includes what is needed to replicate them and Google may benchmark the customer's public products in return. Not on behalf of a hyperscale cloud provider without consent (Google Cloud Service Specific Terms, General Service Terms §7, modified 2026-10-12). | No clause found. Confidential Information names no job metadata or performance data (Google Cloud Terms of Service §7.1, §14.19). | Live page, 2026-10-22. |
| Redshift, Athena | Allowed. Replication information goes in the disclosure and to AWS, with reciprocity (AWS Service Terms §1.8, updated 2026-10-13). Performance observations of Beta or Preview services are AWS Confidential Information (§2.3, §2.5). | No clause found. The Customer Agreement's publicity ban (§11.9) yields to the Service Terms (§11.2), so §1.8 disclosures are allowed. | Live page, 2026-10-22. |
| Snowflake | Allowed if the results are shared with Snowflake reasonably in advance and the disclosure includes what is needed to replicate the test. Preview results need Snowflake's written approval (Acceptable Use Policy §IV, updated 2025-02-03). | No customer-side clause found. Usage Data terms bind only Snowflake (Terms of Service §4.2, updated 2026-04-16). | Live page, 2026-10-22. |
| Databricks | Allowed with replication information and reciprocity; Beta services excluded. Free Edition results need Databricks' express permission (Acceptable Use Policy, modified 2026-03-20; Free Edition AUP). | Risk: AUP item 7(b) bars bulk or systematic export of, or persistent copies of, "metadata generated by the Databricks Services" except as the Agreement expressly authorizes. | Live page, 2026-10-22. |
| ClickHouse Cloud | Allowed. Replication information goes in the disclosure and to ClickHouse, with reciprocity. Beta, preview and early-access results are confidential (Acceptable Use Policy §2, effective 2026-07-16). | No clause found. ClickHouse owns the Usage Data it collects (Cloud Addendum §2.4.2), which places no limit on the customer. | Live page, 2026-10-22. |
| MotherDuck | Allowed. All data, code and information needed to replicate goes in the disclosure and to MotherDuck, with reciprocity (Master Subscription Agreement §1.5, updated 2026-10-05). Performance information from free, trial and beta use is confidential (§15). | Risk: "MotherDuck's Confidential Information includes any technical or performance information about the Services" (§13.1). | Live page, 2026-10-22. |
| Starburst Galaxy | No benchmark clause (Galaxy Terms of Service, no date shown). | No clause found. Confidentiality covers information that "relates to" Starburst's technology (§4). | Live page, 2026-10-22. |

BenchBox result bundles already carry the configuration and query text needed to replicate a run, which meets the replication conditions above. Where a row also asks for the replication information to be sent to the vendor, or for the results to be shared in advance (Snowflake), the publisher meets that condition outside the bundle. The project owner decided on 2026-10-21 that public bundles carry `platform_timing` for every platform. After the reads above, the owner confirmed that decision on 2026-10-22 for MotherDuck and Databricks, treating platform timings as part of the benchmark disclosure that MotherDuck §1.5 and the Databricks benchmark paragraph allow. The MotherDuck §13.1 and Databricks 7(b) risks are accepted as recorded in the table. The fields are timings a customer retrieves about its own queries. Validation limits each request's raw `fields` to numbers, booleans and null, so no vendor name, user, warehouse or query text can ride along, and public anonymization replaces each platform statement ID with a `statement_` pseudonym. The table is not a publication gate. If a later read finds a stricter restriction, a new amendment names the platform and how its fields are withheld.

### Consequences of the amendment

- Readers can answer both questions, delivered latency and the comparison with the round trip removed, without the estimate ever becoming the headline.
- The estimate is conservative: the min floor comes from a warm cursor and leaves out cursor creation and extra fetch round trips, so it under-corrects networked runs.
- Published numbers change when the timer fix lands. `timing_method` keeps old and new runs apart.

## Privacy & Anonymization Analysis

Locality disclosure must never compromise submitter privacy or leak infrastructure topology. We enforce strict boundary invariants:

1. **No IP addresses:** Neither public nor private (RFC 1918) IP addresses are ever collected, probed, or written into `client_link`.
2. **No hostnames or DNS records:** Machine hostnames, FQDNs, local network domain names, and database connection hostnames are excluded.
3. **No cloud resource identifiers:** Cloud instance IDs, MAC addresses, VPC IDs, subnet IDs, security group IDs, and cloud account/tenant IDs are strictly omitted from IMDS extraction.
4. **Machine ID isolation:** Host hardware identification remains confined to `environment.client_host.machine_id`; publication omits this field.
5. **Macro-granularity only:** Locality is disclosed solely at the provider (`"aws"`, `"gcp"`, `"azure"`) and region level (`"us-east-1"`, `"eu-west-2"`). This granularity provides full topological transparency for benchmarking purposes without identifying individual VPCs or accounts.

All fields in `environment.client_link` are safe for public release in open repositories, adhering to the public contracts established in `docs/reference/public-contracts.md` and `docs/reference/hosted-results-contract.md`.

## Consequences

- **Positive:**
  - Full transparency for cloud data warehouse benchmarks: network round trips can no longer be confused with platform query execution speed.
  - Consistent with the DuckLake remote-backed results decision: topology is disclosed truthfully rather than estimated or suppressed.
  - Clean separation of concerns: post-benchmark probe guarantees benchmark integrity while providing accurate protocol-level overhead figures.
  - Privacy-preserving: zero leakage of IPs, hostnames, VPCs, or instance IDs.
- **Neutral:**
  - `environment.client_link` is optional and backward-compatible. Older bundles omitting this block remain valid schema-v2 results.
  - When IMDS is blocked or absent, collection degrades gracefully to `partial` or `unavailable` without interrupting benchmark execution.
