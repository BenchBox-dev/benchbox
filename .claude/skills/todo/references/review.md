# Review TODO quality

Evaluate the quality and readiness of a tracker item before or during
implementation. Read the item with `show_item(id=...)`, then apply **L2** of
`shared-review-protocol/SKILL.md`.

## Review checklist

### 1. Description
- Does the description state the problem and the expected outcome?
- Are non-obvious constraints written down, not assumed?

### 2. Acceptance
- Are the acceptance criteria observable and specific?
- Do they verify substantive behavior rather than cosmetic flags?

### 3. Dependencies
- Are all prerequisite items recorded in `needs`?
- Is the item free of circular dependency chains (creation rejects them)?

### 4. Readiness
- Is it listed by `list_items(ready_only=true)`? If not, is the reason (claim,
  unmet need, `not_before` hold, batch reservation) intended?
- If `blocked`, is the reason recorded in the description or context?

Report gaps concretely; fix them with `update_item` when authorized.

## Quoted-document freshness

When the item quotes a living policy, specification, or goal document, re-read
the current text and compare it with the quotes before implementing.
Line-number citations are not durable; report drift as a gap.
