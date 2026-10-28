# Tiny SQL translation experiment

This local research prototype compares SQLGlot, BenchBox's strict translation
wrapper, CodeT5-small full SQL generation, and SQLGlot plus a separate CodeT5
span-edit model. It does not change the supported package or production SQL
translation. A negative result is a useful outcome.

## Run

Use an Apple Silicon machine with MPS and an explicit artifact directory outside
the checkout. Dependencies, including an editable reference to this checkout,
are isolated in this directory's uv environment. The lockfile pins dependencies;
SQLite comes from the pinned uv Python installation and its actual version is
recorded and checked at evaluation.

```sh
cd _project/sql-translation-ml
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ty check --no-respect-ignore-files oracle.py corpus.py experiment.py train.py evaluate.py
uv run --locked python experiment.py prepare --run-dir /absolute/outside/checkout/run
uv run --locked python train.py --run-dir /absolute/outside/checkout/run --mode full --feasibility
uv run --locked python train.py --run-dir /absolute/outside/checkout/run --mode full --resume
uv run --locked python train.py --run-dir /absolute/outside/checkout/run --mode edit --feasibility
uv run --locked python train.py --run-dir /absolute/outside/checkout/run --mode edit --resume
uv run --locked python evaluate.py --run-dir /absolute/outside/checkout/run --system sqlglot
uv run --locked python evaluate.py --run-dir /absolute/outside/checkout/run --system benchbox
uv run --locked python evaluate.py --run-dir /absolute/outside/checkout/run --system full
uv run --locked python evaluate.py --run-dir /absolute/outside/checkout/run --system edit
uv run --locked python evaluate.py --run-dir /absolute/outside/checkout/run
```

Train the two models sequentially. Each feasibility run stops at 200 optimizer
steps. Resuming continues from its optimizer, random state, and data cursor,
with the same cumulative 24-hour training budget. A run stops after three epochs;
development execution accuracy selects the checkpoint. Training ends early at
100% development accuracy because no later checkpoint can strictly improve the
selection score. SIGINT/SIGTERM request
a stop at the next optimizer boundary. A timeout or interrupted run retains its
resume checkpoint and does not produce a successful training summary. Resume
rejects changed input artifacts. Only load checkpoints produced by this local
experiment: optimizer state uses PyTorch's trusted checkpoint loader.

