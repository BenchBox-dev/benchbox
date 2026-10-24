# External Harnesses

Use these known-good direct configurations whenever an external harness is
selected. Choose the documented command for the delegated role and use it
directly. Only after an actual command failure may reactive diagnosis use
`command -v` and the installed `--help` output to distinguish a missing binary
from flag drift. Do not run those checks proactively.

- Worker commands require already-authorized write scope bounded by a sandbox,
  workspace, or dedicated worktree. Confirmation automation in a documented
  Worker command is allowed only within that bounded scope.
- Reviewer commands use the declared **Hard Read-Only** or **Soft Read-Only**
  classification. Reinforce Soft Read-Only with findings-only instructions that
  forbid edits, commits, pushes, and other mutations.
- Do not add flags that remove workspace, sandbox, or tool boundaries.

## Roles, CLI IDs, and effort

Use the workload roles and fallback order in [Agent Execution](../SKILL.md).
Live OMP assignments take precedence over those portable defaults. Resolve
the selected model to the external harness's ID before dispatch; do not copy
an OMP thinking suffix such as `:auto` into a non-OMP CLI `--model` argument.
External `omp` is the exception: it accepts OMP selectors natively, including
`--thinking auto` and a configured `@alias` (see the OMP section below).

### Model identifiers

| OMP selector | External harness | CLI model ID |
|---|---|---|
| `muse-code/muse-wonder-1.0-contributor` | `muse` | `muse-wonder-1.0-contributor` |
| `muse-code/muse-spark-1.3-contributor` | `muse` | `muse-spark-1.3-contributor` |
| `anthropic/claude-fable-5` | `claude` | `claude-fable-5` |
| `anthropic/claude-opus-5-5` | `claude` | `claude-opus-5-5` |
| `anthropic/claude-sonnet-5-5` | `claude` | `claude-sonnet-5-5` |
| `anthropic/claude-haiku-5-5` | `claude` | `claude-haiku-5-5` |
| `openai-codex/gpt-6-astra` | `codex` | `gpt-6-astra` |
| `openai-codex/gpt-6.1-sol` | `codex` | `gpt-6.1-sol` |
| `openai-codex/gpt-6-luna` | `codex` | `gpt-6-luna` |
| `google-antigravity/gemini-3.8-flash` | `agy` | `gemini-3.8-flash-low`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-high` |
| `google-antigravity/gemini-4-argon` | `agy` | `gemini-4-argon-high` (recognized; completion unverified) |

Only contributor-variant Muse models are selected. Argon's OMP support does
not prove external CLI completion support. Other harnesses have separate
catalogs; do not infer that stripping a provider prefix makes a valid ID.

### Judgment runners

The `judge` role uses Jev over a judgment API, not any CLI below. Primary
`typesafe/jev-1.14.0` (direct TypeSafe route); fallback is the OpenRouter
Jev ID on the same version. No effort flag or suffix applies, and chat
fallback, quota, and reviewer-panel rules do not transfer. Image-carrying
judgments are experimental: prefer text-only state.

### Effort translation

Do not assume a non-OMP external CLI implements adaptive effort. Use the
concrete effort the routing table in `SKILL.md` names for the selected role.
This is a fixed approximation, not adaptive reasoning. Explicit user effort
overrides win. External `omp` uses the same concrete `--thinking` value unless
the user explicitly requests adaptive effort.

| Harness | Effort argument | Supported values |
|---|---|---|
| `omp` | `--thinking "$EFFORT"` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `auto` (`auto` only here) |
| `codex` | `-c model_reasoning_effort="$EFFORT"` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `claude` | `--effort "$EFFORT"` | `low`, `medium`, `high`, `xhigh`, `max` |
| `muse` | `--reasoning-effort "$EFFORT"` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `ultra` |
| `agy` | Model suffix; omit `--effort` | `-low`, `-medium`, `-high` where the model lists that variant |
| `grok` | `--reasoning-effort "$EFFORT"` | `low`, `medium`, `high`, `xhigh` |
| `pi`, `prime-agent` | `--thinking "$EFFORT"` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |

Select only values supported by the chosen model, not merely its harness.
Maximum effort is reserved for an explicitly required final adversarial
review; use the chosen model's documented maximum. `agy` never gains an
invented maximum suffix: its highest listed variant is `-high`.

Non-OMP external processes do not inherit OMP retry or quota settings. An
external `omp` dispatch DOES run under the live local settings (retry,
fallback chains, quota handling): constrain it per run with a `--config`
overlay (see the OMP section below). For ordinary
dispatches, attempt the role's eligible fallbacks explicitly. For a named
reviewer panel, report a failed member as absent rather than silently
replacing it. Filter the author family from every independent-review
candidate, including the primary and every fallback hop, and stop if no
eligible candidate remains.

Model roles do not grant permissions, enter plan mode, or enable an advisor.
Those capabilities require the command boundaries and dispatch modes below.

## OMP (first-class external harness)

External `omp` is OMP itself invoked headless from another process: same model
catalog, same role names, same native `--thinking auto`, and the same live
local settings (retry, fallback chains, quota handling). It is separate from
`pi`: never substitute one for the other. Precedence is explicit user choice,
then live OMP settings, then the portable role defaults in Agent Execution.

### Model identifiers and role aliases

- `$MODEL` is an OMP selector: a configured `@alias` (for example `@review`,
  `@task`) or a full `provider/model` ID (for example
  `openai-codex/gpt-6-astra`).
- `@alias` selectors resolve only when that alias is configured in live OMP
  `modelRoles`. When the alias is absent, dispatch the role's full
  `provider/model` portable default instead of failing or guessing.
- Every Muse model MUST be the contributor variant.
- Independent reviewers disable the advisor in their overlay, rather than
  relying on the caller's current setting or omitting `--advisor`.

### Commands

- **Worker (Write):**
  `omp --cwd "$WORKSPACE" --print --model "$MODEL" --thinking "$EFFORT" --no-title --no-session --max-time "$TIMEOUT" "$PROMPT"`
  - `$WORKSPACE` MUST be an authorized dedicated worktree. `--cwd` selects the
    working directory; it is not a sandbox, so scope and authorization come
    from the worktree itself.
  - Confirmation automation (`--approval-mode`, `--auto-approve`) is allowed
    only within that already-bounded write scope.
- **Reviewer (Soft Read-Only, neutral launch directory and isolation overlay):**
  `omp --cwd "$REVIEW_CWD" --add-dir "$WORKSPACE" --print --model "$MODEL" --thinking "$EFFORT" --tools read,grep,glob --no-extensions --no-lsp --no-skills --no-rules --approval-mode always-ask --no-title --no-session --config "$OVERLAY" --max-time "$TIMEOUT" "$PROMPT"`
  - `$REVIEW_CWD` is a clean, caller-owned temporary directory outside the
    reviewed checkout. `$WORKSPACE` is a dedicated detached worktree at the
    reviewed revision, provided only as an additional read workspace. Use
    absolute paths in the brief.
  - `$OVERLAY` is a trusted, caller-owned private file outside the reviewed
    checkout. It pins these safety settings above project and global config:
    ```yaml
    mcp:
      enableProjectConfig: false
    advisor:
      enabled: false
    retry:
      modelFallback: false
      usageAwareFallback: false
    contextPromotion:
      enabled: false
    prewalk:
      enabled: false
      auto: "off"
    ```
  - Project custom-tool modules can execute during startup before tool
    filtering, and can widen the visible tool set. Never launch a reviewer
    with the reviewed checkout as `--cwd`, even with the overlay. Keeping
    it only in `--add-dir` avoids its ambient tool, extension, and MCP
    discovery. Re-verify that boundary after runtime discovery changes.
  - The overlay also excludes project MCP configuration, but neither it
    nor `--no-extensions` is a process or filesystem sandbox.
  - Cross-model retry, proactive quota routing, context promotion, prewalk,
    and the advisor cannot introduce a second model into this review. If
    the selected model fails, report it absent and let the caller select
    the next author-independent candidate.
  - `--no-title` avoids title generation, `--no-session` avoids persisting
    review history, and `--max-time` bounds the run.
- Without the neutral directory, trusted overlay, and tool-inventory check,
  do not use OMP for repository review. A detached worktree alone cannot
  prevent arbitrary startup code from executing. Use another documented
  reviewer harness if the required setup cannot be established.

### Invocation limits and Manager boundary

- Headless `omp --print` processes prompts and exits. It does not expose the
  continuation and coordination channel required for a Manager.
- `omp --mode rpc` and `acp` are continuation channels, not Manager proof.
  Use them for a Manager only after verifying identity, resume, live status,
  interrupt, and worker/reviewer coordination for that process. RPC goals
  do not continue unless `goal.continuationModes` includes `"rpc"`.
- Tool restriction is not a process or filesystem sandbox.

### Reviewer tool-inventory preflight

Before the one-shot `--print` review, run this RPC probe with the same
neutral directory, additional workspace, model, effort, and safety overlay.
Proceed only when the executable check prints `true`. If the process ends
silently, diagnose transport or overlay errors before dispatch.

```bash
review_tools=$(printf '%s\n' '{"id":"review-tools","type":"get_state"}' |
  omp --mode rpc --no-ui --cwd "$REVIEW_CWD" --add-dir "$WORKSPACE" \
    --model "$MODEL" --thinking "$EFFORT" --tools read,grep,glob \
    --no-extensions --no-lsp --no-skills --no-rules \
    --approval-mode always-ask --no-title --no-session \
    --config "$OVERLAY" --max-time "$TIMEOUT" |
  jq -r 'select(.id == "review-tools") | [.data.dumpTools[].name] | sort | join(",")')
