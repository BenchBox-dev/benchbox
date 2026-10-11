---
develop_sha: b933e20ace1af2ae08f02e415cc94878d52596a8
checked_sha: 8bea633cd0238e7a574faef009f87de1e299ffc4
measured_at_sha: 8bea633cd0238e7a574faef009f87de1e299ffc4
audited_source_sha: 8bea633cd0238e7a574faef009f87de1e299ffc4
candidate_inventory: _project/audits/docs-remediation-site-inventory-8bea633cd0
live_deployment_id: 6924683006
live_source_sha: 8c5104f4de43706598b9e286c585640f62d4126f
result_snapshot_sha: cec1a69d32689e71af0b9246d9ea76d96ae1ef03f31a3bd575b5a8b2bb0ec966
result_corpus_sha: 64f5d8638df5f55a0f36c4b146ebdc73b55d6f66
---

# Full public-surface audit

This audit checks the candidate built from `audited_source_sha` and the
last-known live production snapshot identified above. The candidate inventory
is committed beside this report. A later availability check found
`https://benchbox.dev/` and `/blog/` returning HTTP 404; see the current
availability recheck below. This audit does not claim the candidate changes
are live or that the public site is currently serving the earlier snapshot.

## Candidate inventory and verification

`make site-build` completed from the audited source revision: 1,846 HTML pages
built and 1,839 pages indexed by Pagefind. The inventory contains 4,265 paths
and 1,847 pages. `make site-inventory-check SITE_DIR=website/dist
`SITE_INVENTORY=_project/audits/docs-remediation-site-inventory-8bea633cd0`
reported zero missing paths, fragments, internal links, images, feed entries,
canonical routes, 404 fallback changes, heading or metadata changes,
unreferenced images, and stale allowances.

The inventory has 12 JSON files in
`_project/audits/docs-remediation-site-inventory-8bea633cd0/`. It records the
source SHA, routes, page metadata, paths, and local link graph. The local
`website/dist/results/` build does not include the production `results.duckdb`
artifact, so result-data inspection used the live receipt and snapshot below.

Other candidate checks:

- `npm --prefix website test`: 35 files, 331 tests passed.
- `npm --prefix website run check`: 0 errors, 0 warnings, 11 existing hints.
- `make comment-policy-check`: 5,790 files checked, 0 violations; 19 native
  tests passed.
- The candidate feed retains the ten historical Atom entry IDs while using
  canonical `/blog/` routes for its self-link and all entry links.

The repository's default site-inventory diff baseline is the prior Sphinx site,
not this Astro build, so it is not a valid route-by-route comparison. The
initial parity run found that canonicalizing the ten feed links also changed
their stable entry IDs. The feed now keeps those IDs and canonicalizes only the
link targets. A narrowly scoped approved allowance covers the obsolete
`/blog/blog/` href removals; it does not allow lost entry IDs.

`make site-parity` completed after local E2E fixtures were generated. Its
Results Explorer, shell accessibility, candidate Pagefind searches for a
documentation term and a blog title, and privacy/sensitivity checks passed.
URL compatibility reported 0 broken public URLs and 0 broken fragments. The
Sphinx/Astro and published-baseline diffs are informational because they
compare different site implementations.

## Changed and excluded live routes

Earlier HTTP status checks against the live site were run on 2026-10-11. The source
mirror under `docs/blog/` is not present in the candidate public inventory.
The ten old feed targets are not valid public routes; all return 404. The
candidate feed no longer points to them.

| Route | HTTP | Audit result |
| --- | ---: | --- |
| `/docs/reference/python-api/result-analysis.html` | 200 | Changed API guidance page. |
| `/blog/2026-09-15-results-explorer-introduction.html` | 200 | Changed public blog post. |
| `/docs/blog/2026-09-15-results-explorer-introduction.html` | 404 | Source mirror; absent from the Astro public inventory. |
| `/blog/atom.xml` | 200 | Live feed still has the old invalid `/blog/blog/` links; candidate feed is corrected. |
| `/blog/blog/2026-03-10-extracting-textcharts.html` | 404 | Old feed target. |
| `/blog/blog/2026-04-01-v0-2-0-release-summary.html` | 404 | Old feed target. |
| `/blog/blog/2026-04-26-scale-factor-harmonization.html` | 404 | Old feed target. |
| `/blog/blog/2026-04-26-v0-2-1-release-summary.html` | 404 | Old feed target. |
| `/blog/blog/2026-05-18-joinorder-imdb-2013-dataset.html` | 404 | Old feed target. |
| `/blog/blog/2026-05-18-sketch-functions-databricks-response.html` | 404 | Old feed target. |
| `/blog/blog/2026-05-18-v0-3-0-release-summary.html` | 404 | Old feed target. |
| `/blog/blog/2026-08-31-v0-4-0-release-overview.html` | 404 | Old feed target. |
| `/blog/blog/2026-09-15-results-explorer-introduction.html` | 404 | Old feed target. |

### Current availability recheck

A later browser navigation to `https://benchbox.dev/blog/` displayed GitHub
Pages' “Site not found” page. The static reader returned HTTP 404 for both
`https://benchbox.dev/` and `/blog/`. The search UI was absent, so deployed
Pagefind could not be retested. The cause is not established; no production,
domain, or Pages configuration changes were attempted.

