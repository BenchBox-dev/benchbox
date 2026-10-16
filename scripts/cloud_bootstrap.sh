#!/bin/sh
set -eu

top=$(git rev-parse --show-toplevel)
cd "$top"
git_dir=$(git rev-parse --absolute-git-dir)
marker="$git_dir/benchbox-cloud-bootstrap"

refuse() {
  printf 'cloud-bootstrap: refusing: %s\n' "$1" >&2
  exit 1
}

[ "${BENCHBOX_EPHEMERAL_CLONE:-}" = "1" ] ||
  refuse "BENCHBOX_EPHEMERAL_CLONE=1 is not set in the cloud environment; set it there so every later command and Git hook sees it"

registered_worktrees=$(git worktree list | wc -l | tr -d ' ')
[ "$registered_worktrees" -eq 1 ] ||
  refuse "this clone has $registered_worktrees registered worktrees; cloud bootstrap runs only in a disposable single-checkout clone"

name=${BENCHBOX_GIT_NAME:-}
email=${BENCHBOX_GIT_EMAIL:-}
[ -n "$name" ] && [ -n "$email" ] ||
  refuse "BENCHBOX_GIT_NAME and BENCHBOX_GIT_EMAIL must name the human commit author in the cloud environment"

command -v uv >/dev/null 2>&1 || refuse "uv is not on PATH; install it in the environment setup script"

started=$(date +%s)

git config user.name "$name"
git config user.email "$email"

sync_started=$(date +%s)
uv sync --locked --group dev
sync_seconds=$(($(date +%s) - sync_started))

uv run -- pre-commit install
make agent-write-preflight

{
  printf 'head=%s\n' "$(git rev-parse HEAD)"
  printf 'author=%s\n' "$(git var GIT_AUTHOR_IDENT | sed 's/ [0-9]* [-+][0-9]*$//')"
  printf 'uv_sync_seconds=%s\n' "$sync_seconds"
  printf 'total_seconds=%s\n' "$(($(date +%s) - started))"
} >"$marker"

printf 'cloud-bootstrap: OK\n'
cat "$marker"
