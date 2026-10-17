# Site deploy

benchbox.dev is built and deployed from
[`BenchBox-dev/benchbox-site`](https://github.com/BenchBox-dev/benchbox-site)
by its `.github/workflows/deploy.yml`. Deploys, rollback, the deploy target
variable and the gates are described in the "Deploys" section of that
repository's `AGENTS.md` and in its `README.md`.

This repository feeds the site through one artifact. After each successful
push to `develop`, `.github/workflows/site-inputs.yml` builds the
`site-inputs-<sha>` bundle with `make site-inputs` (`scripts/site_inputs.py`)
and sends it to the site repository as a `core-bundle` dispatch. The bundle
carries the docs Markdown, the Explorer snapshot and fixtures, the prompt
catalog and the attestations the site's deploy checks.

The site, the Results Explorer app, the landing page, the blog and blog drafts
live only in the site repository. Change them there by pull request.
