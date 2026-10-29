#!/bin/sh
set -eu

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

allow_main=${BENCHBOX_ALLOW_MAIN_CLONE_WRITE:-${ALLOW_MAIN_CLONE_WRITE:-}}

registered_worktrees=$(git worktree list 2>/dev/null | wc -l | tr -d ' ')

ephemeral=no
if [ "${BENCHBOX_EPHEMERAL_CLONE:-}" = "1" ] && [ "$registered_worktrees" -eq 1 ]; then
  ephemeral=yes
fi

if [ "$top_abs" -ef "$primary_abs" ] && [ "$allow_main" != "1" ] && [ "$ephemeral" != "yes" ]; then
  cat >&2 <<EOF
Refusing BenchBox write preflight in the primary clone:
  $top_abs

Create a disposable worktree before write work:
  make worktree-create BRANCH=fix/descriptive-slug WORKTREE_PATH=../BenchBox.wt-fix-descriptive-slug
  cd <WORKTREE_PATH>

If this is instead a disposable, ephemeral clone with no canonical clone to
protect -- a remote agent session or a CI runner -- declare that:
  BENCHBOX_EPHEMERAL_CLONE=1 make agent-write-preflight
Normal local agent sessions must use a linked worktree.

Read-only review/research may stay in the primary clone. Emergency main-clone
hotfix work requires explicit user authorization and:
  BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1 make agent-write-preflight
EOF
  exit 1
fi

if [ "$allow_main" != "1" ] && [ "$ephemeral" != "yes" ] && [ "$git_dir_abs" -ef "$common_abs" ]; then
  cat >&2 <<EOF
Refusing BenchBox write preflight: this is a plain clone, not a registered
linked worktree.

Create a disposable worktree before write work:
  make worktree-create BRANCH=fix/descriptive-slug WORKTREE_PATH=../BenchBox.wt-fix-descriptive-slug
  cd <WORKTREE_PATH>
EOF
  exit 1
fi

sh "$(dirname "$0")/check_primary_clone_clean.sh"

if [ "${BENCHBOX_ALLOW_AGENT_GIT_IDENTITY:-}" != "1" ]; then
  author_ident=$(git -C "$top_abs" var GIT_AUTHOR_IDENT 2>/dev/null || true)
  author_email=$(printf '%s' "$author_ident" | sed -n 's/^.*<\([^>]*\)>.*$/\1/p' | tr 'A-Z' 'a-z')
  author_name=$(printf '%s' "$author_ident" | sed -n 's/^\(.*\) <[^>]*>.*$/\1/p' | tr 'A-Z' 'a-z')

  agent_identity=no
  case "$author_email" in
    noreply@anthropic.com | noreply@openai.com) agent_identity=yes ;;
  esac
  case "$author_name" in
    chatgpt | claude | codex | gemini | openai) agent_identity=yes ;;
  esac

  if [ "$agent_identity" = yes ]; then
    origins=$(git -C "$top_abs" config --show-origin --get-regexp '^user\.(name|email)$' 2>/dev/null || true)
    cat >&2 <<EOF
Refusing BenchBox write preflight: Git author identity resolves to a known
agent/service identity.

  author:  $author_ident
  origins:
$(printf '%s\n' "${origins:-  <none>}" | sed 's/^/    /')

A repository-local value overrides the global identity but is not automatically
intentional, and every linked worktree inherits it. Set the human identity, or
-- if this task explicitly authorized the agent identity -- declare it:
  BENCHBOX_ALLOW_AGENT_GIT_IDENTITY=1 make agent-write-preflight

A commit-signing service may still hold the committer slot behind a human
author; only authorship is refused here.
EOF
    exit 1
  fi
fi

