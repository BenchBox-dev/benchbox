# Primary clone stays clean

The primary clone is read-only for agent work. Every change is made in a linked
worktree created by `make worktree-create`. Agents have still written edits into
the primary clone, usually by resolving a relative path against the wrong
working directory, and nothing noticed until the next commit.

## The check

`make agent-write-preflight` run in a worktree records a snapshot of the primary
clone's tracked changes (each changed path with a content hash) in
`$(git rev-parse --git-dir)/primary-clone-snapshot`, which Git never tracks. The
first run wins; later runs, including the commit hook, keep it. Set
`BENCHBOX_PRIMARY_SNAPSHOT_REFRESH=1` to re-record it.

`make pr-preflight` runs `scripts/check_primary_clone_clean.sh report`, which
lists tracked paths in the primary clone that are new or further modified since
the snapshot. Dirt that predates the snapshot is not reported. Untracked files
are ignored. The check only reads the primary clone (one `git diff --name-only`
plus a hash per changed file) and never discards, stashes, or restores anything.

The report is a warning, not a failing gate. A changed primary-clone file can be
the owner's legitimate concurrent edit, which an agent cannot tell apart from a
stray write, so failing would block unrelated PRs.

It is skipped when the primary clone is the working tree itself (the existing
primary-clone refusal applies), for a declared ephemeral clone
(`BENCHBOX_EPHEMERAL_CLONE=1`), and when `BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1`
authorizes primary-clone writes.

## When it fires

Never revert the primary clone with `git checkout`, `git restore`, or any other
command, even if the change looks like yours. Inspect it, re-apply any edit you
want inside your worktree, and report the paths to the owner:

```bash
git -C /abs/path/to/BenchBox status --short
git -C /abs/path/to/BenchBox diff
```

## Worker instructions

Give workers absolute worktree paths and no relative ones. Every file edit and
every command targets `/abs/path/to/BenchBox.wt-<slug>/...`, not `./...` or
`../`, because a worker whose working directory is the primary clone silently
writes there. For another repository, use a linked worktree made from the
owner's clone (`git -C <owner clone> worktree add ...`), never a standalone
clone of it.

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
