# Hudi and Spark support matrix

`HudiMaintenanceOperations` (`benchbox/platforms/dataframe/hudi_maintenance.py`) runs Hudi writes and reads through a Spark session that loads a Hudi Spark bundle. Hudi 1.1.0 ships bundles per Spark minor line, and a bundle fails on a Spark line it was not built for.

## Supported pairs

| Spark | Bundle (`spark.jars.packages`) |
|---|---|
| 3.5 | `org.apache.hudi:hudi-spark3.5-bundle_2.12:1.1.0` |
| 4.0 | `org.apache.hudi:hudi-spark4.0-bundle_2.13:1.1.0` |

The table is `HUDI_BUNDLES_BY_SPARK` in the module. Constructing `HudiMaintenanceOperations` on a session whose `version` is outside it raises `RuntimeError` that names the Spark version and the supported pairs.

## Unsupported

| Spark | Bundle | Result |
|---|---|---|
| 4.1.1 | `hudi-spark4.0-bundle_2.13:1.1.0` | `HoodieException: Unable to load class`, caused by `ClassNotFoundException: org.apache.spark.sql.hudi.analysis.HoodieSpark33DataSourceV2ToV1Fallback` |
| 4.2.0 | `hudi-spark4.0-bundle_2.13:1.1.0` | Same exception |

## Verification record

Local session (`local[2]`, JDK 17.0.21, Hudi `HoodieSparkSessionExtension` and `HoodieCatalog`): a 10-row insert-operation write to a Hudi table followed by a read, with `collect()` equality on `id`, `name`, `val` and `ts` against the source rows.

| PySpark | Bundle | Outcome |
|---|---|---|
| 3.5.7 | `hudi-spark3.5-bundle_2.12:1.1.0` | Rows equal |
| 4.0.1 | `hudi-spark4.0-bundle_2.13:1.1.0` | Rows equal |
| 4.1.1 | `hudi-spark4.0-bundle_2.13:1.1.0` | Write fails with the exception above |
| 4.2.0 | `hudi-spark4.0-bundle_2.13:1.1.0` | Write fails with the exception above |

Spark 3.3 and 3.4 bundles were not run; BenchBox requires PySpark 3.5 or newer.
