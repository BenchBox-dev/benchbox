# SQLGlot versus Polyglot execution evidence

Reproducible local comparison of the BenchBox wrapper, public SQLGlot (pure
Python and compiled) and Polyglot. It supports a permanent-fork decision that is
already made; it does not reopen it. Raw per-case JSON is regenerated on demand
and is not stored in Git.

## What was recovered and what was not

The earlier review logs `/tmp/sqlglot-polyglot-catalog-verified.log` and
`/tmp/sqlglot-compiled-arm.log` no longer exist on the workstation, so no script
could be reconciled against them. The figures below are historical-only, taken
from the tracker item, and are not fresh measurements:

- Synthetic warm median of roughly 23 us for Polyglot versus 43 us for compiled
  SQLGlot.
- Review stack Polyglot 0.9.2, SQLGlot 30.18.0, DuckDB 1.5.5. Both translators
  failed binary DATE plus INTERVAL to SQLite, and Polyglot's `ORDER BY ALL`
  quoted `ALL` and could silently misorder SQLite results.

Everything in the next sections was measured fresh on 2026-10-27.

## Harness

`_project/sqlglot-upstream/repros/transpiler_comparison.py` replays 18 fixture
queries (7 known-good, 11 known-problem) against both SQLite and DuckDB with
hand-written expected rows, giving 36 outcomes per engine. Outcomes are separated into `match`,
`translate_error`, `exec_error` and `wrong_result` (silent). `--summarize` lists
repairs and regressions against the pure-Python SQLGlot baseline.

The wrapper runs through `benchbox.utils.dialect_utils.translate_sql_query` with
`strict=True` (so a failure raises instead of falling back to the original
text), `identify=True` and, for SQLite, an additional run with
`date_columns=["d"]`. The public SQLGlot runs use `read=<source>` and
`identify` on exactly when the wrapper would apply it (the wrapper's
`NO_IDENTIFY_DIALECTS` list). Polyglot's `transpile` offers no `identify`
option, so identifier-quoting behaviour of Polyglot under `identify=True` is not
reproduced here.

Commands (repository revision `818268a2b`, Python 3.12.15, SQLite 3.53.1,
DuckDB 1.5.5):

```bash
S=_project/sqlglot-upstream/repros/transpiler_comparison.py
uv run -- python $S --engine wrapper --out /tmp/tc/wrapper.json
uv run -- python $S --engine wrapper-date-columns --out /tmp/tc/wrapper-date-columns.json
uv run --no-project --isolated --with sqlglot==30.25.0 --with duckdb==1.5.5 -- python $S --engine sqlglot --out /tmp/tc/sqlglot.json
uv run --no-project --isolated --with 'sqlglot[c]==30.25.0' --with duckdb==1.5.5 -- python $S --engine sqlglot-compiled --out /tmp/tc/sqlglot-compiled.json
uv run --no-project --isolated --with polyglot-sql==0.13.4 --with duckdb==1.5.5 -- python $S --engine polyglot --out /tmp/tc/polyglot.json
python3 $S --summarize /tmp/tc/*.json
```

| Engine | Version | Revision / pin |
|--------|---------|----------------|
| Wrapper (bundled fork) | 30.25.0.1 | fork `87a34a6cc16f`, upstream `9d2966ed3f8a`, wheel SHA-256 `c8556873...bfb2`, pure Python |
| SQLGlot pure Python | 30.25.0 | PyPI |
| SQLGlot compiled | 30.25.0 | PyPI `sqlglot[c]`, `sqlglotc` 30.25.0 |
| Polyglot | polyglot-sql 0.13.4 | PyPI |

Compiled import paths were verified: the compiled run loads 52 `sqlglot`
modules from `.so` extension files (the `*__mypyc` helpers plus compiled
`parser`, among others);
the pure run asserts zero and the wrapper imports zero (the bundled wheel is
`py3-none-any`). The harness fails if the expected mode does not match.

## Results

36 outcomes per engine (18 cases times two targets).

| Engine | match | translate error | exec error | silent wrong |
|--------|-------|-----------------|------------|--------------|
| Wrapper | 31 | 0 | 4 | 1 |
| Wrapper with `date_columns` | 31 | 0 | 4 | 1 |
| SQLGlot pure | 27 | 2 | 6 | 1 |
| SQLGlot compiled | 27 | 2 | 6 | 1 |
| Polyglot 0.13.4 | 29 | 2 | 2 | 3 |

