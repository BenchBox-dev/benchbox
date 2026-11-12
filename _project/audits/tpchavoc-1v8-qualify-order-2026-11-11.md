---
develop_sha: de061b539cfeb2a271b9a06341654d50103a3174
checked_sha: 9df749a8649e0d78f097fb4da0451fd3b68f9009
measured_at_sha: 9df749a8649e0d78f097fb4da0451fd3b68f9009
---

# TPC-Havoc 1_v8 after the QUALIFY order fix

Follow-up to `tpchavoc-prestodb-variants-2026-10-26.md`, which recorded that the
rendered `1_v8` returned the right rows in a different order on PrestoDB.

The fix is in BenchBox, not in the SQLGlot fork: the `hoist_qualify_order_by`
rewrite in `benchbox/utils/dialect_utils.py` runs before generation for the
`presto`, `trino` and `athena` targets, performs the `QUALIFY` elimination
itself and puts the original sort keys on the outermost `SELECT`. The vendored
wheel is unchanged and the inner `ORDER BY` is kept. Rendered `1_v8` now ends
`... AS "_t" WHERE "_w" > 0 ORDER BY "l_returnflag", "l_linestatus"` for all
three targets (unit tests in `tests/unit/utils/test_qualify_order_by_rewrite.py`).

Measurement: PrestoDB 0.298.1 (Java, one node, `tpch.tiny`), harness
`python -m _project.scripts.tpchavoc_presto_variant_check variants --only 1_v8 1`,
BenchBox base revision 9df749a86 plus the uncommitted rewrite. The canonical
`Q1` and `1_v8` were both `PASS`, compared in order (`compare: ordered`),
with the largest relative difference 5.9e-15 for `1_v8`. The earlier audit
recorded `ORDER_DIFFERS` on every execution of `1_v8`.

Limits: one execution, on PrestoDB only. Trino and Athena were not run.
