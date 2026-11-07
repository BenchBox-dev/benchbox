# Bootstrap the tracker

Set up tracker state for a repository. State lives on a dedicated Git branch,
outside the code worktree.

`tractable` was named `todo-db` before 1.0.0. A project still on `todo-db`
uses its own pinned copy of this skill until it switches; this page and the
rest of the skill assume `tractable` 1.0.0 or later.

## Install the runtime

A human installs the tool from PyPI and upgrades it the same way:

```sh
uv tool install --default-index https://pypi.org/simple 'tractable[mcp]>=1.0.0'
uv tool upgrade tractable
```

This provides the `tractable` CLI and the `tractable-mcp` server.

## Create the state branch

A human runs, from anywhere with Git access to the remote:

```sh
tractable bootstrap --state-remote <git-url-or-path> [--state-branch todo-state] --write-config
```

`--write-config` writes `.tractable/config.json` (state remote + branch) in the
repo root for server discovery. Bootstrap refuses to overwrite an existing
state branch.

## Migrate from a 0.6.x export

To carry an existing SQLite-era tracker onto the new state branch:

1. With the 0.6.x release (then named `todo-db`), write a lossless export:
   `todo-db --db <old>.sqlite export --output <export>.json` (the envelope must
   be `format_version` 2).
2. `tractable bootstrap ...` to create the empty state branch.
3. `tractable migrate --from-export <export>.json --dry-run` and read the mapping
   report: item count, status breakdown, dropped-claim warnings, and preserved
   row counts.
4. `tractable migrate --from-export <export>.json --backup-dir <dir> --actor <name>`
   for the real run. `--backup-dir` is required — the export is the only
   archive of events, findings, and audit history, none of which migrate.
5. `tractable validate`, then spot-check a few items with `tractable show <id>`.

The migration refuses a non-empty state branch and refuses cutover while any
claim lease is still live. To roll back before any consumer adopts the new
tracker, delete the state branch and re-bootstrap; the old database is
untouched.

## Point the agent at it

- MCP server: `--state-remote` / `--state-branch` / `--cache-dir` flags,
  `TRACTABLE_STATE_REMOTE` / `TRACTABLE_STATE_BRANCH` / `TRACTABLE_CACHE_DIR` env, or
  the discovered `.tractable/config.json` — in that order.
- One server instance is one worker identity (`--actor`, else `TRACTABLE_ACTOR`,
  else the client name from the MCP handshake).
- Registration names the server `tractable` and runs `tractable-mcp`. Claude
  Code reads `.mcp.json` at the project root and needs no arguments:
  `{"mcpServers": {"tractable": {"command": "tractable-mcp", "args": []}}}`.
  Codex needs `[mcp_servers.tractable]` with `command = "tractable-mcp"` in
  `~/.codex/config.toml`. Cursor and Windsurf pass
  `["--repo-root", "${workspaceFolder}"]`; Zed uses `context_servers`, not
  `mcpServers`.
- Per-client registration (Claude Code, Codex, Cursor, Windsurf, Zed) lives in
  the tractable repository's `docs/operations/mcp-clients.md`.

## Preflight verification

1. Run `tractable validate` (human/CI) or `list_items` (agent).
2. A missing branch means bootstrap has not run yet — say so, do not improvise
   state.
3. The state branch shares its repository's access and visibility. Never
   publish credentials to it and never assume it is private.
