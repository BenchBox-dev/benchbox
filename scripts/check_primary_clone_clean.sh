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

primary_clone=$(dirname "$common_abs")
override_abs=$(realpath "${BENCHBOX_AGENT_PRIMARY_CLONE:-}" 2>/dev/null || true)
if [ -n "${BENCHBOX_AGENT_PRIMARY_CLONE:-}" ] && [ -d "$BENCHBOX_AGENT_PRIMARY_CLONE" ] && [ -n "$override_abs" ] &&
  git worktree list --porcelain | sed -n 's/^worktree //p' | while IFS= read -r listed; do realpath "$listed" 2>/dev/null || true; done | grep -Fxq -- "$override_abs"; then
  primary_clone=$override_abs
fi
top_abs=$(realpath "$top")
primary_abs=$(realpath "$primary_clone")
snapshot="$git_dir_abs/primary-clone-snapshot"
session_id=${PI_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-${CODEX_SESSION_ID:-${AGENT_SESSION_ID:-}}}}}
if [ -n "$session_id" ]; then
  snapshot="$snapshot.$(printf '%s' "$session_id" | git hash-object --stdin)"
fi

if [ "${BENCHBOX_ALLOW_MAIN_CLONE_WRITE:-${ALLOW_MAIN_CLONE_WRITE:-}}" = "1" ] || [ "${BENCHBOX_EPHEMERAL_CLONE:-}" = "1" ]; then
  exit 0
fi

if [ "$top_abs" -ef "$primary_abs" ]; then
  exit 0
fi

unquote_path() {
  printf '%s\n' "$1" | awk '
    function oct(s, n, k) { n = 0; for (k = 1; k <= 3; k++) n = n * 8 + substr(s, k, 1); return n }
    {
      s = substr($0, 2, length($0) - 2); out = ""
      for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c != "\\") { out = out c; continue }
        c = substr(s, ++i, 1)
        if (c ~ /[0-7]/) { out = out sprintf("%c", oct(substr(s, i, 3))); i += 2 }
        else if (c == "n") out = out "\n"
        else if (c == "t") out = out "\t"
        else if (c == "r") out = out "\r"
        else if (c == "a") out = out "\007"
        else if (c == "b") out = out "\010"
        else if (c == "f") out = out "\014"
        else if (c == "v") out = out "\013"
        else out = out c
      }
      printf "%s/", out
    }'
}

hash_one() {
  if [ -f "$primary_abs/$1" ]; then
    git hash-object -- "$primary_abs/$1" 2>/dev/null || echo unreadable
  else
    echo deleted
  fi
}

current_state() {
  plain=$(mktemp)
  plain_abs=$(mktemp)
  hashes=$(mktemp)
  (
    unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX
    git -C "$primary_abs" -c core.quotepath=off diff --name-only HEAD 2>/dev/null || true
    git -C "$primary_abs" -c core.quotepath=off ls-files --others --exclude-standard 2>/dev/null || true
  ) | while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
      \"*)
        real=$(unquote_path "$path")
        printf '%s\t%s\n' "$(hash_one "${real%/}")" "$path"
        ;;
      *)
        if [ -f "$primary_abs/$path" ]; then
          printf '%s\n' "$path" >> "$plain"
          printf '%s/%s\n' "$primary_abs" "$path" >> "$plain_abs"
        else
          printf 'deleted\t%s\n' "$path"
        fi
        ;;
    esac
  done
  if [ -s "$plain" ]; then
    if git hash-object --stdin-paths < "$plain_abs" > "$hashes" 2>/dev/null; then
      paste "$hashes" "$plain"
    else
      while IFS= read -r path; do
        printf '%s\t%s\n' "$(hash_one "$path")" "$path"
      done < "$plain"
    fi
  fi
  rm -f "$plain" "$plain_abs" "$hashes"
}

pinned_revs() {
  sed -n 's/^rev[[:space:]]*=[[:space:]]*\([0-9a-f]\{40\}\)[[:space:]]*$/\1/p' "$1" | sort -u
}

