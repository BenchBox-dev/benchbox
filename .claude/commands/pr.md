---
allowed-tools: Bash(git:*), Bash(gh:*), Bash(make:*), Bash(uv:*)
description: BenchBox PR workflow - preflight, push, open PR vs develop, arm, monitor to merge
---

## Context

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Last commit: !`git log -1 --oneline`
- Ahead/behind develop: !`git rev-list --left-right --count origin/develop...HEAD 2>/dev/null || echo "(no origin/develop)"`

## Your task

BenchBox PR workflow to `develop` (linear history, squash-only). Run in order; stop on the first failure of steps 1-5:

1. `make agent-write-preflight`. If it refuses (primary clone), have the user create a worktree
   (`make worktree-create BRANCH=<name> WORKTREE_PATH=<path>`); never write from the primary clone
   without `BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1`.
2. Refuse on `develop` or `main`; use a feature-branch worktree.
3. Stage authorized paths explicitly (never `git add -A`), run `make agent-identity-check`, make a
   conventional commit.
4. `make pr-preflight` (path-aware gate; receipt). Fix root causes; never use `--no-verify`.
5. `make pr-open` pushes the branch and opens the PR.
6. `make pr-arm` checks the live PR for a hold, then enqueues the exact head.
7. Monitor until merged; re-enqueue, fix, and stop-for-owner rules are in `AGENTS.md` [WRITE-CLOSEOUT-001].
