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
carries the Explorer snapshot and fixtures, the prompt catalog, the
`docs-data` and `doc-contracts.json` facts, and the attestations the site's
deploy checks. The site-owned Markdown lives in the site repository; core no
longer ships docs sources or downloads.

The site, the Results Explorer app, the landing page, the blog and blog drafts
live only in the site repository. Change them there by pull request.

## Bundle profiles

The published bundle is schema 5. `scripts/site_inputs.py build` takes
`--profile schema5|schema4|exact3|candidate5|candidate4`, and `schema5` is the
default: the schema 4 members minus `docs` and `downloads` (11 members:
`repo-files.json`, the five `explorer/` members, `landing/prompt-catalog.json`,
`api-public-symbols.json`, `attestations.json`, plus `docs-data` and
`doc-contracts.json`). The publication workflow never passes a profile.
`exact3` is the rollback profile: it emits the previous schema 3 bundle,
which the site still accepts.

Schema 5 keeps the schema 4 contract checks: `verify` selects the exact member
set from `manifest.schema` and rejects any other set, validates
`docs-data/docs-data.json` against `quality/docs-data.schema.json` and
`doc-contracts.json` against `quality/doc-contracts.schema.json`, and checks
`certified_by` the way the site's verifier does (schemas 4 and 5 take
`candidate` or a numeric run id string). `build` records
`candidate` when no `--certified-by` is given (schema 3 keeps `local`). The
site's deploy additionally requires a numeric run id whose run is the
`trunk.yml` push on `develop` for the same commit and succeeded, so a locally
built bundle never deploys. The publication workflow's certifier wiring lives
in `.github/workflows/site-inputs.yml`.

The consumer must accept a schema before core emits it: a site that predates
schema 5 rejects the default bundle, so deploy the site first. Rollback after
the docs removal means building from a core revision that still has the docs
sources and generators: `schema4`, `exact3` and `candidate4` stay selectable,
but building a bundle that needs the relocated `docs` or `downloads` members
fails fast with an error pointing at the pre-removal core revision. `verify`
still accepts those older schemas.
