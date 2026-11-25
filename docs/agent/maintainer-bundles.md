# Maintainer bundles and the pseudonym salt

How a maintainer's local run becomes a corpus bundle, and why the pseudonym
salt is never an owner blocker. The user-facing description is the "Pseudonym
salt" section of `results-data/README.md`.

## The salt is automatic

Resolution order, first hit wins:

1. a value passed in code;
2. the `BENCHBOX_MACHINE_ID_SALT` environment variable (an override);
3. the private file `~/.benchbox/submission_salt`;
4. otherwise a random value is generated and written to that file, but only by
   `benchbox submit`, `benchbox export` and `benchbox publish run`.

Never ask the owner for the salt and never report its absence as a blocker.
Check live state instead, without printing the value:

```bash
test -s ~/.benchbox/submission_salt && echo present || echo missing
```

Never `cat`, `echo`, log, commit or paste the file or the variable. A test or
script that needs to compare pseudonyms recomputes them inside the script and
prints only match or no match.

## Tools that never generate a salt

`scripts/stage_seed_corpus_bundle.py` and
`_project/scripts/results_explorer_corpus_migrate.py` use the variable, then an
existing file, and otherwise refuse naming the file path. The salt feeds the
permanent `result_id` (a digest of the staged bytes), so a silently generated
salt would fork corpus identity. They print a stderr notice when they use the
file. A refusal means neither the variable nor the file exists for the `HOME`
the command ran under: check `HOME`, then run `benchbox export` once or place
the maintainer value in the file, rather than working around the guard.

The CI seed-corpus lane passes the `BENCHBOX_MACHINE_ID_SALT` secret. A runner
has no salt file, so a missing secret fails the lane.

## Staging a local run

```bash
uv run -- benchbox run ... --capture-plans
make corpus-stage-local RESULT=~/Developer/benchmark_runs/results/<run>.json OUT=<staging dir>
uv run -- python scripts/validate_submission.py --require-manifest --trust-internal-source <staging dir>/<run>.json
```

`make corpus-stage-local` wraps
`scripts/stage_seed_corpus_bundle.py --internal-manifest`. It writes the
anonymized bundle and an internal manifest into `OUT`. With `--capture-plans`
it also sanitizes the `<run>.plans.json` companion (local paths redacted,
`raw_explain_output` cleared, operator `platform_metadata` emptied), re-scans
it, and stages it with its hash in the manifest. It still refuses if anything
unsafe remains. Keep `OUT` outside the repository. Copying staged files into
`results-data/bundles/` needs the owner's approval. Published bundle JSON is
never modified.

`--trust-internal-source` is what admits a subscale internal bundle. Without it
the validator answers `only compliance_class=official may be submitted`.
`benchbox publish run` is not needed for the corpus path.

Staging skips a bundle whose `summary.validation` is a non-clean status such as
`failed`, `partial`, `not_run` or `uncertain` (`validation_not_clean`). A run
with no row-count reference at its scale records `no_reference` with the reason
"no row-count reference at this scale factor"; it is staged, ranked and labelled
"Row counts not checked". `passed` requires per-query row-count evidence in the
file. A passed run may carry `SKIPPED` row counts when the skip has a documented
reason and at least one row count was checked: the data was refreshed, the count
was within a tolerance or range, the reference data differs from the run's, or the
query's count depends on the substitution parameters (`parameter sensitive:`).
SQL and DataFrame runs follow the same rule.

## Stream checks on TPC-H and TPC-DS rows

The measured power and throughput streams use parameters the specification never publishes
answers for, so every TPC-H and TPC-DS row is checked by one decision function shared by the
SQL and DataFrame paths (`benchbox/core/validation/stream_checks.py`), in this order:

