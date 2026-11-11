# TPC Compliance Reference

Check benchmark implementations against spec expectations.

## Inspect

- Required query count and names.
- Query SQL/text correctness and parameter handling.
- Data generation scale/seed behavior and bundled binary path.
- Load/schema definitions and validation toggles.
- Standards tests and expected artifacts.

## Rules

Use source/spec evidence, not memory. Report missing queries, drift, platform-specific exceptions, and exact verification commands.

## Spec sources

The specifications are bundled in the repository; read them there, not from
memory or the web:

- TPC-H 3.0.1: `_sources/tpc-h/specification.pdf`
- TPC-DS 4.0.0: `_sources/tpc-ds/specification/specification_4.0.0.pdf`
- TPC-DI is not bundled.

Which code intervals each clause times is recorded in
`docs/internal/tpc-measured-intervals.md`.
