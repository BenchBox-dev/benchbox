# SQLGlot downstream distribution plan review

## Review input

The reviewed plan blob has SHA-256
`26a5a9ad62617b2f01c779235c3812d3dcfc3beed0d2fdef37328ce3442d0fb8`,
against BenchBox baseline `fc7e96f52733890f662910efb6563b32fc83187f`.
The reviewers received the same numbered document and evidence brief.
The scope is the downstream distribution and its BenchBox adoption.

## Muse dispositions

Reviewer: external `muse`, model `muse-spark-1.3`, high reasoning effort,
with writes and shell execution disabled. Its verdict on the submitted plan
was **Do not ship**. Findings below preserve the reviewer's severity labels;
the dispositions distinguish valid plan gaps from unsupported failure claims.

| ID | Reviewer severity | Disposition | Evidence and smallest remedy | Proving check |
| --- | --- | --- | --- | --- |
| M1 | Critical | NARROW | Disable inherited publishing in the source item, before any hosted tag push. Fetching a tag locally does not emit a GitHub push event. The proposed dry tag push is not evidence of a workflow run and must not create a release trigger for testing. | Parse workflow triggers/jobs in a regression test; assert no upstream-named publishing or write jobs and no `v*` push publication trigger. |
| M2 | Critical | ACCEPT | The assembler must create static PEP 621 version metadata and a matching generated `_version.py`; never copy the upstream SCM-dependent project metadata into the downstream build. | Rebuild from an unpacked sdist with no repository or Git metadata and network disabled after seeding build dependencies; compare version, package payload and reproducible artifacts. |
| M3 | Critical | ACCEPT | Coexistence means independent implementations in one environment, not interchangeable AST objects. A transitive dependency must be allowed to install upstream without satisfying the fork requirement. | Install a small local test dependency requiring upstream SQLGlot; test both import orders, distinct AST classes, pickle round trips, and uninstall independence. |
| M4 | Required | ACCEPT | Define `X.Y.Z.N`: increment N for fork changes; reset N to 1 for a new upstream base. Each version identifies immutable artifacts. | Manifest/version validation tests cover fork-only increments and base refreshes. |
| M5 | Required | ACCEPT | Ordinary SQL strings must survive relocation. Rewrite import syntax and enumerated runtime module paths; isolate the dialect plugin entry-point group. Residual runtime module references fail closed, with an explicit retained-text allowlist. | Include SQL strings containing `sqlglot`, dynamic imports, source-style import forms, string annotations and plugin isolation in assembler/artifact tests. |
| M6 | Required | ACCEPT | Source tests alone cannot certify rewritten artifacts. Adapt and run the upstream unit suite against the installed namespace outside the source checkout, retaining fixture SQL. | CI runs the installed-artifact suite and asserts the package path; compare direct-wheel and sdist-built runtime payloads. |
| M7 | Required | ACCEPT | Specify `benchbox_sqlglot/benchbox_provenance.json`. Read it with the standard `importlib.resources` API rather than adding another public accessor abstraction. | A fresh installed-wheel probe reads upstream SHA, fork SHA, manifest and distribution version without a checkout. |
| M8 | Required | ACCEPT | Add `scripts/sqlglot_runtime_baseline.json` with an explicit schema, per-case call shapes/outcomes and workaround decisions. Updates accompany reviewed identity or behavior changes. | Unchanged acknowledged outcomes pass another version update; unexpected outcomes fail; malformed/missing baselines fail closed. |
| M9 | Required | ACCEPT | There are three entrypoints to cover: `translate_sql_query`, `DialectTranslationMixin.translate_sql`, and `BaseBenchmark.translate_query`, not an assumed universal wrapper. Their existing call sites set or compute `identify=True`. | Per-workaround retirement tests exercise the relevant entrypoints and cross-dialect source normalization. |
| M10 | Required | NARROW | An initial release has no preceding fork release. Its rollback target is the pre-migration BenchBox revision with public SQLGlot 30.18.0; later rollbacks target the prior fork version. Do not manufacture another published release to satisfy a drill. | In isolated environments test the initial baseline restoration and documented lock/dependency rollback; subsequent release checks test the previous fork artifact. |
| M11 | Required | REBUT | The submitted plan explicitly forbids touching the foreign BigQuery worktree and calls for an owned develop-based worktree. `/Users/joe/Developer/BenchBox.wt-sqlglot-soft-fork` is the owned feature worktree; it is distinct from `BenchBox.wt-sqlglot-bigquery-filter`. A second new worktree is not necessary for safety. Spell out the owned path and refresh it after the prerequisite merges. | Verify the adoption branch contains the merged prerequisite and inspect the foreign claim/worktree read-only. |
| M12 | Consideration | ACCEPT | Refreshes replay or adapt carried patches and retire upstreamed patches. New capabilities require separately approved scope. | Refresh manifest review identifies each carried, retired and changed patch and rejects silently added entries. |
| M13 | Consideration | ACCEPT | Bound the matrix: four Python wheel-smoke jobs, one installed full unit suite, and one offline sdist rebuild/coexistence job. Import orders and uninstall directions are subprocess checks, not separate matrix axes. | Workflow configuration test counts the intended jobs and each declared Python version; release uses the same checks. |
| M14 | Consideration | ACCEPT | Retain candidate artifacts and `SHA256SUMS` for 30 days. Approval binds exact hashes; expiry requires recreating reviewable evidence and renewing approval for different bytes. | Workflow artifact settings and release validation require retention, checksums and matching identity. |
| M15 | Consideration | ACCEPT | Include pickle/spawn, string annotations, notebooks, containers, docs and mock targets in the migration discovery rule. Historical reports remain evidence rather than rewritten runtime instructions. | Fresh installed-artifact pickle/spawn tests and a scoped active-reference scan. |
| M16 | Consideration | REBUT | No repository requirement or concrete failure establishes a need for a new SBOM/signing system in this change. The smaller sufficient contract is immutable artifacts, hashes, provenance, correct metadata and owner-authorized publishing. PyPI name/access checks and trusted-publisher setup belong to the publication item. | Publication item verifies the actual project/access and fresh public installs; no invented credentials or settings changes. |
| M17 | Consideration | NARROW | Record actual refresh conflict/test effort; do not invent an unmeasured monthly cost. An empty carried patch set triggers an owner decision to retire or retain the fork after equivalence checks. Automatically deprecating a dependency namespace would introduce a new public migration without approval. | Refresh report includes measured work and patch count; the runbook names the retirement-review trigger and rollback path. |

