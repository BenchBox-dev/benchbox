---
name: todo
description: Use when the user asks to "ideate on an idea", "refine an idea", "brainstorm", "write a spec", "create a specification", "create a TODO", "show TODO items", "manage TODOs", "prioritize TODOs", "top N most important todos", "rank the backlog", "what should we work on", "what should I work on", "implement a TODO", "implement a batch of TODOs", "complete a TODO", "finish an item", "release my claim", "why did finish fail", "cleanup TODOs", "review TODO quality", "claim a TODO", "what's ready" / "ready queue", "block"/"unblock a TODO", "create TODOs from a spec", "create a batch handoff", or "close out a reviewed batch". Covers the lifecycle from idea to specification, implementation, and completion.
version: 4.2.0
tools: Bash, Read, Edit, Write, Task
---

# Todo — Idea to Done

The tracker is driven through **MCP tool calls** on the `tractable` server, not
shell commands. Task state is JSON (`index.json` plus one `items/<id>.json`
per item) published on a dedicated Git state branch, one commit per operation.
The `tractable` CLI keeps bootstrap, validation, migration, and recovery for
humans and CI (listed at the end of this page). Never hand-edit state-branch
files; every change goes through a tool so publication stays atomic.

`tractable` is the product this skill drives: the MCP server and the CLI of
that name. Requires tractable 1.0.0 or later.

## Critical rules

- **Call `get_instructions` first** in a session, with `expect_store` set to
  the project's repository as `owner/repo` (for example
  `BenchBox-dev/BenchBox`). It returns the live protocol and, on clients that
  supply no explicit actor, pins your worker identity.
- **Confirm the bound store before the first write.** The
  instructions start with a "Bound store" section naming the state remote and
  branch, and a server bound to another project's tracker answers
  `expect_store` with a `gate`-kind `E_STATE`. On that refusal, stop and write
  nothing; tell the operator to restart `tractable-mcp` with the project's
  `--config` or from the project directory. Since tractable 1.1.1 the
  server compares whole trailing repository path segments of the remote,
  case-insensitively, and accepts `owner/repo`, a bare repository name or a
  full URL (with or without `.git`). Keep passing `owner/repo`.
- **A running server keeps the code it started with.** After
  `get_instructions`, compare the server's `tractable_batch.tool_version`
  (experimental capability, or `serverInfo.version`) with the project's pinned
  runtime; in a vendoring project that is the wheel version under its vendor
  directory (for example BenchBox
  `_project/scripts/vendor/tractable-<v>-py3-none-any.whl`). If the server is
  older, make no tracker writes and ask the operator to restart the session.
- **Drive the tracker through MCP tools.** Never run tracker verbs through the
  `tractable` CLI or invent a second store. Git, worktree, test, and build
  commands in the shell are ordinary repository work.
- **Fail-closed preflight.** If the `tractable` MCP tools are absent from your
  tool registry, stop and tell the operator to register `tractable-mcp`
  (`references/bootstrap.md`). Do not reconstruct the backlog from files or
  chat history — the state branch is the only source.
- **One server instance is one worker identity.** Run separate servers with
  different `--actor` values for concurrent workers, and never share an actor
  across concurrent servers.
- **One live claim per slot.** A worker holds one live claim outside any batch
  and one per active batch; a second claim in the same slot is refused with
  `E_MULTIPLE_CLAIMS`.
- **Only the claim holder may `renew`, `finish`, or `release` an item.** Keep
  the `generation` returned by `take`. The same stable actor may re-adopt after
  its previous server stops. For a default-identity ordinary reconnect, checkpoint
  and release first, confirm store/runtime after reconnect, then take again.
  A batch leader retains its stable owner identity or uses owner recovery.
