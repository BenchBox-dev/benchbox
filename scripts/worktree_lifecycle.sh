#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
operation=${1:-}

usage() {
  echo "Usage: $0 create|remove|release|finish" >&2
  exit 1
}

die() {
  echo "$1" >&2
  exit 1
}

canonical_path() {
  input=$1
  case "$input" in
    /*) candidate=$input ;;
    *) candidate=$(pwd -P)/$input ;;
  esac

  if [ -e "$candidate" ] || [ -L "$candidate" ]; then
    realpath "$candidate"
    return
  fi

  case "$candidate" in
    */*) parent=${candidate%/*}; leaf=${candidate##*/} ;;
    *) parent=.; leaf=$candidate ;;
  esac
  [ -n "$leaf" ] && [ "$leaf" != "." ] && [ "$leaf" != ".." ] || die "Invalid worktree path: $input"
  [ -n "$parent" ] || parent=/
  parent=$(CDPATH= cd -- "$parent" 2>/dev/null && pwd -P) || die "Invalid worktree path: $input"
  printf '%s/%s\n' "$parent" "$leaf"
}

git_common_dir() {
  common_dir=$(git rev-parse --git-common-dir) || die "Not inside a Git repository"
  case "$common_dir" in
    /*) realpath "$common_dir" ;;
    *) realpath "$(git rev-parse --show-toplevel)/$common_dir" ;;
  esac
}

worktree_has_branch() {
  target=$1
  target_ref=refs/heads/$2
  git -C "$primary_clone" worktree list --porcelain | awk -v target="$target" -v target_ref="$target_ref" '
    function finish_block() {
      if (current == target && current_ref == target_ref) found = 1
    }
    /^worktree / {
      finish_block()
      current = substr($0, 10)
      current_ref = ""
      next
    }
    /^branch / { current_ref = substr($0, 8) }
    END {
      finish_block()
      exit(found ? 0 : 1)
    }
  '
}

rollback_started_creation() {
  [ "${creation_started:-no}" = yes ] || return 0
  worktree_has_branch "$worktree_path" "$branch" || return 0

  if git -C "$primary_clone" worktree remove --force "$worktree_path" >/dev/null 2>&1; then
    git -C "$primary_clone" branch -D "$branch" >/dev/null 2>&1 ||
      echo "Rollback warning: removed worktree but could not delete branch: $branch" >&2
  else
    echo "Rollback warning: could not remove created worktree: $worktree_path" >&2
  fi
}

release_creation_lock() {
  [ "${creation_lock_owned:-no}" = yes ] || return 0

  if [ ! -e "$creation_lock_dir/pid" ] || [ "$(cat "$creation_lock_dir/pid")" = "$$" ]; then
    rm -f "$creation_lock_dir/pid"
    rmdir "$creation_lock_dir" 2>/dev/null ||
      echo "Rollback warning: could not release worktree-create lock: $creation_lock_dir" >&2
  fi
  creation_lock_owned=no
}

acquire_creation_lock() {
  if mkdir "$creation_lock_dir" 2>/dev/null; then
    creation_lock_owned=yes
    if ! printf '%s\n' "$$" > "$creation_lock_dir/pid"; then
      release_creation_lock
      die "Could not record worktree-create lock owner: $creation_lock_dir"
    fi
    return 0
  fi

  if [ ! -e "$creation_lock_dir" ] && [ ! -L "$creation_lock_dir" ]; then
    die "Could not create worktree-create lock directory: $creation_lock_dir"
  fi

  lock_pid=$(cat "$creation_lock_dir/pid" 2>/dev/null || true)
  if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
    die "Refusing: another worktree-create is in progress (pid $lock_pid)"
  fi
  die "Refusing: worktree-create lock exists without a live owner; confirm no worktree-create is active, then remove it separately: $creation_lock_dir"
}

on_exit() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    rollback_started_creation
  fi
  release_creation_lock
  exit "$status"
}

on_signal() {
  status=$1
  trap - EXIT HUP INT TERM
  rollback_started_creation
  release_creation_lock
  exit "$status"
}

create_worktree() {
  branch=${BRANCH:-}
  worktree_input=${WORKTREE_PATH:-}
  [ -n "$branch" ] || die "Usage: make worktree-create BRANCH=<branch> WORKTREE_PATH=<path>"
  [ -n "$worktree_input" ] || die "Usage: make worktree-create BRANCH=<branch> WORKTREE_PATH=<path>"

  case "$branch" in
    develop|main|release) die "Refusing to create a worktree for protected branch: $branch" ;;
    feat/?*|fix/?*|docs/?*|style/?*|refactor/?*|perf/?*|test/?*|build/?*|ci/?*|chore/?*|revert/?*) ;;
    *) die "BRANCH must start with a Conventional Commit type and a slug: feat/ fix/ docs/ style/ refactor/ perf/ test/ build/ ci/ chore/ revert/ (got: $branch)" ;;
  esac
  git check-ref-format --branch "$branch" >/dev/null || die "Invalid git branch name: $branch"

  common_dir=$(git_common_dir)
  primary_clone=$(dirname "$common_dir")
  worktree_path=$(canonical_path "$worktree_input")
  creation_lock_dir=$common_dir/worktree-create.lock
  trap on_exit EXIT
  trap 'on_signal 129' HUP
  trap 'on_signal 130' INT
  trap 'on_signal 143' TERM
  acquire_creation_lock

  if git show-ref --verify --quiet "refs/heads/$branch"; then
    die "Local branch already exists: $branch"
  fi
  remote_ref=$(git ls-remote --heads origin "$branch") || die "Could not verify remote branch: $branch"
  [ -z "$remote_ref" ] || die "Remote branch already exists: $branch"
  [ ! -e "$worktree_path" ] && [ ! -L "$worktree_path" ] || die "Worktree path already exists: $worktree_path"

  git fetch origin develop --quiet
  base_oid=$(git rev-parse origin/develop^{commit}) || die "Could not resolve fetched origin/develop"
  creation_started=yes
  git worktree add -b "$branch" "$worktree_path" origin/develop
  worktree_config_file=$(git -C "$worktree_path" rev-parse --absolute-git-dir)/config.worktree
  if [ -f "$worktree_config_file" ]; then
    git config --file "$worktree_config_file" --name-only --get-regexp '^benchbox\.' 2>/dev/null |
      sort -u |
      while IFS= read -r inherited_key; do
        git config --file "$worktree_config_file" --unset-all "$inherited_key"
      done
  fi
  "$script_dir/set_worktree_identity.sh" "$worktree_path"

  py_runner="python3"
  if command -v uv >/dev/null 2>&1; then
    py_runner="uv run --no-project -- python"
  fi

  set -- "$script_dir/worktree_lifecycle_metadata.py" init \
    --worktree-path "$worktree_path" \
    --branch "$branch" \
    --base-ref "origin/develop" \
    --base-oid "$base_oid"
  if [ -n "${CONTROLLER_KIND:-}" ]; then
    set -- "$@" --controller-kind "$CONTROLLER_KIND"
  fi
  if [ -n "${CONTROLLER_ID:-}" ]; then
    set -- "$@" --controller-id "$CONTROLLER_ID"
  fi

  if command -v uv >/dev/null 2>&1; then
    uv run --no-project -- python "$@"
  else
    python3 "$@"
  fi

  creation_started=no
  release_creation_lock
  trap - EXIT HUP INT TERM
  printf 'WORKTREE_PATH=%s\n' "$(cd "$worktree_path" && pwd -P)"
}

worktree_state() {
  target=$1
  git worktree list --porcelain | awk -v target="$target" '
    function finish_block() {
      if (current == target && locked) target_locked = 1
    }
    /^worktree / {
      finish_block()
      current = substr($0, 10)
      locked = 0
      if (current == target) target_found = 1
      next
    }
    current == target && /^locked( |$)/ { locked = 1 }
    END {
      finish_block()
      if (!target_found) print "missing"
      else if (target_locked) print "locked"
      else print "registered"
    }
  '
}

remove_worktree() {
  worktree_input=${WORKTREE_PATH:-}
  [ -n "$worktree_input" ] || die "Usage: make worktree-remove WORKTREE_PATH=<path>"

  primary_clone=$(dirname "$(git_common_dir)")
  target=$(canonical_path "$worktree_input")
  primary_path=$(realpath "$primary_clone")
  [ "$target" != "$primary_path" ] || die "Refusing to remove the primary clone: $target"

  state=$(worktree_state "$target")
  case "$state" in
    missing) die "Refusing: path is not an exact registered worktree: $target" ;;
    locked) die "Refusing: worktree is locked; confirm the mount is inactive, then unlock it separately: $target" ;;
  esac
  [ -d "$target" ] || die "Refusing: registered worktree directory is missing: $target"
  git -C "$target" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Refusing: not a usable worktree: $target"
  git -C "$target" symbolic-ref -q HEAD >/dev/null 2>&1 || die "Refusing: worktree is detached: $target"

  dirty=$(git -C "$target" status --porcelain --untracked-files=all)
  if [ -n "$dirty" ]; then
    echo "Refusing to remove dirty worktree $target:" >&2
    echo "$dirty" >&2
    exit 1
  fi

  git -C "$primary_clone" worktree remove "$target"
  echo "Removed worktree: $target"
}

release_worktree() {
  worktree_input=${WORKTREE_PATH:-}
  [ -n "$worktree_input" ] || die "Usage: make worktree-release WORKTREE_PATH=<path>"

  target=$(canonical_path "$worktree_input")
  [ -d "$target" ] || die "Refusing: worktree directory does not exist: $target"

  py_runner="python3"
  if command -v uv >/dev/null 2>&1; then
    py_runner="uv run --no-project -- python"
  fi

  $py_runner "$script_dir/worktree_lifecycle_metadata.py" release --worktree-path "$target"
}

finish_worktree() {
  worktree_input=${WORKTREE_PATH:-}
  [ -n "$worktree_input" ] || die "Usage: make worktree-finish WORKTREE_PATH=<path> EXPECTED_HEAD_OID=<oid>"

  oid_input=${EXPECTED_HEAD_OID:-}
  [ -n "$oid_input" ] || die "Usage: make worktree-finish WORKTREE_PATH=<path> EXPECTED_HEAD_OID=<oid>"

  primary_clone=$(dirname "$(git_common_dir)")
  target=$(canonical_path "$worktree_input")

  py_runner="python3"
  if command -v uv >/dev/null 2>&1; then
    py_runner="uv run --no-project -- python"
  fi

  format_args=""
  if [ -n "${FORMAT:-}" ]; then
    format_args="--format $FORMAT"
  fi

  apply_args=""
  if [ "${APPLY:-}" = "1" ]; then
    apply_args="--apply"
  fi

  $py_runner "$script_dir/worktree_finish.py" \
    --repo-root "$primary_clone" \
    --worktree-path "$target" \
    --expected-head-oid "$oid_input" \
    $format_args $apply_args
}

case "$operation" in
  create) create_worktree ;;
  remove) remove_worktree ;;
  release) release_worktree ;;
  finish) finish_worktree ;;
  *) usage ;;
esac
