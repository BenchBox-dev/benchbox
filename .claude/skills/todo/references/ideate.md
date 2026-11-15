# Ideate

Refine a raw concept into a clear problem statement and design direction before
creating tracker items.

## Workflow

1. **State the core problem**: Describe the user or system need simply.
   Distinguish the underlying problem from proposed solutions.
2. **Ask decisive questions**: Ask only when the answer changes the
   architecture, scope, or feasibility. Avoid questions the codebase can answer.
3. **Compare viable options**: List two or three concrete approaches with
   direct trade-offs: simplicity, maintenance burden, reversibility,
   performance.
4. **Test assumptions**: Challenge assumptions about platform constraints,
   dependency contracts, and data safety.
5. **Shape candidate tracker fields**: Identify what to prepare for
   `create_item` — candidate IDs and titles (kebab-case, imperative),
   dependencies between candidates, and acceptance criteria for each.
6. **Define boundaries**:
   - **In scope**: the minimal set of changes that solves the core problem.
   - **Out of scope**: explicit non-goals and deferred enhancements.
   - **Open questions**: unknowns requiring spikes or user decisions.

## Before recommending

Apply **L2 and L3** of `shared-review-protocol/SKILL.md`. Record any reframe.
Save only after the user confirms.

## Next step

`create_item` one task per independent piece of work, linked with `needs`.
