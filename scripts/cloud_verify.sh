#!/bin/sh
set -u

level=${1:-tests}
case "$level" in
  static | tests) ;;
  *)
    printf 'cloud-verify: LEVEL must be static or tests, got %s\n' "$level" >&2
    exit 2
    ;;
esac

top=$(git rev-parse --show-toplevel)
cd "$top"
base_ref=${BASE_REF:-origin/develop}
log_dir=${CLOUD_VERIFY_LOG_DIR:-/tmp/benchbox-cloud-verify}
summary="$log_dir/summary.tsv"
mkdir -p "$log_dir"

git fetch origin develop --quiet || {
  printf 'cloud-verify: cannot fetch origin develop\n' >&2
  exit 1
}

head=$(git rev-parse HEAD)
dirty=$(git status --porcelain | wc -l | tr -d ' ')
BENCHBOX_MEDIUM_CHANGED_PATHS_JSON=$(git diff --name-only "$base_ref...HEAD" |
  uv run -- python -c 'import json, sys; print(json.dumps(sys.stdin.read().split()))')
export BENCHBOX_MEDIUM_CHANGED_PATHS_JSON

printf 'step\tstatus\tseconds\tlog\n' >"$summary"
failed=0

run_step() {
  step=$1
  shift
  log="$log_dir/$step.log"
  printf '==> %s: %s\n' "$step" "$*"
  started=$(date +%s)
  "$@" >"$log" 2>&1
  status=$?
  seconds=$(($(date +%s) - started))
  printf '%s\t%s\t%s\t%s\n' "$step" "$status" "$seconds" "$log" >>"$summary"
  printf '    exit %s after %ss (%s)\n' "$status" "$seconds" "$log"
  [ "$status" -eq 0 ] || failed=1
}

run_step comment-policy make comment-policy-check BASE_REF="$base_ref"
run_step duplicate-delta make duplicate-check-delta BASE_REF="$base_ref"
run_step typecheck make typecheck

if [ "$level" = tests ]; then
  run_step pr-preflight make pr-preflight
  run_step test-fast make test-fast
  run_step test-medium-selected make test-medium-selected
fi

end_head=$(git rev-parse HEAD)
end_dirty=$(git status --porcelain | wc -l | tr -d ' ')

printf '\ncloud-verify level=%s head=%s dirty_paths=%s\n' "$level" "$head" "$dirty"
cat "$summary"
if [ "$end_head" != "$head" ] || [ "$end_dirty" != "$dirty" ]; then
  printf 'cloud-verify: the tree changed during verification (head %s, dirty paths %s); rerun on a settled tree\n' \
    "$end_head" "$end_dirty" >&2
  failed=1
fi
exit "$failed"
