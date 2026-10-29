# Makefile architecture

BenchBox uses a few mandatory GNU Make includes but deliberately keeps a large
root `Makefile` because several repository guards treat its source text as a
compatibility API; command-line and CI consumers use GNU Make's evaluated
target graph.

## Supported behavior

- GNU Make 3.81 or newer is supported. The root freezes
  `BENCHBOX_MAKEFILE_ROOT` before any include changes `MAKEFILE_LIST`, using
  `realpath`, `lastword`, and `dir`, all of which 3.81 supports. Its
  `override` directive reserves the value against command-line and `make -e`
  environment assignment.
- Includes are mandatory `include` directives, never `-include`, so a missing
  module stops parsing instead of silently dropping targets.
- Include paths start at the real root-Makefile location, which keeps
  `make -f /path/to/Makefile`, runs from another directory, and the
  symlinked-Makefile worktree tests working.
- `test` remains the default goal; modules are included only after the root
  defines it.

## Ownership

| File | Responsibility | Extension rule |
|---|---|---|
| `Makefile` | Bootstrap, default goal, literal-text compatibility targets, quality/CI orchestration, and release, PR, worktree, and UAT contracts | Keep a target here if an existing consumer intentionally parses the root text or it coordinates several domains |
| `make/platform-tests.mk` | Credentialed live-platform and local compose/container test lifecycle | Add platform-specific test entry points and their directly coupled engine variables/macros here |
| `make/documentation.mk` | Docs build/validation, generated compatibility docs, platform manifest, parity fixtures, and native TPC binary compilation | Add documentation or generated-contract targets here; no general lint targets |
| `make/worktrees.mk` | Disposable worktree creation, exact-path removal, manual release, finish preview, merged-worktree reap, and listing | Native Git lifecycle wrappers belong here; no slot allocation or automatic branch cleanup. `worktree-reap` removes only what a fresh `worktree-finish` evaluation would remove, and only with `APPLY=1` |
| `make/worktree-maintenance.mk` | Finding and soundness reporting utilities | Keep unrelated reporting targets here. Worktree reaping goes through `worktree-finish` checks (`worktree-finish`, `worktree-reap`). The only worktree-less branch reaper, `branch-prune-merged` in the root `Makefile`, is an explicit exception for branches at their historically proven merge-time PR head |
| `make/help.mk` | Exact ordered `make help` recipe | Keep existing category and command order; add the help line in the same change as a new user-facing target |
| `make/inventory.json` | Locally generated contract (the contents listed under [Drift guard](#drift-guard), plus aliases), derived wholly from the `Makefile`, its included modules, and the migration files below | Never edit or commit; only `make/check_makefile_inventory.py --write` writes it |
| `make/monolith-baseline.json` | Durable inventory of the committed pre-split monolith: 198 targets, 195 public targets, default goal `test` | Do not regenerate during ordinary Make changes; changing this migration proof needs a separate architectural decision |
| `make/migration-proof.json` | Compact hashes, counts, include order, and reviewed delta for the initial split | The normal inventory writer never changes it; keep it with the baseline as historical evidence |
| `make/check_makefile_inventory.py` | Inventory reader, writer, and historical verifier | Keep under `make/` so `make makefile-inventory-check` keeps working |

Cross-module prerequisites stay explicit in target headers. A module invokes
another module's public target and must not reach into its file. Variables live
with the recipes they configure, unless they are root bootstrap or cross-domain
orchestration inputs.

## Consumer evidence and root-owned boundaries

The split was designed around repository consumers, not only target size.

| Implementation or consumer | Observed contract | Design consequence |
|---|---|---|
| `.github/workflows/ci.yml`, `nightly-v2.yml`, and `lint.yml` | Invoke public targets through GNU Make | Included targets are transparent to them |
| `tests/system/test_ci_lint_parity.py` and `tests/unit/scripts/test_ci_lint_environment_boundary.py` | Parse the literal root `ci-lint:` recipe | `ci-lint` stays root-owned with byte-identical command order |
| `tests/unit/test_standardized_test_commands.py` and `tests/unit/test_linting_consolidation.py` | Search or parse root test, coverage, correctness, marker, lint, and format definitions | Root-owned; moving them requires first migrating these consumers in a separately scoped change |
| `tests/unit/test_release_infrastructure.py`, `tests/unit/scripts/test_pr_arm.py`, and `tests/unit/scripts/test_soundness_paths.py` | Parse release, PR, auto-merge, worktree lifecycle, package, and UAT gate source text | These operational safety recipes stay root-owned |
| `tests/unit/test_agent_write_preflight.py` | Slices the root `skill-sync` recipe | `skill-sync` and its adjacent check stay root-owned |
| `tests/uat/test_cli_dispatch.py` | Slices the root `uat-sweep` recipe | `uat-sweep` stays root-owned |
| `tests/integration/worktree/test_worktree_*` | Run the real Makefile with `-f` or via a symlink from another repository | `BENCHBOX_MAKEFILE_ROOT` resolves the real root before mandatory includes load |
| `scripts/check_release_curation.py` | Parses the release curation recipe from the root path | Root-owned until the parser supports include expansion |

No repository consumer referenced `MAKEFILE_LIST` before the split. The
bootstrap variable still freezes the root path before includes append to that
built-in list, preventing future ordering-dependent path drift. It is
repository-reserved, not a supported user customization point.

## Drift guard

Run:

```bash
make makefile-inventory-check
```

The guard expands only the repository's narrow, mandatory include syntax and
never runs recipes. It builds the inventory in memory without writing, so it
works on a read-only tree without the untracked `make/inventory.json`. It
records:

- the default goal and ordered include list;
- all explicit and pattern targets plus the phony target set;
- exact target headers and prerequisites;
- exact recipe bytes and command order;
- variable assignments and `define` macro bodies;
- the global ordered stream of assignments, macros, phony declarations, and
  rules after include expansion.

The check fails when it cannot safely inventory the Make sources (a missing
include, an include cycle, unsupported top-level syntax) or when
`make/migration-proof.json` no longer matches `make/monolith-baseline.json`.

The inventory does not freeze the Make interface: a target, prerequisite, or
recipe change needs no manifest update. For a pull request that changes
`Makefile` or `make/`, the CI step summary shows an informational public-target
and recipe diff against its base (`make/interface_summary.py`). To inspect the
evaluated contract in a local manifest, run:

```bash
uv run -- python make/check_makefile_inventory.py --write
```

The writer validates but never rewrites `monolith-baseline.json` or
`migration-proof.json`. `.gitignore` ignores the generated
`make/inventory.json`, so `git status` does not show it. Keep or delete it.

To reproduce the historical comparison, run it explicitly at the commit that
introduced the split, or a checkout rebuilt from it:

```bash
uv run -- python make/check_makefile_inventory.py --verify-migration
```

Later intentional Make changes are expected to diverge from it. The immutable
proof metadata stays valid and auditable without a third full inventory copy.

## Local timing records

Gate-style targets append one JSON line per run to
`~/.benchbox/make-timings.jsonl`, or to `BENCHBOX_MAKE_TIMINGS_FILE` if set.
`BENCHBOX_MAKE_TIMINGS=0` (or `off`, `false`, `no`) stops recording; targets
then run exactly as before. The file lives outside the repository and is never
committed.

Each record has schema `make_timing_v1` and these fields: `target`,
`started_at` and `ended_at` (UTC, ISO 8601), `duration_seconds` (monotonic
clock), `exit_code` (`128` plus the signal number if a signal killed the
command), `cwd` (the worktree), `branch`, `head` (short SHA), `host`, and
`make_goals` (the goals of that `make` invocation). It holds no environment
variables, arguments, or command output. A nested `$(MAKE)` call of a timed
target writes its own record.

`MAKE_TIMED_TARGETS` in the root `Makefile` is the single list of timed
targets. Each runs its recipe under `scripts/make_timing_shell.sh` through a
target-specific `SHELL`, leaving recipe text unchanged. Because every recipe
line runs through that shell and writes a record, a target belongs in the list
only if it has no prerequisites and a single logical recipe line; a test
enforces this. Recording is best effort: if the file cannot be written, a
one-line warning goes to stderr and the target keeps its own exit status.

`make make-timings-report` prints each target's run count, failure count,
median, p90, and maximum for the last `MAKE_TIMINGS_DAYS` days (default 30),
then the `MAKE_TIMINGS_SLOWEST` slowest runs (default 5). The tests set
`BENCHBOX_MAKE_TIMINGS=0` so they never write to the real file.

## Change and rollback cases

| Change | Required action | Rollback |
|---|---|---|
| Add a target inside an existing domain | Add it to the owning module, with help if public | Revert the target and help entry together |
| Move a target from root to a module | First prove no root-literal consumer remains; move comments, variables, rule, and recipe as one block; `make makefile-inventory-check` must still pass | Move the exact block back to its original parse position; remove an empty module |
| Add a module | Add one mandatory rooted include at the intended parse position; update this ownership table | Move its blocks back, remove the include, then remove the module |

## Alternatives rejected

- **Split by line count.** It cuts coupled variables and macros from their
  recipes and ignores literal-root consumers.
- **Move every target and update all parsers at once.** It would combine a
  compatibility migration of the literal-parser tests with the structural
  refactor.
- **Optional or wildcard includes.** Missing or misspelled modules can
  silently remove public targets.
- **A generated monolithic root mirror.** It creates two apparent sources of
  truth, and edits land in generated compatibility text, not the owning
  module.
- **Inventory only target names.** Names cannot detect changed prerequisites,
  aliases, environment-variable wiring, shell flags, exit behavior, or command
  order.