[ "$review_tools" = "glob,grep,read" ] || {
    echo "unexpected reviewer tools: $review_tools" >&2
    exit 2
}
printf '%s\n' true
```

## Frontier Lab Harnesses

- **codex**
  - Worker (Write): `codex exec -C "$WORKSPACE" --model "$MODEL" --sandbox workspace-write -c model_reasoning_effort="$EFFORT" "$PROMPT"`
  - Reviewer (Hard Read-Only): `codex exec -C "$WORKSPACE" --model "$MODEL" --sandbox read-only -c model_reasoning_effort="$EFFORT" "$PROMPT"`
  - Known-good models: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-luna`
- **claude**
  - Worker (Write): `(cd "$WORKSPACE" && claude --print --model "$MODEL" --effort "$EFFORT" "$PROMPT")`
  - Reviewer (Hard Read-Only): `(cd "$WORKSPACE" && claude --print --tools Read,Grep,Glob --model "$MODEL" --effort "$EFFORT" "$PROMPT")`
  - Known-good models: `claude-fable-5`, `claude-opus-5-5`, `claude-sonnet-5-5`
- **agy**
  - Worker (Write): `(cd "$WORKSPACE" && agy --model "$MODEL" --print="$PROMPT")`
  - Reviewer (Soft Read-Only): `(cd "$WORKSPACE" && agy --model "$MODEL" --mode plan --print="$PROMPT")`
  - Known-good models: `gemini-3.8-flash-high`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-low`
  - Note: Effort is the `-low` / `-medium` / `-high` model suffix. Never pass
    `--effort` alongside a suffixed model: `agy` rejects the pair
    (`--model X conflicts with --effort=Y`), including `--effort max`.
  - Recognized model ID: `gemini-4-argon-high`. Model selection accepts it,
    but external completion remains unverified. Report a blocked response
    and apply the dispatch's explicit fallback or named-reviewer failure
    policy; do not treat native OMP completion as proof for this harness.
  - Note: Headless `--print` runs cannot approve tool use. A prompt that needs
    the reviewer to run commands (read the repo, run `git`) is auto-denied and
    returns nothing with exit 0, so keep `agy` prompts self-contained with the
    evidence inline. Live repo access needs a pre-authorized allow-rule in
    `settings.json`; never `--dangerously-skip-permissions` on a Reviewer.
- **grok**
  - Worker (Write): `grok --cwd "$WORKSPACE" --single "$PROMPT" --model "$MODEL" --reasoning-effort "$EFFORT"`
  - Reviewer (Soft Read-Only): `grok --cwd "$WORKSPACE" --single "$PROMPT" --model "$MODEL" --reasoning-effort "$EFFORT" --permission-mode plan`
  - Known-good models: `grok-4.6`, `grok-4.5`
- **muse**
  - Worker (Write): `muse exec --workspace "$WORKSPACE" --disable-approval --model "$MODEL" --reasoning-effort "$EFFORT" "$PROMPT"`
  - Reviewer (Hard Read-Only): `muse exec --workspace "$WORKSPACE" --disable-approval --disable-write --disable-shell --model "$MODEL" --reasoning-effort "$EFFORT" "$PROMPT"`
  - Known-good models: `muse-wonder-1.0-contributor`, `muse-spark-1.3-contributor`
  - Only contributor variants are selected; non-contributor Muse IDs are
    outside this policy.
  - Note: Unset invalid credentials with `env -u META_API_KEY` before execution.

## Extensible and Community Harnesses

- **pi**
  - Worker (Write): `(cd "$WORKSPACE" && pi --print --model "$MODEL" --thinking "$EFFORT" "$PROMPT")`
  - Reviewer (Hard Read-Only): `(cd "$WORKSPACE" && pi --print --tools read,grep,find,ls --model "$MODEL" --thinking "$EFFORT" "$PROMPT")`
  - Catalog-listed role models: `anthropic/claude-fable-5`,
    `meta/muse-spark-1.3-contributor`. Model listing is not completion or
    authentication proof; use only identifiers reported by the installed
    `pi --list-models`.
  - Do not infer an OMP provider prefix. A catalog that lists GPT models under
    `openai` does not establish a subscription-backed `openai-codex` route.
    If the required coding-plan model is absent, use its native harness
    rather than silently switching to a billable API provider. Do not replace
    an unavailable Wonder contributor with the non-contributor Wonder entry.
- **jcode**
  - Worker (Write): `jcode run -C "$WORKSPACE" --model "$MODEL" "$PROMPT"`
  - Reviewer (Hard Read-Only): `jcode run -C "$WORKSPACE" --disable-base-tools --tools read --model "$MODEL" "$PROMPT"`
  - Known-good models: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-luna`, `claude-fable-5`, `claude-opus-5-5`, `claude-sonnet-5-5`, `gemini-3.8-flash-tiered`, `muse-spark-1.3-contributor`