Additional checks from that earlier observation returned 200 for `/blog/`, `/results/`,
`/robots.txt`, `/sitemap.xml`, `/docs/objects.inv`,
`/pagefind/pagefind-entry.json`, and `/pagefind/pagefind.js`. The static host
returns 404 for a direct `/results/query` request; browser evidence below
records its SPA fallback behavior. `/docs/_downloads/` has no directory
listing; individual download files were inspected.

## Published content and asset review

- All 1,752 built documentation HTML pages were text-extracted and scanned;
  high-risk pages were also reviewed manually.
- All 90 built blog pages were reviewed in full. The 54 public images and 304
  text files under `docs/_downloads` were inspected. Two linked academic PDFs
  were read.
- The review found no credentials, email addresses, private endpoints, local
  user paths, internal hostnames, or connection strings in those surfaces.
  Public benchmark hardware and platform metadata remain visible; the API page
  no longer provides the concrete machine-identifier hashing recipe,
  pseudonym mappings, or salt-linkability detail.
- All 1,868 Pagefind files were scanned: 1,856 compressed payloads and 12 other
  assets. No concrete fingerprint or salt-derivation recipe remained in the
  indexed payloads. This was a compressed-string signature scan, not a
  Pagefind-aware semantic decoder.
- All 117 generated JavaScript and CSS assets under `website/dist/_astro/` and
  `website/dist/results/assets/` were scanned for high-confidence credential
  signatures, developer-home paths, and private-host URLs. No credential or
  developer-home-path matches were found. The only private-host match was
  `https://explorer.internal` in the Results Explorer bundle. Source inspection
  traces it to `isInternalExplorerPath` in
  `results-explorer/src/lib/dashboards.ts`, where it is only a URL-parsing base
  for same-origin path validation, not a configured endpoint or network target.
- The supported local browser parity harness passed searches for a distinctive
  documentation term and a blog title. An earlier exploratory candidate and
  live browser run reported `Failed to load Pagefind metadata` and remained in
  `Searching…`. The latest live check could not retest Pagefind because the
  public root and blog routes returned 404. Worker metadata, indexes, and WASM
  loading remain unverified on the live site.

## Last-known live result snapshot

The live receipt identifies deployment `6924683006`, source
`8c5104f4de43706598b9e286c585640f62d4126f`, release `v0.4.2`, and result
snapshot `cec1a69d32689e71af0b9246d9ea76d96ae1ef03f31a3bd575b5a8b2bb0ec966`
with corpus SHA `64f5d8638df5f55a0f36c4b146ebdc73b55d6f66`. The downloaded
snapshot hash matched the receipt. The database has 14 tables and 414 result
records. All 414 public bundle URLs returned successfully (16,861,072 bytes
in total); recursive inspection covered 303,419 string leaves. No email,
credential, private-path, internal-domain, file-URI, connection-string, or IP
matches were found in the live set. Seven `client_link.collection_error_message`
values were the generic `AttributeError: statement overhead probe failed`.

The current source inventory contains 413 bundles, covering 303,094 string
leaves. Its three IPv4-pattern matches were `127.0.0.1` in a local Doris test
configuration. The source and live sets differ: four live-only IDs and three
source-only IDs. They are not byte-for-byte equivalent snapshots. The
published v0.4.0 text warns that backend exception text can repeat values; the
observed messages were generic, but other backend exception paths were not
verified. No current leak was established; code-level verification remains
open.

## Live application behavior

During the earlier successful live observation, the Results Explorer overview
rendered 414 rows. In-app navigation to `/results/query` displayed the rows,
filters, and table. Fresh direct query tabs returned a static-host 404 before
the fallback and then
showed `DuckDB instantiate did not respond within 30000ms`. A separate fresh
overview tab also timed out during DuckDB initialization. The root cause and
behavior in an independent browser profile remain unverified. The candidate
static build lacks the external result database, so this browser path was
checked against production only.

## Source changes and remaining findings

This candidate makes three targeted fixes:

1. Removes concrete hashing, pseudonym-format, and cross-submission salt
   guidance from the public API reference while preserving the API contract,
   environment variable, no-salt behavior, and safe examples.
2. Replaces stable-salt guidance in both blog copies with a private-salt
   requirement and a non-secret placeholder.
3. Emits canonical Atom self and entry links while preserving historical entry
   IDs, and removes the ten obsolete `/blog/blog/` link exceptions from
   `known-broken-links.json`.

Three independent follow-ups remain open:

- An earlier live Pagefind search reported a metadata-load failure. The
  supported candidate browser tests now pass, but deployed search cannot be
  rechecked while the public root and blog routes return 404.
- Fresh Results Explorer loads can time out during DuckDB initialization;
  in-app navigation from a loaded overview worked.
- Backend exception redaction needs a synthetic-value trace. Current evidence
  is generic and does not establish a leak.

No production deployment was performed. The earlier successful route checks
observed older content; the latest check found the public root and blog routes
unavailable. The live feed and API routes were not retested during the latest
check, and the stacked source changes remain undeployed.
