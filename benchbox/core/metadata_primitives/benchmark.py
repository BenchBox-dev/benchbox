# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from benchbox.base import BaseBenchmark
from benchbox.core.connection import DatabaseConnection
from benchbox.core.metadata_primitives.complexity import (
    AclGrant,
    GeneratedMetadata,
    MetadataComplexityConfig,
    PermissionDensity,
    get_complexity_preset,
)
from benchbox.core.metadata_primitives.ddl import (
    generate_create_role_sql,
    generate_drop_role_sql,
    generate_grant_sql,
    generate_revoke_sql,
    generate_wide_table_columns,
    supports_acl,
)
from benchbox.core.metadata_primitives.generator import MetadataGenerator
from benchbox.core.metadata_primitives.queries import MetadataPrimitivesQueryManager
from benchbox.core.metadata_primitives.schema import (
    get_create_tables_sql as get_base_schema_sql,
    get_schema as get_base_schema,
    get_table_names as get_base_table_names,
)
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.utils.dialect_utils import SQLTranslationError

logger = logging.getLogger(__name__)


@dataclass
class MetadataQueryResult:
    query_id: str
    category: str
    execution_time_ms: float
    row_count: int = 0
    success: bool = True
    error: str | None = None


@dataclass
class MetadataBenchmarkResult:
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    total_time_ms: float = 0.0
    results: list[MetadataQueryResult] = field(default_factory=list)
    category_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    acl_mutation_results: list[AclMutationResult] = field(default_factory=list)
    acl_mutation_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplexityBenchmarkResult:
    complexity_config: MetadataComplexityConfig
    generated_metadata: GeneratedMetadata
    setup_time_ms: float = 0.0
    teardown_time_ms: float = 0.0
    benchmark_result: MetadataBenchmarkResult | None = None


