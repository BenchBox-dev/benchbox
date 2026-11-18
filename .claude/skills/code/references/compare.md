# Code Compare Reference

## Extract

- Public functions/classes/CLI/API surfaces and inputs/outputs.
- Side effects, errors, validation, persistence, network/filesystem use.
- Dependencies, registration points, config/env vars.
- Control/data flow and performance-sensitive paths.
- Tests or callers proving expected behavior.

## Compare

Use the Compare scoring in `shared-investigation-framework/SKILL.md`. Mark a
change as breaking if it alters a public contract, error behavior, persistence
format, security property, or required dependency. Missing relationships or
registration paths are high risk.

## Output

Report shared and unique behavior, changed contracts, lost relationships,
confidence, and recommended follow-up tests.