All five engines match all 14 known-good outcomes (7 queries times two targets).

Repairs and regressions relative to pure SQLGlot 30.25.0:

- Wrapper: four repairs, no regressions. Two are the `netezza` source dialect
  (public SQLGlot and Polyglot raise an unknown-dialect error; the wrapper also
  executes `DATE - INTERVAL '90' DAY` correctly on SQLite and DuckDB). The other
  two are `EXTRACT` to SQLite for Postgres and DuckDB sources.
- Compiled SQLGlot: identical outcomes to pure SQLGlot.
- Polyglot: four repairs (`EXTRACT` year and month, `DATE_PART`, and
  `ORDER BY ALL` to SQLite, which it rewrites to `ORDER BY 1, 2`) and two
  regressions (integer division and `STRING_AGG`, below).

Problems that remain, by class:

- Errors, all engines: `DATE + INTERVAL` to SQLite (the wrapper, SQLGlot and
  Polyglot each emit an `INTERVAL` literal SQLite rejects); `GROUP BY ALL` to
  SQLite. The wrapper additionally fails `ORDER BY ALL` and `DATE_PART` to
  SQLite, both of which Polyglot handles.
- Silent wrong results: Postgres integer division `7 / 2` to DuckDB returns 3.5
  on every engine (DuckDB `/` is not integer division). Polyglot also emits
  `CAST(7 AS REAL) / 2` to SQLite and returns 3.5, a regression: the wrapper and
  SQLGlot return 3 there.
- Polyglot drops the `ORDER BY` inside `STRING_AGG` for SQLite
  (`GROUP_CONCAT(w, ',')`). The fixture table `g` is stored out of order, so the
  case returns `z,x,y` instead of `x,y,z` and counts as a silent wrong result, a
  regression against SQLGlot and the wrapper.
- The previously reported Polyglot `ORDER BY ALL` misorder is not present in
  0.13.4 without `identify`; the quoting behaviour under `identify=True` cannot
  be reproduced because the option does not exist.

## Timing

Identical workload for every engine: 14 known-good queries times two targets,
each call timed with the repository clock (`benchbox.utils.clock.mono_time` and
`elapsed_seconds`, loaded by path so the isolated environments need no
`benchbox` install), 300 iterations with 30 discarded as warmup (3,780 timed
calls). Cold start is a freshly spawned interpreter, seven runs, measuring process
wall time with import and first translation separated. Each report records
`os.getloadavg()`; this pass started at a one-minute load average of about 5
on an otherwise idle Apple silicon workstation.

| Engine | warm median us | warm p95 us | cold process median ms | import median ms | first translate median ms |
|--------|---------------|-------------|------------------------|------------------|---------------------------|
| Wrapper | 218.7 | 349.3 | 749.9 | 372.1 | 302.2 |
| SQLGlot pure | 130.4 | 204.5 | 76.8 | 26.7 | 2.8 |
| SQLGlot compiled | 33.5 | 70.4 | 108.1 | 51.6 | 9.4 |
| Polyglot | 16.6 | 23.4 | 52.9 | 10.6 | 0.4 |

Warm ordering is Polyglot, compiled SQLGlot, pure SQLGlot, wrapper. Cold-process
ordering is Polyglot, pure SQLGlot, compiled SQLGlot, wrapper: compiled SQLGlot
pays about 25 ms more import time and a slower first translation than pure
Python in return for roughly 3.9 times faster warm translation. The wrapper
cold start includes importing the `benchbox` package and its first translation
loads the SQLite rewrites, so it is not a translator-only figure. The
Polyglot to compiled warm ratio (about 0.5) matches the historical roughly 23 us
versus 43 us on a different workload.

Earlier passes of an earlier harness revision, run while the shared workstation
showed load averages of 48 to 100, inflated every figure two to five times and
are not recorded. Ratios and orderings were stable across them.

## What was not run

- The historical logs were unrecoverable, so no script was reconciled against
  them.
- No PostgreSQL, cloud or other engine was run; only in-process SQLite and
  DuckDB execute the translated text, with SQLite dates stored as ISO text.
- Polyglot under `identify=True`, and the `netezza` source dialect for Polyglot
  and public SQLGlot, are not available to compare.
- Only one machine and one Python (3.12.15) and a single timing pass, so
  timings are indicative and not a statistical comparison across hardware.
