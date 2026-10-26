# BenchBox SQLGlot downstream distribution

## Objective and completion

> **Superseded in part**: the fork is permanent and owns transpilation, so the "bounded
> patch series" and retirement framing below no longer describe the end state. See
> `docs/development/adr/adr-sqlglot-fork-owns-transpilation.md` and
> `_project/decisions/sqlglot-fork-transpilation-plan.md`. The distribution, bootstrap and
> immutable-wheel mechanics in this record still apply.

Maintain the SQLGlot soft fork in `BenchBox-dev/benchbox-sqlglot`, preserving
upstream ancestry and a bounded tested patch series. BenchBox ships only its
pinned pure-Python runtime wheel, not an unpacked SQLGlot source tree. The
wheel is package data inside both the published BenchBox wheel and sdist.
There is no separately published fork dependency or PyPI trusted publisher.

Ordinary pip and uv installations use the same bundled bytes. A small private
package bootstrap extends `benchbox._vendor.__path__` into the bundled wheel;
Python's standard ZIP importer loads `benchbox._vendor.sqlglot`. There are no
runtime pip calls, downloads, extraction, global `sys.path` modifications,
upstream fallbacks or `sys.modules` aliases. Upstream SQLGlot remains independent;
exchange SQL text, not AST or dialect class instances across namespaces.

Completion requires reviewed merged fork tooling, a verified immutable runtime
wheel, and a reviewed merged BenchBox migration with actual installed-artifact
proof. Publishing a new BenchBox production release remains a separate action.
The serial batch respects cross-repository merge boundaries. Preserve unrelated
worktrees and consume the merged BigQuery prerequisite.

## Prior art and recorded decisions

- Extend the patch branches transferred from `joeharris76/sqlglot` to
  `BenchBox-dev/benchbox-sqlglot`, upstream dialect tests,
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
- Reuse the fork's AST relocation, isolated wheel assembler, source and compiled
  tests, installed relocated suite and refresh helper. Python's standard ZIP
  importer supports package paths inside ZIP archives, avoiding a custom loader
  or installer. Extend BenchBox package-data declarations and existing required
  retirement guard rather than introducing a second release system.

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

## Wheel runtime contract

The internal artifact distribution is `benchbox-sqlglot`; its runtime namespace
is `benchbox._vendor.sqlglot`. Version `30.23.0.2` follows the immutable,
unpublished `30.23.0.1` tag. Versions use `X.Y.Z.N`: increment N for a fork-only
change and reset it to one on a new upstream base. Never reuse a version for
different bytes or rewrite a consumed source/tag.

Keep upstream's `sqlglot/` source layout only in the fork. Its assembler:

1. Requires a clean tracked and untracked checkout, verifies the upstream
   tag/SHA and ancestry, and stages runtime files in a temporary build directory.
2. Relocates imports and enumerated dynamic paths with the existing AST-aware
   rewriter, preserving local bindings, SQL text, logger names and
   `sqlglot.meta` / `sqlglot.anonymous`. Unknown runtime paths fail closed.
3. Uses private dialect plugin group `benchbox._vendor.sqlglot.dialects`.
   Upstream plugins are not silently reused.
4. Generates static version metadata and `benchbox_provenance.json` recording
   repository, namespace, version, upstream/fork SHAs, source timestamp, complete
   patch manifest and SHA256 of runtime files. Preserve LICENSE and `py.typed`.
5. Builds one pure-Python `py3-none-any` wheel with no runtime dependencies,
   optional compiled extras or console scripts. A self-contained fork sdist is
   an internal reproducibility check; BenchBox ships only the resulting wheel.

BenchBox stores one wheel under `benchbox/_vendor/` and a `sqlglot.json` pin
containing its filename, SHA256, version, namespace and source identities.
The offline pin check verifies wheel metadata, resources, provenance and file
hashes and rejects extra wheels, compiled payloads or an unpacked source tree.
The pin and wheel change together in a reviewed PR. A maintenance command
adopts a verified built wheel; normal BenchBox builds never regenerate it.

