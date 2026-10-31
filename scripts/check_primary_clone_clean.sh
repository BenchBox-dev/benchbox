#!/bin/sh
set -eu

mode=${1:-report}

top=$(git rev-parse --show-toplevel)
common_dir=$(git rev-parse --git-common-dir)
git_dir_abs=$(git rev-parse --absolute-git-dir)

case "$common_dir" in
  /*) common_abs=$(realpath "$common_dir") ;;
  *) common_abs=$(cd "$top" && realpath "$common_dir") ;;
esac

primary_clone=${BENCHBOX_AGENT_PRIMARY_CLONE:-$(dirname "$common_abs")}
top_abs=$(realpath "$top")
primary_abs=$(realpath "$primary_clone")
snapshot="$git_dir_abs/primary-clone-snapshot"

if [ "${BENCHBOX_ALLOW_MAIN_CLONE_WRITE:-${ALLOW_MAIN_CLONE_WRITE:-}}" = "1" ] || [ "${BENCHBOX_EPHEMERAL_CLONE:-}" = "1" ]; then
  exit 0
fi

if [ "$top_abs" -ef "$primary_abs" ]; then
  exit 0
fi

current_state() {
  (
    unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX
    git -C "$primary_abs" -c core.quotepath=off diff --name-only HEAD 2>/dev/null || true
  ) | while IFS= read -r path; do
    [ -n "$path" ] || continue
    if [ -f "$primary_abs/$path" ]; then
      hash=$(git hash-object -- "$primary_abs/$path" 2>/dev/null || echo unreadable)
    else
      hash=deleted
    fi
    printf '%s\t%s\n' "$hash" "$path"
  done
}

case "$mode" in
  snapshot)
    if [ -f "$snapshot" ] && [ "${BENCHBOX_PRIMARY_SNAPSHOT_REFRESH:-}" != "1" ]; then
      exit 0
    fi
    tmp="$snapshot.$$"
    current_state > "$tmp"
    mv "$tmp" "$snapshot"
    ;;
  report)
    if [ ! -f "$snapshot" ]; then
      printf 'No primary-clone snapshot at %s; run make agent-write-preflight first. Skipping primary-clone change report.\n' "$snapshot" >&2
      exit 0
    fi
    changed=$(current_state | grep -vxFf "$snapshot" | cut -f2 || true)
    if [ -z "$changed" ]; then
      exit 0
    fi
    cat >&2 <<EOM
WARNING: tracked files in the primary clone changed since this worktree's
write preflight. The primary clone is read-only for agent work; these edits may
be a stray write from a relative path, or the owner's concurrent work.

  primary clone: $primary_abs
  changed paths:
$(printf '%s\n' "$changed" | sed 's/^/    /')

Do NOT revert, restore, or checkout them. Nothing was changed. If a write of
yours landed there, re-apply it inside your worktree at its absolute path
($top_abs/...) and tell the owner. Inspect with:
  git -C "$primary_abs" status --short
EOM
    ;;
  *)
    echo "usage: check_primary_clone_clean.sh [snapshot|report]" >&2
    exit 2
    ;;
esac
