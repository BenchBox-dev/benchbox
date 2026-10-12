#!/usr/bin/env bash

set -euo pipefail

TIMEOUT_SECS=150

MAX=${1:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 8)}
LEVELS=(0 1 2 4 $(seq 8 4 "$MAX"))
LEVELS=($(printf '%s\n' "${LEVELS[@]}" | awk -v max="$MAX" '$1 <= max' | sort -nu))

OUTDIR=$(mktemp -d "${TMPDIR:-/tmp}/concurrency-sweep.XXXXXX")
MARKER='fast and not (slow or stress or resource_heavy or live_integration)'

echo "=== Concurrency Sweep ==="
echo "Levels: ${LEVELS[*]}"
echo "Timeout: ${TIMEOUT_SECS}s per iteration"
echo "Output: $OUTDIR"
echo ""

for n in "${LEVELS[@]}"; do
    label=$( [[ $n -eq 0 ]] && echo "serial" || echo "${n}w" )
    outfile="$OUTDIR/durations-${label}.txt"

    echo "--- Running with -n $n ($label) [timeout ${TIMEOUT_SECS}s] ---"
    start=$(date +%s)

    rc=0
    set +e
    (
        exec uv run -- python -m pytest \
            -m "$MARKER" \
            -n "$n" \
            --tb=no -q --durations=50 2>&1
    ) > "$outfile" &
    child=$!

    ( sleep "$TIMEOUT_SECS"; kill "$child" 2>/dev/null ) &
    watcher=$!

    wait "$child" 2>/dev/null
    rc=$?
    kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
    set -e

    elapsed=$(( $(date +%s) - start ))

    if [[ $rc -ne 143 && $elapsed -lt $TIMEOUT_SECS ]]; then
        tail -5 "$outfile"
    else
        echo "TIMED OUT" >> "$outfile"
        echo "  *** TIMED OUT after ${TIMEOUT_SECS}s ***"
    fi
    echo "  Wall time: ${elapsed}s (exit $rc)"
    echo ""
done

echo ""
echo "=============================================="
echo "  CONCURRENCY IMPACT SUMMARY"
echo "=============================================="
printf "%-8s  %8s  %10s  %s\n" "LEVEL" "WALL(s)" "SLOW_T(s)" "SLOWEST TEST"
echo "----------------------------------------------------------------------"

for n in "${LEVELS[@]}"; do
    label=$( [[ $n -eq 0 ]] && echo "serial" || echo "${n}w" )
    outfile="$OUTDIR/durations-${label}.txt"

    if grep -q 'TIMED OUT' "$outfile"; then
        printf "%-8s  %8s  %10s  %s\n" "$label" "TIMEOUT" "-" "(killed after ${TIMEOUT_SECS}s)"
        continue
    fi

    wall=$(grep -oE 'in [0-9]+\.[0-9]+s' "$outfile" | tail -1 | grep -oE '[0-9]+\.[0-9]+' || echo "?")

    slowest_line=$(grep -E '^[0-9]+\.[0-9]+s (call|setup)' "$outfile" | head -1 || echo "")
    if [[ -n "$slowest_line" ]]; then
        slow_time=$(echo "$slowest_line" | grep -oE '^[0-9]+\.[0-9]+')
        slow_test=$(echo "$slowest_line" | sed 's/^[0-9.]*s \(call\|setup\) *//')
    else
        slow_time="?"
        slow_test="(none)"
    fi

    printf "%-8s  %8s  %10s  %s\n" "$label" "$wall" "$slow_time" "$slow_test"
done

echo ""
echo "Raw output saved to: $OUTDIR"
