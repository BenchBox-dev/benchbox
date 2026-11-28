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
file. A passed run may carry `SKIPPED` row counts when the
skip has a documented reason and at least one row count was checked: the data
was refreshed, the count was within a tolerance or range, or the query's count
depends on the substitution parameters and the run used `--seed`. SQL and
DataFrame runs follow the same rule.

Row-count references live in `benchbox/core/expected_results/reference_row_counts/`, one file per
benchmark and scale factor, read through `row_count_references.py`. Each file lists every query for
every recorded stream (TPC-H streams 0-2, TPC-DS streams 0-3, SSB stream 0) and records how it was
made: data generator and stamp, data checksum, reference engines and versions, date and the
agreement result. The TPC answer set is the reference for TPC-H at SF 1 stream 0; every other
reference is labelled "agreement with <engine versions>" because no TPC answer exists for it.
Validator output carries the reference kind (`reference_kind`, `reference_label`; DataFrame
evidence lists `reference_kinds`). A reference is regenerated, never edited:
`make row-count-references-regen BENCHMARK=<tpch|tpcds|ssb> SCALE=<scale factor> WORK_DIR=<durable directory>`
(run SF 10 only through `make heavy-run`), then
`uv run -- python scripts/generate_corpus_rule_data.py --write`. The generator stops and reports
the query when DuckDB and DataFusion disagree on any query of any stream; it never omits a query.

DataFrame combinations that validate row counts: TPC-H at every scale with a reference, seeded or
not (a seeded run checks fixed-count queries exactly and range-checks the parameter-sensitive
ones; a count inside a range is recorded as passed, and a loose-tolerance query whose count is
not exact is recorded as a documented skip); SSB and TPC-DS at every scale with a reference. A
TPC-DS row is checked against the counts of its own stream when the run uses the reference seed
(`--seed` unset or 1); any other TPC-DS row records a documented skip. A scale with no recorded
reference records `no_reference`. Any other combination records `uncertain` or `not_run` and
cannot be staged.

## Guard refusals

A guard refusal is never worked around: no bypass flag, no edited guard, no
override of a check. Before relaying a refusal as a blocker, verify it against
live repository and machine state (does the file exist, what is `HOME`, what
does the command print now), not against a summary, recap or earlier message.
