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
| `make/monolith-baseline.json` | Historical inventory of the committed pre-split monolith: 198 targets, 195 public targets, default goal `test` | History only; nothing reads or regenerates it |
| `make/migration-proof.json` | Historical hashes, counts, include order, and reviewed delta for the initial split | History only; keep it with the baseline |

Cross-module prerequisites stay explicit in target headers. A module invokes
another module's public target and must not reach into its file. Variables live
with the recipes they configure, unless they are root bootstrap or cross-domain
orchestration inputs.

## Consumer evidence and root-owned boundaries

The split was designed around repository consumers, not only target size.

| Implementation or consumer | Observed contract | Design consequence |
|---|---|---|
| `.github/workflows/ci.yml` and `nightly-v2.yml` | Invoke public targets through GNU Make | Included targets are transparent to them |
| `tests/system/test_ci_lint_parity.py` and `tests/unit/scripts/test_ci_lint_environment_boundary.py` | Parse the literal root `ci-lint:` recipe | `ci-lint` stays root-owned with byte-identical command order |
| `tests/unit/test_standardized_test_commands.py` and `tests/unit/test_linting_consolidation.py` | Search or parse root test, coverage, correctness, marker, lint, and format definitions | Root-owned; moving them requires first migrating these consumers in a separately scoped change |
| `tests/unit/test_release_infrastructure.py`, and `tests/unit/scripts/test_pr_arm.py` | Parse release, PR, auto-merge, worktree lifecycle, package, and UAT gate source text | These operational safety recipes stay root-owned |
| `tests/unit/test_agent_write_preflight.py` | Slices the root `skill-sync` recipe | `skill-sync` and its adjacent check stay root-owned |
| `tests/uat/test_cli_dispatch.py` | Slices the root `uat-sweep` recipe | `uat-sweep` stays root-owned |
| `tests/integration/worktree/test_worktree_*` | Run the real Makefile with `-f` or via a symlink from another repository | `BENCHBOX_MAKEFILE_ROOT` resolves the real root before mandatory includes load |

No repository consumer referenced `MAKEFILE_LIST` before the split. The
bootstrap variable still freezes the root path before includes append to that
built-in list, preventing future ordering-dependent path drift. It is
repository-reserved, not a supported user customization point.

## Interface guard

`scripts/check_make_targets.py` reads the rule database GNU Make prints with
`make -pRrq :`. Any error or warning Make reports while reading the files, such
as a missing include, a parse error or a target whose recipe is defined twice,
fails it. It enforces two things:

- Every target named in a `make` command inside a code span or code block of
  `AGENTS.md`, `CONTRIBUTING.md`, `docs/` (except the decision records under
  `docs/internal/adr/`) or `.claude/skills/` is a defined target.
  `make docs-validate` runs this check, so the `docs` unit catches a
  documentation change that names a missing target, and
  `tests/unit/scripts/test_check_make_targets.py` repeats it in the fast unit
  tier for Make changes.
- The number of targets stays at or below `TARGET_CEILING` in that test.
  Targets are counted the way the original reduction plan measured them: every
  name in the rule database that starts with a letter or digit. A new target
  replaces or consolidates an existing one; raising the ceiling needs the
  owner's agreement.

Tests that need a target's prerequisites, recipe or target-specific variables,
such as the timed-target check below, read them through the same script.

`make/monolith-baseline.json` and `make/migration-proof.json` record the
committed pre-split monolith and the reviewed delta of the first split. No
check reads them; they are kept as history.

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
target-specific `SHELL`, leaving recipe text unchanged. Every recipe line that
runs through that shell writes a record, and a target's prerequisites inherit
its `SHELL`. A target therefore belongs in the list only if its recipe is a
single logical line and each prerequisite sets its own `SHELL := /bin/sh`, as
`.development-tree-required` does; a test enforces both. Recording is best
effort: if the file cannot be written, a one-line warning goes to stderr and
the target keeps its own exit status.

`make make-timings-report` prints each target's run count, failure count,
median, p90, and maximum for the last `MAKE_TIMINGS_DAYS` days (default 30),
then the `MAKE_TIMINGS_SLOWEST` slowest runs (default 5). The tests set
`BENCHBOX_MAKE_TIMINGS=0` so they never write to the real file.

## Change and rollback cases

| Change | Required action | Rollback |
|---|---|---|
| Add a target inside an existing domain | Add it to the owning module, with help if public | Revert the target and help entry together |
| Move a target from root to a module | First prove no root-literal consumer remains; move comments, variables, rule, and recipe as one block; `tests/unit/scripts/test_check_make_targets.py` must still pass | Move the exact block back to its original parse position; remove an empty module |
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
