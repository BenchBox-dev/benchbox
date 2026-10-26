# Fast-lane guards

The "fast lane" is every test collected under `pytest -m fast` (excluding
`slow`/`stress`/`resource_heavy`/`live_integration`) -- the required,
sub-few-minutes suite every develop PR runs in `code-test`
(`.github/workflows/ci.yml`).

## No test-count ceiling

There is no limit on how many tests the fast lane may collect. The
`code-test` job's `timeout-minutes` cap is a hard cancel, not a detection
mechanism; it does not notice a slowdown that stays under the cap.

## Fast-tier duration report

The `core` job runs a report-only `verify-fast-duration (report only)` step
on pull requests. It downloads the PR's `fast-junit` artifact and compares it
with the committed baseline in `tests/fixtures/fast_tier_baseline`
(`python -m scripts.build_fast_duration_baseline verify`). Each run's test
times are scaled by the runner-speed factor from its calibration record, so a
slow hosted runner is not mistaken for a slowdown. A run without a calibration
record is rejected, and a missing baseline gives a neutral verdict.

The job summary shows the normalized summed time over tests present in both
runs, the delta, the summed-time ratio, the median per-test ratio, coverage of
the baseline ids, the runner speed factor and the pytest collection time. The
verdict is `would-block` when the ratio exceeds 1.15 and the delta exceeds 60
seconds (provisional thresholds). Tests missing from the baseline are allowed
0.5 seconds each; time beyond that counts toward the delta. Job wall time is
not a metric. The step has `continue-on-error` and never changes the `core`
result.

## Marker and path guards

A guard script stops the wrong kind of test from entering the lane. It reads
the fast-lane policy file:

- `forbidden_marker_expressions`: for each expression, the script collects
  `pytest -m "fast and <expression>"`. A non-empty selection is a
  `FAST_LANE_VIOLATION` (currently `resource_heavy`, `stress` and
  `live_integration`).
- `forbidden_path_substrings`: any fast-lane test whose node ID contains one
  of these substrings is a `FAST_LANE_VIOLATION`. The list is empty today.
- `enabled`: set to `false` to switch the guards off.

The guards run as `guard-fast-lane-markers` in the `code-lint` job of
`ci.yml`, in `lint.yml` for release branches, in `make ci-lint`, and in the
`timing-policy-fast-lane` pre-push hook, all with `--strict`. If pytest
cannot collect, the script reports `FAST_LANE_ENVIRONMENT_ERROR` instead of a
violation, because nothing is known to be wrong with the lane; run it from the
project environment (for example through `make ci-lint`). To fix a violation,
remove the forbidden marker from the test or move the test out of the fast
tier.

## Medium tier

The `medium-test` job in `.github/workflows/ci.yml` splits the medium tier
across four Linux runners. Its `timeout-minutes` (25 minutes) is a hard cancel
that backstops a hung tier, not a policy check. `make test-medium` runs the
full medium suite locally.
