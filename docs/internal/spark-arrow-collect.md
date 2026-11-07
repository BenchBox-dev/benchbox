# Spark-family result fetch

Spark-family adapters that share `SparkQueryExecutionMixin` (`spark`, `lakesail`, `velox`) receive a query result inside the query timer through one of three paths, chosen at run time from the DataFrame the session returns. The fetch timing contract is in `tpc-measured-intervals.md`: the full result is received inside the interval, and Python rows for digests and validation are built after it.

## Paths

| Order | Condition | Receipt | Recorded `fetch_mode` |
|---|---|---|---|
| 1 | `fetch_mode` is `arrow` and the DataFrame has `toArrow` (pyspark 4.0 and newer, including Spark Connect) | `DataFrame.toArrow()` | `arrow` |
| 2 | `fetch_mode` is `arrow`, no `toArrow`, `toPandas` exists, and `spark.sql.execution.arrow.pyspark.enabled` was set to `true` for the session | Arrow-enabled `toPandas()`, converted to a `pyarrow.Table` typed from the Spark schema | `arrow` |
| 3 | `fetch_mode` is `rows`, or neither path is usable, or the session refused the Arrow configuration | `collect()` | `rows` |

The Arrow configuration is set once per session, before the timer starts.

## Row parity

Rows built from the Arrow table match `collect()` for scalar, decimal, date, timestamp, timestamp without time zone, binary, array, map and struct columns:

- `timestamp` values are converted to the naive local datetime that `collect()` returns. The `toPandas` path reattaches the session time zone first; a wall time that is ambiguous or skipped by a daylight-saving change resolves to the earlier instant.
- Map columns become dictionaries and struct columns become `Row` objects.
- Integer columns that contain nulls keep their Spark integer type on the `toPandas` path.
- A genuine floating-point `NaN` is indistinguishable from null on the `toPandas` path.

## Defaults

| Adapter | Default | Reason |
|---|---|---|
| `spark` | `arrow` when the installed pyspark has `DataFrame.toArrow`, otherwise `rows` | Digest parity run locally on PySpark 4.2.0 with JDK 17 (`toArrow` and `toPandas` paths) |
| `lakesail`, `velox` | `rows` | No local parity run |
| EMR Serverless, Glue, Dataproc, Dataproc Serverless, Fabric Spark, Synapse Spark, Athena Spark, Quanton | `rows`, unchanged | They submit jobs and read serialized results through cloud APIs; no DataFrame is returned to the adapter |
| `snowpark-connect` | `rows`, unchanged | Excluded: Snowpark 1.48.1 has `DataFrame.to_arrow` and its connector gate (`installed_pandas`) passes under pandas 3.1.1 with pyarrow 26, but no Snowflake account is available to execute a query, so no parity run exists |

## Verification record

- PySpark 4.2.0 on JDK 17.0.21 (project environment, pandas 3.0.5, pyarrow 24.0.0): digest and row equality between `collect()` and both Arrow paths for scalar, null, nested, aggregate, empty, duplicate-name and time-zone result shapes. `tests/unit/platforms/test_spark_arrow_fetch.py` repeats this through `_execute_query_spark` against a local session when a compatible Java runtime is present.
- PySpark 3.5.7 on JDK 17.0.21 and Python 3.12 (a scratch environment with pandas 2.2.3, numpy 1.26.4, pyarrow 17.0.0 and setuptools, because PySpark 3.5 does not support pandas 3): the `toPandas` conversion matched `collect()` for the same shapes. The adapter's execute path and PySpark 3.5 with pandas 3 were not run.
- Spark Connect sessions (Databricks Connect 16.1.7 on Spark 3.5, any 4.x client) were not run. Binary columns from a 3.5 Spark Connect session may differ in `bytes` versus `bytearray` from the classic-session behavior that the converters follow.
- Snowpark Connect was not run against an account. In a scratch environment with snowflake-snowpark-python 1.48.1, snowflake-connector-python 4.7.3, pandas 3.1.1 and pyarrow 26.0.1, `DataFrame.to_arrow` exists and the connector's pandas gate reports installed; the `pandas` extra's `pandas<3` pin only affects dependency resolution.
