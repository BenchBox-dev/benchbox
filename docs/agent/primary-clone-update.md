# Keeping the primary clone current

Agent sessions start in the primary clone. They launch the tracker server from
its `_project/scripts` environment and load agent hooks and settings from its
checked-out files. Nothing refreshes that clone on its own, so a merged fix to
session tooling has no effect until the clone is pulled, synced and the sessions
are restarted. One day after the write guard merged, the primary clone was 19
commits behind and had no guard, and its tracker runtime was `tractable` 1.1.0
while `origin/develop` pinned 1.1.1.

## The warning

`make agent-write-preflight` runs `scripts/check_primary_clone_stale.py` right
after the clean-clone check. It only warns and always succeeds. It reads the
`origin/develop` ref that is already fetched and makes no network call, so it is
as fresh as the last fetch. It prints a warning when:

- `origin/develop` has commits not in the primary clone's `HEAD` that touch
  `.claude/settings.json`, `.codex/hooks.json`, `.gemini/settings.json`,
  `.omp/hooks`, `.omp/extensions`, `.mcp.json`,
  `scripts/primary_clone_write_guard.py`, `_project/scripts/pyproject.toml`,
  `_project/scripts/uv.lock` or `_project/scripts/vendor`; changes to any other
  path are not reported;
- the `tractable` version installed in the primary clone's
  `_project/scripts/.venv` differs from the version in `_project/scripts/uv.lock`
  on `origin/develop`.

It is skipped from the commit hook and in a declared ephemeral clone, and it is
silent when `origin/develop` or the environment is absent.

## Updating

Who may run it: the owner. An agent may run it only when the owner explicitly
approved it in the current task, because it changes the clone every other
session loads from. Without approval, report the warning to the owner.

```bash
git -C /abs/path/to/BenchBox pull --ff-only origin develop
uv sync --project /abs/path/to/BenchBox/_project/scripts --locked
```

Then restart every session that was started before the update: a running
session keeps the hooks, settings and tracker server it loaded at start. If the
pull does not fast-forward, or the primary clone has uncommitted changes, stop
and tell the owner.

## Enforcement per agent

The pre-write hook in [primary-clone-guard.md](primary-clone-guard.md) inspects
file-tool writes only. Shell writes (`sed -i`, `cat >`, `cp`, `git checkout`,
scripts run with the primary clone as working directory) are not inspected; the
owner accepted detection afterwards by the snapshot comparison in
`make agent-write-preflight`.

Per-agent enforcement status, with the live-probe result and date for each
agent, is the table in [primary-clone-guard.md](primary-clone-guard.md). On
2026-11-13 live probes (a non-interactive session with the primary clone as
working directory, asked to create `guard-probe-<agent>.txt` through the edit
tool) could not be run for Claude Code, Codex or Gemini CLI, so those three rely
on the preflight backstop. Rerun them when the login, quota or tier problem
clears.
