#!/usr/bin/env bash
set -euo pipefail

BUILDER_REF="4c848ffdd6d55567ce7b4200a027c787586380b3"
BUILDER_URL="https://raw.githubusercontent.com/BenchBox-dev/BenchBox/${BUILDER_REF}/scripts/perf_smoke_baseline.py"
ARTIFACT_NAME="${ARTIFACT_NAME:-perf-regression-result}"
BASELINE_OUTPUT="${BASELINE_OUTPUT:-perf/baselines/duckdb_tpch_sf001.json}"
SOURCES_OUTPUT="${SOURCES_OUTPUT:-perf/baselines/duckdb_tpch_sf001.sources.json}"

usage() {
  cat <<'USAGE'
usage: build-perf-baseline.sh RUN_ID RUN_ID RUN_ID RUN_ID RUN_ID [RUN_ID ...]

Builds the stored baseline from at least five completed runs of the perf-regression
workflow, each downloaded from its own run, then replays every in-baseline source result through
the gate and prints the two floors to set in the workflow.

Requires: gh (authenticated for this repository), jq, python3, curl, and benchbox on PATH.
Environment: ARTIFACT_NAME, BASELINE_OUTPUT, SOURCES_OUTPUT.
USAGE
}

if [[ "$#" -lt 5 ]]; then
  usage >&2
  exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL "$BUILDER_URL" -o "$work/perf_smoke_baseline.py"

sources=()
for run_id in "$@"; do
  gh run download "$run_id" -n "$ARTIFACT_NAME" -D "$work/$run_id"
  results=()
  while IFS= read -r result; do
    results+=("$result")
  done < <(find "$work/$run_id" -name 'tpch_sf001_duckdb_sql_*.json' -print)
  if [[ "${#results[@]}" != "1" ]]; then
    echo "run $run_id: expected exactly one tpch_sf001_duckdb_sql_*.json result, found ${#results[@]}" >&2
    exit 1
  fi
  result="${results[0]}"
  sources+=("$run_id=$result")
done

mkdir -p "$(dirname "$BASELINE_OUTPUT")" "$(dirname "$SOURCES_OUTPUT")"
python3 "$work/perf_smoke_baseline.py" "${sources[@]}" \
  --output "$BASELINE_OUTPUT" \
  --sources-output "$SOURCES_OUTPUT"

query_floor="$(jq -r '.regression_floor.floor_ms' "$SOURCES_OUTPUT")"
aggregate_floor="$(jq -r '[.source_runs[] | select(.in_baseline) | .total_ms] | (max - min) * 1.25 | ceil' "$SOURCES_OUTPUT")"

while IFS= read -r run_id; do
  source_result=""
  for source in "${sources[@]}"; do
    if [[ "${source%%=*}" == "$run_id" ]]; then
      source_result="${source#*=}"
    fi
  done
  if ! replay_output="$(benchbox compare "$BASELINE_OUTPUT" "$source_result" \
    --fail-on-regression 10% \
    --min-regression-delta "${query_floor}ms" \
    --min-aggregate-regression-delta "${aggregate_floor}ms" \
    --non-interactive 2>&1)"; then
    echo "replay of run $run_id failed the gate:" >&2
    echo "$replay_output" >&2
    exit 1
  fi
done < <(jq -r '.source_runs[] | select(.in_baseline) | .workflow_run_id' "$SOURCES_OUTPUT")

echo "MIN_REGRESSION_DELTA: ${query_floor}ms"
echo "MIN_AGGREGATE_REGRESSION_DELTA: ${aggregate_floor}ms"