- **goose**
  - Worker (Write): `(cd "$WORKSPACE" && goose run --text "$PROMPT" --no-session --provider "$PROVIDER" --model "$MODEL")`
  - Reviewer (Soft Read-Only): `(cd "$WORKSPACE" && goose review --prompt "$CRITERIA_FILE" --model "$MODEL")`
- **prime-agent**
  - Worker (Write): `prime-agent -p --cwd "$WORKSPACE" --provider "$PROVIDER" --model "$MODEL" --thinking "$EFFORT" "$PROMPT"`
  - Reviewer (Hard Read-Only): `prime-agent -p --tools read,grep,find,ls --cwd "$WORKSPACE" --provider "$PROVIDER" --model "$MODEL" --thinking "$EFFORT" "$PROMPT"`
- **opencode**
  - Worker (Write): `(cd "$WORKSPACE" && opencode run -m "$MODEL" "$PROMPT")`
  - Reviewer (Soft Read-Only): `(cd "$WORKSPACE" && opencode run --agent plan -m "$MODEL" "$PROMPT")`
  - Note: Model format is `<provider>/<model>`.
- **hermes**
  - Worker (Write): `(cd "$WORKSPACE" && hermes chat -q "$PROMPT")`
  - Reviewer (Hard Read-Only): `(cd "$WORKSPACE" && hermes chat -q --tools read,search "$PROMPT")`
