# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from benchbox.base import BaseBenchmark

import yaml

from benchbox.core.deployment import configured_deployment
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.builder import normalize_benchmark_id
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.core.schemas import (
    BenchmarkConfig,
    DatabaseConfig,
    DryRunResult,
    SystemProfile,
)
from benchbox.platforms import get_platform_adapter


def _extract_df_write_tuning(write_config: Any) -> dict[str, Any]:
    write_dict: dict[str, Any] = {}

    if write_config.sort_by:
        write_dict["sort_by"] = [{"name": col.name, "order": col.order} for col in write_config.sort_by]

    if write_config.partition_by:
        write_dict["partition_by"] = [
            {"name": col.name, "strategy": col.strategy.value} for col in write_config.partition_by
        ]

    if write_config.row_group_size is not None:
        write_dict["row_group_size"] = write_config.row_group_size

    if write_config.repartition_count is not None:
        write_dict["repartition_count"] = write_config.repartition_count

    if write_config.compression != "zstd":
        write_dict["compression"] = write_config.compression

    if write_config.compression_level is not None:
        write_dict["compression_level"] = write_config.compression_level

    if write_config.dictionary_columns:
        write_dict["dictionary_columns"] = write_config.dictionary_columns

    return write_dict


def _build_table_ddl_entry(tuning_clauses: Any) -> dict[str, Any]:
    tuning_summary: dict[str, Any] = {}
    _clause_fields = [
        ("sort_by", "sort_by"),
        ("partition_by", "partition_by"),
        ("cluster_by", "cluster_by"),
        ("distribute_by", "distribute_by"),
        ("order_by", "order_by"),
    ]
    for attr, key in _clause_fields:
        value = getattr(tuning_clauses, attr, None)
        if value:
            tuning_summary[key] = value

    inline_clauses = tuning_clauses.get_inline_clauses() if hasattr(tuning_clauses, "get_inline_clauses") else []

    return {
        "ddl_clauses": "\n".join(inline_clauses) if inline_clauses else None,
        "tuning_summary": tuning_summary,
    }


_REDACTED_CONFIG_FIELDS = ("benchmark_config", "database_config", "platform_config")


class DryRunQueryExtractionError(RuntimeError):
    pass


