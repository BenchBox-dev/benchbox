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

## Candidate bundles

The published bundle is exact schema 3. `scripts/site_inputs.py build` takes
`--profile exact3|candidate4|candidate5`, and `exact3` is the default. Candidate
profiles are local or CI builds that nothing publishes:
`make site-inputs-candidate SITE_INPUTS_CANDIDATE=candidate4` (or `candidate5`)
builds and verifies one. The publication workflow never passes a profile.

| Profile | Schema | Members |
|---|---|---|
| `exact3` | 3 | `docs`, `repo-files.json`, `explorer/{results.duckdb,contract.json,fixtures,parity,bundles}`, `landing/prompt-catalog.json`, `api-public-symbols.json`, `downloads`, `attestations.json` |
| `candidate4` | 4 | the schema 3 members plus `docs-data` (a directory holding `docs-data.json`) and `doc-contracts.json` |
| `candidate5` | 5 | the schema 4 members without `docs` and `downloads` |

`verify` selects the exact member set from `manifest.schema` and rejects any
other set. For schema 4 and 5 it also validates `docs-data/docs-data.json`
against `quality/docs-data.schema.json`, validates `doc-contracts.json` against
`quality/doc-contracts.schema.json`, and requires `certified_by` to be
`candidate`, so a candidate never looks like a certified publication artifact.
A consumer that only accepts schema 3 rejects these bundles. Making schema 4 or
5 the published default is a separate change that follows consumer deployment.
