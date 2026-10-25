# Contributing Benchmark Results

Thank you for contributing to the BenchBox community results dataset! Community-submitted results help everyone compare platforms on real workloads and grow the benchmark coverage.

## Prerequisites

1. **Install BenchBox** - follow the [Getting Started](usage/getting-started.md) guide
2. **Run a benchmark** - you need a complete benchmark result to submit
3. **Configure a private public-submission salt** - set a stable, non-empty
   `BENCHBOX_MACHINE_ID_SALT` before the first `benchbox submit`; store and
   reuse it through a secret manager or protected local environment config

## Step-by-Step Submission Flow

### 1. Run a benchmark

Run a complete benchmark suite (not a cherry-picked subset of queries):

Install the platform extra first in your project environment (for example,
`uv add benchbox --extra duckdb`).

```bash
uv run -- benchbox run --platform duckdb --benchmark tpch --scale 0.01
```

The result JSON is written to `benchmark_runs/results/`.

One optional companion file may be written next to the result JSON. Add
`--capture-plans` on supported platforms to create `<result>.plans.json`;
`benchbox submit` packages it automatically when it sits next to the result
JSON, so a missing plans file does not make a default submission incomplete.

The `--tuning` flag accepts `notuning` (default), `tuned`, `auto`, or a path to
a YAML config; use `tuned`, `auto`, or a YAML path only for intentionally tuned
submissions. Tuning needs no file of its own: the configuration a run requested
and the ledger of what it applied are recorded inside the result JSON under
`platform.tuning`. Older bundles carry `<result>.tuning.json` and
`<result>.applied.json` companions instead, and those are still accepted.

### 2. Configure the public-submission salt

`benchbox submit` pseudonymizes retained public identifiers and refuses to
package a public contribution when `BENCHBOX_MACHINE_ID_SALT` is unset or
empty. Before your first public submission, set it to a stable, private,
non-empty random value:

```bash
export BENCHBOX_MACHINE_ID_SALT="<stable-private-random-value>"
```

Store and reuse the value through a secret manager or protected local
environment configuration. Do not commit it, include it in the submission
package, or paste it into the pull request. Setting a new value for every run
would make pseudonymous identifiers inconsistent across your submissions.

### 3. Package the submission

Use `uv run -- benchbox submit` to create a submission package:

```bash
uv run -- benchbox submit --last --output ./submission

uv run -- benchbox results --paths --limit 25

uv run -- benchbox submit benchmark_runs/results/tpch_sf001_duckdb_20260401_120000.json --output ./submission

uv run -- benchbox submit --last --dry-run
```

The commands package the most recent result, list result paths, package a specific result file, and preview what would be packaged without writing files. The `--paths` option writes one path per line to stdout, so it is safe to pipe to `xargs`. Use it to find the exact path when you need to choose a specific run.

This creates a `submission/` directory containing:

| File | Description |
|------|-------------|
| `bundle/<result>.json` | The canonical schema-v2 result bundle |
| `bundle/<result>.plans.json` | Optional query execution plans from `--capture-plans` |
| `<result>.manifest.json` | Metadata: hash, benchmark, platform, contributor |
| `CONTRIBUTING.md` | PR instructions (for reference) |

The manifest's `submitted_by` field defaults to your `git config user.name`.
If that is empty (e.g. a fresh sandbox or CI runner), `benchbox submit` warns
and writes the field as `""`. Override with `--submitted-by "Your Name"` if
you prefer not to set git config or want a different attribution.

### 4. Fork and open a PR

