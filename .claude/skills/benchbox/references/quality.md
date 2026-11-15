# BenchBox Quality Reference

Run `make ci-lint` and `make ci-test` (the ci.yml code-lint and code-test
equivalents), or `make ci-local` for every CI unit.

## Checks

- `make ci-lint` covers lint, format, typecheck, timing policy, and DDL drift
  (`compat-inventory-check`).
- `make test-fast` for fast tests.

## Output

Summarize pass/fail per check, failures with file/line when available, skipped checks and why, and recommended fix order.
