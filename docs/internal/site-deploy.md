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

## Bundle profiles

The published bundle is schema 4. `scripts/site_inputs.py build` takes
`--profile schema4|exact3|candidate4|candidate5`, and `schema4` is the default.
The publication workflow never passes a profile; the `develop` push run's id
becomes `certified_by`. `exact3` is the rollback profile: a core change that
makes `exact3` the default (or a local `make site-inputs SITE_INPUTS_PROFILE=exact3`)
emits the previous schema 3 bundle, which the site still accepts. Candidate
profiles are local or CI builds that nothing publishes:
`make site-inputs-candidate SITE_INPUTS_CANDIDATE=candidate4` (or `candidate5`)
builds and verifies one.

| Profile | Schema | Members |
|---|---|---|
| `schema4` | 4 | the `exact3` members plus `docs-data` (a directory holding `docs-data.json`) and `doc-contracts.json` |
| `exact3` | 3 | `docs`, `repo-files.json`, `explorer/{results.duckdb,contract.json,fixtures,parity,bundles}`, `landing/prompt-catalog.json`, `api-public-symbols.json`, `downloads`, `attestations.json` |
| `candidate4` | 4 | the same members as `schema4`, certified by `candidate` |
| `candidate5` | 5 | the schema 4 members without `docs` and `downloads` |

`verify` selects the exact member set from `manifest.schema` and rejects any
other set. For schema 4 and 5 it also validates `docs-data/docs-data.json`
against `quality/docs-data.schema.json` and `doc-contracts.json` against
`quality/doc-contracts.schema.json`, and checks `certified_by` the way the
site's verifier does: schema 4 takes `candidate` or a numeric run id, schema 5
takes only `candidate`. The site has no `local` certifier for schema 4, so
`build` records `candidate` when no `--certified-by` is given for `schema4`
(schema 3 keeps `local`). The site's deploy additionally requires a numeric run
id whose run is the `trunk.yml` push on `develop` for the same commit and
succeeded, so a locally built bundle never deploys.

The consumer must accept a schema before core emits it: a site that predates
schema 4 rejects the default bundle, so deploy the site first and roll core
back to `exact3` if a deployed site ever needs it.
