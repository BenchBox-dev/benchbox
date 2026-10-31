---
name: todo
description: Use when the user asks to "ideate on an idea", "refine an idea", "brainstorm", "write a spec", "create a specification", "create a TODO", "show TODO items", "manage TODOs", "prioritize TODOs", "top N most important todos", "rank the backlog", "what should we work on", "what should I work on", "implement a TODO", "implement a batch of TODOs", "complete a TODO", "finish an item", "release my claim", "why did finish fail", "cleanup TODOs", "review TODO quality", "claim a TODO", "what's ready" / "ready queue", "block"/"unblock a TODO", "create TODOs from a spec", "create a batch handoff", or "close out a reviewed batch". Covers the lifecycle from idea to specification, implementation, and completion.
version: 3.0.0
tools: Bash, Read, Edit, Write, Task
---

# Todo — Idea to Done

The tracker is driven through **MCP tool calls** on the `todo-db` server, not
shell commands. Task state is JSON (`index.json` plus one `items/<id>.json`
per item) published on a dedicated Git state branch, one commit per operation.
The `todo-db` CLI keeps bootstrap, validation, migration, and recovery for
humans and CI (listed at the end of this page). Never hand-edit state-branch
files; every change goes through a tool so publication stays atomic.

This is the only tracker skill. There is no separate `todo-db` skill: `todo-db`
is the *product* this skill drives — the MCP server named `todo-db` and the CLI
of the same name. Do not author a second skill for it, in this catalog or in
the product repository.

## Critical rules

- **Call `get_instructions` first** in a session, with `expect_store` set to
  the project's repository as `owner/repo` (for example
  `BenchBox-dev/BenchBox`). It returns the live protocol and, on clients that
  supply no explicit actor, pins your worker identity.
- **Confirm the bound store before the first write.** From todo-db 0.11.0 the
  instructions start with a "Bound store" section naming the state remote and
  branch, and a server bound to another project's tracker answers
  `expect_store` with a `gate`-kind `E_STATE`. On that refusal, stop and write
  nothing; tell the operator to restart `todo-db-mcp` with the project's
  `--config` or from the project directory. The match is a case-insensitive
  substring of the remote only, so pass the full `owner/repo`, not a short
  word. Older servers ignore `expect_store` and show no "Bound store"
  section: on those, check that `list_items` shows this project's work before
  writing.
- **Drive through MCP tools, never the shell.** Do not invoke tracker verbs
  through the `todo-db` floor CLI, hand-edit state, or invent a second store.
  Use `Bash` strictly for repo test suites, linters, and builds.
- **Fail-closed preflight.** If the `todo-db` MCP tools are absent from your
  tool registry, stop and tell the operator to register `todo-db-mcp`
  (`references/bootstrap.md`). Do not reconstruct the backlog from files or
  chat history — the state branch is the only source.
- **One server instance is one worker identity.** Run separate servers with
  different `--actor` values for concurrent workers.
- **One live claim per worker.** Taking a second item without releasing or
  finishing the first is refused with `E_MULTIPLE_CLAIMS`.
- **Only the claim holder may `renew`, `finish`, or `release` an item.** Keep
  the `generation` returned by `take`; after a restart, `take` the same item
  again to re-adopt it (fresh generation, refreshed lease — any earlier process
  image holding the old generation goes stale).
- **Take over a foreign live claim only when its session cannot resume.**
  `takeover(item_id, expected_holder, reason)` transfers a live claim held by
  another worker: name the exact holder you inspected with `show_item` and say
  why that session is dead. It is refused when the task has no live claim (use
  `take`), when you already hold it (use `take` to re-adopt), when the holder
  changed under you (re-read, then decide), or when you hold another live claim
  (`E_MULTIPLE_CLAIMS`). Batch ownership held by the displaced worker transfers
  with the item. Requires todo-db 0.8.1 or later.
- **Prepared work is not completion.** The prepared lifecycle is fail-closed
  and capability-versioned: `register_batch` must first persist one project /
  repository identity, owner generation, integration branch/worktree and
  immutable start head, ordered members, frozen scope, delivery boundary,
  terminal outcome, and (later) one final PR identity. Register each member's
  explicit implementation edge, then `take` and `prepare` it with a clean exact
  checkout, actual member base, accepted head, current integration head, scope hash,
  and passed bounded-suite evidence. `prepare` releases the claim while
  leaving the item `open`; it does not mark work done. The integrator verifies
  the cumulative current tree and binds the final PR before re-taking and
  `finish`ing members. If the server does not advertise the registered-batch
  contract and compatible schema version, stop and use serial mode; never treat
  legacy per-item receipts as valid. Ordinary dependencies remain done-only;
  prepared receipts never unlock review, approval, merge, deployment, or soak
  gates.
