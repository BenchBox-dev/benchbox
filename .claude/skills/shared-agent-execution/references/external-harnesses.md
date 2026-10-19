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
an OMP thinking suffix such as `:auto` into a CLI `--model` argument.

### Model identifiers

| OMP selector | External harness | CLI model ID |
|---|---|---|
| `muse-code/muse-wonder-1.0-contributor` | `muse` | `muse-wonder-1.0-contributor` |
| `muse-code/muse-spark-1.3-contributor` | `muse` | `muse-spark-1.3-contributor` |
| `anthropic/claude-fable-5` | `claude` | `claude-fable-5` |
| `openai-codex/gpt-6-astra` | `codex` | `gpt-6-astra` |
| `openai-codex/gpt-6.1-sol` | `codex` | `gpt-6.1-sol` |
| `openai-codex/gpt-6-luna` | `codex` | `gpt-6-luna` |
| `google-antigravity/gemini-3.8-flash` | `agy` | `gemini-3.8-flash-low`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-high` |
| `google-antigravity/gemini-4-argon` | `agy` | `gemini-4-argon-high` (recognized; completion unverified) |

Only contributor-variant Muse models are selected. Argon's OMP support does
not prove external CLI completion support. Other harnesses have separate
catalogs; do not infer that stripping a provider prefix makes a valid ID.

### Effort translation

Do not assume an external CLI implements OMP's adaptive `auto`. For the
commands below, use the concrete effort selected by Agent Execution:
`medium` normally (including routine review), `low` for mechanical/scouting
work, and `high` for deep planning, adversarial review, or complex research
and vision. This is a fixed approximation, not adaptive reasoning. Explicit
user effort overrides win.

| Harness | Effort argument | Supported values |
|---|---|---|
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

External processes do not inherit OMP retry or quota settings. For ordinary
dispatches, attempt the role's eligible fallbacks explicitly. For a named
reviewer panel, report a failed member as absent rather than silently
replacing it. Filter the author family from every independent-review
candidate, including the primary, and stop if no eligible candidate remains.

Model roles do not grant permissions, enter plan mode, or enable an advisor.
Those capabilities require the command boundaries and dispatch modes below.

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
  - Known-good models: `openai-codex/gpt-6-astra`, `openai-codex/gpt-6-sol`, `openai-codex/gpt-6-luna`, `anthropic/claude-fable-5`, `anthropic/claude-opus-5-5`, `anthropic/claude-sonnet-5`, `xai/grok-4.6`, `xai/grok-4.5`, `muse-spark/muse-spark-1.2-contributor`
  - Note: pi's catalog lags the native CLIs. Use only IDs that
    `pi --list-models` reports.
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
| Soft Read-Only | `agy --mode plan`, `grok --permission-mode plan`, `opencode --agent plan`, `goose review`, `aider --chat-mode ask` | A dedicated detached worktree at the reviewed revision. |

A Soft Read-Only mode is an instruction, not an enforced sandbox. Never run
two Soft Read-Only members, or a Soft Read-Only member and any writer, in the
same worktree at the same time. Create the isolation worktree from the exact
revision under review and remove it after the panel reports:

```bash
# Create isolation worktree outside the repository to prevent nesting and collisions:
REVIEW_WT=$(mktemp -d "${TMPDIR:-/tmp}/review-wt.XXXXXX")
git -C "$WORKSPACE" worktree add --detach "$REVIEW_WT" "$REVISION"

# Dispatch Soft Read-Only member in background in its own process group:
set -m
(cd "$REVIEW_WT" && ...) &
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
does not add an independent viewpoint. When the author is a Muse model,
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
