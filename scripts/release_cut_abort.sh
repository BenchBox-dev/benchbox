#!/bin/sh
set -eu

version=${1:?Usage: release_cut_abort.sh X.Y.Z}
if ! printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.+-]+)?$'; then
  echo "Error: VERSION must be X.Y.Z with an optional release suffix." >&2
  exit 1
fi
branch="v$version"
current_ref=$(git symbolic-ref --quiet HEAD) || {
  echo "Error: release-cut-abort requires a named branch." >&2
  exit 1
}
case "$current_ref" in
  refs/heads/*) current=${current_ref#refs/heads/} ;;
  *) echo "Error: release-cut-abort requires a named branch." >&2; exit 1 ;;
esac

git_dir=$(git rev-parse --absolute-git-dir)
common_dir=$(git rev-parse --git-common-dir)
if [ "$git_dir" -ef "$common_dir" ]; then
  echo "Error: release-cut-abort requires a linked worktree." >&2
  exit 1
fi

original=$(git config --worktree --get benchbox.worktree.branch || true)
case "$original" in
  chore/*|fix/*|feat/*|docs/*) ;;
  *) echo "Error: the creating worktree branch is missing or invalid." >&2; exit 1 ;;
esac
case "$current" in
  "$branch") ;;
  "$original") ;;
  *) echo "Error: expected $branch or its creating branch $original, found $current." >&2; exit 1 ;;
esac

git show-ref --verify --quiet "refs/heads/$branch" || {
  echo "Error: no local $branch branch to abort." >&2
  exit 1
}
git show-ref --verify --quiet "refs/heads/$original" || {
  echo "Error: creating branch $original is missing." >&2
  exit 1
}
base=$(git rev-parse "refs/heads/$original^{commit}")
cut=$(git rev-parse "refs/heads/$branch^{commit}")
if [ "$cut" != "$base" ]; then
  echo "Error: $branch has commits or the creating branch moved; inspect both branches before cleanup." >&2
  exit 1
fi

remote_branch=$(git ls-remote --heads origin "refs/heads/$branch")
remote_tag=$(git ls-remote --tags origin "refs/tags/$branch")
if [ -n "$remote_branch" ] || [ -n "$remote_tag" ] || git show-ref --verify --quiet "refs/tags/$branch"; then
  echo "Error: $branch exists on origin or as a local tag; published release state needs manual disposition." >&2
  exit 1
fi

status=$(git status --porcelain --untracked-files=all)
if printf '%s\n' "$status" | grep -q '^?? '; then
  echo "Error: untracked files are present; inspect them before discarding the cut." >&2
  exit 1
fi
if [ "$current" = "$branch" ]; then
  git switch --discard-changes "$original"
elif [ -n "$status" ]; then
  echo "Error: the creating branch is dirty; inspect it before retrying abort." >&2
  exit 1
fi

git branch -d "$branch"
echo "Aborted uncommitted $branch; restored $original."
