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
Role `@alias` selectors (for example `@review`) resolve only when that alias
is configured in live OMP settings; otherwise dispatch the role's full
`provider/model` default, with `--thinking auto` on OMP.

## Workload Roles

Roles describe workload fit, not a quality hierarchy. A role never
auto-classifies a prompt, grants permissions, enters plan mode, enables an
advisor, or guarantees independent review on its own.

<!-- routing:begin -->
| Role | Primary | Fallbacks, in order | Use for |
| :--- | :--- | :--- | :--- |
| `default` | `anthropic/claude-sonnet-5-5:high` | `openai-codex/gpt-6.1-sol:medium` → `muse-code/muse-spark-1.3-contributor:high` → `google-antigravity/gemini-3.8-flash:high` | Common implementation and management. |
| `smol` | `openai-codex/gpt-6-luna:xhigh` | `anthropic/claude-sonnet-5-5:medium` → `muse-code/muse-spark-1.3-contributor:medium` → `google-antigravity/gemini-3.8-flash:medium` | Simple, mechanical, and scouting work. |
| `slow` | `anthropic/claude-fable-5:medium` | `openai-codex/gpt-6-astra:high` → `muse-code/muse-wonder-1.0-contributor:xhigh` → `google-antigravity/gemini-4-argon:xhigh` | Maximum complexity and special cases. |
| `plan` | `anthropic/claude-opus-5-5:xhigh` | `openai-codex/gpt-6.1-sol:high` → `muse-code/muse-wonder-1.0-contributor:medium` → `google-antigravity/gemini-4-argon:medium` | Planning and high-capability analysis. |
| `review` | `anthropic/claude-opus-5-5:medium` | `openai-codex/gpt-6.1-sol:high` → `muse-code/muse-wonder-1.0-contributor:medium` → `google-antigravity/gemini-4-argon:medium` | Independent review; filter the author family. |
| `research` | `anthropic/claude-opus-5-5:high` | `openai-codex/gpt-6.1-sol:high` → `muse-code/muse-wonder-1.0-contributor:medium` → `google-antigravity/gemini-4-argon:medium` | High-capability research and synthesis. |
| `task` | `anthropic/claude-sonnet-5-5:medium` | `openai-codex/gpt-6.1-sol:medium` → `muse-code/muse-spark-1.3-contributor:high` → `google-antigravity/gemini-3.8-flash:high` | Common delegated implementation. |
| `commit` | `openai-codex/gpt-6-luna:high` | `anthropic/claude-sonnet-5-5:medium` → `muse-code/muse-spark-1.3-contributor:medium` → `google-antigravity/gemini-3.8-flash:medium` | Commit-message and changelog generation. |
| `vision` | `google-antigravity/gemini-3.8-flash:high` | `openai-codex/gpt-6.1-sol:medium` → `google-antigravity/gemini-3.8-flash:high` → `muse-code/muse-spark-1.3-contributor:medium` | Image-question helpers; not image generation. |
| `advisor` | `anthropic/claude-fable-5:high` | `openai-codex/gpt-6-astra:high` → `muse-code/muse-wonder-1.0-contributor:xhigh` → `google-antigravity/gemini-4-argon:xhigh` | Advisory second opinions when enabled. |
<!-- routing:end -->

Every Muse model MUST be the contributor variant. Never copy an OMP effort
suffix into a non-OMP external CLI model ID; resolve each role or fallback
name to the harness-specific identifier in
[references/external-harnesses.md](references/external-harnesses.md).
Non-OMP external processes do not inherit OMP retry or quota settings. An
external `omp` dispatch runs under the same live local settings (retry,
fallback chains, quota handling) unless a per-run `--config` overlay
constrains it; independent reviewers MUST carry that overlay to disable
cross-model fallback.

Native `task` agent model precedence matters: agent settings can shadow
frontmatter. For a task-specific choice, use the eval bridge's invocation-local
model override or an explicit external command instead of frontmatter alone.
OMP's `task` and `sonic` are agent names, distinct from the `task` model role.
`sonic` is the mechanical worker; routing it to `@smol` changes only its model,
not its instructions or limits. `scout` also uses `@smol`; write-capable
workers use `@task`.

## Role-Local Fallbacks

The table above is generated from the live OMP config by
`scripts/routing_sync.py`; do not edit it by hand. These are portable
defaults, not overrides of a live OMP chain. Keep fallback configuration
role-local; model-wide chains can shadow it. Resolve each model name to the
documented harness identifier.

For `review`, exclude the author's family from every candidate and fail
closed if none qualifies. A Claude author makes a Claude primary ineligible.

## Judgment Role

`judge` is a model-kind role, not a workload role: it selects a judgment
runner (System One API), never a chat model. It takes no effort suffix, does
not appear in the routing table above, and is excluded from
`scripts/routing_sync.py` by design. The portable default is
`typesafe/jev-1.14.0`, with an OpenRouter Jev ID as the fallback route.
Never place a judgment ID in a chat role or fallback chain, and never use
the judge as a reviewer-panel member: it classifies, it does not review.

## Effort Translation

Use the effort the routing table names for the selected role. The table is
generated from the OMP configuration, so it is the only source for each role's
effort; do not substitute a general rule for it, and do not put every role on
`auto`. Maximum effort is only for an explicit final adversarial review.
External harnesses without `auto` use the same concrete effort; that is a fixed
approximation, not adaptive reasoning. External `omp` also uses the role's
concrete `--thinking` value unless the user explicitly requests adaptive
effort.

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
  Worker or Reviewer choices. Headless `omp --print` is Worker/Reviewer only:
  it processes prompts and exits without exposing a Manager's coordination
  channel. `omp --mode rpc` and `acp` may serve a Manager only when the same
  checks pass for the live process; flags alone do not prove the capability.
- **Worker:** use a write-capable mode only within the authorized workspace or
  sandbox. Require the repository's narrowest proving check and explicit-path
  staging; never permit `git add -A`. For external `omp`, scope the Worker to
  an authorized dedicated worktree passed via `--cwd`; `--cwd` selects the
  working directory and is not a sandbox.
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
