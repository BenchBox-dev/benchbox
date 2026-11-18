# TODO queries and item management

Inspect, filter, and read tracker items with bounded output. The tool
choices are in SKILL.md, "Finding the right tool"; this page covers creation
checks and output limits.

## Create and amend

- `create_item` rejects a dependency cycle.

## Managing output limits

- Page with `limit` + `cursor`. Every page with items remaining carries
  `next_cursor`; an empty page means you are done.
- Large fields arrive as section reads:
  `show_item(id=..., field="description", offset=0, budget=6000)`. Follow
  `continuation` until it disappears; each step is guaranteed to make progress.
- There is no full-dump tool. If the MCP tools omit data you need, report the
  gap instead of probing for an export.