- **Take over a foreign live claim only when its session cannot resume.**
  `takeover(id, expected_holder, reason)` transfers a live claim held by
  another worker: name the exact holder you inspected with `show_item` and say
  why that session is dead. It is refused when the task has no live claim (use
  `take`), when you already hold it (use `take` to re-adopt), when the holder
  changed under you (re-read, then decide), or when you hold another live claim
  (`E_MULTIPLE_CLAIMS`). On an active
  registered batch only the batch owner may take over a member
  (`E_BATCH_OWNER`); a dead leader is handled by `recover_batch_owner`, not by
  taking over a member.
- **Prepared work is not completion.** `prepare` releases the claim and leaves
  the item `open`; only `finish` after the final PR has actually merged closes
  it. A prepared receipt satisfies only a registered same-batch implementation
  edge; ordinary dependencies stay done-only, and receipts never unlock review,
  approval, merge, or deployment gates. Prepared mode needs the live
  `tractable_batch` version 2 capability and repository policy that selects it;
  otherwise work serially. Procedure: `references/batch.md`.
- **Role boundaries in a batch.** While a batch is active, only its owner (the
  leader) may `take`, `takeover`, `prepare`, or `finish` its members, and the
  leader is the only tracker and ledger writer. Member workers implement in their
  own worktrees and stop at a committed, checked change handed to the leader as
  a landing request; they make no tracker writes and open no PR. The leader claims
  a member only to prepare or finish it. The refusal holds only for a distinct
  identity: a member on the leader's server instance or actor
  (`--actor`/`TRACTABLE_ACTOR`), such as a subagent, is not refused, so
  dispatch it with an instruction to make no tracker writes. Repository policy
  decides whether such a member is allowed. Procedure: `references/batch.md`.
- **Close-out authorization.** `closeout` is write-shaped under
  `shared-review-protocol/SKILL.md` §1 (`[REVIEW-AUTH-001]`). A message that
  explicitly invokes `closeout` — alone or paired with review or validation —
  authorizes it that turn. A message asking only to review or validate stays
  review-only: report findings and stop until a later message authorizes
  `closeout`.

## Gates and what to do about them

| Code | Meaning | Do |
|---|---|---|
| `E_NOTHING_READY` | Nothing claimable, or `take`/`finish` hit a `not_before` hold. | Report it. Do not invent work or clear someone else's hold to get around it. |
| `E_OUTPUT_TRUNCATED` | Response exceeded 16 KiB. | Narrow filters, smaller `limit`, or a section read. |
| `E_STATE` | Malformed state or rejected request. As a `gate` from `get_instructions`, the server is bound to another project's store. | Read the message; report it if you cannot fix the request. For the `expect_store` gate, stop and write nothing. |
| `E_SCHEMA` | State format newer than this package. On a batch call: store below schema 4 (register with the cutover first) or a legacy receipt; see `references/batch.md`. | Stop; a human upgrades the package. For a batch call, follow `references/batch.md` (Register and dispatch). |
| `E_OPEN_DEFERRALS` | `finish` refused: the task has open deferrals. | Promote or dismiss each blocking deferral named in the response, then retry `finish`. |
| `E_FINAL_EVIDENCE` | Prepared member lacks current final-tree proof; a named acceptance identifies a missing binding. | Start a new final-verification invocation: every member's receipt command and the combined checks on the clean final head. Re-take the member and retry `finish` with that complete bound evidence after actual merge. |

The server's `get_instructions` text lists the other codes. Lost outcomes
(`E_UNKNOWN`), open deferrals, and the batch codes (`E_BATCH_OWNER`,
`E_BATCH_HEAD`) in full: `references/recovery.md`.

## Planning

`create_item` takes `id` (`a-z0-9-`, alphanumeric at both ends), `title`
(1–200 characters), and optional `priority` (default `medium`), `description`,
`needs`, `acceptance`, `links`, `context`, `not_before`, and `batch` metadata
(`batch_id`, `member_id`, `implementation_dependencies`; explicit, never
inferred from `needs`, attached only after `register_batch`). An ordinary task
closes with `take` + `finish`.