@dataclass
class AclMutationResult:
    operation: str
    target_type: str
    target_name: str
    grantee: str
    privileges: list[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    success: bool = True
    error: str | None = None


@dataclass
class AclBenchmarkResult:
    setup_time_ms: float = 0.0
    teardown_time_ms: float = 0.0
    mutation_results: list[AclMutationResult] = field(default_factory=list)
    introspection_results: MetadataBenchmarkResult | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary and self.mutation_results:
            self._calculate_summary()

    def _calculate_summary(self) -> None:
        total_ops = len(self.mutation_results)
        successful_ops = sum(1 for r in self.mutation_results if r.success)
        failed_ops = total_ops - successful_ops

        if total_ops == 0:
            self.summary = {
                "total_operations": 0,
                "successful_operations": 0,
                "failed_operations": 0,
            }
            return

        times = [r.execution_time_ms for r in self.mutation_results]
        grants = [r for r in self.mutation_results if r.operation == "GRANT"]
        revokes = [r for r in self.mutation_results if r.operation == "REVOKE"]

        self.summary = {
            "total_operations": total_ops,
            "successful_operations": successful_ops,
            "failed_operations": failed_ops,
            "total_time_ms": sum(times),
            "avg_time_ms": sum(times) / len(times),
            "min_time_ms": min(times),
            "max_time_ms": max(times),
            "grants_count": len(grants),
            "revokes_count": len(revokes),
            "grants_per_second": len(grants) / (sum(r.execution_time_ms for r in grants) / 1000)
            if grants and sum(r.execution_time_ms for r in grants) > 0
            else 0.0,
        }


class MetadataPrimitivesBenchmark(TranslatableQueryMixin, BaseBenchmark):
    _source_dialect = "duckdb"
    _native_source_passthrough = True
    SKIP_DATA_LOADING = True

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: str | Path | None = None,
        **config: Any,
    ):
        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **config)

        self._name = "Metadata Primitives Benchmark"
        self._version = "1.0"
        self._description = "Metadata Primitives benchmark - Testing database catalog introspection performance"

        self.query_manager = MetadataPrimitivesQueryManager()

    def get_data_source_benchmark(self) -> str | None:
        return None

    def generate_data(
        self,
        tables: list[str] | None = None,
        output_format: str = "csv",
    ) -> dict[str, str]:
        return {}

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Any = None,
    ) -> str:
        return get_base_schema_sql(dialect=dialect, tuning_config=tuning_config)

    def get_table_names(self) -> list[str]:
        return get_base_table_names()

    def get_schema(self) -> dict[str, dict[str, Any]]:
        return get_base_schema()

    def get_query(self, query_id: int | str, *, params: dict[str, Any] | None = None) -> str:
        if params is not None:
            raise ValueError("Metadata Primitives queries are static and don't accept parameters")
        return self.query_manager.get_query(str(query_id))

    def _resolve_query_sql(self, query_id: str, dialect: str | None) -> str:
        sql = self.query_manager.get_query(query_id, dialect=dialect)
        if dialect and not self.query_manager.has_variant(query_id, dialect):
            return self.translate_query_text(sql, dialect)
        return sql

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        if dialect:
            return {
                query_id: self._resolve_query_sql(query_id, dialect)
                for query_id in self.query_manager.get_queries_for_dialect(dialect)
            }
        return self.query_manager.get_all_queries()

    def get_queries_by_category(self, category: str) -> dict[str, str]:
        return self.query_manager.get_queries_by_category(category)

    def get_query_categories(self) -> list[str]:
        return self.query_manager.get_query_categories()

    def execute_query(
        self,
        query_id: str,
        connection: DatabaseConnection,
        dialect: str | None = None,
    ) -> MetadataQueryResult:
        entry = self.query_manager.get_query_entry(query_id)
        category = entry.category

        try:
            sql = self._resolve_query_sql(query_id, dialect)
        except (ValueError, SQLTranslationError) as e:
            return MetadataQueryResult(
                query_id=query_id,
                category=category,
                execution_time_ms=0.0,
                row_count=0,
                success=False,
                error=str(e),
            )

        start_time = time.perf_counter()
        try:
            if hasattr(connection, "execute"):
                cursor = connection.execute(sql)
            else:
                cursor = connection.cursor()
                cursor.execute(sql)

            results = cursor.fetchall()
            row_count = len(results)

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            return MetadataQueryResult(
                query_id=query_id,
                category=category,
                execution_time_ms=elapsed_ms,
                row_count=row_count,
                success=True,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return MetadataQueryResult(
                query_id=query_id,
                category=category,
                execution_time_ms=elapsed_ms,
                row_count=0,
                success=False,
                error=str(e),
            )

    def run_benchmark(
        self,
        connection: DatabaseConnection,
        dialect: str | None = None,
        categories: list[str] | None = None,
        query_ids: list[str] | None = None,
        iterations: int = 1,
    ) -> MetadataBenchmarkResult:
        queries_to_run = self._select_queries_to_run(dialect=dialect, categories=categories, query_ids=query_ids)

        result = MetadataBenchmarkResult()
        all_results: list[MetadataQueryResult] = []

        for query_id in queries_to_run:
            for _ in range(iterations):
                query_result = self.execute_query(query_id, connection, dialect=dialect)
                all_results.append(query_result)

                if query_result.success:
                    result.successful_queries += 1
                else:
                    result.failed_queries += 1

                result.total_time_ms += query_result.execution_time_ms

        result.total_queries = len(all_results)
        result.results = all_results
        result.category_summary = self._build_category_summary(all_results)

        if dialect and supports_acl(dialect):
            acl_results = self._run_default_acl_mutations(connection, dialect)
            result.acl_mutation_results = acl_results
            result.acl_mutation_summary = self._build_acl_summary(acl_results)

        return result

    def _select_queries_to_run(
        self,
        *,
        dialect: str | None,
        categories: list[str] | None,
        query_ids: list[str] | None,
    ) -> list[str]:
        if query_ids:
            return query_ids
        if categories:
            queries_to_run: list[str] = []
            for category in categories:
                queries_to_run.extend(self.query_manager.get_queries_by_category(category).keys())
            return queries_to_run
        if dialect:
            return list(self.query_manager.get_queries_for_dialect(dialect).keys())
        return list(self.query_manager.get_all_queries().keys())

    def _build_category_summary(self, all_results: list[MetadataQueryResult]) -> dict[str, dict[str, float | int]]:
        category_summary: dict[str, dict[str, float | int]] = {}
        for query_result in all_results:
            entry = category_summary.setdefault(
                query_result.category,
                {
                    "total_queries": 0,
                    "successful": 0,
                    "failed": 0,
                    "total_time_ms": 0.0,
                    "avg_time_ms": 0.0,
                    "min_time_ms": 0.0,
                    "max_time_ms": 0.0,
                },
            )
            total_queries = int(entry["total_queries"]) + 1
            total_time_ms = float(entry["total_time_ms"]) + query_result.execution_time_ms
            min_time_ms = (
                query_result.execution_time_ms
                if total_queries == 1
                else min(float(entry["min_time_ms"]), query_result.execution_time_ms)
            )
            max_time_ms = max(float(entry["max_time_ms"]), query_result.execution_time_ms)
            successful = int(entry["successful"]) + int(query_result.success)
            failed = total_queries - successful
            entry.update(
                {
                    "total_queries": total_queries,
                    "successful": successful,
                    "failed": failed,
                    "total_time_ms": total_time_ms,
                    "avg_time_ms": total_time_ms / total_queries,
                    "min_time_ms": min_time_ms,
                    "max_time_ms": max_time_ms,
                }
            )
        return category_summary

    def setup_complexity(
        self,
        connection: DatabaseConnection,
        dialect: str,
        config: MetadataComplexityConfig | str,
    ) -> GeneratedMetadata:
        if isinstance(config, str):
            config = get_complexity_preset(config)

        generator = MetadataGenerator()
        return generator.setup(connection, dialect, config)

    def teardown_complexity(
        self,
        connection: DatabaseConnection,
        dialect: str,
        generated: GeneratedMetadata,
    ) -> None:
        generator = MetadataGenerator()
        generator.teardown(connection, dialect, generated)

    def cleanup_benchmark_objects(
        self,
        connection: DatabaseConnection,
        dialect: str,
        prefix: str = "benchbox_",
    ) -> int:
        generator = MetadataGenerator()
        return generator.cleanup_all(connection, dialect, prefix)

    def run_complexity_benchmark(
        self,
        connection: DatabaseConnection,
        dialect: str,
        config: MetadataComplexityConfig | str,
        iterations: int = 1,
        categories: list[str] | None = None,
    ) -> ComplexityBenchmarkResult:
        if isinstance(config, str):
            config = get_complexity_preset(config)

        if categories is None:
            categories = self._get_complexity_categories(config)

        logger.info(f"Setting up complexity structures: {config.to_dict()}")
        setup_start = time.perf_counter()
        generator = MetadataGenerator()
        generated = generator.setup(connection, dialect, config)
        setup_time_ms = (time.perf_counter() - setup_start) * 1000

        logger.info(
            f"Created {generated.total_objects} objects "
            f"({len(generated.tables)} tables, {len(generated.views)} views) "
            f"in {setup_time_ms:.1f}ms"
        )

        benchmark_result = None
        try:
            benchmark_result = self.run_benchmark(
                connection,
                dialect=dialect,
                categories=categories,
                iterations=iterations,
            )
        finally:
            logger.info("Tearing down complexity structures...")
            teardown_start = time.perf_counter()
            generator.teardown(connection, dialect, generated)
            teardown_time_ms = (time.perf_counter() - teardown_start) * 1000
            logger.info(f"Teardown completed in {teardown_time_ms:.1f}ms")

        return ComplexityBenchmarkResult(
            complexity_config=config,
            generated_metadata=generated,
            setup_time_ms=setup_time_ms,
            teardown_time_ms=teardown_time_ms,
            benchmark_result=benchmark_result,
        )

    def _get_complexity_categories(self, config: MetadataComplexityConfig) -> list[str]:
        categories = []

        if config.width_factor > 0:
            categories.append("wide_table")

        if config.catalog_size > 1:
            categories.append("large_catalog")

        if config.view_depth > 0:
            categories.append("view_hierarchy")

        from benchbox.core.metadata_primitives.complexity import TypeComplexity

        if config.type_complexity != TypeComplexity.SCALAR:
            categories.append("complex_type")

        from benchbox.core.metadata_primitives.complexity import ConstraintDensity

        if config.constraint_density != ConstraintDensity.NONE:
            categories.append("constraint")

        if config.acl_role_count > 0:
            categories.append("acl")

        return categories

    def get_complexity_categories(self) -> list[str]:
        return [
            "wide_table",
            "view_hierarchy",
            "complex_type",
            "large_catalog",
            "constraint",
            "acl",
        ]

    def run_acl_benchmark(
        self,
        connection: DatabaseConnection,
        dialect: str,
        config: MetadataComplexityConfig | str,
        iterations: int = 1,
    ) -> AclBenchmarkResult:
        if isinstance(config, str):
            config = get_complexity_preset(config)

        if not supports_acl(dialect):
            logger.warning(f"Dialect '{dialect}' does not support ACL operations")
            return AclBenchmarkResult(summary={"error": f"Dialect '{dialect}' does not support ACL operations"})

        if config.acl_role_count == 0:
            logger.warning("ACL benchmark requires acl_role_count > 0")
            return AclBenchmarkResult(summary={"error": "ACL benchmark requires acl_role_count > 0"})

        mutation_results: list[AclMutationResult] = []
        created_roles: list[str] = []
        created_grants: list[AclGrant] = []

        setup_start = time.perf_counter()

        for i in range(config.acl_role_count):
            role_name = f"{config.prefix}role_{i:04d}"
            result = self._measure_create_role(connection, dialect, role_name)
            mutation_results.append(result)
            if result.success:
                created_roles.append(role_name)

        test_tables = self._create_grant_test_tables(connection, dialect, config)

        grants_to_create = self._get_grants_per_table(config.acl_permission_density)
        privileges = ["SELECT", "INSERT", "UPDATE"]

        for table_name in test_tables:
            for i, role_name in enumerate(created_roles[:grants_to_create]):
                priv_set = privileges[: (i % len(privileges)) + 1]
                result = self._measure_grant(connection, dialect, role_name, table_name, priv_set)
                mutation_results.append(result)
                if result.success:
                    created_grants.append(AclGrant(role_name, "table", table_name, priv_set))

        setup_time_ms = (time.perf_counter() - setup_start) * 1000

        introspection_results = self.run_benchmark(
            connection,
            dialect=dialect,
            categories=["acl"],
            iterations=iterations,
        )

        teardown_start = time.perf_counter()

        for grant in reversed(created_grants):
            result = self._measure_revoke(connection, dialect, grant.grantee, grant.object_name, grant.privileges)
            mutation_results.append(result)

        self._drop_grant_test_tables(connection, dialect, test_tables)

        for role_name in reversed(created_roles):
            result = self._measure_drop_role(connection, dialect, role_name)
            mutation_results.append(result)

        teardown_time_ms = (time.perf_counter() - teardown_start) * 1000

        return AclBenchmarkResult(
            setup_time_ms=setup_time_ms,
            teardown_time_ms=teardown_time_ms,
            mutation_results=mutation_results,
            introspection_results=introspection_results,
        )

    def _measure_create_role(
        self,
        connection: DatabaseConnection,
        dialect: str,
        role_name: str,
    ) -> AclMutationResult:
        sql = generate_create_role_sql(role_name, dialect)

        if sql.startswith("--"):
            return AclMutationResult(
                operation="CREATE_ROLE",
                target_type="role",
                target_name=role_name,
                grantee=role_name,
                success=False,
                error="CREATE ROLE not supported on this platform",
            )

        start = time.perf_counter()
        try:
            self._execute(connection, sql)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="CREATE_ROLE",
                target_type="role",
                target_name=role_name,
                grantee=role_name,
                execution_time_ms=elapsed_ms,
                success=True,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="CREATE_ROLE",
                target_type="role",
                target_name=role_name,
                grantee=role_name,
                execution_time_ms=elapsed_ms,
                success=False,
                error=str(e),
            )

    def _measure_drop_role(
        self,
        connection: DatabaseConnection,
        dialect: str,
        role_name: str,
    ) -> AclMutationResult:
        sql = generate_drop_role_sql(role_name, dialect)

        if sql.startswith("--"):
            return AclMutationResult(
                operation="DROP_ROLE",
                target_type="role",
                target_name=role_name,
                grantee=role_name,
                success=False,
                error="DROP ROLE not supported on this platform",
            )

        start = time.perf_counter()
        try:
            self._execute(connection, sql)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="DROP_ROLE",
                target_type="role",
                target_name=role_name,
                grantee=role_name,
                execution_time_ms=elapsed_ms,
                success=True,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="DROP_ROLE",
                target_type="role",
                target_name=role_name,
                grantee=role_name,
                execution_time_ms=elapsed_ms,
                success=False,
                error=str(e),
            )

    def _measure_grant(
        self,
        connection: DatabaseConnection,
        dialect: str,
        grantee: str,
        object_name: str,
        privileges: list[str],
    ) -> AclMutationResult:
        sql = generate_grant_sql(
            grantee=grantee,
            object_name=object_name,
            privileges=privileges,
            dialect=dialect,
        )

        start = time.perf_counter()
        try:
            self._execute(connection, sql)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="GRANT",
                target_type="table",
                target_name=object_name,
                grantee=grantee,
                privileges=privileges,
                execution_time_ms=elapsed_ms,
                success=True,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="GRANT",
                target_type="table",
                target_name=object_name,
                grantee=grantee,
                privileges=privileges,
                execution_time_ms=elapsed_ms,
                success=False,
                error=str(e),
            )

    def _measure_revoke(
        self,
        connection: DatabaseConnection,
        dialect: str,
        grantee: str,
        object_name: str,
        privileges: list[str],
    ) -> AclMutationResult:
        sql = generate_revoke_sql(
            grantee=grantee,
            object_name=object_name,
            privileges=privileges,
            dialect=dialect,
        )

        start = time.perf_counter()
        try:
            self._execute(connection, sql)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="REVOKE",
                target_type="table",
                target_name=object_name,
                grantee=grantee,
                privileges=privileges,
                execution_time_ms=elapsed_ms,
                success=True,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AclMutationResult(
                operation="REVOKE",
                target_type="table",
                target_name=object_name,
                grantee=grantee,
                privileges=privileges,
                execution_time_ms=elapsed_ms,
                success=False,
                error=str(e),
            )

    def _get_grants_per_table(self, density: PermissionDensity) -> int:
        density_map = {
            PermissionDensity.NONE: 0,
            PermissionDensity.SPARSE: 2,
            PermissionDensity.MODERATE: 7,
            PermissionDensity.DENSE: 20,
        }
        return density_map.get(density, 0)

    def _create_grant_test_tables(
        self,
        connection: DatabaseConnection,
        dialect: str,
        config: MetadataComplexityConfig,
    ) -> list[str]:
        test_tables: list[str] = []
        num_tables = min(5, config.catalog_size) if config.catalog_size > 0 else 3

        for i in range(num_tables):
            table_name = f"{config.prefix}acl_test_{i:04d}"
            sql = f"CREATE TABLE IF NOT EXISTS {table_name} (id INTEGER PRIMARY KEY, name VARCHAR(100));"
            try:
                self._execute(connection, sql)
                test_tables.append(table_name)
            except Exception as e:
                logger.warning(f"Could not create test table {table_name}: {e}")

        return test_tables

    def _drop_grant_test_tables(
        self,
        connection: DatabaseConnection,
        dialect: str,
        table_names: list[str],
    ) -> None:
        for table_name in table_names:
            try:
                sql = f"DROP TABLE IF EXISTS {table_name};"
                self._execute(connection, sql)
            except Exception as e:
                logger.warning(f"Could not drop test table {table_name}: {e}")

    def _run_default_acl_mutations(
        self,
        connection: DatabaseConnection,
        dialect: str,
    ) -> list[AclMutationResult]:
        results: list[AclMutationResult] = []
        created_roles: list[str] = []
        created_grants: list[tuple[str, str, list[str]]] = []

        role_names = [
            "benchbox_test_role_reader",
            "benchbox_test_role_writer",
            "benchbox_test_role_admin",
        ]

        for role_name in role_names:
            result = self._measure_create_role(connection, dialect, role_name)
            results.append(result)
            if result.success:
                created_roles.append(role_name)

        test_table = "benchbox_acl_mutation_test"
        try:
            self._execute(
                connection,
                f"CREATE TABLE IF NOT EXISTS {test_table} (id INTEGER, data VARCHAR(100));",
            )
        except Exception as e:
            logger.warning(f"Could not create ACL test table: {e}")
            for role_name in reversed(created_roles):
                results.append(self._measure_drop_role(connection, dialect, role_name))
            return results

        privilege_sets = [
            ["SELECT"],
            ["SELECT", "INSERT"],
            ["SELECT", "INSERT", "UPDATE", "DELETE"],
        ]

        for role_name, privs in zip(created_roles, privilege_sets):
            result = self._measure_grant(connection, dialect, role_name, test_table, privs)
            results.append(result)
            if result.success:
                created_grants.append((role_name, test_table, privs))

        for role_name, table_name, privs in reversed(created_grants):
            result = self._measure_revoke(connection, dialect, role_name, table_name, privs)
            results.append(result)

        try:
            self._execute(connection, f"DROP TABLE IF EXISTS {test_table};")
        except Exception as e:
            logger.warning(f"Could not drop ACL test table: {e}")

        for role_name in reversed(created_roles):
            result = self._measure_drop_role(connection, dialect, role_name)
            results.append(result)

        return results

    def _build_acl_summary(self, results: list[AclMutationResult]) -> dict[str, Any]:
        if not results:
            return {}

        total_ops = len(results)
        successful_ops = sum(1 for r in results if r.success)
        failed_ops = total_ops - successful_ops

        times = [r.execution_time_ms for r in results if r.success]

        by_operation: dict[str, list[AclMutationResult]] = {}
        for r in results:
            by_operation.setdefault(r.operation, []).append(r)

        operation_summary = {}
        for op, op_results in by_operation.items():
            op_times = [r.execution_time_ms for r in op_results if r.success]
            operation_summary[op] = {
                "count": len(op_results),
                "successful": sum(1 for r in op_results if r.success),
                "failed": sum(1 for r in op_results if not r.success),
                "total_time_ms": sum(op_times) if op_times else 0.0,
                "avg_time_ms": sum(op_times) / len(op_times) if op_times else 0.0,
            }

        return {
            "total_operations": total_ops,
            "successful_operations": successful_ops,
            "failed_operations": failed_ops,
            "total_time_ms": sum(times) if times else 0.0,
            "avg_time_ms": sum(times) / len(times) if times else 0.0,
            "min_time_ms": min(times) if times else 0.0,
            "max_time_ms": max(times) if times else 0.0,
            "by_operation": operation_summary,
        }

    def _execute(self, connection: DatabaseConnection, sql: str) -> Any:
        if hasattr(connection, "execute"):
            return connection.execute(sql)
        elif hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor
        else:
            raise TypeError(f"Unsupported connection type: {type(connection)}")

    def supports_dataframe_mode(self) -> bool:
        return True

    def skip_dataframe_data_loading(self) -> bool:
        return True

    def get_dataframe_operations(
        self,
        platform_name: str,
        spark_session: Any = None,
    ) -> Any:
        from benchbox.core.metadata_primitives.dataframe_operations import (
            get_dataframe_metadata_manager,
        )

        manager = get_dataframe_metadata_manager(platform_name, spark_session=spark_session)
        if manager is None:
            raise ValueError(
                f"Platform '{platform_name}' does not support DataFrame metadata operations. "
                f"Supported platforms: polars-df, pandas-df, pyspark-df, datafusion-df"
            )
        return manager

    def get_dataframe_capabilities(
        self,
        platform_name: str,
        spark_session: Any = None,
    ) -> Any:
        from benchbox.core.metadata_primitives.dataframe_operations import (
            get_dataframe_metadata_manager,
        )

        manager = get_dataframe_metadata_manager(platform_name, spark_session=spark_session)
        if manager is None:
            from benchbox.core.metadata_primitives.dataframe_operations import (
                get_platform_capabilities,
            )

            return get_platform_capabilities(platform_name)
        return manager.get_capabilities()

    def execute_dataframe_workload(
        self,
        *,
        ctx: Any,
        adapter: Any,
        benchmark_config: Any,
        query_filter: set[str] | None = None,
        monitor: Any | None = None,
        run_options: Any | None = None,
    ) -> list[dict[str, Any]]:
        platform_name = adapter.platform_name
        spark_session = getattr(ctx, "spark_session", None) or getattr(adapter, "spark", None)

        config_options = getattr(benchmark_config, "options", {}) or {}
        iterations = int(config_options.get("power_iterations", 1) or 1)

        categories = config_options.get("metadata_categories")
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(",")]

        complexity_preset: MetadataComplexityConfig | str | None = None
        if categories is not None and "complexity" in categories:
            complexity_preset = config_options.get("metadata_complexity_preset", "wide_tables")

        dataframes = self._get_registered_dataframes(ctx)
        if not dataframes:
            logger.info("Bootstrapping in-memory schema fixtures for Metadata Primitives DataFrame mode")
            dataframes = self._bootstrap_dataframe_fixture_tables(ctx, adapter, complexity_preset=complexity_preset)
        else:
            self._register_dataframes_with_adapter(adapter, dataframes)

        result = self.run_dataframe_benchmark(
            platform_name=platform_name,
            dataframes=dataframes,
            spark_session=spark_session,
            categories=categories,
            iterations=iterations,
        )

        output = []
        unique_ops: dict[str, int] = {}
        for res in result.results:
            if query_filter and res.query_id.upper() not in query_filter:
                continue

            iteration = unique_ops.get(res.query_id, 0) + 1
            unique_ops[res.query_id] = iteration

            output.append(
                {
                    "query_id": res.query_id,
                    "status": "SUCCESS" if res.success else "FAILED",
                    "execution_time_seconds": res.execution_time_ms / 1000.0,
                    "rows_returned": res.row_count,
                    "error": res.error,
                    "iteration": iteration,
                    "run_type": "measurement",
                }
            )
        return output

    def _bootstrap_dataframe_fixture_tables(
        self,
        ctx: Any,
        adapter: Any,
        complexity_preset: MetadataComplexityConfig | str | None = None,
    ) -> dict[str, Any]:
        dataframes = self._build_dataframe_fixture_tables(adapter)
        if complexity_preset is not None:
            dataframes.update(self.build_complexity_dataframes(adapter, complexity_preset))

        register_table = getattr(ctx, "register_table", None)
        if callable(register_table):
            for table_name, dataframe in dataframes.items():
                register_table(table_name, dataframe)

        self._register_dataframes_with_adapter(adapter, dataframes)
        return dataframes

    @staticmethod
    def _register_dataframes_with_adapter(adapter: Any, dataframes: dict[str, Any]) -> None:
        register_table = getattr(adapter, "register_table", None)
        if not callable(register_table):
            return

        for table_name, dataframe in dataframes.items():
            register_table(table_name, dataframe)

    def _build_dataframe_fixture_tables(self, adapter: Any) -> dict[str, Any]:
        schema = self.get_schema()
        return {
            table_name: self._create_fixture_dataframe(
                adapter,
                self._build_dataframe_fixture_row(table_name, table_meta.get("columns", [])),
            )
            for table_name, table_meta in schema.items()
        }

    @staticmethod
    def _build_dataframe_fixture_row(
        table_name: str,
        columns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for ordinal, column in enumerate(columns, start=1):
            column_name = str(column.get("name", f"col_{ordinal}"))
            column_type = str(column.get("type", "")).upper()
            row[column_name] = MetadataPrimitivesBenchmark._sample_dataframe_value(
                table_name=table_name,
                column_name=column_name,
                column_type=column_type,
                ordinal=ordinal,
            )
        return row

    @staticmethod
    def _sample_dataframe_value(
        *,
        table_name: str,
        column_name: str,
        column_type: str,
        ordinal: int,
    ) -> Any:
        normalized = column_type.upper().strip()
        if (
            normalized.startswith("STRUCT")
            or normalized.startswith("TUPLE")
            or normalized.startswith("OBJECT")
            or normalized.startswith("JSONB")
        ):
            return MetadataPrimitivesBenchmark._sample_struct_value(
                table_name=table_name,
                column_name=column_name,
                column_type=column_type,
                ordinal=ordinal,
            )
        if normalized.startswith("MAP"):
            return MetadataPrimitivesBenchmark._sample_map_value(
                table_name=table_name,
                column_name=column_name,
                column_type=column_type,
                ordinal=ordinal,
            )
        if normalized.startswith("ARRAY") or normalized.endswith("[]"):
            if "STRUCT" in normalized:
                return [
                    MetadataPrimitivesBenchmark._sample_struct_value(
                        table_name=table_name,
                        column_name=column_name,
                        column_type=normalized[normalized.index("STRUCT") :],
                        ordinal=ordinal,
                    )
                ]
            if "CHAR" in normalized or "VARCHAR" in normalized or "STRING" in normalized or "TEXT" in normalized:
                return [f"{table_name}_{column_name}_{ordinal}_item"]
            return [ordinal]
        if normalized.startswith("INTEGER") or normalized.startswith("BIGINT") or normalized.startswith("SMALLINT"):
            return ordinal
        if normalized.startswith("DECIMAL") or normalized.startswith("NUMERIC"):
            from decimal import Decimal

            return Decimal(ordinal) + Decimal("0.25")
        if normalized.startswith("DOUBLE") or normalized.startswith("FLOAT") or normalized.startswith("REAL"):
            return float(ordinal) + 0.5
        if normalized.startswith("BOOLEAN") or normalized.startswith("BOOL"):
            return ordinal % 2 == 0
        if normalized.startswith("TIMESTAMP") or normalized.startswith("DATETIME"):
            from datetime import datetime

            return datetime(1998, 1, min(ordinal, 28), min(ordinal, 23), 0, 0)
        if normalized.startswith("DATE"):
            return date(1998, 1, min(ordinal, 28))
        if "CHAR" in normalized or "VARCHAR" in normalized or "STRING" in normalized or "TEXT" in normalized:
            return f"{table_name}_{column_name}_{ordinal}"
        return f"{table_name}_{column_name}_{ordinal}"

    @staticmethod
    def _sample_struct_value(
        *,
        table_name: str,
        column_name: str,
        column_type: str,
        ordinal: int,
    ) -> dict[str, Any]:
        inner = MetadataPrimitivesBenchmark._bracket_inner(column_type)
        if inner is not None:
            value: dict[str, Any] = {}
            for index, part in enumerate(MetadataPrimitivesBenchmark._split_top_level(inner)):
                tokens = part.strip().split(None, 1)
                if len(tokens) != 2:
                    continue
                field_name, field_type = tokens
                value[field_name.strip("<>():")] = MetadataPrimitivesBenchmark._sample_dataframe_value(
                    table_name=table_name,
                    column_name=f"{column_name}_{field_name.strip('<>():') or index}",
                    column_type=field_type,
                    ordinal=ordinal,
                )
            if value:
                return value
        return {
            f"{table_name}_{column_name}_{ordinal}_key": ordinal,
        }

    @staticmethod
    def _sample_map_value(
        *,
        table_name: str,
        column_name: str,
        column_type: str,
        ordinal: int,
    ) -> dict[str, Any]:
        inner = MetadataPrimitivesBenchmark._bracket_inner(column_type)
        if inner is not None:
            parts = MetadataPrimitivesBenchmark._split_top_level(inner)
            if len(parts) == 2:
                key = MetadataPrimitivesBenchmark._sample_dataframe_value(
                    table_name=table_name,
                    column_name=f"{column_name}_key",
                    column_type=parts[0],
                    ordinal=ordinal,
                )
                map_value = MetadataPrimitivesBenchmark._sample_dataframe_value(
                    table_name=table_name,
                    column_name=f"{column_name}_value",
                    column_type=parts[1],
                    ordinal=ordinal,
                )
                try:
                    hash(key)
                except TypeError:
                    key = f"{table_name}_{column_name}_{ordinal}_key"
                return {key: map_value}
        return {
            f"{table_name}_{column_name}_{ordinal}_key": ordinal,
        }

    @staticmethod
    def _bracket_inner(column_type: str) -> str | None:
        for opening, closing in (("(", ")"), ("<", ">")):
            start = column_type.find(opening)
            end = column_type.rfind(closing)
            if start != -1 and end != -1 and end > start:
                return column_type[start + 1 : end]
        return None

    @staticmethod
    def _split_top_level(text: str) -> list[str]:
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        pairs = {"(": ")", "<": ">", "[": "]"}
        closers = set(pairs.values())
        for char in text:
            if char in pairs:
                depth += 1
                current.append(char)
            elif char in closers:
                depth = max(depth - 1, 0)
                current.append(char)
            elif char == "," and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(char)
        parts.append("".join(current))
        return parts

    def build_complexity_dataframes(
        self,
        adapter: Any,
        config: MetadataComplexityConfig | str = "wide_tables",
    ) -> dict[str, Any]:
        if isinstance(config, str):
            config = get_complexity_preset(config)
        wide_columns = generate_wide_table_columns(
            width=config.width_factor,
            dialect="duckdb",
            type_complexity=config.type_complexity,
        )
        wide_row: dict[str, Any] = {}
        for ordinal, column in enumerate(wide_columns, start=1):
            wide_row[column.name] = self._sample_dataframe_value(
                table_name="stress_wide",
                column_name=column.name,
                column_type=column.data_type,
                ordinal=ordinal,
            )
        tables: dict[str, Any] = {
            "stress_wide": self._create_fixture_dataframe(adapter, wide_row),
        }
        for index in range(1, max(config.catalog_size, 1) + 1):
            table_name = f"stress_catalog_{index:04d}"
            row = self._build_dataframe_fixture_row(
                table_name,
                [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR(255)"}],
            )
            tables[table_name] = self._create_fixture_dataframe(adapter, row)
        return tables

    @staticmethod
    def _create_fixture_dataframe(adapter: Any, row: dict[str, Any]) -> Any:
        platform_name = str(getattr(adapter, "platform_name", "")).lower()

        if "polars" in platform_name:
            import polars as pl

            return pl.DataFrame([row])

        if "pandas" in platform_name:
            import pandas as pd

            return pd.DataFrame([row])

        if "pyspark" in platform_name or "spark" in platform_name:
            spark_session = getattr(adapter, "spark", None)
            if spark_session is None:
                raise ValueError("PySpark metadata fixtures require an active SparkSession on the adapter.")
            return spark_session.createDataFrame([row])

        if "datafusion" in platform_name:
            import pyarrow as pa

            session_ctx = getattr(adapter, "session_ctx", None)
            if session_ctx is None:
                raise ValueError("DataFusion metadata fixtures require an active SessionContext on the adapter.")
            return session_ctx.from_arrow(pa.Table.from_pylist([row]))

        raise ValueError(
            f"Metadata Primitives DataFrame fixtures are not implemented for platform '{adapter.platform_name}'."
        )

    @staticmethod
    def _get_registered_dataframes(ctx: Any) -> dict[str, Any]:
        if hasattr(ctx, "list_tables") and hasattr(ctx, "get_table"):
            dataframes: dict[str, Any] = {}
            for table_name in ctx.list_tables():
                dataframe = ctx.get_table(table_name)
                dataframes[table_name] = getattr(dataframe, "native", dataframe)
            return dataframes

        for attr_name in ("tables", "_tables"):
            tables = getattr(ctx, attr_name, None)
            if isinstance(tables, dict):
                return {name: getattr(dataframe, "native", dataframe) for name, dataframe in tables.items()}

        raise TypeError(
            "Metadata Primitives DataFrame execution requires a context exposing "
            "list_tables()/get_table() or a table dictionary."
        )

    def run_dataframe_benchmark(
        self,
        platform_name: str,
        dataframes: dict[str, Any],
        spark_session: Any = None,
        categories: list[str] | None = None,
        iterations: int = 1,
    ) -> MetadataBenchmarkResult:
        from benchbox.core.metadata_primitives.dataframe_operations import (
            OPERATION_CATEGORIES,
            MetadataOperationCategory,
            MetadataOperationType,
        )

        manager = self.get_dataframe_operations(platform_name, spark_session=spark_session)
        capabilities = manager.get_capabilities()

        operations_to_run = self._resolve_dataframe_operations(
            capabilities=capabilities,
            categories=categories,
            operation_categories=OPERATION_CATEGORIES,
            operation_type_cls=MetadataOperationType,
            category_cls=MetadataOperationCategory,
        )

        result = MetadataBenchmarkResult()
        all_results: list[MetadataQueryResult] = []

        dataframe_scoped_ops = {
            MetadataOperationType.LIST_COLUMNS,
            MetadataOperationType.GET_DTYPES,
            MetadataOperationType.GET_SCHEMA,
            MetadataOperationType.DESCRIBE_STATS,
            MetadataOperationType.ROW_COUNT,
            MetadataOperationType.COLUMN_COUNT,
            MetadataOperationType.WIDE_TABLE_SCHEMA,
            MetadataOperationType.COMPLEX_TYPE_INTROSPECTION,
        }
        catalog_global_ops = {
            MetadataOperationType.LIST_DATABASES,
            MetadataOperationType.LIST_TABLES,
            MetadataOperationType.LARGE_CATALOG_LIST,
        }
        catalog_table_ops = {
            MetadataOperationType.LIST_TABLE_COLUMNS,
            MetadataOperationType.TABLE_EXISTS,
            MetadataOperationType.GET_TABLE_INFO,
        }

        for _ in range(iterations):
            for op in operations_to_run:
                if op in dataframe_scoped_ops:
                    for table_name, df in dataframes.items():
                        df_result = self._execute_dataframe_operation(manager, op, df, table_name)
                        query_result = self._convert_df_result_to_query_result(df_result, table_name)
                        all_results.append(query_result)

                        if query_result.success:
                            result.successful_queries += 1
                        else:
                            result.failed_queries += 1
                        result.total_time_ms += query_result.execution_time_ms

                elif op in catalog_global_ops:
                    df_result = self._execute_catalog_operation(manager, op)
                    query_result = self._convert_df_result_to_query_result(df_result, "catalog")
                    all_results.append(query_result)

                    if query_result.success:
                        result.successful_queries += 1
                    else:
                        result.failed_queries += 1
                    result.total_time_ms += query_result.execution_time_ms

                elif op in catalog_table_ops:
                    for table_name in dataframes:
                        df_result = self._execute_catalog_operation(manager, op, table_name=table_name)
                        query_result = self._convert_df_result_to_query_result(df_result, table_name)
                        all_results.append(query_result)

                        if query_result.success:
                            result.successful_queries += 1
                        else:
                            result.failed_queries += 1
                        result.total_time_ms += query_result.execution_time_ms

        result.total_queries = len(all_results)
        result.results = all_results

        self._build_dataframe_category_summary(result, all_results)

        return result

    def _execute_dataframe_operation(
        self,
        manager: Any,
        operation: Any,
        dataframe: Any,
        table_name: str,
    ) -> Any:
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
        )

        op_map = {
            MetadataOperationType.LIST_COLUMNS: manager.execute_list_columns,
            MetadataOperationType.GET_DTYPES: manager.execute_get_dtypes,
            MetadataOperationType.GET_SCHEMA: manager.execute_get_schema,
            MetadataOperationType.DESCRIBE_STATS: manager.execute_describe_stats,
            MetadataOperationType.ROW_COUNT: manager.execute_row_count,
            MetadataOperationType.COLUMN_COUNT: manager.execute_column_count,
            MetadataOperationType.WIDE_TABLE_SCHEMA: manager.execute_wide_table_schema,
            MetadataOperationType.COMPLEX_TYPE_INTROSPECTION: manager.execute_complex_type_introspection,
        }

        if operation in op_map:
            return op_map[operation](dataframe)

        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataResult,
        )

        return DataFrameMetadataResult.failure_result(
            operation,
            f"Operation {operation.value} not supported for DataFrame introspection",
        )

    def _execute_catalog_operation(
        self,
        manager: Any,
        operation: Any,
        *,
        table_name: str | None = None,
    ) -> Any:
        from benchbox.core.metadata_primitives.dataframe_operations import (
            MetadataOperationType,
        )

        if operation == MetadataOperationType.LIST_DATABASES:
            return manager.execute_list_databases()
        if operation == MetadataOperationType.LIST_TABLES:
            return manager.execute_list_tables()
        if operation == MetadataOperationType.LARGE_CATALOG_LIST:
            return manager.execute_large_catalog_list()
        if operation == MetadataOperationType.LIST_TABLE_COLUMNS:
            return manager.execute_list_table_columns(table_name or "")
        if operation == MetadataOperationType.TABLE_EXISTS:
            return manager.execute_table_exists(table_name or "")
        if operation == MetadataOperationType.GET_TABLE_INFO:
            return manager.execute_get_table_info(table_name or "")

        from benchbox.core.metadata_primitives.dataframe_operations import (
            DataFrameMetadataResult,
        )

        return DataFrameMetadataResult.failure_result(
            operation,
            f"Catalog operation {operation.value} not implemented",
        )

    @staticmethod
    def _resolve_dataframe_operations(
        *,
        capabilities: Any,
        categories: list[str] | None,
        operation_categories: dict[Any, Any],
        operation_type_cls: Any,
        category_cls: Any,
    ) -> list[Any]:
        default_operations = [
            operation_type_cls.LIST_COLUMNS,
            operation_type_cls.GET_DTYPES,
            operation_type_cls.GET_SCHEMA,
            operation_type_cls.DESCRIBE_STATS,
            operation_type_cls.ROW_COUNT,
            operation_type_cls.COLUMN_COUNT,
            operation_type_cls.LIST_DATABASES,
            operation_type_cls.LIST_TABLES,
        ]
        benchmark_managed_operations = {
            *default_operations,
            operation_type_cls.LIST_TABLE_COLUMNS,
            operation_type_cls.TABLE_EXISTS,
            operation_type_cls.GET_TABLE_INFO,
            operation_type_cls.WIDE_TABLE_SCHEMA,
            operation_type_cls.LARGE_CATALOG_LIST,
            operation_type_cls.COMPLEX_TYPE_INTROSPECTION,
        }

        if categories:
            category_set = {category_cls(c.lower()) for c in categories}
            candidate_operations = [
                op
                for op in operation_type_cls
                if op in benchmark_managed_operations and operation_categories.get(op) in category_set
            ]
        else:
            candidate_operations = default_operations

        return [op for op in candidate_operations if capabilities.supports_operation(op)]

    def _convert_df_result_to_query_result(
        self,
        df_result: Any,
        table_name: str,
    ) -> MetadataQueryResult:
        from benchbox.core.metadata_primitives.dataframe_operations import (
            OPERATION_CATEGORIES,
        )

        category = OPERATION_CATEGORIES.get(df_result.operation_type)
        category_str = category.value if category else "schema"

        return MetadataQueryResult(
            query_id=f"df_{df_result.operation_type.value}_{table_name}",
            category=category_str,
            execution_time_ms=df_result.duration_ms,
            row_count=df_result.result_count,
            success=df_result.success,
            error=df_result.error_message,
        )

    def _build_dataframe_category_summary(
        self,
        result: MetadataBenchmarkResult,
        all_results: list[MetadataQueryResult],
    ) -> None:
        category_times: dict[str, list[float]] = {}
        category_counts: dict[str, int] = {}
        category_successes: dict[str, int] = {}

        for qr in all_results:
            cat = qr.category
            if cat not in category_times:
                category_times[cat] = []
                category_counts[cat] = 0
                category_successes[cat] = 0

            category_times[cat].append(qr.execution_time_ms)
            category_counts[cat] += 1
            if qr.success:
                category_successes[cat] += 1

        for cat in category_times:
            times = category_times[cat]
            result.category_summary[cat] = {
                "total_queries": category_counts[cat],
                "successful": category_successes[cat],
                "failed": category_counts[cat] - category_successes[cat],
                "total_time_ms": sum(times),
                "avg_time_ms": sum(times) / len(times) if times else 0.0,
                "min_time_ms": min(times) if times else 0.0,
                "max_time_ms": max(times) if times else 0.0,
            }


__all__ = [
    "AclBenchmarkResult",
    "AclMutationResult",
    "ComplexityBenchmarkResult",
    "MetadataBenchmarkResult",
    "MetadataPrimitivesBenchmark",
    "MetadataQueryResult",
]
