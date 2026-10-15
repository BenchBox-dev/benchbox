# Agent handoffs

`[HANDOFF-LOCATION-001]` in `AGENTS.md` is the authority. This note gives the
layout and the reason for it.

## Location and naming

- Write each handoff to `~/.benchbox/handoffs/<YYYY-MM-DD>-<topic>-handoff.md`.
  Put supporting evidence in a sibling `<stem>-evidence/` directory.
- Revise a handoff in place. A newer plan replaces the older text; do not add
  a second file for the same work.
- Never write a handoff inside a checkout or worktree, in a tracked directory
  such as `docs/internal/`, or in `/tmp`.
- Never commit a handoff, and never open a PR whose only purpose is to carry
  one.

A batch ledger kept by the `todo` skill under `.todo-batch/` is not a handoff,
but it must stay untracked too.

## Why

Handoffs had drifted across six locations: `_project/handoffs/` in several
worktrees, `.todo-batch/`, `docs/internal/`, `/tmp`, and a directory outside
the repository. Some were committed and later edited on other branches. A
resuming agent could not tell which copy was current, and a copy left in a
worktree vanished when that worktree was removed. One directory in the home
folder is shared by every worktree and every agent harness, survives worktree
cleanup, and cannot be committed by accident.

A handoff records session state: claims, run IDs, and decisions in progress.
`[DURABLE-ARTIFACTS-001]` keeps that state out of durable artifacts, and the
repository is one of them.

## Enforcement

`scripts/check_untracked_handoffs.sh` fails when a tracked file is under
`_project/handoffs/`, `.claude/handoffs/`, or `.todo-batch/`, or has a name
matching `*handoff*.md`. It skips generated skill references under
`.claude/skills/` and this note. It runs in pre-commit, in `make ci-lint`, and in the
`base-guard` CI job that feeds the required `tooling` result. `.gitignore`
ignores `.claude/handoffs/` and `.todo-batch/`. It cannot ignore
`_project/handoffs/` while that directory holds tracked files.

The guard lists 17 files under `_project/handoffs/` that were committed before
this rule. They stay as frozen history because specs and blind-spot records cite
them. Do not add to that list.
