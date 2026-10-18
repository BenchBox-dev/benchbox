# BenchBox SQLGlot downstream distribution

## Objective and completion

BenchBox will depend on an independently versioned SQLGlot distribution that
carries a small, tested patch series and can regularly refresh from tagged
upstream releases. Ordinary pip and uv installations must select the same
implementation. Installing upstream SQLGlot in the same environment must not
overwrite the fork or change the AST classes BenchBox uses.

Implementation covers `joeharris76/sqlglot` and `BenchBox-dev/BenchBox`.
Completion means the source and packaging changes are reviewed and merged in
the fork, a verified release is available to downstream installers, and the
BenchBox migration is reviewed, tested and merged. Public package publication
requires a separate owner approval after the artifacts are reviewable. Do not
mark the publication or adoption items done before those outcomes occur.

The implementation batch is serial. Cross-repository and publication-separated
items do not use prepared feature delivery or pretend a local artifact is a
published dependency. Preserve the other agent's live BigQuery compatibility
claim and consume its merged result rather than editing its worktree.

## Prior art and recorded decisions

- Extend the patch branches in `joeharris76/sqlglot`, upstream dialect tests,
  `sqlglot/parsers/postgres.py`, `sqlglot/generators/sqlite.py`,
  `sqlglot/optimizer/annotate_types.py`, and AST serialization tests.
- Extend BenchBox's `_project/sqlglot-upstream/repros/repro_all.py`,
  `repros/sqlite_extract.py`, `repros/generator.py`,
  `scripts/check_sqlglot_repro_retirement.py`, its unit tests, and the required
  `ci.yml` wiring. Do not introduce another compatibility rule engine.
- Supersede the no-fork non-goal and main-release-line assumption in
  `docs/development/adr/adr-sqlglot-use-and-non-use.md`. Preserve the boundary
  in `adr-sql-compat-phase-aware-pipeline.md`: engine bugs, physical DDL,
  benchmark variants, DataFrame translation and governance remain BenchBox's.
- The existing `sql-transpiler-maintenance-decision` describes an earlier
  SQLGlot-versus-Polyglot comparison. The owner's current request selects a
  SQLGlot fork; record this decision without claiming the comparison or ML
  experiments were completed. Preserve their unfinished evidence separately.
- Consume `sqlglot-upper-bound-bigquery-filter-compat` as an ordinary merged
  prerequisite. Extend the already completed auto-retirement implementation.
- The future-state index does not propose a transpiler replacement or authorize
  moving other BenchBox subsystems into a companion package. This distribution
  changes only the SQLGlot dependency and its release ownership.
- New infrastructure is limited to fork release assembly, artifact verification,
  and a refresh workflow. Python distribution names and import names are
  independent. A package named `benchbox-sqlglot` that also installs `sqlglot/`
  can collide with upstream. pip's automated vendoring/import relocation is
  relevant prior art; reuse an existing tool if it fully handles SQLGlot's
  dynamic imports and self-contained sdists, otherwise implement the smallest
  standard-library assembly step and prove those boundaries.

## Source base and patch admission

Use upstream `v30.23.0`, commit
`ad8764a0436a7b5c500b783ebe50dbf20c1e477c`, as the initial candidate.
BenchBox currently locks public SQLGlot 30.18.0. Adoption of the candidate is
conditional on the existing BigQuery audit and all migration checks passing.
Do not silently substitute a different upstream base after review.

Create a named fork feature branch from this tag and open a PR targeting the
existing fork's `main`. Preserve the upstream ancestry through normal PR
integration; do not rewrite the consumed default branch or release tags.
Future refreshes rebase an unpublished candidate patch series onto a new tag
and integrate a reviewed final tree through the same PR workflow.

Admit these commits in dependency order, resolving adjacent upstream changes
without copying unrelated branch work:

1. PostgreSQL extraction return types:
   `eb360e1e2962b467ca093b9f6ab1000f9ced1a02`.
2. SQLite extraction and source-aware division:
   `34cd3e9fdb29a4376023f9c6cd32932de07acd3d`. This branch includes the
   PostgreSQL commit; apply its unique extraction commit only once.
