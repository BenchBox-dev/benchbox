# Code Compare Reference

## Extract

- Public functions/classes/CLI/API surfaces and inputs/outputs.
- Side effects, errors, validation, persistence, network/filesystem use.
- Dependencies, registration points, config/env vars.
- Control/data flow and performance-sensitive paths.
- Tests or callers proving expected behavior.

## Compare

Use the Compare pairing and verdict in `shared-investigation-framework/SKILL.md`.
For code, the critical items also include a required dependency and every
registration path; a change to any critical item is breaking.

## Output

Report shared and unique behavior, changed contracts, lost relationships,
confidence, and recommended follow-up tests.
