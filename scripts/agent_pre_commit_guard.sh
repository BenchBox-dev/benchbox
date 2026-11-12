#!/bin/sh
set -eu

export BENCHBOX_PREFLIGHT_FROM_COMMIT_HOOK=1

configured=$(git config --bool --get benchbox.agent-write-preflight 2>/dev/null || true)
if [ "$configured" = "true" ] || [ "${BENCHBOX_AGENT_SESSION:-}" = "1" ]; then
  exec sh scripts/agent_write_preflight.sh
fi

allow_main=${BENCHBOX_ALLOW_MAIN_CLONE_WRITE:-${ALLOW_MAIN_CLONE_WRITE:-}}
if [ "$allow_main" = "1" ] || [ "${BENCHBOX_EPHEMERAL_CLONE:-}" = "1" ]; then
  exit 0
fi

exec sh scripts/agent_write_preflight.sh
