# Browser-test fixture sources

Deterministic input corpus for the Results Explorer browser suite, which
runs in `BenchBox-dev/benchbox-site`. The generator
`_project/scripts/explorer_pipeline/browser_fixtures.py generate <dir>`
copies files from here into `<dir>/source/`, applies controlled metadata
variants, and then runs
`uv run -- python _project/scripts/explorer_publish.py build` to produce
`<dir>/data/results.duckdb`, the companion bundle copies and
`fixture-ids.json`. `scripts/site_inputs.py` ships the result as the
`explorer/fixtures` member of the site-inputs bundle.

The source corpus under `results-data/bundles/` is never touched.

## Why these specific bundles

- **TPC-H SF 0.01 power across DuckDB / DataFusion / Polars / Spark /
  CedarDB** - gives a valid ≥4-platform compare cohort at a single
  `benchmark × scale_factor × phase` key, which is required for the
  compare-happy-path and honesty-control tests. All five are
  byte-for-byte copies of curated `results-data/bundles` power runs
  (see `provenance/*.source.manifest.json`, hash-verified by the
  generator before use). The Polars source is a genuine SF 0.01 power
  run over a partial query subset (60k lineitem rows, matching SF 0.01
  scale); its narrower query coverage is real, not rescaled.
  Pandas stays a `standard`-phase fixture because its source is a `power`
  run relabelled fixture-only under `synthetic/`.
- **Star-schema SF 0.01 DuckDB** - provides a second `benchmark` so the
  compare-invalid benchmark-mismatch hard-block test can reference a
  real bundle rather than a synthesised stub.

The Pandas input is a byte-for-byte copy of the immutable internal corpus
bundle named in `provenance/tpch-pandas-sf0.01-20260826-8bde2222.source.manifest.json`.
The generator verifies both hashes before use. Its source is a `power` run;
the standard-phase Pandas row is generated under `synthetic/` with run ID
`8bde2222-fixture-standard`. It is therefore fixture-only, not relabelled
contributed evidence.

## Generator-derived honesty coverage

The generator also synthesises additive variants from these sources
(never written back here):

- **Trust labels** - `maintainer-run` (verbatim sources),
  `community-submission` (DuckDB under `community/` with a submission
  manifest), and `vendor-supplied` (Pandas under `vendor/`).
- **Tuned / notuned pairs** - DuckDB, Polars, and Pandas each have a
  tuned sibling (`config.tuning_mode="tuned"` plus a `.tuning.json`
  sidecar whose notes mark it synthetic fixture-only). DuckDB and Polars
  pair in the power cohort; Pandas pairs in standard. At least two
  platforms in the ≥4-platform power cohort exercise both modes. The
  tuned rows assert UI tuning affordances, never tuning efficacy: their
  timings are the untuned baseline's and no speedup is claimed.

## Adding new sources

Prefer reusing existing curated bundles from `results-data/bundles/`
rather than committing new fixtures. If a variant cannot be expressed by copying
an existing bundle plus a sidecar or metadata mutation, that's a signal
the explorer pipeline needs the capability - raise the question before
growing this directory.