## Antigravity dispositions

Reviewer: external `agy`, model `gemini-3.8-flash-high`, plan mode, in a
separate detached read-only review worktree. Its verdict was **Do not ship**.
The workspace remained unchanged. The complete brief was provided inline;
the reviewer did not independently inspect the repository. Its unnumbered
actionable L2/L3 suggestions are assigned A11–A13 below.

| ID | Reviewer severity | Disposition | Evidence and smallest remedy | Proving check |
| --- | --- | --- | --- | --- |
| A1 | Critical | NARROW | Disable inherited mutating workflows in the first source PR, as in M1. The actual `package-publish.yml` is tag-triggered, not triggered by a `main` push; local fetches do not publish. Do not replace semantic workflow checks with `grep publish` or require disabling read-only CI. | Regression tests enumerate actual triggers, publishing targets and mutating jobs before source PR integration. |
| A2 | Critical | REBUT | At v30.23.0, `pyproject.toml` has no `[project.scripts]` and `setup.py` defines no console scripts. The claimed `sqlglot.cli:main` entry point is absent; the CLI is `sqlglot/__main__.py`. Fresh downstream metadata already avoids inherited entry points. Add an explicit no-upstream-script assertion without treating the fictional entry point as a current defect. | Inspect built entry-point metadata and installed scripts; compare with the pinned upstream metadata. |
| A3 | Required | ACCEPT | Same valid shipped-code coverage gap as M6. Run the relocated installed upstream unit suite and all declared Python smoke jobs, while preserving upstream source/compiled checks. | Installed package path assertion plus unit-suite and Python-matrix results. |
| A4 | Required | ACCEPT | Explicitly retain `py.typed` as package data. BenchBox's configured type checker is `ty`, not the reported mypy/pyright, so use its native gate rather than introducing a new checker requirement. | Wheel and sdist payload checks plus BenchBox's configured `uv run ty check`. |
| A5 | Required | ACCEPT | Prevent future public SQLGlot runtime imports using the existing Ruff banned-api rule. Raw upstream probes receive narrow exemptions. | A rejected production import must produce blocking TID251. |
| A6 | Required | NARROW | A second rebased patch series cannot simply be merged into old downstream history. Keep upstream ancestry in fork main, rebase a disposable source candidate, then merge the upstream tag into a descendant integration branch and reconcile manifest-owned source paths to the verified candidate tree. A separate mirror/integration branch alone would not solve the old-versus-new patch-history problem. | A local Git fixture covers a second refresh and patch retirement, checks tree equality, and proves no retired change survives. |
| A7 | Consideration | ACCEPT | Initial rollback must restore public SQLGlot and all migrated imports; a prior fork does not exist. This is incorporated with M10. | Isolated full adoption-revert drill, followed by baseline tests. |
| A8 | Consideration | NARROW | Explicit isolated upstream probes are useful. Co-installing public SQLGlot does not violate this plan's namespace isolation, and `--isolated` alone still permits a project environment; include `--no-project`. | Run the unpatched raw repro in a standalone uv environment and wrapper/fork tests in the locked project environment. |
| A9 | Consideration | REBUT | The pinned upstream `sqlglot/__init__.py:65-70` imports `_version.py`; it does not call `importlib.metadata.version('sqlglot')`. Static downstream version generation is now explicit under accepted M2. | A fresh install without public SQLGlot reports the downstream manifest version from `_version.py`. |
| A10 | Consideration | NARROW | Add prospective BenchBox consumer tests before publication. A genuine new semantic regression blocks publication for an owner scope decision; do not automatically admit an unreviewed fourth patch. Preserve the foreign BigQuery claim. | Built-artifact consumer preview uses the merged prerequisite and runs wrapper, SQLite and BigQuery regression assertions. |
| A11 | L2 suggestion | ACCEPT | Verify every built-in dialect loads into the fork's registry and isolate third-party plugin groups. Dialect-name strings remain portable; dialect class and AST instances are independent. | Artifact tests enumerate built-in dialects and use a foreign plugin fixture. |
| A12 | L2 suggestion | ACCEPT | Use `downstream/` for fork assembly and manifests, avoiding a top-level directory sharing BenchBox's import name. | Build/source tests import real BenchBox from the intended environment and assembler discovery is explicit. |
| A13 | L3 suggestion | REBUT | Requiring an active upstream PR for each carried patch conflicts with the owner's selected soft-fork direction after upstream declined submissions. New upstream submissions are not authorized. Retain upstream references and review retirement when all carried patches become unnecessary, as in M17. | Manifest accepts closed/declined upstream references and the refresh runbook has an owner-controlled exit review. |