1. Fork the [BenchBox repository](https://github.com/BenchBox-dev/BenchBox) on GitHub (or use your existing fork)
2. Copy the contents of `submission/bundle/` into `results-data/bundles/` in your fork
3. Copy `submission/<result>.manifest.json` alongside the bundle files (the per-bundle manifest filename inherits the bundle stem so two contributors submitting the same week cannot collide)
4. Regenerate the inventory before you commit:

   ```bash
   uv run -- python scripts/generate_corpus_inventory.py --write
   ```

5. Commit and open a pull request against the `published-results` branch of [`BenchBox-dev/BenchBox`](https://github.com/BenchBox-dev/BenchBox) (the public repository)

Use this PR title format:

```
results: <benchmark> <platform> sf<scale>
```

Example: `results: tpch DuckDB sf1.0`

### 5. CI validation

When your PR is opened, the **Validate Submission** workflow runs automatically. It checks:

- **Schema compliance** - the bundle is valid schema-v2 JSON with all required fields
- **Hash verification** - the SHA-256 hash in the manifest matches the bundle contents
- **Sanity checks** - no all-zero timings, no negative durations, valid platform/benchmark names
- **Cache evidence** - a recorded session cache-control receipt must confirm the cache is disabled; on Snowflake/Redshift a bundle declaring an enabled result cache without a disabling receipt is refused; all-SUCCESS runs with zero rows everywhere are flagged
- **Timing plausibility (warnings only)** - `timing-plateau` (implausibly tight per-query band), `small-scale-floor` (tiny data answered slowly), `scale-invariant` (timings flat across a 10x scale span), and informational `floor-outlier` (fastest query far above the peer median). Warnings never fail validation; sub-millisecond rows are timer noise and excluded from the evidence
- **Compliance gate** - unofficial `compliance_class` values are refused; only `official` may be submitted. The `official` class marks BenchBox's scale-factor and seed gate; it is not a TPC audit, certification or compliance claim. TPC-derived results are labelled "Derived from <benchmark>" and do not comply with the TPC specification
- **Query-set coverage** - the normalized query IDs must cover the benchmark's canonical query set (e.g. TPC-H Q1–Q22 in any Q-prefixed, bare, or padded spelling); the benchmark id is matched case-insensitively
- **Cost provenance** - direct cost totals require `normalized_cost` provenance; totals alongside `cost_status: unavailable` are refused
- **Metadata extraction** - a summary comment is posted on the PR showing what the submission adds

Community submissions must include a manifest, execute at least one query, and
report `summary.validation=passed` with no failed measurement evidence.
Truthful partial results are accepted only from the trusted maintainer mirror
path and remain excluded from rankings.

### 5a. Plausibility overrides

Timing-plausibility findings (`timing-plateau`, `scale-invariant`,
`small-scale-floor`) block community publication until covered by a
committed `<bundle_stem>.override.json` companion beside the bundle.
The artifact lists the covered rules with exact registry pins, the
reason, an evidence link, an expiry date (`YYYY-MM-DD`) or
`single-batch`, and the approver. The approver must differ from the PR author, and an APPROVED
review from the approver must exist — file content alone never
authorizes. Approval is required on every PR that adds or touches a
covered bundle, including PRs that reuse an unchanged override.
Overrides never substitute for the `unofficial-research`
compliance label and vice versa. Expired or malformed artifacts fail
validation; the mirror lane renders overrides advisory.

Submissions are **data-only**: every file a PR adds under
`results-data/bundles/` must be a supported `.json` result bundle, companion,
sidecar manifest, or the inventory. Scripts, workflows, symlinks, executables,
package manifests, and hidden control paths are rejected. If your submission
is blocked for a non-`.json` file, remove it and open a separate
`develop`-targeted PR for any non-data change.

If validation fails, the PR comment will explain what to fix. The workflow also
checks that `results-data/corpus-inventory.json` matches the submitted bundles.
If that check fails, rerun:

```bash
uv run -- python scripts/generate_corpus_inventory.py --write
```

### 6. Review and merge

A maintainer reviews the submission for quality and environment consistency.
Once approved and merged into `published-results`, the bundle enters the
complete results archive. It does not automatically enter `develop` or the
curated static Explorer snapshot.

## What Makes a Good Submission

- **Complete benchmark suite** - run the full query set, not a cherry-picked subset
- **Stable environment** - run on a dedicated machine or instance, not a shared laptop under load
- **Default configuration** - unless you're specifically benchmarking a tuned configuration, use defaults
- **Reproducible** - include enough environment metadata that someone else could replicate the run
- **Honest results** - don't hand-optimize queries or cherry-pick favorable runs

## Trust Labels

Results in the explorer carry trust labels. In
`benchbox/core/results/provenance.py`, `RESULT_SOURCES` defines the manifest
`result_source` values and `SOURCE_TO_TRUST_LABEL` maps them to trust labels.
`TRUST_LABELS` is the complete canonical trust-label vocabulary:

| Canonical label | Explorer display | Meaning |
|-----------------|------------------|---------|
| `maintainer-run` | **Maintainer Run** | Generated by BenchBox CI or project maintainers |
| `community-submission` | **Community Submission** | Contributed via PR from an arms-length community member |
| `vendor-supplied` | **Vendor Supplied** | Produced by the platform vendor itself; applied by maintainers, never self-declared on a submission |
| `verified` | **Verified** | Reserved for future third-party attestation |

The publisher also accepts `ci`, `local`, and `unofficial-research` as
publish-only workflow labels through `benchbox publish run --label`. These labels
are not derived from `result_source` and are not part of the canonical Explorer
trust-label vocabulary above.

Community submissions are labeled "Community Submission" in the explorer to
distinguish them from maintainer-curated results, and are currently excluded
from ranked tables. We will re-evaluate their inclusion over the coming months.
**Vendor Supplied** (`vendor-supplied`) results appear in ranked tables but
always carry the distinct vendor badge, because the platform vendor has a direct
interest in the outcome.
The `vendor-supplied` label is maintainer-applied and never self-declared on a
submission. You cannot self-apply the vendor label through a community PR. The
label is derived from a bundle living under `results-data/bundles/vendor/`, and the
submission CI (`validate-submission.yml`) rejects any pull request from a
non-maintainer that adds files under that path. Community submissions belong in
`results-data/bundles/` (not `vendor/`); maintainers place and review vendor
results.

## Funding Disclosure

Independent of who produced a result, you may disclose how the run was funded.
This is optional and orthogonal to the trust label (a community run can be on a
free trial; a vendor result can be employer-funded).

| Funding value | Meaning |
|---------------|---------|
| `employer` | Paid for by your employer |
| `personal` | Paid for out of pocket |
| `free-trial` | Free trial or promotional credits |
| `vendor-sponsored` | The platform vendor paid for or sponsored the run |
| `grant` | Research or institutional grant |
| `unspecified` | Not disclosed (the default) |

## Quality Expectations

Submissions that don't meet these criteria may be asked for revisions:

1. **Full query coverage** - all queries in the benchmark must be included
2. **No synthetic data** - results must come from actual benchmark execution
3. **Reasonable timings** - query durations should be plausible for the platform and scale factor
4. **Valid metadata** - benchmark ID, platform name, and scale factor must match known values
5. **Schema v2 format** - BenchBox currently writes top-level `version` `"2.2"`. Public submission validation accepts numeric `2.x` versions for forward-compatible schema-v2 bundles, but rejects missing or malformed values. To check your bundle: `uv run -- python -c "import json; print(json.load(open('bundle.json'))['version'])"`

## Running Validation Locally

You can validate your bundle before opening a PR. Run validation through
`uv run -- python` so it uses the same environment as BenchBox.

```bash
uv run -- python scripts/validate_submission.py path/to/result.json

uv run -- python scripts/validate_submission.py results-data/bundles/

uv run -- python scripts/generate_corpus_inventory.py --check
```

The first command validates one bundle, the second validates every bundle in a directory, and the third verifies the inventory is current before you open the PR.

If you use pre-commit locally, install the shared hooks once from the primary
clone (never from a linked worktree) so inventory drift is checked
automatically:

```bash
uv run -- pre-commit install
```

## Questions?

Start a [discussion](https://github.com/BenchBox-dev/BenchBox/discussions) if you need help with a submission or want to report a correction.