3. SQLite date/interval arithmetic:
   `b60935672d3fc52c4982077131d4cb5140a69b72`.

Record every prepared branch in `benchbox/patches.json`, including its source
SHA, applicability, tests, dependency, and carried/upstreamed/excluded decision.
SingleStore percentile ordering is upstream PR 8352. Projection-width behavior
overlaps PR 8325 and its replacement in 8440. Qualified-star exclusions overlap
8247; the branch's broader unfinished design is not automatically equivalent.
Test the relevant behavior on the candidate before omitting a patch. Keep
regression coverage for upstreamed behavior. QuestDB remains in BenchBox;
building a new dialect is a separate capability, not a prerequisite for this
bounded distribution.

Verify PostgreSQL EXTRACT numeric versus DATE_PART double return types,
PostgreSQL integer division, DuckDB integral EXTRACT, explicit casts, negative
and NULL values, columns, nested expressions and CTEs. Execute generated
SQLite SQL, including date arithmetic and timestamp cases, and compare
expected values and types. Parsing or matching output strings is insufficient.
Source database execution may use existing local engines; no live-cloud spend
or new database service is authorized. State any source-engine proof limitation.

## Distribution and artifact contract

The distribution is `benchbox-sqlglot`; its import package is
`benchbox_sqlglot`. Initial public version is `30.23.0.1`, where the first three
components identify the upstream base and the fourth identifies this fork's
release. Never reuse a version for different bytes. Do not use a local `+`
version identifier for PyPI. Preserve upstream license files and notices.

Keep upstream's `sqlglot/` source layout for tests and upstream refreshes.
Add a narrow release assembler under `benchbox/` and Make entrypoints for
building and checking the downstream distribution. Assemble into a fresh
output directory; never rename source files in place or modify another tree.
The release assembler must:

1. Validate the manifest, upstream base, fork source revision and clean input.
2. Copy only runtime sources, required resources and license/readme material
   into an isolated build project providing `benchbox_sqlglot/`.
3. Rewrite absolute Python imports and dynamic module-loading references,
   including `sqlglot.dialects.{key}`. Preserve public API names and SQL text.
   Do not add a `sys.modules['sqlglot']` alias or fallback to upstream imports.
4. Generate consistent version metadata plus packaged provenance containing
   the upstream SHA, fork SHA, patch manifest and distribution version.
5. Build a pure-Python wheel and self-contained sdist with the existing uv /
   setuptools toolchain. Building a wheel from the unpacked sdist must work
   without the original repository, its `.git`, or a sibling source path.
6. Exclude upstream's `sqlglotc` and `rs` extras from the downstream metadata.
   Preserve upstream development and compiled-test infrastructure separately.
7. Produce artifact hashes and test the actual artifacts, not the checkout.

Artifact checks install the wheel and the sdist-built wheel into fresh
environments outside the repository. Cover Python 3.11 through 3.14. Also
install upstream SQLGlot, import the two packages in both orders, exercise
dynamic dialect lookup, AST round trips, serialization and optimizer entry
points, and prove each class/module belongs to its own namespace. Uninstall
one distribution and verify the other remains usable. An import scanner is
supporting evidence; it is not a substitute for execution.

## CI, release and refresh operations

Add fork CI for source regressions and built-artifact checks. Use uv and
existing Make entrypoints. Run upstream `make check` before source publication;
the downstream pure-Python wheel does not imply compiled upstream tests may be
silently removed. Keep new CI focused on the declared downstream Python range.

Replace or disable the inherited automatic `v*` package-publish workflow in
the fork before any release tag is created. Its existing jobs publish
upstream-named `sqlglot` and `sqlglotc` and write generated docs/changelog.
The downstream release workflow validates an explicit `benchbox-v30.23.0.1`
tag/version pair, builds and verifies artifacts before publication, uses the
correct distribution identity, and requires the owner's approved publishing
credentials / protected environment. Do not weaken settings or invent secrets.
Retain downloadable CI artifacts if publishing configuration is unavailable.