## Consensus and disagreement

Both reviewers independently identified gaps in post-relocation test coverage,
first-PR release safety, and initial rollback. They are addressed together but
their findings remain separate above. Antigravity's claimed console entry
point and metadata version lookup do not exist in the pinned source. Muse's
suggested new worktree is unnecessary because the owned and foreign worktrees
are already distinct. These rebuttals preserve the actual isolation contract.

## Interpretation

The plan revisions implement accepted remedies before source implementation.
Narrowed findings retain their valid concern and state the excluded mechanism
and reason. Rebutted findings do not become unapproved follow-up infrastructure.
Source-level and artifact-level proof are distinct; neither a passing plan
review nor a parser-only reproduction certifies a release.

## Wheel-only delivery amendment

The owner superseded separate PyPI publication and rejected unpacked source
vendoring. The maintained fork is `BenchBox-dev/benchbox-sqlglot`; BenchBox
ships only its pinned pure-Python runtime wheel plus pin metadata. Standard
ZIP import provides the private namespace without extraction, runtime package
installation, downloads or global import-path changes.

The original reviews remain evidence for source correctness, namespace
independence, complete relocation tests, reproducibility, refresh and rollback.
Publication-specific remedies are replaced by checked-in wheel/source hashes,
offline artifact verification and actual BenchBox wheel/sdist install proof.
No source-vendoring ownership exemption is part of this amended delivery model.