hook_config="$top_abs/.pre-commit-config.yaml"
hook_types=pre-commit
if [ -f "$hook_config" ]; then
  configured_hook_types=$(awk '
    function emit_inline(    cleaned) {
      cleaned = value
      sub(/^.*\[/, "", cleaned)
      sub(/\].*$/, "", cleaned)
      gsub(/,/, " ", cleaned)
      gsub(/"/, " ", cleaned)
      gsub(/\047/, " ", cleaned)
      print cleaned
      collecting = 0
    }
    /^default_install_hook_types:[[:space:]]*\[/ {
      value = $0
      sub(/^[^:]*:[[:space:]]*/, "", value)
      emit_inline()
      next
    }
    /^default_install_hook_types:[[:space:]]*$/ {
      collecting = 1
      next
    }
    collecting {
      if ($0 !~ /^[[:space:]]+/) {
        collecting = 0
        next
      }
      if ($0 ~ /^[[:space:]]*-[[:space:]]*/) {
        value = $0
        sub(/^[[:space:]]*-[[:space:]]*/, "", value)
        sub(/[[:space:]]+\043.*$/, "", value)
        gsub(/[[:space:]]+$/, "", value)
        print value
        next
      }
      if (index($0, "[")) {
        value = $0
        emit_inline()
      }
    }
  ' "$hook_config")
  [ -n "$configured_hook_types" ] && hook_types=$configured_hook_types
fi

for hook_name in $hook_types; do
  case "$hook_name" in
    *[!A-Za-z0-9_-]*)
      echo "Refusing BenchBox write preflight: invalid configured hook type: $hook_name" >&2
      exit 1
      ;;
  esac

  hook_path="$common_abs/hooks/$hook_name"
  if [ ! -f "$hook_path" ]; then
    cat >&2 <<EOF
Refusing BenchBox write preflight: the shared $hook_name hook is missing.

  hook: $hook_path
  owner: $primary_abs

Install it from the primary clone, then rerun preflight:
  (cd "$primary_abs" && uv run -- pre-commit install)
Do not run pre-commit install from a linked worktree.
EOF
    exit 1
  fi

  if [ ! -x "$hook_path" ]; then
    cat >&2 <<EOF
Refusing BenchBox write preflight: the shared $hook_name hook is not executable.

  hook: $hook_path
  owner: $primary_abs

Install it from the primary clone, then rerun preflight:
  (cd "$primary_abs" && uv run -- pre-commit install)
Do not run pre-commit install from a linked worktree.
EOF
    exit 1
  fi

  hook_interpreter=$(sed -n 's/^INSTALL_PYTHON=//p' "$hook_path" | head -n 1)
  case "$hook_interpreter" in
    \'*\') hook_interpreter=${hook_interpreter#\'}; hook_interpreter=${hook_interpreter%\'} ;;
    \"*\") hook_interpreter=${hook_interpreter#\"}; hook_interpreter=${hook_interpreter%\"} ;;
  esac
  if [ -n "$hook_interpreter" ] && [ ! -x "$hook_interpreter" ]; then
    cat >&2 <<EOF
Refusing BenchBox write preflight: the shared $hook_name hook is not usable.

  hook:             $hook_path
  INSTALL_PYTHON:   $hook_interpreter
  primary clone:    $primary_abs

Install it from the primary clone, then rerun preflight:
  (cd "$primary_abs" && uv run -- pre-commit install)
Do not run pre-commit install from a linked worktree.
EOF
    exit 1
  fi
done

session_id=${PI_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-${CODEX_SESSION_ID:-${AGENT_SESSION_ID:-}}}}}
if [ -n "$session_id" ] && [ "$ephemeral" != "yes" ] && ! [ "$git_dir_abs" -ef "$common_abs" ]; then
  claim_args=
  [ "${BENCHBOX_WORKTREE_TAKEOVER:-}" = "1" ] && claim_args=--takeover
  if command -v python3 >/dev/null 2>&1; then
    set -- python3
  elif command -v uv >/dev/null 2>&1; then
    set -- uv run --no-project -- python
  else
    echo "Refusing BenchBox write preflight: neither uv nor python3 is on PATH for the ownership check." >&2
    exit 1
  fi
  "$@" "$(dirname "$0")/worktree_lifecycle_metadata.py" claim --worktree-path "$top_abs" $claim_args || {
    echo "Refusing BenchBox write preflight: worktree ownership check failed." >&2
    exit 1
  }
fi

if [ "$ephemeral" = "yes" ]; then
  printf 'BenchBox write preflight OK (ephemeral clone): %s\n' "$top_abs"
else
  printf 'BenchBox write preflight OK: %s\n' "$top_abs"
fi
