---
audited_source_sha: 435ff8b884d6af12703238c8850b894a276e882d
candidate_inventory: _project/audits/docs-remediation-site-inventory-435ff8b88
live_deployment_id: 6924683006
live_source_sha: 8c5104f4de43706598b9e286c585640f62d4126f
result_snapshot_sha: cec1a69d32689e71af0b9246d9ea76d96ae1ef03f31a3bd575b5a8b2bb0ec966
result_corpus_sha: 64f5d8638df5f55a0f36c4b146ebdc73b55d6f66
---

# Full public-surface audit

This audit checks the candidate built from `audited_source_sha` and the live
production snapshot identified above. The candidate inventory is committed
beside this report. Production is still on an older source revision; this audit
does not claim that the candidate changes are live.

## Candidate inventory and verification

`make site-build` completed from the audited source revision: 1,846 HTML pages
built and 1,839 pages indexed by Pagefind. The inventory contains 4,265 paths
and 1,847 pages. `make site-inventory-check SITE_DIR=website/dist
SITE_INVENTORY=_project/audits/docs-remediation-site-inventory-435ff8b88`
reported zero missing paths, fragments, internal links, images, feed entries,
canonical routes, 404 fallback changes, heading or metadata changes,
unreferenced images, and stale allowances.

The inventory has 12 JSON files in
`_project/audits/docs-remediation-site-inventory-435ff8b88/`. It records the
source SHA, routes, page metadata, paths, and local link graph. The local
`website/dist/results/` build does not include the production `results.duckdb`
artifact, so result-data inspection used the live receipt and snapshot below.

Other candidate checks:

- `npm --prefix website test`: 35 files, 331 tests passed.
- `npm --prefix website run check`: 0 errors, 0 warnings, 11 existing hints.
- `make comment-policy-check`: 5,790 files checked, 0 violations; 19 native
  tests passed.
- Candidate browser smoke showed the submission steps correctly nested and
  the Atom self-link plus all 10 entry links on canonical `/blog/` routes.

The repository's default site-inventory diff baseline is the prior Sphinx site,
not this Astro build, so it is not a valid route-by-route comparison. A diff
against the earlier Astro candidate inventory reported 89 missing
content-hashed Pagefind files and the 10 old `/blog/blog/` Atom entry IDs as
removed. These are expected content-hash and canonical-ID changes, not missing
current pages: the final inventory check is clean, and every replacement feed
link points to a built canonical page. The diff command itself returned
non-zero; this report does not represent it as a passing check.

## Changed and excluded live routes

HTTP status checks were run against the live site on 2026-10-11. The source
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
| `/blog/blog/2026-10-07-site-redesign.html` | 404 | Old feed target. |

Additional live route and asset checks returned 200 for `/blog/`, `/results/`,
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
- The candidate and live Pagefind dialog both failed a real search with
  `Failed to load Pagefind metadata`; the query remained in `Searching…`. The
  JavaScript and entry manifest returned HTTP 200, but this does not establish
  that the worker's metadata, indexes, and WASM all load. This is a separate
  confirmed search defect.

## Live result snapshot

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

The initial live Results Explorer overview rendered 414 rows. In-app
navigation to `/results/query` displayed the rows, filters, and table. Fresh
direct query tabs returned a static-host 404 before the fallback and then
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
3. Emits canonical Atom self and entry links and removes the ten obsolete
   `/blog/blog/` exceptions from `known-broken-links.json`.

Three independent follow-ups remain open:

- Pagefind metadata loading fails in both candidate and live search.
- Fresh Results Explorer loads can time out during DuckDB initialization;
  in-app navigation from a loaded overview worked.
- Backend exception redaction needs a synthetic-value trace. Current evidence
  is generic and does not establish a leak.

No production deployment was performed. The live Atom feed and API/blog prose
therefore still reflect the older deployment until the stacked changes merge
and an authorized deployment occurs.
