# SQLGlot upstream contribution prep

## Active BenchBox runtime

BenchBox uses the pinned private runtime wheel from
[`BenchBox-dev/benchbox-sqlglot`](https://github.com/BenchBox-dev/benchbox-sqlglot).
Only the `.whl` and pin metadata are bundled under `benchbox/_vendor/`; source
stays in the fork. `make sqlglot-repro-retirement-check` verifies the wheel hash
and the acknowledged pass/unsupported outcomes in `scripts/sqlglot_runtime_baseline.json`.
An acknowledged passing case is not a recurring retirement failure.

The generated-case pilot exercises BenchBox's real wrapper and parses with the
same bundled runtime. Replay uses the checkout's pinned wheel and rejects a
different recorded runtime version. The standalone upstream repros remain
separate comparisons and must run in an isolated environment. The historical
contribution evidence below does not describe the current bundled runtime.

The standalone JOB authoring tool retains its own public SQLGlot dependency in
`_project/scripts/pyproject.toml`; its ASTs do not cross into BenchBox runtime
translation. The import ban exempts that tool and the two isolated upstream
comparison probes, not the generated-case pilot or self-binding detector.


Drafted bug reports and a feature request for the SQLGlot project, plus the
minimal reproducer harness that pins each defect to a specific SQLGlot
version. SQLite extraction has an
[upstream follow-up on existing issue #2592](https://github.com/tobymao/sqlglot/issues/2592#issuecomment-5653487646).
The remaining drafts under `issues/` need the validation described below before
submission. No SQLGlot fix PR has been submitted from this preparation.

## Submission readiness

The historical tier table below is not a filing queue or an execution-equivalence
gate. First reproduce on a pinned current upstream revision and distinguish
source-dialect selection, syntax support and result semantics.

The extraction report now includes a standalone SQLite execution reproducer:

```bash
uv run --no-project --isolated --with sqlglot==30.18.0 -- python _project/sqlglot-upstream/repros/sqlite_extract.py
```

It returns 1 for observed translation failures and 2 for infrastructure errors.
It uses explicit expected calendar results and rejects two naive-lowering
counterexamples. No PostgreSQL engine is run. Keep the DATE-to-TEXT fixture
contract visible; these bounded witnesses do not prove universal equivalence.

On 30.6.0, 30.18.0 and upstream `5cfb5997a99010940138670adf3d6b34ac5a0a08`,
all 12 extraction cases still fail SQLite execution. DuckDB DATE_PART has a
separate parser gap. Read draft 03's timestamp-cast and integer-division
counterexamples before implementing a STRFTIME replacement. Upstream #2592
and #7152 are closed as not planned with invitations to contribute; that is
not a rejection of a properly scoped fix or proof of resolution.

Draft 02 needs DATE/TIMESTAMP and calendar-semantic validation. Draft 05 needs
native SingleStore validation rather than a MySQL-wide claim. Draft 06 requires
engine revalidation, preservation of CTE column names, a maintenance decision,
and fixture licensing checks. None should be filed unchanged from its earlier
version. Human review precedes upstream publication.

## Layout

| Path | What it is |
|------|------------|
| `repros/repro_all.py` | Single-file Python harness covering all candidates; pinned to `sqlglot==30.6.0` |
| `repros/generator.py` | Deterministic nightly translation generator and seed-replay entrypoint |
| `repros/transpiler_comparison.py` | Replays fixtures through the BenchBox wrapper, public SQLGlot (pure and compiled) and Polyglot on SQLite and DuckDB; see `transpiler-comparison.md` |
| `transpiler-comparison.md` | Recorded versions, commands, outcomes and timings from that comparison, with historical-only figures labelled |
| `issues/02-sqlite-interval-date-arithmetic.md` | Drafted bug report (Tier A) |
| `issues/03-sqlite-extract-not-lowered-to-strftime.md` | Drafted bug report (Tier B confirmed) |
| `issues/05-mysql-percentile-cont-extra-case-decorator.md` | Drafted bug report (Tier A) |
| `issues/06-questdb-dialect-feature-request.md` | Drafted feature request (Tier A) |

## Run the harness

```bash
uv run --no-project --isolated --with sqlglot==30.6.0 -- python _project/sqlglot-upstream/repros/repro_all.py
```

Exit code is zero only if all syntax/capability observations pass. A PASS is
not a semantic retirement gate, and a FAIL does not necessarily identify an
upstream defect. The proxy-source and native-source cases must be distinguished.

## Generated-case known-failure policy

The generator is advisory at the program level: it runs only in a non-required
nightly job and stays outside develop post-merge auto-revert. A generated-case
discovery still leaves that nightly job red after its evidence is validated.
Known failures must remain explicit, reproducible, and younger than seven
calendar days. Validate the policy with:

```bash
uv run -- python scripts/check_sqlglot_generator_known_failures.py --policy _project/sqlglot-upstream/generator-policy.json
```

An entry added to `generator-policy.json` must include its owner, canonical
`https://github.com/tobymao/sqlglot/issues/<number>` issue, first-known date,
failure artifact, SQLGlot version, source and target dialects, and non-negative
seed. Replay values must use the guard's narrow shell-safe token grammar. The
entry is valid on days 0 through 6 and fails the guard on day 7. Its artifact
must be an existing regular JSON file inside the repository, with top-level
`id`, `seed`, `sqlglot_version`, `source_dialect`, and `target_dialect` values
that exactly match the entry. Use `--today YYYY-MM-DD` for deterministic
boundary checks.

The policy's replay template invokes
`_project/sqlglot-upstream/repros/generator.py`. Replay a failure by
substituting the stored version, seed, dialects, and repository-relative
artifact path without changing any value. The template passes that path to
both `--failure-artifact` and explicit `--replay`; a missing replay file is an
infrastructure error rather than a new campaign. The path-independent template
travels inside the artifact, and the policy guard validates it after the
downloaded artifact is moved into its repository policy path.

The nightly pilot runs 1,024 DuckDB cases with a five-minute internal deadline
inside a ten-minute job. Its GitHub run ID is the deterministic seed, so every
discovery has an exact replay value while successive scheduled runs explore
new cases. Each case calls BenchBox's real `translate_sql_query` helper with
`strict=True` for both `duckdb -> duckdb` and `postgres -> duckdb`, then parses
the translated output in the target dialect. The generator minimizes a failing
case before writing its JSON evidence.

Exit status `0` means the bounded cohort was clean, `1` means a generated case
was discovered or exactly reproduced, and statuses `2` and above mean the
generator's contract or infrastructure failed. For status `1`, the nightly
workflow separately validates the summary and failure evidence and then
propagates the nonzero status, so the non-required nightly job remains red. A
bare process exit cannot be treated as a valid discovery. The policy guard and
every other nonzero status also remain blocking; the job does not use
`continue-on-error`. Summary and failure evidence are retained as workflow
artifacts for 14 days. Nightly remains outside develop post-merge auto-revert
and no required PR lane invokes the generator.

The workflow keeps policy shape, age, artifact, discovery, and other
infrastructure failures red. A generated-case discovery is advisory only in
its program placement; it does not make the nightly job green.

### Wrapper-call-shape convention

Any new repro for an item that lives in
`benchbox/utils/dialect_utils.py:translate_sql_query` MUST exercise both:

- `read=<target>, write=<target>` — direct dialect probe.
- `read="postgres", write=<target>` — wrapper / cross-dialect probe.
  BenchBox normalizes Netezza-style SQL into postgres before the SQLGlot
  call, so the wrapper's actual `read=` argument at the helper's call
  site is `postgres`.

A single-dialect probe is not a valid retirement gate: the 2026-05-04
`_restore_group_order_by_all_keyword` retirement TODO was filed against
a `read=duckdb` PASS while the wrapper path still emits `ORDER BY "ALL"`
on the same sqlglot version. Both probes must PASS before any post-fixup
in `dialect_utils.translate_sql_query` is retired. Per-repro audit
verdicts on coverage are recorded in the
[Audit verdicts](#repro-coverage-audit) section below.

### Repro coverage audit

Verdicts after the 2026-05-06 sweep (TODO
`sqlglot-repro-harness-wrapper-call-shape`):

| Repro | Read/Write pair exercised      | Production call site read/write | Verdict |
|-------|--------------------------------|--------------------------------- |---------|
| 1     | duckdb->duckdb + postgres->duckdb | postgres->duckdb              | OK -- both probes present after this TODO. |
| 2     | postgres->sqlite               | postgres->sqlite                 | OK -- BenchBox runs SQLite via the postgres-normalized wrapper path. |
| 3     | postgres->sqlite               | postgres->sqlite                 | OK -- same wrapper path as #2. |
| 5     | postgres->mysql                | postgres->mysql                  | OK -- MySQL output goes through the postgres-normalized wrapper. |
| 6     | dialect lookup (no transpile)  | n/a (capability check)           | OK -- shape-agnostic, no read/write pair to validate. |

## Current verdict (sqlglot 30.6.0)

| # | Item | Tier | Repro result | BenchBox workaround | Action |
|---|------|------|--------------|---------------------|--------|
| 1 | DuckDB `GROUP/ORDER BY ALL` quoted under `identify=True` | B (verify) | PASS for `read=duckdb`; **FAIL for `read=postgres,write=duckdb`** (the path BenchBox actually exercises) | `dialect_utils._restore_group_order_by_all_keyword` | **Keep workaround.** PR #3756 fixed direct DuckDB only; cross-dialect path still emits `ORDER BY "ALL"` on `sqlglot==30.6.0`. Do not file (narrower repro warranted before). |
| 2 | SQLite `DATE + INTERVAL` not lowered to date modifier | A | FAIL | `dialect_utils._fix_sqlite_unsupported_syntax` | File: `issues/02-sqlite-interval-date-arithmetic.md` |
| 3 | SQLite `EXTRACT` not lowered to `STRFTIME` | B (verify) | FAIL | `dialect_utils._fix_sqlite_unsupported_syntax` | File: `issues/03-sqlite-extract-not-lowered-to-strftime.md` (refs prior #2592) |
| 4 | Postgres `date + integer` not promoted to `INTERVAL` | D | n/a (AST-ambiguous) | `dialect_utils.fix_postgres_date_arithmetic` | Do not file. Keep as BenchBox pre-processor. |
| 5 | MySQL `PERCENTILE_CONT` extra `CASE WHEN x IS NULL` decorator | A | FAIL | `h2odb_variants.MYSQL_Q9_SQL` | File: `issues/05-mysql-percentile-cont-extra-case-decorator.md` |
| 6 | QuestDB dialect missing | A | FAIL (no dialect) | `platforms/questdb_rewriter.py` | File: `issues/06-questdb-dialect-feature-request.md` |
| 7 | Netezza / Vertica missing | C | n/a (existing issues) | `dialect_utils.normalize_dialect_for_sqlglot` | Comment on existing tracker issues; no new issues. |

## What's filed already upstream

- Netezza: open feature requests
  [#6040](https://github.com/tobymao/sqlglot/issues/6040),
  [#1289](https://github.com/tobymao/sqlglot/issues/1289); failed PRs
  [#7402](https://github.com/tobymao/sqlglot/pull/7402),
  [#5637](https://github.com/tobymao/sqlglot/pull/5637).
- Vertica: closed-without-merge PRs
  [#7277](https://github.com/tobymao/sqlglot/pull/7277),
  [#3351](https://github.com/tobymao/sqlglot/pull/3351),
  [#3325](https://github.com/tobymao/sqlglot/pull/3325).
- DuckDB `ORDER BY ALL` parsing: [#3755](https://github.com/tobymao/sqlglot/issues/3755) closed by [#3756](https://github.com/tobymao/sqlglot/pull/3756) (merged in v25.6.0). Our repro confirms the fix covers the `identify=True` case for `read="duckdb", write="duckdb"`. The fix does **not** cover the `read="postgres", write="duckdb"` path BenchBox uses for Netezza/Postgres-shaped TPC sources — that still emits `ORDER BY "ALL"` on `sqlglot==30.6.0`, which is why the BenchBox post-fixup stays.
- SQLite `EXTRACT`: prior [#2592](https://github.com/tobymao/sqlglot/issues/2592) (2023, closed without linked fix).

## Pre-flight before filing

1. Re-run the harness to confirm `FAIL` lines still reproduce on the latest published `sqlglot` version. Update the `sqlglot_version` frontmatter in each draft to match the version you reproduce against.
2. Skim each draft for tone — they are written in a neutral, evidence-led voice; no advocacy, no project promotion beyond a single offer line.
3. Check existing issues and PRs, correct the draft's semantic claims, and get
   human review. Follow up on an existing report when appropriate; otherwise
   submit one bounded report with a standalone reproducer.
4. After filing, set `filed: true` and add a `tracker_url:` line to each draft's frontmatter.

## Remaining preparation

- The ALL reproducer already exercises both native DuckDB and PostgreSQL proxy
  declarations. Preserve both during retirement checks, but first correct the
  source declaration when the input actually uses DuckDB-only syntax.
- If maintainers accept the QuestDB dialect contribution offer, the
  rewriter in `benchbox/platforms/questdb_rewriter.py` becomes the natural
  starting point.