- **Skill-only actions** — `ideate`, `spec`, `prioritize`, `batch`, `handoff`,
  `closeout` — are workflows, not tools. Follow their reference guides.
- **Close-out authorization.** `closeout` is write-shaped under
  `shared-review-protocol/SKILL.md` §1 (`[REVIEW-AUTH-001]`). A message that
  explicitly invokes `closeout` — alone or paired with review or validation —
  authorizes it that turn. A message asking only to review or validate stays
  review-only: report findings and stop until a later message authorizes
  `closeout`.

## The loop

| Step | Tool | Notes |
|---|---|---|
| 1 | `list_items` | Brief rows; filters `status`, `priority`, `text`, `ready_only`; paging with `limit` (default 5) + `cursor`. |
| 2 | `take` | Claim a task. Returns the claim `generation` plus enough context to begin work. |
| 3 | `show_item` | One task with needs, readiness, and sections. Large fields spill to `field`/`offset`/`budget` reads. |
| 4 | `renew` | Extend a long-running claim. Same generation; no progress milestones required. |
| 5 | `finish` | Close the task with the `generation` from `take`. Every member of an active registered batch must already have a valid prepared receipt and bound final-tree evidence; ordinary tasks use the serial finish path. |
| — | `register_batch` / `bind_batch_pr` / `abort_batch` | Register the immutable batch contract, bind exactly one final PR identity, or owner-abort after claims are released. Reject duplicates, foreign owners, late membership, and incompatible capability/schema versions. |
| — | `prepare` | Persist verified member work and hand back the claim without marking the member done. Requires the registered batch, explicit same-batch edge, exact clean source checkout, original base, accepted/current heads, and frozen scope. |
| — | `release` | Hand the claim back without finishing (needs the `generation`). |
| — | `takeover` | Take over a live claim whose session cannot resume: name the inspected holder and why it is dead. Requires todo-db 0.8.1. |
| — | `drop` | Abandon a task as dropped. Requires `reason`: the owner's decision and any successor, at most 2000 characters, stored with the status change in one commit. Requires todo-db 0.12.0 or later. Unclaimed tasks drop freely; a live claim needs its `generation`. |

## Reading the response envelope

```json
{"ok": true,  "data": {...}}
{"ok": false, "code": "E_...", "error": "...", "recovery": [...], "kind": "gate|error"}
```

`get_instructions` returns markdown text directly; the task tools return
the `{ok, ...}` JSON envelope.

`kind: "gate"` is an expected result you should act on. `kind: "error"` is an
environment or protocol failure — stop and report it. The `recovery` list names
concrete next steps; read it before improvising.

Responses are capped at 16 KiB. Lists page with cursors scoped to a state
revision: `E_CURSOR_STALE` means the branch moved under you — restart from the
first page, never skip ahead.

## Gates and what to do about them

| Code | Meaning | Do |
|---|---|---|
| `E_NOTHING_READY` | Nothing claimable, or `take`/`finish` hit a `not_before` hold. | Report it. Do not invent work or clear someone else's hold to get around it. |
| `E_MULTIPLE_CLAIMS` | You already hold a claim. | Finish or `release` it first. |
| `E_CLAIM_STALE` | Wrong generation or another holder. | `show_item`, then `take` again if it is free. |
| `E_CONFLICT` | Someone changed the task first. | Re-read, re-evaluate; never overwrite blindly. |
| `E_CURSOR_STALE` | State moved under your pages. | Restart listing without a cursor. |
| `E_OVERSIZED` | One field exceeds the cap. | Read it with `show_item` `field`/`offset`/`budget`. |
| `E_OUTPUT_TRUNCATED` | Response exceeded 16 KiB. | Narrow filters, smaller `limit`, or a section read. |
| `E_NO_PRINCIPAL` | Identity not resolved. | Call `get_instructions`, then retry. |
| `E_OFFLINE` | Remote unreachable. | Reads may be cached and marked stale; mutations fail — retry when reachable. |
| `E_UNKNOWN` | Outcome undetermined. | Reconcile with the operation ID in `recovery`; do not re-apply blindly. |
| `E_STATE` | Malformed state or rejected request. As a `gate` from `get_instructions`, the server is bound to another project's store. | Read the message; report it if you cannot fix the request. For the `expect_store` gate, stop and write nothing. |
| `E_SCHEMA` | State format newer than this package. | Stop; a human upgrades the package. |
| `E_FINAL_EVIDENCE` | Prepared member lacks current final-tree proof. | Re-take the member, attach exact clean combined-tree evidence, then retry `finish`. |

Conflicts, lost claims, and offline handling in full: `references/recovery.md`.