class DryRunExecutor:
    def __init__(self, output_dir: Optional[Path] = None):
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            import tempfile

            self.output_dir = Path(tempfile.mkdtemp(prefix="benchbox_dryrun_"))

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def execute_dry_run(
        self,
        benchmark_config: BenchmarkConfig,
        system_profile: SystemProfile,
        database_config: Optional[DatabaseConfig],
        *,
        preview_platform: str | None = None,
        preview_platform_note: str | None = None,
    ) -> DryRunResult:
        execution_mode = self._resolve_execution_mode(database_config)

        result = DryRunResult(
            benchmark_config=self._serialize_config(benchmark_config),
            database_config=self._serialize_config(database_config)
            if database_config
            else {"type": "data_only", "name": "No Database"},
            system_profile=self._serialize_config(system_profile),
            platform_config={},
            queries={},
            execution_mode=execution_mode,
        )

        if database_config is not None:
            result.deployment = configured_deployment(database_config) or PlatformRegistry.get_default_deployment(
                database_config.type
            )

        try:
            benchmark = self._get_benchmark_instance(benchmark_config, system_profile)

            if database_config is None:
                platform_adapter = None
                result.platform_config = {"data_only": True}
            else:
                platform_config = self._get_platform_config(
                    database_config, system_profile, benchmark_config, benchmark=benchmark
                )
                platform_config["dry_run"] = True
                result.platform_config = platform_config

                platform_adapter = None
                if execution_mode != "dataframe" or self._has_sql_adapter(database_config.type):
                    try:
                        platform_adapter = get_platform_adapter(database_config.type, **platform_config)
                    except Exception as e:
                        result.warnings.append(f"Platform adapter initialization failed: {e}")
                result.deployment = getattr(platform_adapter, "deployment_selected", None) or result.deployment

            platform_type = database_config.type if database_config else None
            result.queries = self._extract_queries(
                benchmark, benchmark_config, platform_adapter, execution_mode, platform_type
            )

            test_execution_type = getattr(benchmark_config, "test_execution_type", "standard")
            result.query_preview = {
                "query_count": len(result.queries),
                "queries": list(result.queries.keys()) if result.queries else [],
                "estimated_time": f"{self._estimate_runtime(benchmark):.1f} seconds",
                "data_size_mb": self._estimate_data_size(benchmark, benchmark_config.name),
                "test_execution_type": test_execution_type,
                "execution_context": self._get_execution_context(benchmark_config, len(result.queries)),
            }

            try:
                if execution_mode == "dataframe":
                    result.dataframe_schema = self._generate_dataframe_schema(benchmark, benchmark_config)
                else:
                    result.schema_sql = self._generate_schema_sql(benchmark, benchmark_config)
            except Exception as e:
                result.warnings.append(f"Schema generation failed: {e}")

            if benchmark_config.options.get("tuning_enabled", False):
                result.tuning_config = self._extract_tuning_config(benchmark, benchmark_config, platform_type)

            preview_type = database_config.type if database_config else preview_platform
            if execution_mode == "sql" and preview_type:
                if database_config is None:
                    reason = f" ({preview_platform_note})" if preview_platform_note else ""
                    result.warnings.append(
                        f"No {preview_type} configuration is available{reason}; the tuning DDL preview comes "
                        f"from the {preview_type} generator only."
                    )
                try:
                    ddl_preview, post_load, notes = self._extract_ddl_preview(
                        benchmark, benchmark_config, preview_type, platform_adapter
                    )
                    result.ddl_preview = ddl_preview
                    result.post_load_statements = post_load
                    result.ddl_preview_notes = notes
                except Exception as e:
                    result.warnings.append(f"DDL preview extraction failed: {e}")

            result.constraint_config = self._extract_constraint_config(benchmark_config)

            result.estimated_resources = self._estimate_resources(benchmark, system_profile, benchmark_config.name)

        except DryRunQueryExtractionError:
            raise
        except Exception as e:
            result.warnings.append(f"Dry run execution error: {e}")

        return result

    @staticmethod
    def _has_sql_adapter(platform_type: str) -> bool:
        canonical_name = PlatformRegistry.resolve_platform_name(platform_type)
        return canonical_name in PlatformRegistry.get_available_platforms()

    @staticmethod
    def _resolve_execution_mode(database_config: Optional[DatabaseConfig]) -> str:
        from benchbox.core.platform_registry import PlatformRegistry
        from benchbox.platforms.adapter_factory import is_dataframe_mode

        if not database_config:
            return "sql"

        explicit_mode = getattr(database_config, "execution_mode", None)
        if explicit_mode is not None:
            return explicit_mode

        if is_dataframe_mode(database_config.type, getattr(database_config, "execution_mode", None)):
            return "dataframe"

        try:
            registry_mode = PlatformRegistry.get_default_mode(database_config.type)
            if registry_mode == "dataframe":
                return "dataframe"
        except Exception:
            pass

        return "sql"

    def save_dry_run_results(self, result: DryRunResult, filename_prefix: str = "dryrun") -> dict[str, Path]:
        if not self.output_dir:
            return {}

        saved_files: dict[str, Path] = {}
        timestamp = result.timestamp.strftime("%Y%m%d_%H%M%S")

        self._save_json_and_yaml(result, filename_prefix, timestamp, saved_files)
        self._save_queries(result, filename_prefix, timestamp, saved_files)
        self._save_ddl_and_post_load(result, filename_prefix, timestamp, saved_files)
        self._save_schema(result, filename_prefix, timestamp, saved_files)

        return saved_files

    def _save_json_and_yaml(
        self, result: DryRunResult, prefix: str, timestamp: str, saved_files: dict[str, Path]
    ) -> None:
        result_dict = result.model_dump()
        for field in _REDACTED_CONFIG_FIELDS:
            result_dict[field] = sanitize_platform_options(result_dict[field])

        json_path = self.output_dir / f"{prefix}_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=2, default=str)
        saved_files["json"] = json_path

        yaml_path = self.output_dir / f"{prefix}_{timestamp}.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump({**result_dict, "timestamp": str(result_dict["timestamp"])}, f, default_flow_style=False)
        saved_files["yaml"] = yaml_path

    def _save_queries(self, result: DryRunResult, prefix: str, timestamp: str, saved_files: dict[str, Path]) -> None:
        is_dataframe = result.execution_mode == "dataframe"
        suffix = "dataframe_queries" if is_dataframe else "queries"
        ext = ".py" if is_dataframe else ".sql"

        queries_dir = self.output_dir / f"{prefix}_{suffix}_{timestamp}"
        queries_dir.mkdir(exist_ok=True)

        for query_id, query_content in result.queries.items():
            if query_id.startswith("_"):
                continue
            query_file = queries_dir / f"query_{query_id}{ext}"
            with open(query_file, "w", encoding="utf-8") as f:
                f.write(query_content)

        saved_files["queries_dir"] = queries_dir

    def _save_ddl_and_post_load(
        self, result: DryRunResult, prefix: str, timestamp: str, saved_files: dict[str, Path]
    ) -> None:
        if result.ddl_preview:
            ddl_path = self.output_dir / f"{prefix}_ddl_{timestamp}.sql"
            with open(ddl_path, "w", encoding="utf-8") as f:
                f.write("-- DDL Preview with Tuning Clauses\n")
                f.write(f"-- Generated by BenchBox dry run at {result.timestamp}\n\n")
                for table_name, table_info in result.ddl_preview.items():
                    f.write(f"-- Table: {table_name}\n")
                    tuning_summary = table_info.get("tuning_summary", {})
                    if tuning_summary:
                        f.write(f"-- Tuning: {tuning_summary}\n")
                    ddl_clauses = table_info.get("ddl_clauses")
                    if ddl_clauses:
                        f.write(ddl_clauses)
                        f.write("\n\n")
            saved_files["ddl"] = ddl_path

        if result.post_load_statements:
            post_load_path = self.output_dir / f"{prefix}_post_load_{timestamp}.sql"
            with open(post_load_path, "w", encoding="utf-8") as f:
                f.write("-- Post-Load Operations\n")
                f.write(f"-- Generated by BenchBox dry run at {result.timestamp}\n\n")
                for table_name, statements in result.post_load_statements.items():
                    f.write(f"-- Table: {table_name}\n")
                    for stmt in statements:
                        f.write(stmt)
                        f.write(";\n")
                    f.write("\n")
            saved_files["post_load"] = post_load_path

    def _save_schema(self, result: DryRunResult, prefix: str, timestamp: str, saved_files: dict[str, Path]) -> None:
        if result.execution_mode == "dataframe" and result.dataframe_schema:
            schema_path = self.output_dir / f"{prefix}_schema_{timestamp}.py"
            with open(schema_path, "w", encoding="utf-8") as f:
                f.write(result.dataframe_schema)
            saved_files["schema"] = schema_path
        elif result.schema_sql:
            schema_path = self.output_dir / f"{prefix}_schema_{timestamp}.sql"
            with open(schema_path, "w", encoding="utf-8") as f:
                f.write(result.schema_sql)
            saved_files["schema"] = schema_path

    def _serialize_config(self, obj: Any) -> dict[str, Any]:
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_") and not callable(v)}
        return {}

    def _get_benchmark_instance(self, config: BenchmarkConfig, system_profile: SystemProfile):
        from benchbox import (
            H2ODB,
            SSB,
            TPCDI,
            TPCDS,
            TPCDSOBT,
            TPCH,
            AMPLab,
            ClickBench,
            CoffeeShop,
            JoinOrder,
            MetadataPrimitives,
            ReadPrimitives,
            TPCHavoc,
            TSBSDevOps,
            WritePrimitives,
        )

        benchmark_classes = {
            "tpch": TPCH,
            "tpcds": TPCDS,
            "tpcds_obt": TPCDSOBT,
            "tpcdi": TPCDI,
            "ssb": SSB,
            "clickbench": ClickBench,
            "h2odb": H2ODB,
            "amplab": AMPLab,
            "joinorder": JoinOrder,
            "metadata_primitives": MetadataPrimitives,
            "read_primitives": ReadPrimitives,
            "write_primitives": WritePrimitives,
            "tpchavoc": TPCHavoc,
            "coffeeshop": CoffeeShop,
            "tsbs_devops": TSBSDevOps,
        }

        benchmark_name = config.name.lower()
        if benchmark_name not in benchmark_classes:
            raise ValueError(f"Unknown benchmark: {benchmark_name}")

        benchmark_class = benchmark_classes[benchmark_name]
        benchmark_config = {
            "scale_factor": config.scale_factor,
            "output_dir": "/tmp/dryrun",
            "verbose": False,
        }

        if hasattr(config, "options") and config.options:
            benchmark_config.update(config.options)

        return benchmark_class(**benchmark_config)

    def _get_platform_config(
        self,
        database_config: Optional[DatabaseConfig],
        system_profile: SystemProfile,
        benchmark_config: Optional[BenchmarkConfig] = None,
        benchmark: Optional["BaseBenchmark"] = None,
    ) -> dict[str, Any]:
        from benchbox.core.platform_config import get_platform_config

        if database_config is None:
            return {"data_only": True}

        benchmark_name = getattr(benchmark_config, "name", None) if benchmark_config else None
        scale_factor = getattr(benchmark_config, "scale_factor", None) if benchmark_config else None

        if benchmark is not None:
            data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()
            if data_source:
                benchmark_name = data_source

        return get_platform_config(database_config, system_profile, benchmark_name, scale_factor)

    def _extract_queries(
        self,
        benchmark,
        benchmark_config: BenchmarkConfig,
        platform_adapter=None,
        execution_mode: str = "sql",
        platform_type: Optional[str] = None,
    ) -> dict[str, str]:
        try:
            test_execution_type = getattr(benchmark_config, "test_execution_type", "standard")

            if test_execution_type == "load_only":
                return {}

            if execution_mode == "dataframe" and test_execution_type in ("maintenance", "combined"):
                return {
                    "_maintenance_not_supported": (
                        "-- Maintenance phase is not yet implemented for DataFrame mode.\n"
                        "--\n"
                        "-- DataFrame platforms like Spark (Delta Lake, Iceberg), Polars, and others\n"
                        "-- can support maintenance operations (INSERT/UPDATE/DELETE), but this\n"
                        "-- functionality has not been implemented yet.\n"
                        "--\n"
                        "-- For now, please use SQL mode (e.g., --platform duckdb) for maintenance\n"
                        "-- phase benchmarks.\n"
                        "--\n"
                        f"-- Requested: test_execution_type={test_execution_type}, execution_mode={execution_mode}\n"
                    )
                }

            if execution_mode == "dataframe":
                return self._extract_dataframe_queries(benchmark_config, benchmark, platform_type)

            benchmark_id = normalize_benchmark_id(benchmark_config.name)

            if benchmark_id == "tpch":
                if test_execution_type == "maintenance":
                    return self._extract_tpch_maintenance_operations(benchmark, benchmark_config)
                elif test_execution_type == "combined":
                    queries = self._extract_standard_queries(benchmark, benchmark_config, platform_adapter)
                    maintenance_ops = self._extract_tpch_maintenance_operations(benchmark, benchmark_config)
                    queries.update(maintenance_ops)
                    return queries
                elif test_execution_type == "power":
                    return self._extract_queries_via_real_test_execution(
                        benchmark, benchmark_config, test_execution_type, platform_adapter
                    )

            if benchmark_id == "tpcds":
                if test_execution_type == "maintenance":
                    return self._extract_tpcds_maintenance_operations(benchmark, benchmark_config)
                elif test_execution_type == "combined":
                    queries = self._extract_standard_queries(benchmark, benchmark_config, platform_adapter)
                    maintenance_ops = self._extract_tpcds_maintenance_operations(benchmark, benchmark_config)
                    queries.update(maintenance_ops)
                    return queries
                elif test_execution_type != "standard":
                    return self._extract_queries_via_real_test_execution(
                        benchmark, benchmark_config, test_execution_type, platform_adapter
                    )

            return self._extract_standard_queries(benchmark, benchmark_config, platform_adapter)

        except DryRunQueryExtractionError:
            raise
        except Exception:
            return {}

    def _extract_queries_via_real_test_execution(
        self,
        benchmark,
        benchmark_config: BenchmarkConfig,
        test_execution_type: str,
        platform_adapter=None,
    ) -> dict[str, str]:
        return self._extract_standard_queries(benchmark, benchmark_config, platform_adapter)

    def _extract_standard_queries(
        self,
        benchmark,
        benchmark_config: BenchmarkConfig | None = None,
        platform_adapter=None,
    ) -> dict[str, str]:
        if platform_adapter is not None and hasattr(platform_adapter, "_get_dialect_queries"):
            benchmark_name = normalize_benchmark_id(benchmark_config.name) if benchmark_config else ""
            try:
                queries = platform_adapter._get_dialect_queries(
                    benchmark,
                    benchmark_slug=benchmark_name,
                    connection=None,
                    strict_translation=True,
                )
                if benchmark_config is not None and hasattr(platform_adapter, "_filter_queries"):
                    options = benchmark_config.options if isinstance(benchmark_config.options, dict) else {}
                    queries = platform_adapter._filter_queries(
                        queries,
                        benchmark,
                        benchmark_name,
                        {
                            "query_subset": benchmark_config.queries,
                            "categories": options.get("categories"),
                        },
                    )
            except Exception as exc:
                raise DryRunQueryExtractionError(f"Dry-run query extraction failed: {exc}") from exc
            if queries and isinstance(next(iter(queries.keys())), int):
                return self._seed_tpch_family_preview(
                    benchmark, benchmark_config, platform_adapter, {str(k): v for k, v in queries.items()}
                )
            return self._seed_tpch_family_preview(benchmark, benchmark_config, platform_adapter, queries)

        if hasattr(benchmark, "get_queries"):
            queries = benchmark.get_queries()
            if queries:
                if queries and isinstance(next(iter(queries.keys())), int):
                    return self._seed_tpch_family_preview(
                        benchmark, benchmark_config, platform_adapter, {str(k): v for k, v in queries.items()}
                    )
                return self._seed_tpch_family_preview(benchmark, benchmark_config, platform_adapter, queries)

        if hasattr(benchmark, "get_all_queries"):
            queries = benchmark.get_all_queries()
            if queries:
                if isinstance(next(iter(queries.keys())), int):
                    return self._seed_tpch_family_preview(
                        benchmark, benchmark_config, platform_adapter, {str(k): v for k, v in queries.items()}
                    )
                return self._seed_tpch_family_preview(benchmark, benchmark_config, platform_adapter, queries)

        if hasattr(benchmark, "query_manager") and hasattr(benchmark.query_manager, "get_all_queries"):
            queries = benchmark.query_manager.get_all_queries()
            if queries:
                if isinstance(next(iter(queries.keys())), int):
                    return self._seed_tpch_family_preview(
                        benchmark, benchmark_config, platform_adapter, {str(k): v for k, v in queries.items()}
                    )
                return self._seed_tpch_family_preview(benchmark, benchmark_config, platform_adapter, queries)

        return {}

    def _seed_tpch_family_preview(
        self,
        benchmark,
        benchmark_config: BenchmarkConfig | None,
        platform_adapter,
        queries: dict[str, str],
    ) -> dict[str, str]:
        from benchbox.core.results.builder import benchmark_family

        if not queries or benchmark_config is None or not hasattr(benchmark, "get_query"):
            return queries
        benchmark_id = normalize_benchmark_id(benchmark_config.name)
        if benchmark_family(benchmark_id) != "tpch":
            return queries
        options = benchmark_config.options if isinstance(benchmark_config.options, dict) else {}
        seed = options.get("seed")
        if seed is None:
            return queries

        from benchbox.core.tpch.benchmark import power_stream_seed

        stream_seed = power_stream_seed(int(seed), 0)
        scale_factor = getattr(benchmark_config, "scale_factor", 1.0) or 1.0
        target = None
        base = None
        if platform_adapter is not None:
            if hasattr(platform_adapter, "get_target_dialect"):
                try:
                    target = platform_adapter.get_target_dialect()
                except Exception:
                    target = None
            if hasattr(platform_adapter, "get_tpc_base_dialect"):
                try:
                    base = platform_adapter.get_tpc_base_dialect(benchmark_family(benchmark_id))
                except Exception:
                    base = None

        import inspect

        parameters = inspect.signature(benchmark.get_query).parameters
        render: dict[str, str] = {}
        for key in queries:
            query_id: Any = int(key) if str(key).isdigit() else key
            kwargs: dict[str, Any] = {"seed": stream_seed, "scale_factor": scale_factor}
            if "dialect" in parameters and target is not None:
                kwargs["dialect"] = target
            if "base_dialect" in parameters and base is not None:
                kwargs["base_dialect"] = base
            try:
                render[key] = benchmark.get_query(query_id, **kwargs)
            except Exception as exc:
                raise DryRunQueryExtractionError(
                    f"Dry-run seeded query rendering failed for query {key}: {exc}"
                ) from exc
        return render

    def _extract_tpch_maintenance_operations(self, benchmark, benchmark_config: BenchmarkConfig) -> dict[str, str]:
        try:
            from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest

            scale_factor = getattr(benchmark_config, "scale_factor", 1.0)

            maintenance_test = TPCHMaintenanceTest(
                connection_factory=lambda: None,
                scale_factor=scale_factor,
                verbose=False,
            )

            operations = maintenance_test.get_maintenance_operations_sql(pair_id=0, placeholder="?")

            result = {}
            for op_id, statements in operations.items():
                result[op_id] = "\n\n".join(statements)

            return result

        except Exception as e:
            return {"_maintenance_error": f"-- Failed to extract maintenance operations: {e}"}

    def _extract_tpcds_maintenance_operations(self, benchmark, benchmark_config: BenchmarkConfig) -> dict[str, str]:
        try:
            from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest

            scale_factor = getattr(benchmark_config, "scale_factor", 1.0)

            maintenance_test = TPCDSMaintenanceTest(
                benchmark=benchmark,
                connection_factory=lambda: None,
                scale_factor=scale_factor,
                verbose=False,
            )

            operations = maintenance_test.get_maintenance_operations_sql(placeholder="?")

            result = {}
            for op_id, statements in operations.items():
                if isinstance(statements, list):
                    result[op_id] = "\n\n".join(statements)
                else:
                    result[op_id] = statements

            return result

        except Exception as e:
            return {"_maintenance_error": f"-- Failed to extract TPC-DS maintenance operations: {e}"}

    def _extract_dataframe_queries(
        self,
        benchmark_config: BenchmarkConfig,
        benchmark_instance,
        platform_type: Optional[str] = None,
    ) -> dict[str, str]:
        import inspect

        from benchbox.core.dataframe.query_resolution import get_dataframe_queries_for_benchmark
        from benchbox.platforms.dataframe.platform_checker import (
            DATAFRAME_PLATFORMS,
            DataFrameFamily,
        )

        family = "expression"
        if platform_type:
            normalized_type = platform_type.replace("-df", "").lower()
            if normalized_type in DATAFRAME_PLATFORMS:
                platform_info = DATAFRAME_PLATFORMS[normalized_type]
                family = "pandas" if platform_info.family == DataFrameFamily.PANDAS else "expression"

        try:
            queries = get_dataframe_queries_for_benchmark(benchmark_config, benchmark_instance, bind_parameters=False)
            result = {}

            for query in queries:
                query_id = query.query_id
                impl = query.expression_impl if family == "expression" else query.pandas_impl

                if impl is None:
                    impl = query.pandas_impl if family == "expression" else query.expression_impl
                    if impl is None:
                        continue

                try:
                    source = inspect.getsource(impl)
                    result[query_id] = source
                except (OSError, TypeError):
                    result[query_id] = f"# Could not extract source for {query_id}\n# {query.description}"

            return result

        except Exception as e:
            return {"_error": f"# Failed to extract DataFrame queries: {e}"}

    def _generate_schema_sql(self, benchmark, config: BenchmarkConfig) -> Optional[str]:
        table_mode = config.options.get("table_mode", "native")
        if table_mode == "external":
            return self._generate_external_schema_sql(benchmark, config)

        try:
            if hasattr(benchmark, "get_create_tables_sql"):
                unified_config = config.options.get("unified_tuning_configuration")
                enable_primary_keys = unified_config.primary_keys.enabled if unified_config else False
                enable_foreign_keys = unified_config.foreign_keys.enabled if unified_config else False

                try:
                    import inspect

                    sig = inspect.signature(benchmark.get_create_tables_sql)
                    if "enable_primary_keys" in sig.parameters:
                        return benchmark.get_create_tables_sql(
                            enable_primary_keys=enable_primary_keys,
                            enable_foreign_keys=enable_foreign_keys,
                        )
                    else:
                        return benchmark.get_create_tables_sql()
                except (TypeError, AttributeError):
                    return benchmark.get_create_tables_sql()
            else:
                return None
        except Exception as e:
            raise Exception(f"Schema SQL generation failed: {e}") from e

    def _generate_external_schema_sql(self, benchmark, config: BenchmarkConfig) -> Optional[str]:
        table_format = config.options.get("table_format") or getattr(config, "table_format", None)

        scan_fn_map = {
            "iceberg": "iceberg_scan",
            "delta": "delta_scan",
            "parquet": "read_parquet",
            "vortex": "read_vortex",
        }
        scan_fn = scan_fn_map.get(table_format or "parquet", "read_parquet")

        schema: dict | None = None
        if hasattr(benchmark, "get_schema"):
            raw = benchmark.get_schema()
            if isinstance(raw, dict):
                schema = raw
            elif isinstance(raw, list):
                schema = {t["name"]: t for t in raw if isinstance(t, dict) and "name" in t}

        if not schema:
            return f"-- External mode ({table_format or 'parquet'}): schema not available for preview"

        lines = [f"-- External table mode: views using {scan_fn}()", ""]
        for table_name in schema:
            lines.append(f"CREATE VIEW {table_name} AS\n    SELECT * FROM {scan_fn}('<data_dir>/{table_name}');")
            lines.append("")

        return "\n".join(lines)

    def _generate_dataframe_schema(self, benchmark, config: BenchmarkConfig) -> Optional[str]:
        import re

        sql_schema = self._generate_schema_sql(benchmark, config)
        if not sql_schema:
            return None

        type_mapping = {
            "INTEGER": "pl.Int64",
            "INT": "pl.Int64",
            "BIGINT": "pl.Int64",
            "SMALLINT": "pl.Int32",
            "TINYINT": "pl.Int8",
            "DECIMAL": "pl.Float64",
            "NUMERIC": "pl.Float64",
            "FLOAT": "pl.Float64",
            "DOUBLE": "pl.Float64",
            "REAL": "pl.Float64",
            "CHAR": "pl.Utf8",
            "VARCHAR": "pl.Utf8",
            "TEXT": "pl.Utf8",
            "STRING": "pl.Utf8",
            "DATE": "pl.Date",
            "TIMESTAMP": "pl.Datetime",
            "DATETIME": "pl.Datetime",
            "TIME": "pl.Time",
            "BOOLEAN": "pl.Boolean",
            "BOOL": "pl.Boolean",
        }

        def sql_type_to_polars(sql_type: str) -> str:
            base_type = re.sub(r"\([^)]*\)", "", sql_type).strip().upper()
            return type_mapping.get(base_type, "pl.Utf8")

        tables = {}
        current_table = None
        current_columns = []

        for line in sql_schema.split("\n"):
            line = line.strip()

            table_match = re.match(r"CREATE TABLE\s+(\w+)\s*\(", line, re.IGNORECASE)
            if table_match:
                current_table = table_match.group(1)
                current_columns = []
                continue

            if current_table and line and not line.startswith(")"):
                col_match = re.match(r"(\w+)\s+([A-Z]+(?:\([^)]*\))?)", line, re.IGNORECASE)
                if col_match:
                    col_name = col_match.group(1)
                    col_type = col_match.group(2)
                    polars_type = sql_type_to_polars(col_type)
                    current_columns.append((col_name, polars_type))

            if current_table and line.startswith(")"):
                tables[current_table] = current_columns
                current_table = None

        benchmark_name = config.name.upper().replace("-", "_")
        lines = [
            '"""',
            f"{benchmark_name} Schema for Polars DataFrame",
            "",
            "This file defines the expected schema for each table when loading data",
            "into Polars DataFrames. Use these schemas for validation or explicit casting.",
            "",
            "Generated by BenchBox dry run.",
            '"""',
            "",
            "import polars as pl",
            "",
            "",
        ]

        for table_name, columns in tables.items():
            lines.append(f"{table_name.upper()}_SCHEMA = {{")
            for col_name, polars_type in columns:
                lines.append(f'    "{col_name}": {polars_type},')
            lines.append("}")
            lines.append("")

        lines.append("")
        lines.append("# All table schemas")
        lines.append("SCHEMAS = {")
        for table_name in tables:
            lines.append(f'    "{table_name}": {table_name.upper()}_SCHEMA,')
        lines.append("}")
        lines.append("")

        lines.extend(
            [
                "",
                "def get_schema(table_name: str) -> dict:",
                '    """Get the Polars schema for a table."""',
                "    return SCHEMAS.get(table_name.lower(), {})",
                "",
            ]
        )

        return "\n".join(lines)

    def _extract_tuning_config(
        self, benchmark, config: BenchmarkConfig, platform: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        try:
            tuning_dict: dict[str, Any] = {}

            unified_config = config.options.get("unified_tuning_configuration")
            if unified_config:
                tuning_dict = self._extract_unified_tuning(unified_config, platform)

            df_tuning_config = config.options.get("df_tuning_config")
            if df_tuning_config:
                df_tuning_dict = self._extract_df_tuning(df_tuning_config)
                if df_tuning_dict:
                    tuning_dict["dataframe_tuning"] = df_tuning_dict

            if tuning_dict:
                return tuning_dict

            if hasattr(benchmark, "get_tunings"):
                tunings = benchmark.get_tunings()
                if tunings:
                    return tunings.to_dict()
            return None
        except Exception:
            return None

    _PLATFORM_SPECIFIC_OPTIMIZATION_FIELDS: dict[str, str] = {
        "databricks_clustering_strategy": "databricks",
        "physical_rendering_id": "databricks",
    }

    @staticmethod
    def _extract_unified_tuning(unified_config: Any, platform: Optional[str] = None) -> dict[str, Any]:
        platform_key = (platform or "").lower()

        tuning_dict: dict[str, Any] = {
            "constraints": {
                "primary_keys": {
                    "enabled": unified_config.primary_keys.enabled,
                    "enforce_uniqueness": unified_config.primary_keys.enforce_uniqueness,
                    "nullable": unified_config.primary_keys.nullable,
                },
                "foreign_keys": {
                    "enabled": unified_config.foreign_keys.enabled,
                    "enforce_referential_integrity": unified_config.foreign_keys.enforce_referential_integrity,
                    "on_delete_action": unified_config.foreign_keys.on_delete_action,
                    "on_update_action": unified_config.foreign_keys.on_update_action,
                },
            },
            "platform_optimizations": {
                "z_ordering": unified_config.platform_optimizations.z_ordering_enabled,
                "physical_rendering_id": unified_config.platform_optimizations.physical_rendering_id,
                "liquid_clustering": unified_config.platform_optimizations.liquid_clustering_enabled,
                "auto_optimize": unified_config.platform_optimizations.auto_optimize_enabled,
                "bloom_filters": unified_config.platform_optimizations.bloom_filters_enabled,
                "materialized_views": unified_config.platform_optimizations.materialized_views_enabled,
            },
        }

        for field_name, owning_platform in DryRunExecutor._PLATFORM_SPECIFIC_OPTIMIZATION_FIELDS.items():
            if platform_key == owning_platform:
                tuning_dict["platform_optimizations"][field_name] = getattr(
                    unified_config.platform_optimizations, field_name
                )

        if unified_config.table_tunings:
            tuning_dict["table_tunings"] = {}
            for table_name, table_tuning in unified_config.table_tunings.items():
                table_dict: dict[str, Any] = {"table_name": table_tuning.table_name}
                for attr in ("partitioning", "sorting", "clustering", "distribution"):
                    columns = getattr(table_tuning, attr, None)
                    if columns:
                        table_dict[attr] = [{"name": col.name, "type": col.type, "order": col.order} for col in columns]
                tuning_dict["table_tunings"][table_name] = table_dict

        return tuning_dict

    @staticmethod
    def _extract_df_tuning(df_tuning_config: Any) -> dict[str, Any]:
        df_tuning_dict: dict[str, Any] = {}

        _SECTIONS: list[tuple[str, list[tuple[str, bool]]]] = [
            ("parallelism", [("thread_count", True), ("worker_count", True)]),
            ("memory", [("memory_limit", True), ("chunk_size", True), ("spill_to_disk", False)]),
            ("execution", [("streaming_mode", False), ("engine_affinity", True)]),
        ]
        for section_attr, fields in _SECTIONS:
            section = getattr(df_tuning_config, section_attr, None)
            if section is None or section.is_default():
                continue
            section_dict: dict[str, Any] = {}
            for field_attr, use_value in fields:
                value = getattr(section, field_attr, None)
                if use_value and value is not None:
                    section_dict[field_attr] = value
                elif not use_value and value:
                    section_dict[field_attr] = True
            if section_dict:
                df_tuning_dict[section_attr] = section_dict

        if hasattr(df_tuning_config, "write") and not df_tuning_config.write.is_default():
            write_dict = _extract_df_write_tuning(df_tuning_config.write)
            if write_dict:
                df_tuning_dict["write"] = write_dict

        return df_tuning_dict

    def _extract_constraint_config(self, config: BenchmarkConfig) -> dict[str, Any]:
        tuning_enabled = config.options.get("tuning_enabled", False)
        if not tuning_enabled:
            return {
                "enable_primary_keys": False,
                "enable_foreign_keys": False,
            }

        tuning_config = config.options.get("unified_tuning_configuration")
        if tuning_config and hasattr(tuning_config, "primary_keys") and hasattr(tuning_config, "foreign_keys"):
            return {
                "enable_primary_keys": tuning_config.primary_keys.enabled,
                "enable_foreign_keys": tuning_config.foreign_keys.enabled,
            }
        return {
            "enable_primary_keys": False,
            "enable_foreign_keys": False,
        }

    def _estimate_resources(self, benchmark, system_profile: SystemProfile, benchmark_slug: str) -> dict[str, Any]:
        try:
            benchmark_name = benchmark_slug

            estimates = {
                "scale_factor": benchmark.scale_factor,
                "estimated_data_size_mb": self._estimate_data_size(benchmark, benchmark_name),
                "estimated_memory_usage_mb": self._estimate_memory_usage(benchmark, system_profile, benchmark_name),
                "estimated_runtime_minutes": self._estimate_runtime(benchmark),
                "cpu_cores_available": getattr(system_profile, "cpu_cores_logical", 1),
                "memory_gb_available": getattr(system_profile, "memory_gb", 4),
            }
            return estimates
        except Exception as e:
            return {"error": str(e)}

    def _estimate_data_size(self, benchmark, benchmark_name: str) -> float:
        base_size_mb = {
            "tpch": 8,
            "tpcds": 24,
            "ssb": 6,
            "clickbench": 100,
            "primitives": 1,
        }.get(benchmark_name.lower(), 10)

        return base_size_mb * benchmark.scale_factor * 100

    def _estimate_memory_usage(self, benchmark, system_profile: SystemProfile, benchmark_name: str) -> float:
        data_size = self._estimate_data_size(benchmark, benchmark_name)
        return min(data_size * 2.5, getattr(system_profile, "memory_gb", 4) * 1024 * 0.8)

    def _estimate_runtime(self, benchmark) -> float:
        query_count = len(getattr(benchmark, "get_all_queries", dict)())
        base_seconds = query_count * 10
        scaled_seconds = base_seconds * max(benchmark.scale_factor, 0.01)
        return scaled_seconds / 60.0

    def _extract_ddl_preview(
        self,
        benchmark,
        config: BenchmarkConfig,
        platform_type: str,
        platform_adapter: Any = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str]]:
        from benchbox.core.tuning.ddl_generator import get_ddl_generator

        ddl_preview: dict[str, dict[str, Any]] = {}
        post_load_statements: dict[str, list[str]] = {}

        unified_config = config.options.get("unified_tuning_configuration")
        if not unified_config:
            return ddl_preview, post_load_statements, []

        try:
            generator = get_ddl_generator(platform_type)
        except Exception as exc:
            raise RuntimeError(f"no tuning DDL generator for {platform_type}: {exc}") from exc

        notes = tuning_preview_notes(platform_type, unified_config)

        if not hasattr(benchmark, "get_schema"):
            return ddl_preview, post_load_statements, notes

        raw_schema = benchmark.get_schema()
        if isinstance(raw_schema, dict):
            tables = list(raw_schema.keys())
        elif isinstance(raw_schema, list):
            tables = [t["name"] for t in raw_schema if isinstance(t, dict) and "name" in t]
        else:
            return ddl_preview, post_load_statements, notes

        table_tunings_by_upper = {str(key).upper(): value for key, value in unified_config.table_tunings.items()}
        execution_preview = getattr(platform_adapter, "preview_tuning_statements", None)

        for table_name in tables:
            table_tuning = table_tunings_by_upper.get(str(table_name).upper())
            if not table_tuning:
                continue

            tuning_clauses = generator.generate_tuning_clauses(table_tuning)
            executed = execution_preview(table_name, table_tuning) if callable(execution_preview) else None
            if tuning_clauses.is_empty() and not executed:
                continue

            if not tuning_clauses.is_empty():
                ddl_preview[table_name] = _build_table_ddl_entry(tuning_clauses)

            post_load = executed if executed else generator.get_post_load_statements(table_name, tuning_clauses)
            if post_load:
                post_load_statements[table_name] = post_load

        return ddl_preview, post_load_statements, notes

    def _get_execution_context(self, benchmark_config: BenchmarkConfig, query_count: int) -> str:
        test_execution_type = getattr(benchmark_config, "test_execution_type", "standard")
        benchmark_name = getattr(benchmark_config, "name", "").lower()

        if test_execution_type == "power":
            if benchmark_name == "tpcds":
                return "TPC-DS PowerTest stream permutation (99 queries in randomized order)"
            elif benchmark_name == "tpch":
                return "TPC-H PowerTest stream permutation (22 queries in a specific, randomized order)"
            else:
                return "Power test execution (stream permutation)"
        elif test_execution_type == "throughput":
            if benchmark_name == "tpcds":
                return f"TPC-DS ThroughputTest (4 concurrent streams, {query_count} queries total)"
            else:
                return "Throughput test execution (concurrent streams)"
        elif test_execution_type == "maintenance":
            if benchmark_name == "tpcds":
                return "TPC-DS MaintenanceTest (data operations: INSERT/UPDATE/DELETE)"
            else:
                return "Maintenance test execution (data operations)"
        else:
            return f"Standard sequential execution ({query_count} queries)"