- **aider**
  - Worker (Write): `(cd "$WORKSPACE" && aider --model "$MODEL" --message "$PROMPT" --yes-always --no-auto-commits)`
  - Reviewer (Soft Read-Only): `(cd "$WORKSPACE" && aider --model "$MODEL" --message "$PROMPT" --chat-mode ask)`

## Reviewer panels

A panel is several Reviewer dispatches over one revision, used when the user
asks for independent cross-model critique or when one harness is rate-limited.
Each panel member follows the Reviewer rules above; the panel adds isolation,
diversity, and delivery requirements.

### Isolation

Classify each member before dispatch:

| Classification | Harnesses | Workspace |
|---|---|---|
| Hard Read-Only | `codex --sandbox read-only`, `claude --tools Read,Grep,Glob`, `muse --disable-write --disable-shell`, `pi --tools read,...`, `jcode --tools read`, `prime-agent --tools read,...`, `hermes --tools read,search` | The reviewed worktree is acceptable. |
| Soft Read-Only | `agy --mode plan`, `grok --permission-mode plan`, `opencode --agent plan`, `goose review`, `aider --chat-mode ask`, `omp` with the neutral launch directory, trusted overlay, and verified read-only tool inventory | A dedicated detached worktree at the reviewed revision. |

A Soft Read-Only mode is an instruction, not an enforced sandbox. Never run
two Soft Read-Only members, or a Soft Read-Only member and any writer, in the
same worktree at the same time. Create the isolation worktree from the exact
revision under review and remove it after the panel reports. Set `HARNESS`
to the chosen harness and build `REVIEW_COMMAND` as a Bash argument array
from its documented command after creating `REVIEW_WT`. For OMP, that array
must use the clean `REVIEW_CWD` as `--cwd` and `REVIEW_WT` as `--add-dir`;
complete its tool-inventory preflight before dispatch.

