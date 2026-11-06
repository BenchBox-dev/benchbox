# UAT gate-tuning rubric

The owner approved a standing rubric on 2026-11-05 so that envelope tuning no
longer needs an owner interrupt. This page describes what is automated, what is
still the owner's decision, and how to run each part. The release campaign
procedure is in [UAT release campaign](uat-release-campaign.md).

## What the rubric allows

| Situation | Action | Who decides |
| --- | --- | --- |
| A cell fails only with an infrastructure signature | One automatic retry in the same shard | Automatic |
| A cell's infrastructure-failure rate exceeds 20% over its last 10 runs and another scale of the same platform x benchmark stays gated | Add a runtime envelope entry for that cell | Any maintainer, from the generated proposal |
| That cell is the last gated scale of its platform x benchmark | Never pruned; file a tracker item | Maintainer |
| A cell's only failures in history were infrastructure failures | Proposed for the drop list | Owner, through the choice dialog |
| Product failure: wrong results, validation failure, crash | Never retried, never pruned | Not applicable |
| Anything else (platform-wide exclusion, any drop-list removal) | Owner decision | Owner |

The rubric never changes a timeout, memory limit, validator setting or floor.
`python -m tests.uat.config_guard --base <ref>` compares those values in
`tests/uat/configs/` and `docker/` against a git ref and fails on any
difference; run it against `origin/develop` before opening a PR that touches
UAT.

## Failure signatures

`tests/uat/failure_signature.py` classifies a failed cell.

| Kind | Signature | Evidence |
| --- | --- | --- |
| Infrastructure | `timeout` | The cell hit `per_cell_timeout_s` |
| Infrastructure | `memory` | The cell was killed (exit 137 or -9): the per-cell memory cap or the OOM killer |
| Infrastructure | `download_error` | `DownloadError` or `download failed after` in the failure output, with no failed queries |
| Infrastructure | `singlestore_1712` | SingleStore error 1712 in the failure output, or in every failed query's error |
| Product | `query_failure` | A failed query whose error is not 1712 (a mix of 1712 and other errors counts here) |
| Product | `validation_failure` | Result or schema validation failed, or the validator rollup reports an error |
| Product | `crash` | Any other non-zero exit |

A cell is infrastructure-only when its single failure matches an
infrastructure signature and carries no product evidence. Everything else is a
product failure.

## Automatic retry

A release-gate config enables the retry with
`execute.infrastructure_retry_job_budget_s`, the execute phase's share of the
job time in seconds. The three release-gate configs set it to 19800, which
leaves 15 minutes of the 345-minute step limit for validate, package and
report. A config without the key never retries.

- A failed cell whose signature is infrastructure runs once more with the same
  arguments, the same timeout and the same memory limit.
- No retry starts when the remaining budget is below `per_cell_timeout_s`; the
  skipped retry is recorded.
- The cell passes only if the retry passes. A second infrastructure failure
  fails the cell, and a product failure is never retried.
- The cell row in `cells.jsonl` records `attempt`, `retry_signature`,
  `retry_outcome` (`retry_passed`, `retry_failed` or
  `skipped_insufficient_time`) and the first attempt's status, log and elapsed
  time. The first attempt's log stays beside the retry's.
- `uat_gate_summary.json` carries `infrastructure_retry` with `triggered`,
  `retried`, `retry_passed`, `retry_failed`, `skipped_insufficient_time` and
  `retried_by_signature`. Zeros are a valid result. The `singlestore_1712`
  count is the per-run retry count that `singlestore-tpchavoc-10v8-memory`
  needs: error 1712 stays retryable only until that item is done, because it may
  be a plan defect.

## Pinned images

Every image in `docker/**/docker-compose*.y*ml` carries an `@sha256` digest. A
service that builds a local image pins the base image in its Dockerfile
instead. `tests/uat/image_pins.py` rejects floating references, and the
repository test `test_every_uat_compose_image_in_the_repository_is_pinned`
runs it:

```bash
uv run -- python -m tests.uat.image_pins
```

To bump an image, resolve the tag to its index digest and change the reference
in one commit:

```bash
skopeo inspect --raw docker://<image>:<tag> | shasum -a 256
```

Keep the tag next to the digest so the version stays readable. Revert a single
image by reverting its line.

## History reports

`scripts/uat_rubric.py` reads trunk UAT history and writes two reports.
History is a directory of run directories, each holding `cells.jsonl` and
`uat_gate_summary.json` (and `validator_rollup.tsv` when present), for example
the downloaded `release-uat-*` shard evidence artifacts. Dry runs are ignored.

```bash
uv run -- python scripts/uat_rubric.py --history-dir <dir> --output-dir <dir>
```

`uat_rubric_proposals.json`:

- `proposals`: cells above the threshold with another scale still gated. Each
  has an `envelope` with `reason_line` and `evidence_line`, which paste into
  `_RELEASE_GATE_RUNTIME_ENVELOPES` and `_RELEASE_GATE_RUNTIME_ENVELOPE_EVIDENCE`
  in `tests/uat/compatibility.py`, and a `tracker_item` draft.
- `tracker_item_suggestions`: flaky cells that are the last gated scale. Fix the
  infrastructure instead of pruning.
- `held_for_product_signal`: flaky cells that ever failed with a product
  signature; they are not proposed.
- `red_shards`: how many red shards there were and how many carried a product
  signature, for the dashboard.

`uat_signal_record.json`:

- `cells`: per cell, the runs, the passes and the product and infrastructure
  failures by signature. `defect_classes_caught` lists the product signatures
  the cell has ever caught.
- `drop_list`: cells with at least one infrastructure failure and no product
  failure over at least 10 runs, status `proposed_for_owner_review`. Present
  the first real drop list to the owner through the choice dialog before
  dropping any cell. Last gated scales appear under `kept_last_gated_scale`.

Window, threshold and minimum history are the `--window`, `--threshold` and
`--min-runs` options; the defaults are the rubric's 10 runs and 20%. A cell with
fewer runs than the window is not judged. An absorbed retry counts as an
infrastructure failure of that run. A cell is gated when the release-gate stage
configs (`--stage-config`) still enumerate it.

## Filing rubric items

Tracker items filed from these reports use the title prefix `UAT envelope:` and
list `uat-rubric` among their links, so the dashboard can count them. The
`tracker_item` drafts in the proposals report already follow that shape.
