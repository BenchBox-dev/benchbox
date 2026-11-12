# Primary clone stays clean

The primary clone is read-only for agent work. Every change is made in a linked
worktree created by `make worktree-create`. Agents have still written edits into
the primary clone, usually by resolving a relative path against the wrong
working directory, and nothing noticed until the next commit.

## Why it happens

Harness read, edit, write and grep tools resolve a relative path against the
directory the session started in, which is the primary clone. `cd` in a shell
tool changes only that shell's directory, so `git rev-parse --show-toplevel`
there reports the worktree while a relative-path edit still lands in the primary
clone. Reads have the mirror problem: a relative read returns the primary
clone's files, which can lag `origin/develop` by many commits. This reproduces
with a scratch repository and a linked worktree: the process holds the primary
as its working directory, writes `app.txt`, and `git status` shows the primary
modified and the worktree clean.

## Pre-write hook

`scripts/primary_clone_write_guard.py` reads a tool call as JSON on stdin and
exits 2 with a message on stderr when a write tool would modify a path inside
the primary clone's working tree while at least one linked worktree exists. The
primary clone is found with `git rev-parse --git-common-dir`, not a fixed path,
from the target's nearest existing parent directory, and only for the
repository the session or the script belongs to. Relative paths are resolved
against the payload's `cwd`; `..`, symlinks and a differently cased path on a
case-insensitive filesystem are compared by file identity.

Targets it reads: `path`, `file_path`, `notebook_path`, `paths`, the
`[PATH#TAG]` headers, `MV DEST` lines and `*** Update File:`-style headers of an
edit `input` (so every section of a multi-file edit and every move destination
is checked), and `file://` URLs. Targets with any other URI scheme (`xd://`,
`local://`, `ssh://`, `conflict://`, `proc://`, `agent://`, `out://`,
`artifact://`) are not files and are ignored; `./xd://x` is a real relative
path and is checked.

The refusal gives the absolute path inside the linked worktree that matches the
session (by session id), or the cwd's own worktree, or the most recently
modified linked worktrees. Allowed: paths inside any linked worktree (including
one nested under the primary), paths under the primary's `.git`, paths outside
the repository, and read-only tools. Set `BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1` to
authorize primary-clone writes.

On an internal error (unparseable payload, git missing or slow) the hook allows
the call and prints `failed open on internal error`. Failing closed would block
every edit in every worktree whenever the hook itself breaks; the failing
preflight below still catches a leaked write. The configured commands also allow
the call when the script is absent from the checkout the session started in.

| Harness | Tracked configuration | Status |
| --- | --- | --- |
| omp | `.omp/hooks/pre/primary-clone-write-guard.ts` (`tool_call` handler returning `block`), loaded from `<cwd>/.omp` | **Proven live**: a relative write in the primary was blocked, a worktree write allowed, the override honoured, and the hook applied inside omp subagents. The module is also executed under Node or Bun by `tests/unit/test_primary_clone_write_guard.py`. |
| Claude Code | `.claude/settings.json` `hooks.PreToolUse`, matcher `Write\|Edit\|MultiEdit\|NotebookEdit` | Wired, not proven: it could not be exercised live (authentication or quota). The configured command is run by the tests. |
| Codex | `.codex/hooks.json` `PreToolUse`, matcher `apply_patch\|Edit\|Write` | Wired, not proven: not exercised live. The project layer must be trusted and each hook reviewed with `/hooks`; open upstream reports claim deny is not enforced for `apply_patch` on some builds (openai/codex#27833). |
| Gemini CLI | `.gemini/settings.json` `hooks.BeforeTool`, matcher `write_file\|replace` | Wired, not proven: not exercised live. The workspace must be trusted and project hooks are fingerprinted. Antigravity CLI is not covered. |

Nothing audits the `.codex`, `.gemini` and `.omp` hook files: only
`.claude/settings.json` is checked, by the instruction audit, which accepts
exactly the one approved hook. Edits to the other three files go unnoticed.

Gaps: shell commands (`bash`, `sed -i`, `cat >`) are not inspected; a hook only
runs from a checkout that already contains it, so a primary clone behind
`origin/develop` has none until it is updated; the Codex and Gemini trust
prompts can be declined. The preflight below is the backstop for all of these.

## The check

`make agent-write-preflight` run in a worktree records a snapshot of the primary
clone's tracked changes (each changed path with a content hash) in
`$(git rev-parse --git-dir)/primary-clone-snapshot.<hash of the session id>`,
which Git never tracks. The session id is the one the worktree-ownership check
uses (`PI_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_SESSION_ID`,
`CODEX_SESSION_ID`, `AGENT_SESSION_ID`). A session's first preflight in a
worktree takes the snapshot and later runs keep it, so edits that predate the
session are never reported and a worktree reused by a new session starts clean.
Set `BENCHBOX_PRIMARY_SNAPSHOT_REFRESH=1` to re-record it.

The explicit `make agent-write-preflight` then runs
`scripts/check_primary_clone_clean.sh enforce` and fails when tracked paths in
the primary clone are new or further modified since the session's snapshot,
naming the paths and the recovery. Untracked files are ignored. With no session
identity the check only reports. The commit hook (`agent_pre_commit_guard.sh`
sets `BENCHBOX_PREFLIGHT_FROM_COMMIT_HOOK=1`) also only reports, so an owner's
deliberate edit in the primary never blocks commits. After an owner's
legitimate concurrent edit, or once a stray edit is dealt with, run
`BENCHBOX_PRIMARY_SNAPSHOT_REFRESH=1 make agent-write-preflight` to accept the
primary's current state. Other targets that call the preflight (`pr-open`,
`trunk-revert`) enforce too. `make pr-preflight` runs `report`, which prints the
same list as a warning. The check only reads the primary clone (one
`git diff --name-only` plus a hash per changed file) and never discards,
stashes, or restores anything.

It is skipped when the primary clone is the working tree itself (the existing
primary-clone refusal applies), for a declared ephemeral clone
(`BENCHBOX_EPHEMERAL_CLONE=1`), and when `BENCHBOX_ALLOW_MAIN_CLONE_WRITE=1`
authorizes primary-clone writes.

## When it fires

Never revert the primary clone with `git checkout`, `git restore`, or any other
command, even if the change looks like yours. Inspect it, move any edit you want
into your worktree as a patch
(`git -C /abs/BenchBox diff -- <path> | git apply`), and report the paths to the
owner:

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
`make/monolith-baseline.json`).

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
