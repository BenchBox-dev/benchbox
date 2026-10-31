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

stop_if_started() {
  if [ "$prior" = "stopped" ]; then
    echo "==> linux machine '$machine' was stopped before this run; stopping it again" >&2
    container machine stop "$machine" >&2 || echo "WARNING: could not stop machine '$machine'" >&2
  fi
}

trap 'exit 130' INT
trap 'exit 129' HUP
trap 'exit 143' TERM
trap stop_if_started EXIT

container machine run -n "$machine" "$@"
status=$?
exit "$status"