The trainer uses all parameters of the pinned
[CodeT5-small model](https://huggingface.co/Salesforce/codet5-small/tree/b1ee9570c289f21b5922b9c768a1ce12957bf968),
batch size one, accumulation to sixteen, AdamW at `5e-5`, and greedy decoding.
Inputs and outputs have a 1,024-token limit. No text is silently truncated.
Final inference uses four CPU threads for both model candidates; training and
development selection use MPS. The report records the inference device, so these
latencies are not GPU throughput measurements.
Inference has a 30-second generation limit and SQL has a two-second interrupt
limit. These limits are cooperative, so the current operation may finish after
the deadline. No execution-feedback retry or candidate repair is performed.

Weights, datasets, and logs stay outside Git. `verdict.md` is the readable result;
JSONL retains failures and case evidence. `run-manifest.json` records file hashes,
runtime versions, checkpoint size and identity, and directional measurements.

## Frozen population and comparison

The source-native grammar covers joins, CTEs, scalar/correlated subqueries,
aggregations, windows, set operations, CASE, casts, strings, and date arithmetic.
The concrete supported operations are the explicit expressions, predicates, and
relational forms in `corpus.py`. This is a finite synthetic population over two
typed tables, not a claim of support for every expression in those categories.
DDL, writes, extensions, recursive CTEs, nondeterminism, decimals, timezone
conversions, and unconstrained order-sensitive limits are outside the population.
The prototype must not be promoted as a general SQL replacement from this corpus.

`prepare` writes the contract and split manifest before trying translations.
Families contain related expressions and predicate/skeleton combinations.
Families connected by an identifier/constant-normalized structure are merged
before splitting. Reverse directions stay together. Every INTERSECT template is
held out, alongside other held-out combinations of familiar constructs.
The first 10,000 accepted training pairs and 2,000 development pairs in seeded
order are used for each model, pooled across directions. Final metrics are
separate by direction. Locked tests use distinct normalized structures, with
at least 1,000 required in each direction for a positive result.

The model interface contains original source SQL, direction, actual engine
versions, schema, and session semantics. Full generation never receives a
SQLGlot candidate. The edit interface additionally receives that candidate and
emits a JSON array of `{start, end, text}` replacements at Python string offsets.
Offsets must be ordered, nonoverlapping, and within the original candidate.
An empty array means no change.

Each query runs against five independently seeded small fixtures. They contain
NULLs, duplicate rows and grouping keys, unmatched joins, an empty relation,
negative and zero values, leap/year boundary dates, whitespace, Unicode, and
combining characters. All five results must agree. Shape, multiplicity, NULLs,
and strings are compared exactly; numeric values use exact equality with no
tolerance. Native dates are read as ISO strings. Unordered results are multisets;
an explicit top-level order is compared as a sequence. The generated ordered
queries include a unique ID tie-breaker and explicit NULL placement.

Training labels prefer execution-validated baseline candidates. When both fail,
an independently authored native spelling from the paired grammar is accepted
only after the same five-fixture validation. Neither baseline's failure changes
test eligibility. Invalid sources are recorded before translation. All-empty
fixture results remain visible and are counted separately in evaluation: they
are weaker witnesses than nonempty results.

Source-only long inputs are a separate reported stratum. On an eligible input,
parsing/execution errors, timeouts, empty outputs, output overflow, invalid edits,
and abstentions are failures. The report includes feature/length/held-out
breakdowns and baseline cases repaired or broken by each model.

The one-sided 95% decision bound is the minimum of an exact binomial lower bound,
a seeded family-cluster bootstrap lower bound, and an exact bound treating a
family as successful only when all its members pass. The latter guards against
an all-success bootstrap falsely implying certainty. At least 100 family clusters
and 1,000 structures per direction are required. A model supports the 99%
hypothesis only if its bound reaches 99% in both directions. Full and hybrid
claims are separate. Five finite witnesses never prove universal equivalence.

## Prior art and external material

The production decision remains the
SQLGlot use-and-non-use ADR.
The baselines call SQLGlot directly and
`benchbox.utils.dialect_utils.translate_sql_query(..., strict=True)` respectively.
The wrapper's built-in SQLite fixes are part of that named baseline.

The deterministic generator at `_project/sqlglot-upstream/repros/generator.py`
provides the seed/replay precedent. Its untyped SQL grammar is not an execution
oracle. `benchbox/core/validation/cross_platform.py` provides a reporting pattern,
but its trailing-space stripping and NaN-to-NULL conversion are unsuitable here;
the experiment uses a strict comparator instead. Tests inject wrong DISTINCT,
join, predicate, integer-division, NULL-order, and rounded-aggregate translations.

`supplemental.py` adapts four existing read-primitives aggregation/join queries
only by mapping identifiers to the fixture schema. They are an independent
existing-workload supplement, never training or development input and never
pooled into synthetic accuracy. The original catalog hashes and query identifiers
are recorded. The catalog retains its original Apache Impala and BenchBox
attribution in `benchbox/core/read_primitives/catalog/queries.yaml`.

External-data audit (2026-09-12):

- [PARROT](https://github.com/OpenDataBox/PARROT/tree/6978c9e4269850e8e01904dc35475896db96e47f)
  aggregates many source benchmarks, including TPC-DS material overlapping
  BenchBox's benchmark families. Its README says MIT, but the inspected tree has
  no root LICENSE and constituent data requires separate provenance review.
  No PARROT data is imported, trained on, or scored here.
- [DLBench](https://github.com/DLBenchll/DLBench/tree/a3525919033faac73e60a13a88c2d14ab6953f23)
  has an Apache-2.0 root license and BIRDTrans SQLite-to-DuckDB tasks with schema
  and INSERT fixtures. Its task schema does not offer SQLite as a target.
  The inspected app-store fixture contains string `nan` sentinels and floating
  columns; it is not automatically compatible with this experiment's typed
  fixture contract. Corpus-specific provenance, overlap, and five-fixture
  adaptation remain necessary before use. It stays reserved for external testing.

Public benchmark exposure in CodeT5's pretraining cannot be ruled out. External
benchmark results would therefore remain supplementary. Do not expand data,
compute, metrics, or production integration automatically after this run.