report_stale_agent_skills() {
  receipt="$primary_abs/.agents/skills/skill-sync.receipt"
  config="$top_abs/skill-sync.conf"
  [ -f "$receipt" ] && [ -f "$config" ] || return 0
  [ "$(pinned_revs "$receipt")" = "$(pinned_revs "$config")" ] && return 0
  cat >&2 <<EOM
WARNING: the primary clone's .agents/skills copy is not at the catalog revisions
this branch pins in skill-sync.conf. Codex, Gemini and Antigravity sessions
started in the primary clone read that copy, so they see outdated skills.
Agents may not refresh it. Owner: run 'make skill-sync' in $primary_abs.
EOM
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

report_stale_ignored_skills() {
  manifest="$top_abs/.claude/skills/skill-sync.manifest"
  [ -f "$manifest" ] && [ -d "$primary_abs/.claude/skills" ] || return 0
  expected=$(mktemp)
  sed -n 's/^\([0-9a-f]\{64\}\) [0-7]\{3\} \(.*\)$/\1 .claude\/skills\/\2/p' "$manifest" > "$expected"
  stale=$(
    cut -d' ' -f2- "$expected" | (unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE; git -C "$primary_abs" check-ignore --stdin 2>/dev/null || true) |
      while IFS= read -r path; do
        [ -f "$primary_abs/$path" ] || continue
        want=$(grep -F " $path" "$expected" | while IFS=' ' read -r hash listed; do [ "$listed" = "$path" ] && printf '%s\n' "$hash"; done)
        [ "$(sha256_of "$primary_abs/$path")" = "$want" ] || printf '  %s\n' "$path"
      done
  )
  rm -f "$expected"
  [ -n "$stale" ] || return 0
  cat >&2 <<EOM
WARNING: git-ignored skill files in the primary clone differ from the
.claude/skills/skill-sync.manifest this branch pins, so Claude sessions started
there read outdated skills:
$stale
Agents may not refresh them. Owner: back up and delete those files, then run
'tools/skill-sync apply' in $primary_abs.
EOM
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
  report | enforce)
    report_stale_agent_skills
    report_stale_ignored_skills
    if [ ! -f "$snapshot" ]; then
      printf 'No primary-clone snapshot at %s; run make agent-write-preflight first. Skipping primary-clone change check.\n' "$snapshot" >&2
      exit 0
    fi
    recorded=$(mktemp)
    LC_ALL=C sort -u "$snapshot" > "$recorded"
    changed=$(current_state | LC_ALL=C sort -u | LC_ALL=C comm -23 - "$recorded" | cut -f2 || true)
    rm -f "$recorded"
    if [ -z "$changed" ]; then
      exit 0
    fi
    if [ "$mode" = enforce ] && [ -z "$session_id" ]; then
      mode=report
    fi
    if [ "$mode" = enforce ]; then
      heading="ERROR: files in the primary clone were added or changed after this worktree's snapshot."
    else
      heading="WARNING: files in the primary clone were added or changed since this worktree's write preflight."
    fi
    cat >&2 <<EOM
$heading
The primary clone is read-only for agent work; these edits are probably a stray
write through a relative path (file tools resolve it against the session
directory, the primary clone), or the owner's concurrent work.

  primary clone: $primary_abs
  changed paths:
$(printf '%s\n' "$changed" | sed 's/^/    /')

Do NOT revert, restore, or checkout them. Nothing was changed: other sessions'
edits must not be lost.
Recovery:
  1. Inspect: git -C "$primary_abs" diff -- <path>   (a new untracked file has no diff; read it)
  2. If a write of yours landed there, move it into this worktree as a patch:
       git -C "$primary_abs" diff -- <path> | git -C "$top_abs" apply
     or, for a new untracked file, copy it to the same path under $top_abs/,
     and use absolute paths under $top_abs/ from now on.
  3. Report the paths to the owner; the owner decides what happens to the
     primary clone's copy.
  4. Accept the primary's current state and continue:
       BENCHBOX_PRIMARY_SNAPSHOT_REFRESH=1 make agent-write-preflight
EOM
    if [ "$mode" = enforce ]; then
      exit 1
    fi
    ;;
  *)
    echo "usage: check_primary_clone_clean.sh [snapshot|report|enforce]" >&2
    exit 2
    ;;
esac