Verify direct ZIP import before installing the fork wheel, as well as the full
relocated upstream unit suite from an installed wheel outside the checkout.
Cover all built-in dialects, independent plugin groups, SQL execution, resources,
optimizer/serialization APIs, pickle/spawn, import order and coexistence.
Rebuild the fork sdist offline after seeding pinned build backends and compare
complete wheel bytes. Do not certify relocation only from original-source tests.

## CI, release and refresh operations

Retain the native seven-Python source/compiled checks and the bounded runtime
artifact matrix: four Python 3.11–3.14 smoke jobs, one full installed suite,
and one offline sdist/coexistence job. Add the direct ZIP-import path to artifact
verification. Candidate artifacts and SHA256SUMS are retained for 30 days.
Remove the fork production publisher and obsolete publication helper. No active
workflow may publish `sqlglot`, `sqlglotc` or `benchbox-sqlglot`.

Before adoption, verify BenchBox's real wrapper, platform mixin, base benchmark,
SQLite arithmetic/Q6 bounds, AST consumers and BigQuery behavior against the
bundled wheel. A new semantic regression needs an owner scope decision; do not
silently add a fourth patch or change the reviewed upstream base.

The maintainer reviews upstream monthly and promptly for correctness/security
fixes. The scheduled check is read-only. Refresh replays carried patches onto
an explicit newer tag in a disposable candidate, stops on conflicts, and then
merges upstream ancestry into a descendant integration branch. Reconcile the
union of old/new patch-owned source paths to the tested candidate so retired
patches cannot resurrect. Prove two refreshes and an upstreamed patch in the
existing Git fixture. Record conflict count and measured check/maintainer effort.

After fork review and merge, build and verify a clean exact-commit wheel,
adopt its bytes and pin in BenchBox, update behavioral baseline decisions,
and pass consumer/package gates before merge. A fork change reaches users only
through a BenchBox release. An empty carried patch set triggers an owner decision
on retiring the fork, never an automatic namespace change.

Initial rollback reverts the complete adoption, restoring pre-migration imports
and public SQLGlot 30.18.0. Later rollback restores the previous bundled wheel
and pin together. Neither rollback rewrites immutable fork refs.

## BenchBox adoption

Use the owned `BenchBox.wt-sqlglot-soft-fork` worktree on a current merged
`develop` base containing the BigQuery prerequisite.

- Remove the public SQLGlot runtime dependency using uv and regenerate the lock.
  Do not add a published fork requirement, Git URL or developer-only source override.
- Include exactly the pinned nested wheel and its metadata as package data in
  both BenchBox wheel and sdist. There must be no unpacked SQLGlot source tree
  in the repository or outer distributions. No source-vendoring policy exemption
  or comment stripping is needed.
- Migrate all runtime consumers, direct AST users, tests, mocks, metadata queries
  and active tools/docs to the private namespace. Audit annotations, pickle,
  notebooks and containers; retain historical evidence. Ban public SQLGlot
  runtime imports with Ruff TID251 and an execution-based negative proving case.
  Raw upstream comparisons remain isolated with
  `uv run --no-project --isolated --with sqlglot==VERSION -- python ...`.
- Version overlays are vacuous. `benchbox/_vendor/__init__.py` appends the bundled
  wheel to `benchbox._vendor.__path__`, so BenchBox code never imports a PyPI
  `sqlglot` added by `uv run --with sqlglot==VERSION`; such a run reports the
  overlaid version while exercising the vendored one. Alternate-parser checks
  use only the isolated, benchbox-free form above.
  `tests/unit/sql_compat/test_sqlglot_overlay_guard.py` fails when a workflow,
  Makefile, pre-commit config, Dockerfile or script passes `--with sqlglot...`
  to `uv run`/`uvx` without `--no-project`, or installs PyPI `sqlglot` with
  `pip install`, `uv pip install` or `uv add`.
