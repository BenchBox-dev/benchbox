---
name: agent-execution
description: Select OMP workload roles, map reasoning effort, and dispatch delegated work through native or external agent harnesses. Use when a workflow must choose an agent role or effort level, or launch a manager, worker, or independent reviewer; do not use for direct, undelegated tool calls.
---

# Agent Execution

Use this component when another skill delegates work and needs a consistent
workload role, reasoning effort, or agent harness configuration. The calling skill
continues to own task decomposition, authorization, workspace isolation, and
acceptance criteria.

This skill is a portable fallback for other agents and contexts. It never
overrides live OMP configuration. Precedence is: explicit user choice first,
then live OMP role aliases when they exist, then the portable defaults below.
`review` and `research` are custom OMP aliases, not automatic features: select
them explicitly or bind an agent to them. Portable callers select the workload
role themselves and translate its model through the harness reference.

## Workload Roles

Roles describe workload fit, not a quality hierarchy. A role never
auto-classifies a prompt, grants permissions, enters plan mode, enables an
advisor, or guarantees independent review on its own.

| Role | Portable default (effort `auto`) | Use for |
| :--- | :--- | :--- |
| `default` | `muse-code/muse-wonder-1.0-contributor:auto` | General implementation and management. |
| `smol` | `google-antigravity/gemini-3.8-flash:auto` | Mechanical bulk work, scouting, low-risk coverage. |
| `slow` | `anthropic/claude-fable-5:auto` | Hard reasoning, high-risk tradeoffs. |
| `plan` | `anthropic/claude-fable-5:auto` | Planning, decomposition, architecture. |
| `review` | `openai-codex/gpt-6-astra:auto` | Independent review and adversarial gates. |
| `research` | `google-antigravity/gemini-4-argon:auto` | Complex research and synthesis; use `task` for bounded collection. |
| `task` | `muse-code/muse-spark-1.3-contributor:auto` | Bounded implementation and delegated workers, including mechanical `sonic` work. |
| `commit` | `muse-code/muse-spark-1.3-contributor:auto` | Commit-message and changelog generation, not Git authorization. |
| `vision` | `google-antigravity/gemini-4-argon:auto` | Image-question helpers; not image generation or every attached image. |
| `advisor` | `anthropic/claude-fable-5:auto` | Advisory second opinions. |

Every Muse model MUST be the contributor variant. Never copy an OMP `:auto`
selector into an external CLI model ID; resolve each role or fallback name to
the harness-specific identifier in
[references/external-harnesses.md](references/external-harnesses.md).
External processes do not inherit OMP retry or quota settings.

Native `task` agent model precedence matters: agent settings can shadow
frontmatter. For a task-specific choice, use eval invocation-local selection
or an explicit external command rather than relying on frontmatter alone.

## Role-Local Fallbacks

These are portable fallback defaults, not overrides of a live OMP chain.
Keep fallback configuration role-local; model-wide chains can shadow it.
Resolve each model name to the documented harness identifier.

- `default`: Codex Sol6.1, Fable, Argon.
- `smol`: Spark contributor, Luna.
- `task`, `commit`: Flash, Luna.
- `slow`, `plan`: Astra, Wonder contributor, Argon.
- `review`: Fable, Argon. Exclude the author's family from the primary and
  every fallback; fail closed if none qualifies. The static defaults exclude
  Muse but still need filtering when the author is Claude, Codex, or Gemini.
- `research`, `vision`: Wonder contributor, Sol6.1.
- `advisor`: Astra, Argon.

## Effort Translation

OMP dispatches keep effort `auto`, capped at `xhigh` unless an explicit final
review requires `max`; per-dispatch overrides honor user intent. This `auto`
is not portable: external harnesses without `auto` support need an explicit
concrete effort, which is a manual approximation, not an adaptive equivalent.

- Default to `medium`.
- `low` for mechanical or scouting work (`smol`, bulk coverage).
- `medium` for routine review; `high` for deep planning, adversarial review,
  or complex research and vision (`slow`, `plan`, `review`, `research`, `vision`).
- Maximum effort only when final adversarial review explicitly requires it.

## Dispatch Rules

Treat native subagents and external harnesses as peer dispatch choices. Select
between them using the factors relevant to the assignment: task and model fit,
provider usage or capacity, cost, isolation or read-only strength, and useful
parallelism. Assign an explicit role, bounded goal, path constraints,
permission scope, success criteria, and output contract.

Choose the dispatch mode from the delegated role:

- **Manager:** select a channel only when current runtime capabilities or
  behavioral evidence verify a stable live session identity, resume or
  follow-up, live status and interrupt, and a working path to dispatch or
  coordinate Workers and Reviewers. Do not infer Manager capability from a
  one-shot Worker or Reviewer command. Invocations such as `--print`, `exec`,
  `--single`, or equivalents are not Manager-capable unless a separate verified
  continuation channel supplies every required capability; they remain valid
  Worker or Reviewer choices.
- **Worker:** use a write-capable mode only within the authorized workspace or
  sandbox. Require the repository's narrowest proving check and explicit-path
  staging; never permit `git add -A`.
- **Reviewer:** use a separate dispatch that did not author the work. Prefer a
  hard read-only sandbox or tool allowlist. A plan mode is soft read-only and
  requires explicit findings-only instructions that forbid edits, commits,
  pushes, and other mutations.

For review panels, exclude the author family from every candidate and fail
closed when no eligible model remains. Never silently substitute another model
for a user-named reviewer; report the absence as a gate blocker.

When selecting an external or headless harness, read
[references/external-harnesses.md](references/external-harnesses.md), choose the
documented command for the role, and use it directly.

Headless Worker dispatch may automate confirmations only when write scope is
already bounded by a sandbox, workspace flag, or dedicated worktree. Never add
flags that remove workspace, sandbox, or tool boundaries.

The external harness reference contains the worker and reviewer commands, model
identifiers, and hard-versus-soft read-only classifications.
