---
name: docs
description: Use when the user asks to "create documentation", "build docs", "review docs", "compare documents", "compress docs", "adversarial review docs", or "commit docs".
version: 0.3.0
tools: Bash, Read, Write, Edit, Task
---

# Docs Workflow

Route the request below and read the selected action file or follow the table
entry. Preserve local markup, navigation, and build conventions.

## Resolve

Use the repository's docs config and Makefile for the builder, source
directory, build/serve/linkcheck/validate commands, markup, and doc locations.

## Actions

| Action | Trigger | Read or do |
|---|---|---|
| `create` | create/add documentation | infer the type (`guide`, `reference`, `tutorial`, or `concept`), add navigation/toctree when needed, then build |
| `build` | build/validate docs | an empty request runs the build; a named input runs the clean, serve, linkcheck, or validate command; report warnings and failures |
| `review` | review/check docs | verify commands and paths where practical; report broken examples, missing prerequisites, outdated CLI flags, accuracy gaps, and organization problems |
| `compare` | compare documents | `shared-investigation-framework/SKILL.md` (Compare section) |
| `shrink` | compress/shrink docs | `shared-investigation-framework/SKILL.md` (Shrink section) |
| `adversarial` | adversarial/user-perspective review | `shared-review-protocol/references/adversarial-review.md` with the requested scope; use a persona the repository names, or `new-user`, `developer`, `ops`, or `contributor`; follow the journey literally and report friction |
| `commit` | commit documentation | `shared-change-framework/SKILL.md` §4 for the changed docs sources: prefix `docs`, verify with the docs build |
| `help` | help/list actions | this table |

## Global rules

- `review`, `adversarial`, and `compare` are read-only under
  `shared-review-protocol/SKILL.md`.
- Repository write actions use the shared change framework's named branch,
  verification, commit, and approved-plan PR workflow. The `commit` action
  handles existing documentation changes; it is not required for other writes.
- Shrink follows the shared framework and preserves frontmatter, commands,
  paths, thresholds, relationships, decisions, and public contracts. Compress
  READMEs, changelogs, decisions, generated docs, or study artifacts only when
  explicitly requested.
