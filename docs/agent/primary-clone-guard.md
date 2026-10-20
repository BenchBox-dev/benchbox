# Primary clone stays clean

The primary clone is read-only for agent work. Every change is made in a linked
worktree created by `make worktree-create`. Agents have still written edits into
the primary clone, usually by resolving a relative path against the wrong
working directory, and nothing noticed until the next commit.

## The check

`scripts/check_primary_clone_clean.sh` fails when the primary clone has
uncommitted changes to tracked files and names every changed path. Untracked
files are ignored. It never discards, stashes, or restores anything.

It runs from:

| Entry point | When |
|---|---|
| `make agent-write-preflight` | Start of a task, from the worktree, and on every commit through the pre-commit hook |
| `make pr-preflight` | Before opening a PR |

It is skipped when the primary clone is the working tree itself (the existing
primary-clone refusal applies), for a declared ephemeral clone
(`BENCHBOX_EPHEMERAL_CLONE=1`), and when `BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1`
authorizes primary-clone writes.

## When it fires

Do not clean the primary clone yourself. Inspect it, re-apply any edit you want
inside your worktree, and tell the owner the primary clone is dirty:

```bash
git -C /abs/path/to/BenchBox status --short
git -C /abs/path/to/BenchBox diff
```

## Worker instructions

Give workers absolute worktree paths and no relative ones. Every file edit and
every command targets `/abs/path/to/BenchBox.wt-<slug>/...`, not `./...` or
`../`, because a worker whose working directory is the primary clone silently
writes there. Check that `git -C <primary clone> status --short` is empty when
the task ends.

## Worktree edits lost to a reset

No live repository script resets or cleans a worktree: `scripts/` and
`make/worktrees.mk` contain no `reset --hard`, `git clean`, or `checkout -f`
(the only matches are recipes of the retired pool targets recorded in
`make/monolith-baseline.json`, and `release-cut-abort`, which acts on its own
`v<version>` branch).

The probable cause is the pre-commit unstaged-changes stash. Before running
hooks, pre-commit moves unstaged edits into a patch file under
`~/.cache/pre-commit/patch*` and re-applies it afterwards. A hook run that is
killed or times out, which the roughly four-minute commit hook invites, leaves
the edits in that patch and the worktree reverted. On the author's machine
`~/.cache/pre-commit/` held 466 such patch files, including several from
2026-10-19 and 2026-10-20. This is an inference: no specific lost-edit incident
was tied to a specific patch file. After a suspected reset, look there first and
re-apply with `git apply <patch>`. Run commits with a timeout longer than the
hook.