`update_item` moves status only between `open` and `blocked`; closing goes
through `finish`. Add progress notes with `context_append` so concurrent notes
are not lost. Dropping a task is the owner's decision: report it, and once they
decide, call `drop` with their decision and any successor as `reason` (no
separate note).

On a prepared member, `update_item` keeps the receipt only for a `context`
edit (including `context_append`); any other field change (`description`,
`acceptance`, `links`, `needs`, `title`, `priority`, `status`, `not_before`, or
a mix with `context`) invalidates it and its dependents, and the member is
prepared again afterwards (re-prepare). Batch metadata already on a registered
member cannot be changed (abort to reassign); attaching it to a member that has
none is allowed. Once any member is `done` every
invalidating edit on the batch is refused.

Use `not_before` rather than `blocked` when a task is waiting for a known time,
such as the end of a measurement window. It takes a future RFC 3339 time with
`Z` or an offset. The task stays `open`, leaves the ready queue until then, and
returns on its own; rows and `show_item` show `waiting_until` meanwhile, and
`take` and `finish` refuse it with `E_NOTHING_READY` — a hold set while you
hold the claim still blocks `finish`, so `release` instead. The hold also
applies while the task is `blocked`. `not_before=""` clears the hold; tractable 1.1.1+ also accepts
`update_item(clear_not_before=true)`, for clients that drop empty-string
arguments, and refuses it together with a non-empty `not_before`. Use
`blocked` for work stalled on something with no known end.

`list_items(ready_only=true)` returns only claimable tasks: `open`, unclaimed,
not held by a future `not_before`, dependencies all `done`, except that an explicitly registered same-batch
implementation edge may consume a valid prepared receipt whose accepted head is
present at the registered integration head and whose base, scope, owner epoch,
repository, and member identity all match. Readiness and unlock
counts are computed by the program — never scan history yourself. Any
non-context edit of a member, or a dependency, conflict, or other change to
the evidence it binds, invalidates affected prepared evidence transitively.

## Finding the right tool

| You want to | Tool |
|---|---|
| See what is ready | `list_items(ready_only=true)` |
| Inspect one item | `show_item` |
| List or search items | `list_items` with `status`/`priority`/`text` |
| Create or amend work | `create_item`, `update_item` |
| Claim, extend, close, hand back, drop | `take`, `renew`, `finish`, `release`, `drop` (`drop` needs `reason`) |
| Take over a dead session's live claim | `takeover` |
| Defer work, then resolve the deferral | `defer`, `list_deferrals`, `promote_deferral`, `dismiss_deferral` (`finish` refuses with `E_OPEN_DEFERRALS` until every open deferral on the task is promoted or dismissed) |
| Read a batch | `show_batch`: heads, owner and `owner_epoch`, final PR, member status, receipts, log. It never serves the owner generation, so the leader keeps it in its ledger. Scripts and CI read the same data with `tractable batch show <batch_id>`. |
| Record a head change | `advance_batch_head`, owner-only; see `references/batch.md` |
| Recover a dead leader | `recover_batch_owner`, with the recorded owner and generation and a `new_owner_generation` you generate (32 hex) and record in your ledger **before** the call. The server stores it and never returns it. Checkout relocation and abort: `references/recovery.md`. |

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

- `tractable bootstrap --state-remote <url> [--write-config]` — create the state branch.
- `tractable validate` — check the accepted tip and report counts.
- `tractable migrate --from-export <file> --backup-dir <dir> [--dry-run]` — migrate a legacy v2 export onto a freshly bootstrapped branch. Dry-run first; a real run requires `--backup-dir`.
- `tractable recover --op-id <id>` / `--restore-rev <sha>` / `--limit N` — reconcile, restore, or show history.
- `tractable list` / `tractable show <id>` / `tractable batch show <batch_id>` — read-only inspection for humans and CI.

Details: `references/recovery.md` and `references/bootstrap.md`.
