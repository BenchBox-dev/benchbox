# Publication deployment and cutover (retired)

The publication control plane no longer deploys benchbox.dev. These
workflows are disabled in GitHub:

- `publication-transaction.yml`
- `publication-deploy.yml`
- `publication-preview-deploy.yml`
- `publication-corpus-cutover.yml`

benchbox.dev is built and deployed from `BenchBox-dev/benchbox-site` by its
`.github/workflows/deploy.yml`. For deploys, redeploys and rollback, see the
"Deploys" section of that repository's `AGENTS.md`. To get a change under
`results-explorer/`, `website/`, `landing/`, `docs/blog/`, `_blog/` or
`_project/design/site-inventory/` to production, replay it there by pull
request; see "Replaying core changes" in the same file.

`sync-results-data-to-published.yml` is not a Pages writer. It mirrors
`results-data/` into `published-results` and stays.

The decision to split the site out of this repository is
[`adr/adr-site-repo-split.md`](adr/adr-site-repo-split.md). The earlier
publication design is in
[`adr/adr-independent-publication-authorities.md`](adr/adr-independent-publication-authorities.md).
The old promotion, rollback and cutover procedures are in this file's git
history.