1. LIMIT and one-row bounds derived from the templates (`structural_bounds.json`, regenerated with
   `uv run -- python _project/scripts/generate_structural_bounds.py`, checked against every
   template's generated SQL by a unit test). Runs at every scale. Recorded as `check: structural`;
   a violation fails the row. A structural pass alone is never "checked".
2. An exact reference for these exact parameters: the dsqgen RNGSEED plus STREAMS file index, or
   the qgen seed, recorded on the row (`seed`, `streams_index`; a null seed is the generator
   default). Never keyed by stream number. `check: exact`.
3. An invariant count: a count that never varied across the recorded sample. `check: invariant`.
4. An enumerated range (TPC-H SF 1 only: Q11, Q13, Q18, Q20 and the Q16 tolerance). `check: range`.
5. Must return rows: the statement never returned zero rows in the sample. `check: never_empty`.
6. Otherwise a documented `parameter sensitive:` skip.

The invariant and must-return-rows checks come from the `integrity` section of each reference
file: at least 512 draws per scale at SF 0.01, 0.1 and 1 (256 above SF 1) spread over the
per-template and STREAMS generation paths, the seedless seeds and load-end-time-style seeds. DuckDB
runs every draw; DataFusion must agree on a 64-draw subset or generation stops. A reference applies
only when the run's data stamp and the query generation identity (generator binary digests,
template set and the below-SF-1 template overrides in `benchbox/core/tpcds/c_tools.py`) equal the
reference's. Otherwise the row records a documented `reference data differs:` skip, the structural
checks still run, and the run records `no_reference`. A unit test fails when the generator stamp or
the query generation identity changes without regenerated references. Written bundles (schema 2.7)
carry `validation.stream_checks` (the reference files applied, with hashes and stamps, and counts
per check kind) and, on each row, `check`, `bound`, `seed` and `streams_index`.

A violation makes the row `FAILED`, so the run is `failed`, refused and never official. The checks
run at write time on new runs only; they are never applied to the outcomes of stored files.

### Settling a disputed stream check

A false failure from an invariant or must-return-rows check is possible when a count varies less
often than the sample can show. Settle it, never by editing a stored file or relaxing a check:

1. Take the statement text recorded for the failing row (the row's `seed` and `streams_index`
   regenerate it exactly) and run it on DuckDB against data with the same data stamp.
2. If DuckDB returns the platform's count, the reference was incomplete: add that seed to the
   sample, regenerate the references with the make target below, and re-run the result.
3. If DuckDB disagrees, the platform answer is wrong.

Row-count references live in `benchbox/core/expected_results/reference_row_counts/`, one file per
benchmark and scale factor, read through `row_count_references.py`. Each file lists the exact
streams (TPC-H qgen defaults, TPC-DS per-template RNGSEED 1001-1004 and STREAMS RNGSEED 42 files 0-8,
SSB stream 0) and records how it was made: data generator and stamp, query generation identity, data
checksum, reference engines and versions, date and the agreement result. The TPC answer set is the
reference for TPC-H at SF 1 on the default parameters; every other reference is labelled "agreement
with <engine versions>" because no TPC answer exists for it. Validator output carries the
reference kind (`reference_kind`, `reference_label`; DataFrame evidence lists `reference_kinds`). A
reference is regenerated, never edited:
`make row-count-references-regen BENCHMARK=<tpch|tpcds|ssb> SCALE=<scale factor> WORK_DIR=<durable directory>`
(run it only through `make heavy-run`), then
`uv run -- python scripts/generate_corpus_rule_data.py --write`. The generator stops and reports
the statement and draw when DuckDB and DataFusion disagree; it never omits a query.

DataFrame combinations that validate row counts: TPC-H and TPC-DS at every scale with a reference,
seeded or not, and SSB at every scale with a reference. A scale with no recorded reference records
`no_reference`. Any other combination records `uncertain` or `not_run` and cannot be staged.

## Guard refusals

A guard refusal is never worked around: no bypass flag, no edited guard, no
override of a check. Before relaying a refusal as a blocker, verify it against
live repository and machine state (does the file exist, what is `HOME`, what
does the command print now), not against a summary, recap or earlier message.