- Replace the version-only retirement regex with the bundled artifact/source
  identity. Parse TOML when comparing the pre-migration lock or validating
  dependency removal. Run offline artifact verification and schema-1
  `scripts/sqlglot_runtime_baseline.json` cases with explicit
  source/target/call shape, expected pass/unsupported outcome and acknowledged
  workaround decision/path. Missing/malformed data or changed outcomes fail
  closed. Acknowledged passing cases must not fail every subsequent update.
- Cover utility, mixin and BaseBenchmark translation with `identify=True` and
  normalized PostgreSQL input. Retire only proven redundant syntax fixes;
  preserve mixed DATE_TRUNC, timestamp/unknown extraction, month/year interval,
  Q6 exact-decimal, DuckDB ALL, normalization, DDL and engine-semantic behavior.
- Update ADR, ownership, maintenance, dependency and rollback documentation.

Run targeted consumer/guard tests, configured Ruff and ty, comment-policy,
compatibility-doc checks and native preflight. Build actual BenchBox wheel and
sdist and install outside the checkout with pip and uv, without separately
installing the fork. Prove nested ZIP import, all dialects, resources, pickle/
spawn, upstream coexistence and uninstall independence. Measure package size
and import overhead against the unpacked runtime; report residual limits.
Complete the initial rollback drill, required CI and current-head Oracle review,
then use native PR open/arm through verified merge and post-merge smoke proof.

## Measured results

Fork runtime `30.23.0.2` is built from clean fork commit
`6de7947e0b90aed7e62adf53e597bf6e05a6147e` (merged into
`BenchBox-dev/benchbox-sqlglot` `main`) with SHA256
`006b739c9935789ab373ac91a8adba6e5d9c28e8369b3b4e4b08bc40f1578da1`. The
bundled archive is 799,094 bytes. BenchBox wheel size changes from 9,399,548 to
10,176,525 bytes (+8.3%); sdist size changes from 8,774,605 to 9,554,993 bytes
(+8.9%). Both artifacts contain exactly the pinned archive, pin metadata,
plain-text notice and bootstrap; neither contains an unpacked SQLGlot tree.

On one Apple Silicon development machine, a fresh interpreter importing the
runtime and transpiling one statement had a 373 ms median from the nested
archive and 70 ms from an unpacked, already-compiled wheel. Archive loading is
the accepted cost of not shipping source files. Cache a compiled runtime only if
measured user-visible startup warrants a separate approved change.

Verification covered installed wheel and offline-rebuilt sdist with pip and uv,
without a separately installed fork; upstream SQLGlot 30.23.0 was installed
before and after BenchBox, then uninstalled in both directions. The initial
rollback drill used a disposable checkout of the pre-migration base, which locks
public SQLGlot 30.18.0, built its distributions and passed the dialect utility
and SQLite Q6 tests.

## TODO breakdown and delivery order

1. `sqlglot-soft-fork-source`: bounded patch admission and source verification;
   terminal evidence is the merged source PR and exact revision.
2. `sqlglot-soft-fork-distribution`: isolated wheel/reproducibility checks and
   refresh controls; retain its historical completed evidence.
3. `sqlglot-soft-fork-publish`: repurpose the former publication item for the
   owner-approved wheel-only contract: canonical fork name, private wheel build,
   no production publisher, direct ZIP-import proof and merged fork tooling.
   Terminal evidence is a merged fork PR and verified immutable runtime artifact.
   It must not publish a separate PyPI project.
4. `sqlglot-soft-fork-adoption`: bundle only that wheel/pin, migrate every
   consumer, guard/docs/tests, verify actual packages and rollback, then merge
   through BenchBox's native gates. Needs the fork tooling and merged BigQuery
   prerequisite. Finish only after current-head gates, merge and post-merge proof.

## Review contract

The independent plan reviews judge this revision before implementation.
Each finding retains its reviewer, severity and evidence. Accepted findings
name a plan correction and the proving check; rebuttals require checkable
counter-evidence. The revised plan and the full disposition table accompany
the implementation TODOs. No reviewer writes code or changes tracker state.
