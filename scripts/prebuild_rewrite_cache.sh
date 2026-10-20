#!/usr/bin/env bash
set -e

dir="${RUNNER_TEMP:?RUNNER_TEMP must name a scratch directory}"
find tests \( -name 'test_*.py' -o -name '*_test.py' \) -not -path '*/__pycache__/*' | sort \
  | awk -v n="$(nproc)" -v dir="$dir" '{ print > (dir "/rewrite-chunk." (NR % n)) }'
pids=()
for chunk in "$dir"/rewrite-chunk.*; do
  xargs uv run -- python -m pytest --collect-only -q -n 0 -p no:cacheprovider < "$chunk" > "$chunk.log" 2>&1 &
  pids+=("$!:$chunk")
done
for entry in "${pids[@]}"; do
  status=0
  wait "${entry%%:*}" || status=$?
  if [ "$status" -ne 0 ] && [ "$status" -ne 5 ]; then
    echo "::warning::Assertion-rewrite warm-up chunk ${entry#*:} exited $status"
    tail -n 20 "${entry#*:}.log"
  fi
done
