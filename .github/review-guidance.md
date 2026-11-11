# Reviewer guidance

BenchBox conventions for the automated reviewer. They add to the review template's rules for origin, blast radius and one defect per location; they never narrow those rules, and no entry below excludes a gate path, a soundness path or a data path from review.

## Failure modes to catch

- A duration measured with a wall clock. Timing durations use `benchbox.utils.clock.mono_time()` and `elapsed_seconds()`; wall clocks are for event and audit timestamps only.
- An adapter SDK imported at module level. SDK imports stay lazy so a platform that is not installed never breaks import of the others.
- A new `CREATE TABLE` rewrite that bypasses `benchbox.sql_compat.context.Phase.DDL_OPTIMIZE`.
- A CLI dry run that drops an explicit phase, or a deterministic run that takes no seed.
- A comparison, validation or result-digest path that now passes where it used to fail: a loosened tolerance, a skipped row or column, a digest recomputed from the output it validates, or an expected-result or answer-set file regenerated in the same change that consumes it.
- A baseline or lock file edited by hand, or changed without the change that justifies it.
- A claim that a focused or fast test run certifies UAT or production behavior.
- A new explanatory comment or docstring in maintained first-party source. The checker blocks them, so name the line and ask for clearer names or structure instead.

## Do not flag

- A missing docstring or explanatory comment in Python or other first-party source. BenchBox has a no-source-docstring policy: contracts live in API docs, and the comment-policy registry lists the few directives, notices and fixtures that must stay.
- A missing, short or oddly worded `changelog.d/` fragment, or a PR that does not edit `CHANGELOG.md`. Fragments are JSON files in `changelog.d/`; `CHANGELOG.md` is never edited by hand, and a fragment is required only when a changed path ships in the package.
- The names `oracle` (environment), `ORACLE_*` (secrets), `tag:oracle-ci` and the mirrored `oracle-gate` and `oracle-verdict` status contexts. They remain on purpose until separate tracked work renames them. This covers the names only: still flag a change that hands those secrets to another job or step, or loosens the environment.
- Individual lines of `uv.lock` and of the generated documentation reference pages. Judge the change or generator that produced them, and still flag a dependency change with no matching source change.
- Formatting, import order, line length and type-hint style that Ruff or the pre-commit hooks already enforce.
- Commit author, committer or attribution trailers. Hooks and CI check actual commits.
- PR size, branch layout, stack depth or tracker state.
