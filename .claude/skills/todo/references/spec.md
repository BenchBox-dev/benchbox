# Write a specification

Structure a feature or refactoring plan so it can be ingested directly into the
tracker via `create_item`.

## Structure

A complete specification defines:

1. **Objective**: A clear statement of what the change accomplishes, plus the
   assumptions it rests on and the success criteria that close it.
2. **Prior art** (AGENTS.md): existing patterns by file path,
   and whether this work extends, supersedes, or introduces a new one.
3. **Item breakdown**: Define fields matching the `create_item` schema:
   - `id`: Kebab-case identifier (for example, `mcp-logging-refactor`).
   - `title`: Short imperative sentence, 1–200 characters.
   - `priority`: `critical`, `high`, `medium-high`, `medium`, or `low`.
   - `description`: Context, rationale, and non-obvious constraints.
   - `needs`: IDs of prerequisite tasks, if any.
   - `acceptance`: Observable outcomes that prove the change works.
   - `links`: Related URLs or file paths.
   - `context`: Retained background the implementer will need.

Split work so each item is independently implementable and closable; use
`needs` for ordering, not a work breakdown inside one item.

## Before finalizing

Include a reframe only if it changes the specification. Save only after the
user confirms.
