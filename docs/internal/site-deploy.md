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

## Documentation source freeze

Moving the documentation sources to the site repository needs the site-owned
Markdown to stay identical between the pinned snapshot and the import. While the
move is pending, `scripts/check_docs_freeze.py` rejects any pull request that
adds, edits or deletes a site-owned source. It is temporary and is retired by
the migration removal pull request.

**What is frozen.** The `site-owned` entries of `quality/docs-ownership.json`,
split by `split_ownership` in `scripts/docs_history_filter.py`, so the gate, the
history filter and the site bundle read the same ownership. `core-only` and
`core-generated` entries, the generated outputs the manifest lists
(`docs/benchmarks/queries/`, the compat and tuning matrices) and everything
outside `docs/` are not frozen. Core-only documents stay editable, and a new
`core-only` entry may be added to the manifest. A change to the manifest that
alters the frozen set or the generated list (re-owning a site-owned path, adding
a site-owned path, or deleting the manifest) fails too, so the freeze cannot be
dissolved by editing the map it reads.

**Where it runs.** The gate is a step of the `example-validation` job in
`.github/workflows/ci.yml`, which the required `docs` context aggregates
(`needs: [ci-paths, example-validation, spellcheck]`). A skipped or failed
`example-validation` fails `docs`, so the existing required context enforces the
freeze with no change to hosted settings or check names. Every frozen path lives
under `docs/`, which always routes the change to the docs unit. The step runs on
`pull_request` events from a copy of the script and of
`scripts/docs_history_filter.py` taken from the PR base revision, so a pull
request cannot weaken the gate it is judged by. It diffs `base...HEAD`; if the
base has no gate, it exits successfully, which is what lets the pull request that
introduces the gate and the pull request that removes it merge. Locally, run
`make docs-freeze-check BASE_REF=origin/develop`.

**Snapshot S.** S is the full `develop` head after the pull request that adds
this gate merges. The query-links change merged before the gate, so S contains
it. Record S and the ownership-map digest only then; any later frozen-path
change is rejected, so S stays equal to the site-owned projection until the
import.

**Retirement.** The migration removal pull request deletes
`scripts/check_docs_freeze.py`, the `ci.yml` step and the `docs-freeze-check`
target, deletes the frozen sources and transitions the ownership entries, all in
one pull request. The gate judges that pull request from the base copy, and
treats it as the reviewed removal only when the head tree has no
`scripts/check_docs_freeze.py` and neither `.github/workflows/ci.yml` nor
`make/documentation.mk` mentions the gate. In a removal, deleting frozen
sources and rewriting the ownership manifest are allowed. Adding or editing a
frozen source still fails, and a pull request that keeps any part of the
registration gets no exemption. Because `ci.yml` and the gate are protected
paths, the removal needs the oracle review.

**After S.** The history filter runs through S as described in
`docs/internal/docs-history-filter-runbook.md`, and the filtered history is
archived on the site repository as `history/docs-through-<S>`. The import pull
request then squashes the snapshot. The freeze stays in place until the removal
merges.
