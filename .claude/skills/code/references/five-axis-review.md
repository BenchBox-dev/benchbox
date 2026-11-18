# Five-Axis Code Review Reference

Evaluate code on five axes and classify findings by severity. This file adds a
code rubric only; `shared-review-protocol/SKILL.md` controls authorization,
planning depth, defect handling, capture, and remediation.

## Router-specific checks

- Accept a file, directory, staged diff, commit range, PR, topic, or repository.
  List Critical, Required, Nit, and Consider findings first.
- For a PR, record the live head SHA and check status before content review.
  Classify a failing required check as Critical when the change caused it;
  distinguish infrastructure failures and pending protected gates explicitly.
- For multi-PR work, run `gh pr diff <N> --name-only` and perform that blocker
  triage for every PR; fetch `gh pr view <N> --json body,files` only when
  needed.
- Load project-specific checks only from `code.review_checklist` when
  `skill-sync.config.yaml` configures it. Do not add them here.
- A review-only request stays read-only under [REVIEW-AUTH-001]; a request that
  explicitly asks for review and fix does both in that turn, fixing only
  non-structural issues, then verifies and follows the commit framework.

## The Five Axes

### 1. Correctness
- Does the code meet the specification and task?
- Does it handle null, empty, boundary, and error cases?
- Do tests cover the correct behavior?
- Are there off-by-one errors, races, or inconsistent states?

### 2. Readability & Simplicity
- Are names clear and consistent with project conventions?
- Is control flow direct, without nested ternaries or deep callbacks?
- Does each abstraction justify its complexity?
- Can dead code, compatibility shims, or stale comments be removed?

**Deletion checks:** Apply `shared-change-framework/SKILL.md` Section 1.
- For each finding, report `file:line`, what to cut, and its replacement.

### 3. Architecture
- Does the change follow existing patterns or justify a new one?
- Are module boundaries clean and free of circular dependencies?
- Should duplicated code be shared?
- Is the abstraction neither over-engineered nor tightly coupled?

### 4. Security
- Is user input validated at boundaries?
- Are secrets absent from code, logs, and version control?
- Is SQL parameterized rather than concatenated?
- Is external data treated as untrusted?

### 5. Performance
- Are there N+1 queries, unbounded loops, or unconstrained fetches?
- Are large objects used in hot paths?
- Should synchronous work be asynchronous?

## Severity Classification

| Prefix | Meaning | Author Action |
|--------|---------|---------------|
| **Critical** | Blocks merge -- security, data loss, broken functionality | Must fix |
| *(none)* | Required change | Must address |
| **Nit** | Minor, optional | May ignore |
| **Consider** | Suggestion worth evaluating | Not required |

## Change Sizing

- About 100 lines: reviewable in one sitting.
- About 300 lines: acceptable for one logical change.
- About 1,000 lines: too large; split it.

## Rules

- Include "What's Done Well"; criticism-only reviews are incomplete.
- Separate refactoring from feature work.
- Approve an imperfect change when it clearly improves code health.
- Support approval with evidence; do not rubber-stamp with "LGTM."

## Branches

Apply a branch only when its trigger matches.

### Matrix/audit-doc branch
**Trigger:** An audit, curation, or inventory document built around numeric
tables.

- Regenerate and diff every numeric claim from source.
- For policy-gated recommendations, include an "Alternatives considered"
  section and quantify it.

### Mixed tooling+data branch
**Trigger:** A PR combines tooling artifacts with fixtures, JSON, bundles, or
other data artifacts.

- Assess each component's reversibility.
- If tooling hides a data defect, require the upstream fix to be named as a
  follow-up.

### Defect follow-up branch
**Trigger:** One orchestration phase parses artifacts from another.

- Confirm parsed files came from the current invocation.
- Flag stale-file reuse, including `os.path.exists` skips and cached-result
  short-circuits.

### Verification-only branch
**Trigger:** A verification-only PR or a commit that claims evidence without a
durable artifact.

- Require durable, replayable evidence under the project's convention: a
  committed command with a pinned SHA or version and a PASS/FAIL result. A
  referenced retained CI artifact may accompany that pin. Raw logs need not be
  committed.
- Reject claims supported only by transient terminal output.
