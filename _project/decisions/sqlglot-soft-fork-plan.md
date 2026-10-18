# BenchBox SQLGlot downstream distribution

## Objective and completion

BenchBox will depend on an independently versioned SQLGlot distribution that
carries a small, tested patch series and can regularly refresh from tagged
upstream releases. Ordinary pip and uv installations must select the same
implementation. Installing upstream SQLGlot in the same environment must not
overwrite the fork or change the AST classes BenchBox uses.
Coexistence guarantees independent implementations, not exchange of AST or
dialect class instances between them. No implicit AST conversion is promised.

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
existing fork's `main`. Disable inherited publishing and mutating documentation
or upstream-sync workflows in this first source PR. Do not push upstream tags.
Preserve upstream ancestry through a merge commit in the fork; BenchBox's own
PR still uses its required squash workflow. Do not rewrite consumed refs.

Fork `main` contains both upstream ancestry and downstream commits. For each
refresh, first rebase only the carried source patch series onto the new tag in
a disposable candidate. Conflicts require explicit resolution and tests. Start
the actual integration branch from current fork `main`, merge the new upstream
tag, and reconcile the manifest-owned source paths to that tested candidate's
exact tree while retaining downstream packaging. Verify source-tree equality
before the refresh PR. This avoids merging two independently rebased patch
histories or resurrecting a retired patch. Prove the procedure in a local Git
fixture with a second refresh, one carried patch and one upstreamed patch.

Admit these commits in dependency order, resolving adjacent upstream changes
without copying unrelated branch work:

1. PostgreSQL extraction return types:
   `eb360e1e2962b467ca093b9f6ab1000f9ced1a02`.
2. SQLite extraction and source-aware division:
   `34cd3e9fdb29a4376023f9c6cd32932de07acd3d`. This branch includes the
   PostgreSQL commit; apply its unique extraction commit only once.
3. SQLite date/interval arithmetic:
   `b60935672d3fc52c4982077131d4cb5140a69b72`.

Record every prepared branch in `downstream/patches.json`, including its source
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
Increment N for a fork-only change; reset N to 1 when X.Y.Z changes. For
example, a fix after `30.23.0.1` becomes `30.23.0.2`, and the first release
based on upstream `30.24.0` becomes `30.24.0.1`.

Keep upstream's `sqlglot/` source layout for tests and upstream refreshes.
Add a narrow release assembler under `downstream/` and Make entrypoints for
building and checking the downstream distribution. Assemble into a fresh
output directory; never rename source files in place or modify another tree.
The release assembler must:

1. Validate the manifest, upstream base, fork source revision and clean input.
2. Copy only runtime sources, required resources and license/readme material
   into an isolated build project providing `benchbox_sqlglot/`.
   Explicitly include `py.typed` and declare it as package data. Generate fresh
   metadata with no inherited console scripts; assert no `sqlglot` executable
   or upstream entry points are installed.
3. Rewrite absolute Python imports and dynamic module-loading references,
   including `sqlglot.dialects.{key}`. Preserve public API names and SQL text.
   Do not add a `sys.modules['sqlglot']` alias or fallback to upstream imports.
   Use syntax-aware import rewriting, isolate the dialect plugin group as
   `benchbox_sqlglot.dialects`, and enumerate dynamic runtime path replacements.
   A fail-closed residual-import scan has an explicit retained-text allowlist.
   Test ordinary SQL literals containing `sqlglot` for unchanged content.
4. Generate static PEP 621 version metadata and a matching `_version.py`;
   the downstream build does not use SCM version discovery. Package
   `benchbox_sqlglot/benchbox_provenance.json` containing upstream SHA, fork
   SHA, patch manifest and distribution version. Read it after installation
   through `importlib.resources`; no checkout or custom public accessor is
   required. Use a recorded SOURCE_DATE_EPOCH and stable file ordering.
5. Build a pure-Python wheel and self-contained sdist with the existing uv /
   setuptools toolchain. Building a wheel from the unpacked sdist must work
   without the original repository, its `.git`, or a sibling source path.
   Rebuild offline after seeding the pinned build dependencies. Verify version,
   runtime payload and reproducible wheel output against the direct build.
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
Use a local fixture distribution requiring upstream SQLGlot to exercise
transitive resolver installation. Cover pickle and spawn-based round trips,
negative class identity checks, every built-in dialect registration, and
namespace-separated third-party dialect entry points.

Relocate the upstream unit test imports into a separate fixture directory and
run the installed-artifact unit suite there with the checkout absent from
sys.path. Preserve SQL fixture text and adapt explicit module-identity
expectations. The direct wheel and sdist-built wheel must have equal runtime
payloads; test both installation paths. Never certify the rewritten runtime
solely from tests of the original source.

## CI, release and refresh operations

Add fork CI for source regressions and built-artifact checks. Use uv and
existing Make entrypoints. Run upstream `make check` before source publication;
the downstream pure-Python wheel does not imply compiled upstream tests may be
silently removed. Keep new CI focused on the declared downstream Python range.
The added matrix is four wheel-smoke jobs (Python 3.11–3.14), one installed
full upstream unit suite, and one offline sdist rebuild/coexistence job. Import
orders and uninstall directions are subprocess cases, not matrix dimensions.

