#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: linux_machine_run.sh <machine> <container machine run args...>" >&2
  exit 2
fi
machine=$1
shift

if [ -z "${BENCHBOX_TEST_SESSION_OWNER:-}" ]; then
  echo "linux_machine_run.sh: refusing to boot the Linux machine outside the host lock; run it through 'python -m benchbox.utils.host_lock run --guard --'." >&2
  exit 2
fi

machine_status() {
  container machine list --format json 2>/dev/null |
    tr '}' '\n' |
    grep -F "\"id\":\"$1\"" |
    sed -n 's/.*"status":"\([A-Za-z]*\)".*/\1/p' |
    sed -n '1p'
}

prior=$(machine_status "$machine" || true)

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

foreign_pid() {
  if [ -n "${BENCHBOX_PYTHON:-}" ]; then
    PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}" "$BENCHBOX_PYTHON" -m benchbox.utils.linux_machine busy "$machine" --own-pid "$$"
  elif command -v uv >/dev/null 2>&1; then
    uv run --project "$repo" --no-sync -q -- python -m benchbox.utils.linux_machine busy "$machine" --own-pid "$$"
  else
    PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}" python3 -m benchbox.utils.linux_machine busy "$machine" --own-pid "$$"
  fi
}

stop_machine() {
  limit=${BENCHBOX_LINUX_STOP_TIMEOUT_SECONDS:-60}
  container machine stop "$machine" >&2 &
  stop_pid=$!
  waited=0
  while kill -0 "$stop_pid" 2>/dev/null; do
    if [ "$waited" -ge "$limit" ]; then
      kill "$stop_pid" 2>/dev/null
      echo "WARNING: stopping machine '$machine' timed out after ${limit}s" >&2
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$stop_pid" 2>/dev/null || echo "WARNING: could not stop machine '$machine'" >&2
}

finish_machine() {
  trap : INT HUP TERM
  keep=${BENCHBOX_KEEP_LINUX_MACHINE:-}
  if [ "$keep" = "1" ] && [ "$prior" != "stopped" ]; then
    return 0
  fi
  other=$(foreign_pid 2>/dev/null)
  if [ "$?" -eq 0 ] && [ -n "$other" ]; then
    echo "==> linux machine '$machine' left running: in use by pid $other" >&2
    return 0
  fi
  if [ "$keep" = "1" ]; then
    echo "==> linux machine '$machine' was stopped before this run; stopping it again" >&2
  else
    echo "==> stopping linux machine '$machine'" >&2
  fi
  stop_machine
}

trap 'exit 130' INT
trap 'exit 129' HUP
trap 'exit 143' TERM
trap finish_machine EXIT

container machine run -n "$machine" "$@"
status=$?
exit "$status"
