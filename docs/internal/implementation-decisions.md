# Implementation decisions behind the public contracts

Maintainer notes split out of `docs/reference/public-contracts.md`: where
behaviour lives in the code and how the SQL compatibility catalog is governed.

## SQL Compatibility Governance Decision

`sql_compat` is a governance catalog. CREATE TABLE rewriting is performed by the
SQLGlot fork through `translate_ddl`; adapters supply the options and the
benchmark-owned key tables.

`governance_only=True` rules are allowed to represent real runtime behavior.
They are not dispatch targets; they are the auditable source of intent that the
drift checker requires before CI can call DDL governance clean. `compat_lint CLEAN`
means the source scanner found no unregistered or uninspectable adapter
CREATE TABLE rewrite behavior. It does not prove semantic equivalence of the
rewrite output.

## DataFrame Runner Lifecycle Decision

Production DataFrame execution is `run_benchmark_lifecycle()` ->
`adapter.run_benchmark()` -> `BenchmarkExecutionMixin.run_benchmark()`.
The helpers `dataframe_compliance_class` and `no_dataframe_queries_message`
live in `benchbox/platforms/dataframe/benchmark_mixin.py`, and the DataFrame
mode predicate lives in `benchbox.core.run_service` beside run-plan
resolution. New lifecycle behavior belongs in
`benchbox/platforms/dataframe/benchmark_mixin.py`.