Replace or disable the inherited automatic `v*` package-publish workflow in
the first source PR before any hosted release tag is created. Its jobs publish
upstream-named `sqlglot` and `sqlglotc` and write generated docs/changelog.
The downstream release workflow validates an explicit `benchbox-v30.23.0.1`
tag/version pair, builds and verifies artifacts before publication, uses the
correct distribution identity, and requires the owner's approved publishing
credentials / protected environment. Do not weaken settings or invent secrets.
Retain downloadable CI artifacts if publishing configuration is unavailable.
Retain artifacts and SHA256SUMS for 30 days. Approval binds their exact hashes;
different bytes or unavailable evidence require new reviewable artifacts and
renewed approval. Verify workflow trigger structure locally rather than
performing a tag push as a safety test. Prefer owner-configured trusted
publishing; missing configuration remains an owner-only blocker.

Before publication, run the relevant BenchBox wrapper, SQLite and BigQuery
tests against the built artifact using a disposable source copy with only the
planned import migration applied. The copy must come from the merged BigQuery
prerequisite, not the other agent's worktree. This preview is distribution
verification, not tracker completion or a substitute for the final adoption PR.
If failures expose a real 30.23.0 semantic regression, block publication and
resolve its scope with the owner rather than silently adding a fourth patch or
changing the reviewed upstream base. Representation-only changes may update
tests while preserving execution assertions.

The maintainer owns a monthly upstream refresh review and urgent correctness
or security refreshes. A scheduled read-only check reports a newer tagged base;
it must not auto-publish, rewrite refs, or open unrequested upstream PRs.
Provide an explicit refresh command/runbook that records the old/new upstream
SHAs, replays the carried patches on a candidate, fails on conflicts, reruns
source and artifact tests, and requires review of upstreamed/excluded patches.
Refreshes replay/adapt carried patches and retire upstreamed patches; new
capabilities require their own approved scope. Record actual conflict count,
test runtime and maintainer effort rather than asserting an unmeasured budget.
An empty carried set plus passing upstream equivalence tests triggers an owner
decision on retiring the fork; no automatic namespace migration is implied and
no active upstream PR is required for a previously declined patch.

Keep immutable artifacts and tags. Initial rollback reverts the complete
BenchBox adoption PR, restoring its old imports and public SQLGlot 30.18.0
lock. Later rollback selects the preceding tested fork release and restores
the associated dependency/lock change. The adoption item owns an isolated
initial-rollback drill; later release checks own the previous-fork drill.

## BenchBox adoption

After the verified package is published and the BigQuery prerequisite is
merged, refresh the owned `BenchBox.wt-sqlglot-soft-fork` feature worktree to
that merged base. Never edit `BenchBox.wt-sqlglot-bigquery-filter`, take its
claim, or change its branch. Reconcile live Git/tracker state first.

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
  Inventory active container/notebook/docs examples, string annotations and
  persistence uses; retain historical evidence unchanged. Ban upstream runtime
  imports in `benchbox/` with Ruff TID251/banned-api and a negative proving
  case. Exempt only explicitly isolated upstream-comparison/research code.
  Unpatched probes use `uv run --no-project --isolated --with sqlglot==VERSION
  -- python ...`; fork and BenchBox-wrapper probes use the locked project.
- Make the retirement guard parse TOML and compare the dependency identity
  (distribution, version and artifact/source identity), rather than a regex
  matching only the public `sqlglot` version. Run on any identity change.
  Compare explicit baseline outcomes / accepted retirement decisions so a
  previously acknowledged passing reproduction does not fail every update.
  The baseline is `_project/sqlglot-upstream/runtime-baseline.json`, schema 1:
  dependency identity, named case IDs, source/target/call shape, expected
  pass/unsupported outcome, and acknowledged workaround decision/path. Update
  it with reviewed identity/behavior changes; reject missing or malformed data.
- Retire only syntax workarounds demonstrated redundant through both actual
  BenchBox translation paths with `identify=True`. Preserve exact SQLite Q6
  decimal bounds, engine-semantic transforms, physical DDL, source normalization
  and unrelated query variants. Narrow helpers rather than deleting mixed
  policy/code wholesale. Record any remaining overlap and its reason.
  Cover `benchbox.utils.dialect_utils.translate_sql_query`,
  `benchbox.platforms.base.dialect_translation.DialectTranslationMixin.translate_sql`,
  and `benchbox.base.BaseBenchmark.translate_query`; exercise the applicable
  `identify=True` and postgres-normalized cross-dialect call shapes.
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
   disable inherited mutating workflows, disposition the other branches, add
   the manifest and execute source tests.
   Terminal evidence: reviewed merged fork source PR and exact revision.
2. `sqlglot-soft-fork-distribution`: implement isolated release assembly,
   artifact tests, CI, release safety and the refresh command/runbook.
   Needs source and the existing BigQuery compatibility item. Terminal
   evidence: reviewed merged fork packaging PR, prospective consumer tests,
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
   Its rollback drill proves restoration of the complete pre-migration baseline.

## Review contract

The independent plan reviews judge this revision before implementation.
Each finding retains its reviewer, severity and evidence. Accepted findings
name a plan correction and the proving check; rebuttals require checkable
counter-evidence. The revised plan and the full disposition table accompany
the implementation TODOs. No reviewer writes code or changes tracker state.
