# TODO queries and item management

Inspect, filter, and read tracker items with bounded output. Filtering,
sorting, readiness, and unlock counts run inside the program — never scan
history yourself.

## Query patterns

| Goal | Tool call | Notes |
|---|---|---|
| Ready work | `list_items(ready_only=true)` | `open`, unclaimed, no future `not_before`, dependencies all `done`. |
| List or search | `list_items(status=..., priority=..., text=...)` | Brief rows; default 5 per page. |
| Inspect one task | `show_item(id=...)` | Needs, unmet needs, unlock count, sections. |
| Next page | `list_items(cursor=<next_cursor>)` | Cursors bind to a state revision. |

## Create and amend

- `create_item(id=..., title=..., priority=..., description=..., needs=[...], acceptance=..., links=..., context=...)`
  — `priority` defaults to `medium`; IDs use `a-z0-9-`; titles are 1–200
  characters. Creation rejects a dependency cycle. Pass `not_before` to
  create a task that waits until a set time.
- `update_item(id=..., ...)` — amends `title` / `priority` / `description` /
  `needs` / sections / `not_before`, or moves `status` between `open` and
  `blocked`. Give a `reason` when the change is not self-evident.
- `update_item(id=..., not_before="2026-10-02T00:00:00Z")` holds an open task
  out of the ready queue until that time; `not_before=""` clears it. Prefer
  this to `blocked` when the wait has a known end.
- `update_item(id=..., context_append="...")` adds a note to the end of the
  current context when the write lands, without resending the existing text.
  It cannot be combined with `context`. Requires todo-db 0.11.0 or later.
- Closing goes through `finish`. Dropping is a human decision reported to the
  user. Once the owner decides, `drop(id=..., reason="...")` records their
  decision and any successor in the same commit as the status change, with no
  separate `context_append`; `reason` is required (todo-db 0.12.0 or later)
  and `drop` also needs the `generation` if the task is claimed.

## Managing output limits

Responses are capped at 16 KiB at their final serialization.

- Page with `limit` + `cursor`. Every page with items remaining carries
  `next_cursor`; an empty page means you are done.
- `E_CURSOR_STALE` means the branch moved under you. Restart without a cursor;
  never skip ahead.
- Large fields arrive as section reads:
  `show_item(id=..., field="description", offset=0, budget=6000)`. Follow
  `continuation` until it disappears; each step is guaranteed to make progress.
- There is no full-dump tool. If the nine tools omit data you need, report the
  gap instead of probing for an export.