The maintainer owns a monthly upstream refresh review and urgent correctness
or security refreshes. A scheduled read-only check reports a newer tagged base;
it must not auto-publish, rewrite refs, or open unrequested upstream PRs.
Provide an explicit refresh command/runbook that records the old/new upstream
SHAs, replays the carried patches on a candidate, fails on conflicts, reruns
source and artifact tests, and requires review of upstreamed/excluded patches.
Keep immutable existing artifacts and tags. Rollback selects the last tested
fork release and restores the corresponding BenchBox dependency/lock change.

## BenchBox adoption

After the verified package is published and the BigQuery prerequisite is
merged, update the current develop-based write worktree to that base.

- Use `uv remove` / `uv add` to replace the public `sqlglot` dependency with
  the exact tested `benchbox-sqlglot` release and regenerate `uv.lock`.
  Keep plain pip-installed wheel metadata consistent with uv resolution.
- Change runtime imports in the discovered `benchbox/**/*.py` consumers to
  `benchbox_sqlglot`, including direct AST users, the base benchmark path,
  platform adapter mixin and the central dialect wrapper. Inventory again at
  implementation time; the prior count of 24 modules is not a frozen target.
- Update tests, monkeypatch targets, type configuration, generator/repro tools
  and dependency inventories where they describe the active runtime. Keep
  unpatched-upstream comparison probes explicitly separate so they can still
  establish when a carried patch is removable.
- Make the retirement guard parse TOML and compare the dependency identity
  (distribution, version and artifact/source identity), rather than a regex
  matching only the public `sqlglot` version. Run on any identity change.
  Compare explicit baseline outcomes / accepted retirement decisions so a
  previously acknowledged passing reproduction does not fail every update.
- Retire only syntax workarounds demonstrated redundant through both actual
  BenchBox translation paths with `identify=True`. Preserve exact SQLite Q6
  decimal bounds, engine-semantic transforms, physical DDL, source normalization
  and unrelated query variants. Narrow helpers rather than deleting mixed
  policy/code wholesale. Record any remaining overlap and its reason.
- Update the SQLGlot ADR, dialect translation docs and repro documentation with
  dependency ownership, supported version, boundary, refresh and rollback.

Run targeted wrapper, adapter-delegation, SQLite Q6, TPC-Havoc BigQuery, AST
consumer, repro/generator and retirement-guard tests. Run configured Ruff,
type checks, comment-policy, compatibility-doc checks and the repository
preflight. Validate BenchBox's built wheel and sdist outside the checkout with
both pip and uv and upstream SQLGlot present. Complete required CI and current
head oracle review, then use the native PR workflow through verified merge.

## TODO breakdown and delivery order

1. `sqlglot-soft-fork-source`: rebase/admit the bounded source patch series,
   disposition the other branches, add the manifest and execute source tests.
   Terminal evidence: reviewed merged fork source PR and exact revision.
2. `sqlglot-soft-fork-distribution`: implement isolated release assembly,
   artifact tests, CI, release safety and the refresh command/runbook.
   Needs source item. Terminal evidence: reviewed merged fork packaging PR,
   verified wheel/sdist hashes and passing artifact checks.
3. `sqlglot-soft-fork-publish`: obtain owner publication approval for those
   exact artifacts, configure only owner-authorized publishing access, publish
   the downstream version and verify a fresh public install. Needs distribution
   item. A denied approval or missing owner-only configuration blocks this
   item without marking it done or bypassing it with a local path dependency.
4. `sqlglot-soft-fork-adoption`: migrate BenchBox imports/dependencies, update
   the guard and decision/docs, retire proved redundant syntax fixes, verify
   built distributions and merge through native gates. Needs publication and
   the existing BigQuery compatibility item. Terminal evidence: merged
   BenchBox PR, exact dependency, required checks and post-merge verification.

## Review contract

The independent plan reviews judge this revision before implementation.
Each finding retains its reviewer, severity and evidence. Accepted findings
name a plan correction and the proving check; rebuttals require checkable
counter-evidence. The revised plan and the full disposition table accompany
the implementation TODOs. No reviewer writes code or changes tracker state.