GENERATOR_DRIVEN_EXECUTION_PLATFORMS = frozenset({"duckdb", "motherduck", "clickhouse", "starrocks", "postgresql"})


def tuning_preview_notes(platform_type: str, unified_config: Any) -> list[str]:
    from benchbox.core.tuning.capability_registry import get_capability, resolve_platform_key, unapplied_reason

    platform_key = resolve_platform_key(platform_type)
    notes: list[str] = []
    if platform_key not in GENERATOR_DRIVEN_EXECUTION_PLATFORMS:
        notes.append(
            f"Preview only: the tuning DDL shown for {platform_type} comes from the tuning generator; "
            "execution builds its own table DDL, so what runs can differ."
        )
    sorted_ingestion_mode = getattr(unified_config.platform_optimizations, "sorted_ingestion_mode", None)
    for tuning_type in sorted(unified_config.get_enabled_tuning_types(), key=lambda item: item.value):
        capability = get_capability(platform_type, tuning_type)
        reason = unapplied_reason(capability, sorted_ingestion_mode) if capability is not None else None
        if reason is not None:
            notes.append(f"Preview only: {tuning_type.value} is not applied at execution on {platform_type}: {reason}")
    return notes


def preview_benchmark_run(
    platform: str,
    benchmark: str,
    scale_factor: float,
    queries: str | None,
    mode: str | None,
) -> dict[str, Any]:
    import logging as _logging

    from benchbox.core.benchmark_registry import get_all_benchmarks
    from benchbox.core.platform_registry import PlatformRegistry
    from benchbox.core.schemas import BenchmarkConfig as _BenchmarkConfig, DatabaseConfig as _DatabaseConfig
    from benchbox.core.system import SystemProfiler as _SystemProfiler

    _logger = _logging.getLogger(__name__)

    try:
        benchmark_lower = benchmark.lower()
        all_benchmarks = get_all_benchmarks()

        if benchmark_lower not in all_benchmarks:
            return {
                "status": "error",
                "error": f"Benchmark '{benchmark}' not found",
                "error_code": "RESOURCE_NOT_FOUND",
                "details": {"requested": benchmark, "available": list(all_benchmarks.keys())},
            }

        platform_lower = platform.lower()
        base_platform = platform_lower.replace("-df", "")
        platform_info = PlatformRegistry.get_platform_info(base_platform)

        warnings: list[str] = []
        if platform_info is None:
            warnings.append(f"Unknown platform: {platform}")
        elif not platform_info.available:
            warnings.append(f"Platform '{platform}' dependencies not installed: {platform_info.installation_command}")

        resolved_mode: str
        if mode is not None:
            m_lower = mode.lower()
            if m_lower in ("datagen", "generate"):
                m_lower = "data_only"
            if m_lower not in ("sql", "dataframe", "data_only"):
                return {
                    "status": "error",
                    "error": f"Invalid mode: {mode}. Must be 'sql', 'dataframe', or 'data_only'",
                    "error_code": "VALIDATION_ERROR",
                }
            if m_lower == "data_only":
                resolved_mode = "data_only"
            elif platform_info is not None and not PlatformRegistry.supports_mode(base_platform, m_lower):
                supported = [m for m in ["sql", "dataframe"] if PlatformRegistry.supports_mode(base_platform, m)]
                supported.append("data_only")
                return {
                    "status": "error",
                    "error": f"Platform '{platform}' does not support {m_lower} mode",
                    "error_code": "VALIDATION_UNSUPPORTED_MODE",
                    "details": {
                        "platform": platform,
                        "requested_mode": m_lower,
                        "supported_modes": supported,
                    },
                }
            else:
                resolved_mode = m_lower
        elif platform_lower.endswith("-df"):
            resolved_mode = "dataframe"
        else:
            try:
                resolved_mode = PlatformRegistry.get_default_mode(base_platform)
            except Exception:
                resolved_mode = "sql"

        meta = all_benchmarks[benchmark_lower]
        display_name = meta.get("display_name", benchmark_lower.upper())

        benchmark_config = _BenchmarkConfig(
            name=benchmark_lower,
            display_name=display_name,
            scale_factor=scale_factor,
            queries=[q.strip() for q in queries.split(",")] if queries else None,
        )

        database_config = _DatabaseConfig(
            type=platform_lower,
            name=f"mcp_dryrun_{platform_lower}",
            execution_mode=resolved_mode,
        )

        profiler = _SystemProfiler()
        system_profile = profiler.get_system_profile()

        executor = DryRunExecutor(output_dir=None)
        result = executor.execute_dry_run(benchmark_config, system_profile, database_config)

        warnings.extend(result.warnings)

        response: dict[str, Any] = {
            "status": "dry_run",
            "platform": platform,
            "benchmark": benchmark,
            "scale_factor": scale_factor,
            "execution_mode": result.execution_mode,
        }

        if result.query_preview:
            query_ids = result.query_preview.get("queries", [])
            response["execution_plan"] = {
                "phases": ["load", "power"],
                "total_queries": result.query_preview.get("query_count", 0),
                "query_ids": query_ids[:30],
                "query_ids_truncated": len(query_ids) > 30,
            }

        if result.estimated_resources:
            data_size_mb = result.estimated_resources.get("estimated_data_size_mb", 0)
            response["resource_estimates"] = {
                "data_size_gb": round(data_size_mb / 1024, 2),
                "memory_recommended_gb": max(2, round(data_size_mb / 1024 * 2, 1)),
            }

        if warnings:
            response["warnings"] = warnings

        return response

    except Exception as e:
        _logger.error("Dry run preview failed (%s)", type(e).__name__)
        return {
            "status": "error",
            "error": f"Dry run failed: {e}",
            "error_code": "INTERNAL_ERROR",
            "details": {"exception_type": type(e).__name__},
        }
