#!/bin/sh
set -eu

top=$(git rev-parse --show-toplevel)
common_dir=$(git rev-parse --git-common-dir)

case "$common_dir" in
  /*) common_abs=$(realpath "$common_dir") ;;
  *) common_abs=$(cd "$top" && realpath "$common_dir") ;;
esac

primary_clone=${BENCHBOX_AGENT_PRIMARY_CLONE:-$(dirname "$common_abs")}
top_abs=$(realpath "$top")
primary_abs=$(realpath "$primary_clone")

if [ "${BENCHBOX_ALLOW_MAIN_CLONE_WRITE:-${ALLOW_MAIN_CLONE_WRITE:-}}" = "1" ] || [ "${BENCHBOX_EPHEMERAL_CLONE:-}" = "1" ]; then
  exit 0
fi

if [ "$top_abs" -ef "$primary_abs" ]; then
  exit 0
fi

dirty=$(unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX; git -C "$primary_abs" status --porcelain --untracked-files=no 2>/dev/null || true)
if [ -z "$dirty" ]; then
  exit 0
fi

cat >&2 <<EOM
Refusing: the primary clone has uncommitted tracked changes. It is read-only
for agent work; edits there were written by mistake.

  primary clone: $primary_abs
  changed paths:
$(printf '%s\n' "$dirty" | sed 's/^/    /')

Nothing was discarded. Inspect with:
  git -C "$primary_abs" status --short
  git -C "$primary_abs" diff
If the edits are wanted, re-apply them inside your worktree at its absolute
path ($top_abs/...) and have the owner revert the primary clone. Never run
destructive git commands there on your own.
EOM
exit 1