## Planning

`register_batch` takes the immutable repository and integration contract before
member enrollment. `create_item` takes `id`, `title`, and optionally `priority` (default
`medium`), `description`, `needs` (IDs this task waits on), `acceptance`,
`links`, `context`, `not_before`, and explicit batch metadata. Batch metadata names a
`batch_id`, `member_id`, and `implementation_dependencies`; it is never
inferred from `needs`. IDs use `a-z0-9-` (start/end alphanumeric). Titles are
1–200 characters. There is no work breakdown, scope gate, or verification
ladder: an ordinary task closes with `take` + `finish`.

`update_item` amends title/priority/description/needs/sections/`not_before`,
or moves status between `open` and `blocked`. Text and list fields are
replaced whole; an empty value clears one. To add a progress note without
resending the context, pass `context_append` (todo-db 0.11.0 or later): the
server adds it to the end of the current context when the write lands, so a
concurrent note is not lost. Closing goes through `finish`; dropping a task is
a human decision reported to the user. Once the owner has decided, call `drop`
with their decision and any successor item as `reason` (todo-db 0.12.0 or
later requires it, up to 2000 characters, newlines allowed); the server stores
it as `drop_reason` in the same commit, so do not write a separate
`context_append` note first.

Use `not_before` rather than `blocked` when a task is waiting for a known time,
such as the end of a measurement window. It takes a future RFC 3339 time with
`Z` or an offset. The task stays `open`, leaves the ready queue until then, and
returns on its own; rows and `show_item` show `waiting_until` meanwhile, and
`take` and `finish` refuse it with `E_NOTHING_READY` — a hold set while you
hold the claim still blocks `finish`, so `release` instead. The hold also
applies while the task is `blocked`. `not_before=""` clears the hold. Use
`blocked` for work stalled on something with no known end. Clients older than
todo-db 0.8.0 keep the field but do not enforce the hold.

`list_items(ready_only=true)` returns only claimable tasks: `open`, unclaimed,
not held by a future `not_before`, dependencies all `done`, except that an explicitly registered same-batch
implementation edge may consume a valid prepared receipt whose accepted head is
present at the registered integration head and whose base, scope, owner
generation, repository, and member identity all match. Readiness and unlock
counts are computed by the program — never scan history yourself. Any member,
scope, dependency, conflict, or late-enrollment change invalidates affected
prepared evidence transitively.

## Finding the right tool

| You want to | Tool |
|---|---|
| See what is ready | `list_items(ready_only=true)` |
| Inspect one item | `show_item` |
| List or search items | `list_items` with `status`/`priority`/`text` |
| Create or amend work | `create_item`, `update_item` |
| Claim, extend, close, hand back, drop | `take`, `renew`, `finish`, `release`, `drop` (`drop` needs `reason`, 0.12.0+) |
| Take over a dead session's live claim | `takeover` (0.8.1+) |

## Process guides

For multi-step workflows, follow the dedicated reference guide. None is a tool.

| Workflow | When to use it | Guide |
|---|---|---|
| `ideate` | Refine a rough idea into an actionable problem statement | `references/ideate.md` |
| `spec` | Structure a specification for ingestion by `create_item` | `references/spec.md` |
| `implement` | Drive a claimed item through the working loop | `references/implement.md` |
| `review` | Audit task quality and readiness before closing | `references/review.md` |
| `queries` | Search, filter, and read items with bounded output | `references/queries.md` |
| `prioritize` | Rank open items by topic, severity, readiness, and unlock value | `references/prioritize.md` |
| `batch` | Implement a set of items in dependency order across bounded contexts | `references/batch.md` |
| `handoff` | Create a self-contained handoff prompt for another agent session | `references/handoff.md` |
| `closeout` | Remediate reviewed findings and close batch items | `references/closeout.md` |
| `bootstrap` | Initialize state-branch config for a repository | `references/bootstrap.md` |
| `recovery` | Resolve error codes, conflicts, and offline states | `references/recovery.md` |

## Human-only floor verbs

These are not tools. When you hit a condition that needs one, stop and give the
human the exact command:

- `todo-db bootstrap --state-remote <url> [--write-config]` — create the state branch.
- `todo-db validate` — check the accepted tip and report counts.
- `todo-db migrate --from-export <file> --backup-dir <dir> [--dry-run]` — migrate a legacy v2 export onto a freshly bootstrapped branch. Dry-run first; a real run requires `--backup-dir`.
- `todo-db recover --op-id <id>` / `--restore-rev <sha>` / `--limit N` — reconcile, restore, or show history.
- `todo-db list` / `todo-db show <id>` — read-only inspection for humans and CI.

Details: `references/recovery.md` and `references/bootstrap.md`.
