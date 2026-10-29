# Prioritize TODOs

Rank open items by topic without changing tracker state. There is no
`prioritize` tool; this is a skill-only workflow driven by the read tools.

## Before you start

1. Call `get_instructions` with `expect_store` set to the project's
   `owner/repo` to confirm the protocol, the bound store, and your identity.
2. If the tracker is unreachable or reports a schema problem, stop. Do not
   reconstruct the backlog from files or chat history — the state branch is the
   only source.

## Inputs

| Input | Default | Notes |
|---|---|---|
| `N` | 25 | Max items to show |
| Scope | all `open` | Can also include `blocked`, or limit to `ready_only` |
| Write-back | off | Only rewrite priorities when the user explicitly asks |

## Workflow

Start with the live picture:

- `list_items(ready_only=true, limit=50)` — claimable items; page with `cursor`
  until exhausted.
- `list_items(status="open", limit=50)` — every open item; page fully. Brief
  rows carry `id`, `title`, `priority`, `status`.
- `list_items(status="blocked", limit=50)` — only if blocked work affects the
  ranking.

Derive the counts you need (open per priority, ready vs. blocked) from the
pages themselves; there is no `stats` tool. Use `show_item(id=...)` for a
candidate's `needs`, `unmet_needs`, and `unlocks` (the count of open tasks
waiting on it).

For about 15 or fewer open items, rank up to `N` directly by severity,
readiness, unlock value, and keyword risk.

### When you need a structured ranking

For more than about 15 candidates or requested topic groups, page every open
item first, then `show_item` each candidate once for its edges. For each
candidate, compute:

| Signal | How to get it |
|---|---|
| Severity | `critical` > `high` > `medium-high` > `medium` > `low` |
| Ready | `status` is `open`, unclaimed, and `unmet_needs` is empty |
| Unlock value | `show_item` → `unlocks` |
| In-flight | open but absent from `ready_only` with no unmet needs — a live claim; small boost only |
| Risk keywords | Title/description terms suggesting privacy, security, or correctness risk, such as leak, secret, credential, egress, provenance, or silent |
| Human-only | Maintainer/admin work; demote from the agent-actionable top N but note critical items |

Default order:

1. severity band
2. ready before blocked or dependent
3. risk keyword boost within the band
4. unlock value
5. claimed
6. stable tie-break on `id`

A claim gives only a small boost; it never outranks a higher-severity ready item.

### Topic groups

Give each ranked item exactly one topic.

1. Prefer a keyword or theme bucket drawn from titles and descriptions.
2. A shared `id` prefix or a common path in `links` is a good secondary signal.
3. For a top 25, keep four to eight groups; merge singletons into the nearest
   group or "Other high-priority."

### Report

Include:

1. Method: tracker identity, open counts by priority, `N`, and write-back off.
2. Topic tables: rank, id, priority, status, readiness, unlocks, and a one-line
   reason.
3. Suggested dependency-aware order across groups, with privacy, security, and
   tracker reliability before product work.
4. High or critical demotions and their reasons.
5. User-owned blocked criticals.

Ranks are session recommendations and do not change tracker state.

### Write-back — only when the user asks

Only when the user asks to apply the ranking:

1. Update only items whose stored priority differs from the recommended band,
   with `update_item(id=..., priority=..., reason=...)`.
2. Give a one-line reason per update. Prefer band moves (`medium` → `high`)
   over invented ranks the schema cannot store.
3. Re-page `list_items` and show before/after counts.

Never change priorities, block, or claim during read-only ranking. Respect `N`
and requested grouping; do not dump all medium items, create one topic per item,
or return a flat list when groups were requested.