```bash
# Create isolation worktree outside the repository to prevent nesting and collisions:
REVIEW_WT=$(mktemp -d "${TMPDIR:-/tmp}/review-wt.XXXXXX")
git -C "$WORKSPACE" worktree add --detach "$REVIEW_WT" "$REVISION"

# Dispatch Soft Read-Only member in background in its own process group:
set -m
LAUNCH_CWD="$REVIEW_WT"
if [ "$HARNESS" = omp ]; then
    LAUNCH_CWD="$REVIEW_CWD"
fi
[ "${#REVIEW_COMMAND[@]}" -gt 0 ] || exit 2
(cd "$LAUNCH_CWD" && "${REVIEW_COMMAND[@]}") &
REVIEW_PID=$!
set +m

# Wait with timeout or handle termination:
# wait "$REVIEW_PID" || true

# Terminate entire process tree before worktree removal to prevent file descriptor races:
if [ -n "$REVIEW_PID" ] && kill -0 "$REVIEW_PID" 2>/dev/null; then
    PGID=$(ps -o pgid= -p "$REVIEW_PID" 2>/dev/null | tr -d ' ')
    if [ -n "$PGID" ]; then
        kill -TERM "-$PGID" 2>/dev/null || true
        for _ in $(seq 1 5); do
            kill -0 "-$PGID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "-$PGID" 2>/dev/null || true
    fi
    wait "$REVIEW_PID" 2>/dev/null || true
fi

# Verify the reviewer respected read-only boundaries before cleanup:
if [ -n "$(git -C "$REVIEW_WT" status --porcelain 2>/dev/null)" ]; then
    echo "Warning: Soft Read-Only member modified files in $REVIEW_WT; dropping its findings."
fi

git -C "$WORKSPACE" worktree remove --force "$REVIEW_WT"
```

Members must confine their working directory and tool writes to the worktree
they were given; read-only inputs (brief path, diff patch) may reside outside
the worktree. A member that cannot be constrained to findings-only output or
that writes files to the worktree is dropped from the panel rather than
re-dispatched with weaker boundaries.

### Diversity

Exclude models from the authoring agent's own family from every candidate: the
role default, each fallback, and any user-suggested substitute. Self-review
does not add an independent viewpoint. This filter covers live retry hops, not
just the primary: an external `omp` reviewer inherits the global
`retry.fallbackChains`, whose entries can belong to the author's family even
when the primary is safe, so the reviewer overlay MUST set
`retry.modelFallback: false` and a failed reviewer is reported absent rather
than retried into an excluded family. When the author is a Muse model,
`muse` members are ineligible, so the `review` chain reduces to `codex`
`gpt-6-astra` then `claude` `claude-fable-5` then `agy` `gemini-4-argon-high`;
the same family filter applies when the author is a Claude, Codex, or Gemini
model. Fail closed: if the filter leaves no eligible model, report the panel
as blocked instead of reviewing with an excluded family member.

Match effort to the work: `high` for deep or adversarial review, `medium` for
routine review, `maximum` only when the user explicitly requires a final
adversarial gate. A user-named reviewer or a user-set effort overrides these
defaults, but a user-named reviewer from the author's own family is still
ineligible: report it as a gate blocker and offer a substitute rather than
running it.

### Brief delivery

An external harness cannot read the dispatching agent's memory or an unsaved
chat plan. Serialize what the member must judge to an atomically created,
private file and pass the path:

```bash
BRIEF=$(mktemp "${TMPDIR:-/tmp}/review-brief.XXXXXX")
chmod 0600 "$BRIEF"
# write brief content to "$BRIEF"
# pass "$BRIEF" to reviewer commands
# clean up when panel concludes:
rm -f "$BRIEF"
```

The brief states the requested outcome, the exact revisions or paths under
review, the constraints that bind the work, and the output contract (severity
table with `file:line` evidence, per the adversarial-review reference in
`shared-review-protocol`). It does not contain the author's preferred
conclusions.

### Failure and quorum

Dispatch members in parallel and bound each with a timeout. A member that
fails, crashes, times out, or is excluded for safety is reported as absent.
A member that completes its review and reports no defects (verdict Ship, empty
severity table) is a clean pass, not an absent reviewer. State which members
reported and which did not. When the user asked for a specific named reviewer
who is absent, report this missing reviewer as a gate blocker. Do not silently
substitute another model; offer the substitution to the user, and report
findings from the remaining reviewers who responded.

Attribution, consensus, and dissent handling for the merged report are owned by
`shared-review-protocol/references/adversarial-review.md`.
