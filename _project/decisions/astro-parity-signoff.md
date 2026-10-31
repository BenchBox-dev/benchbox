# Astro parity sign-off

Status: approved
Approval: approved

This record is the cutover gate for the Astro site. The owner reviewed the URL
compatibility report below and set both pending lines above to approved.

## How the report is produced

`make site-parity` builds the Sphinx site and the Astro site from one tree,
assembles the Sphinx site the way production does, and writes the URL
compatibility report to `site-parity/report/`. The CI job `site-parity` runs it
on every pull request that touches `docs/` or `website/`. The job is not a
required check. It uploads the report as the `url-compatibility-report`
artifact and copies it into the job summary.

The report covers:

- missing paths, lost anchors and fragments, changed titles and headings,
  broken internal links and missing images;
- tag pages, archives, series and categories, and generated query pages;
- Atom feed entry ids;
- the API URL map in `inventory/api-reference-url-map.json` of `BenchBox-dev/benchbox-site`;
- axe over every page template, in light and dark themes;
- Explorer and DuckDB bytes on docs and blog pages;
- the privacy scan on the Astro output;
- a Pagefind smoke search.

Every redirect page and every allowed difference carries a reason and an owner
approval field. They live in `inventory/` of `BenchBox-dev/benchbox-site`:
`allowed-differences.json`, `redirect-pages.json`, `added-paths.json` and the
`expected-removals-*.json` files. Their approval field reads `pending` until
the owner edits it.

## Report summary

The numbers below come from the last local `make site-parity` run, made on
commit `ab44cd56a338eb82a462a8e257fedd061c5a280e`, which carries the stricter
canonical and objects inventory checks and the Astro visual capture. They are a
record of that run only. The SHA the owner approves is the one named in the
`site-parity` CI report, which records the pull request head, so the report SHA
below stays pending until the owner copies it from that run. Verdict of the
local run: PASS.

| Measure | Value |
| --- | --- |
| Public paths in the Astro site | 4617 |
| Pages in the Astro site | 1903 |
| Broken public URLs | 0 |
| Broken fragments | 0 |
| Broken internal links beyond the known-broken list | 0 |
| Missing images | 0 |
| Lost Atom entry ids | 0 |
| Missing tag, archive and query pages | 0 |
| Missing API URL map pages and anchors | 0 |
| Sphinx objects inventory entries lost (2104 compared) | 0 beyond 2 allowed |
| Objects inventory entries whose address differs or does not resolve | 0 |
| Objects inventory entries checked for validity only (blog posts moved out of docs) | 34 |
| Pages with a wrong canonical URL | 0 |
| Allowances that matched nothing | 0 |
| Redirect pages | 4 |
| Page templates under `website/src/pages` plus content templates checked by axe | 21, light and dark |
| Axe failures | 0 |
| Remaining differences of any kind | 0 |

Public files kept at their Sphinx paths: 50 images under `/docs/_images/`, 183
downloads under `/docs/_downloads/` (same hashed directories), and a generated
`/docs/objects.inv`. The inventory lists pages, labels, blog post labels and
heading targets, and the report compares it entry by entry with the Sphinx
inventory. It has no Python object entries, because the authored API pages
replaced autodoc, and it omits the two module index labels.

Informational only: against the published baseline taken at an earlier
commit, some download paths, fragments and headings differ. They come from
docs edits made since the baseline and affect both renderers.

## Decisions for the owner

Approved by the owner on 2026-10-04 and recorded as `approved` in the policy
files:

- every redirect page in `redirect-pages.json`: the not-found fallback and the
  retired `/docs/genindex.html`, `/docs/search.html` and `/docs/blog.html`;
- every entry in `expected-removals-sphinx-assets.json`: the Sphinx and Furo
  assets under `/_static/`, `/docs/_static/` and `/docs/_sphinx_design_static/`,
  `/docs/.buildinfo`, `/docs/searchindex.js` and the old landing page assets
  `/style.css`, `/script.js` and `/shared/`;
- the two module index labels in `allowed-inventory-losses.json`;
- every rule in `allowed-differences.json`, including the docs index title,
  the Footnotes heading, the headings on generated index pages, the added
  canonical and description metadata and the redirect page placeholders.

Approved by the owner on 2026-10-05 and recorded as `approved` in
`allowed-differences.json`, after the landing page was redesigned:

- `landing-redesign-headings`: the headings the redesigned landing page adds
  and removes on `/index.html`, with catalogue entries as `h4` under their
  group;
- `landing-hero-image`: `hero.png`, which the landing page no longer shows but
  still publishes as the social preview image.

Still pending: the new paths in `added-paths.json` (sitemap, robots, favicon,
`/_astro/`, `/pagefind/`, `/_images/` and `/blog.html`) and the
`expected-removals-*.json` files that carry no approval field.

## Approval

- Owner approval: approved
- Approved by: joeharris76
- Approval date: 2026-10-05
- Report SHA: 74f2faab8a95ede2e6714fd2b856606d2e6cb945

## Re-run on the cutover base SHA

An approval holds only for the SHA it was given on. Before the cutover starts,
run `make site-parity` on the cutover base SHA, attach the new report, and
record the result here. A report that is not clean, or that was produced on a
different SHA, voids the approval.

- Cutover base SHA: 172276a3216c6d76b15ec90ab7b9f2bc2d303c63
- Re-run result: PASS (site-parity, CI run 37360524148, job 111933957845)
- Re-run approval: approved (joeharris76, 2026-10-05)
