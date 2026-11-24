# Contributing to `published-results`

This branch accepts **result-bundle contributions only**. Source-code
contributions to BenchBox itself go to the
[`develop`](https://github.com/BenchBox-dev/benchbox/tree/develop) branch
— see
[`CONTRIBUTING.md` on develop](https://github.com/BenchBox-dev/benchbox/blob/develop/CONTRIBUTING.md)
for that flow.

## Submitting a bundle

The full contributor guide lives at
[Contributing Benchmark Results](https://benchbox.dev/docs/contributing-results.html).
The 30-second version:

1. Run your benchmark with BenchBox:
   ```bash
   benchbox run --platform <platform> --benchmark <benchmark> --scale <sf>
   ```
2. Package the result for submission:
   ```bash
   benchbox submit --output ./submission
   ```
3. Copy the generated `<run_id>.json` and `<run_id>.manifest.json` files
   into `results-data/bundles/` on a fork of this branch.
4. Regenerate the inventory:
   ```bash
   uv run --no-project --python 3.11 -- python scripts/generate_corpus_inventory.py --write
   ```
5. Open a pull request **against `published-results`** (this branch).
6. The submission validator workflow will check schema, hashes, timing
   sanity, and inventory consistency, and post the results as a PR
   comment.

## What goes where

- **Bundles only.** PRs that add files outside the allowlist documented
  in [`README.md`](README.md) will be redirected to `develop`.
- **One submission, one PR.** Multiple unrelated benchmark runs should
  go in separate PRs so each can be reviewed independently.
- **Don't edit existing bundles.** Bundle JSONs are hash-pinned by their
  manifest sidecars; modifying them invalidates the contract. If a run
  needs to be replaced, delete-and-add in a single PR with the
  rationale documented in the description.

## Trust labels

Bundle trust labels are derived automatically from the presence of a
per-bundle `<stem>.manifest.json` sidecar (see
`scripts/generate_corpus_inventory.py`):

- Sidecar present → `community-submission`
- Sidecar absent → `maintainer-run`

Do not hand-set trust labels in bundles; the inventory generator
resolves them.

## Maintainer review

Bundles are reviewed for:

- Schema compliance (`scripts/validate_submission.py`).
- Cohort depth (`results-data/validate_corpus.py` enforces ≥3 platforms
  per `(benchmark, scale_factor)` cohort).
- Plausibility of timings, environment metadata, and cost data.
- Consistency with existing corpus naming conventions.

Stale PRs are closed after 14 days without contributor response, with a
brief thank-you note. Closure is reversible — reopen with the requested
fixes whenever you have cycles.

## Reporting bundle-validator bugs

The validator scripts on this branch are vendored from `develop`. File
validator-side bug reports against
[`BenchBox-dev/benchbox`](https://github.com/BenchBox-dev/benchbox/issues)
on the develop branch. Fixes are mirrored back to this branch via the
develop ↔ published-results sync mechanism.

## License

By contributing, you agree that your submission is licensed under the
[MIT License](LICENSE) — see [`COPYRIGHT.md`](COPYRIGHT.md) for project
copyright notice.
