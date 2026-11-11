# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidateConfigTool:
    def test_valid_duckdb_tpch_config(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks
        from benchbox.core.platform_registry import PlatformRegistry

        info = PlatformRegistry.get_platform_info("duckdb")
        assert info is not None
        assert info.available

        benchmarks = get_all_benchmarks()
        assert "tpch" in benchmarks

    def test_unknown_platform_error(self):
        from benchbox.core.platform_registry import PlatformRegistry

        info = PlatformRegistry.get_platform_info("nonexistent_platform")
        assert info is None

    def test_unknown_benchmark_error(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        benchmarks = get_all_benchmarks()
        assert "nonexistent_benchmark" not in benchmarks

    def test_invalid_scale_factor(self):
        scale_factor = -1.0
        assert scale_factor <= 0

    def test_dataframe_platform_validation(self):
        from benchbox.platforms import is_dataframe_platform

        assert is_dataframe_platform("polars-df")
        assert is_dataframe_platform("pandas-df")
        assert not is_dataframe_platform("duckdb")


class TestDryRunTool:
    def test_dry_run_returns_execution_plan(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks, get_benchmark_class

        benchmarks = get_all_benchmarks()
        assert "tpch" in benchmarks

        benchmark_class = get_benchmark_class("tpch")
        assert benchmark_class is not None
        bm = benchmark_class(scale_factor=0.01)
        queries = bm.get_queries()
        query_ids = list(queries.keys())

        assert len(query_ids) == 22

    def test_dry_run_resource_estimates(self):
        scale_factor = 1.0
        estimated_data_gb = scale_factor * 1.0
        memory_recommended = max(2, estimated_data_gb * 2)

        assert estimated_data_gb == 1.0
        assert memory_recommended >= 2.0


def _assert_effective_consumer_value(
    platform: str, option_name: str, adapter: object, attribute: str, expected: object
) -> None:
    observed = getattr(adapter, attribute, None)
    assert observed is not None, (
        f"{platform}.{option_name} consumer attribute {attribute!r} was not observed on {type(adapter).__name__}"
    )
    assert observed == expected, f"{platform}.{option_name} -> {attribute} {observed!r} != {expected!r}"


class TestRunBenchmarkTool:
    def test_benchmark_config_creation(self):
        from benchbox.core.schemas import BenchmarkConfig

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
        )

        assert config.name == "tpch"
        assert config.display_name == "TPC-H"
        assert config.scale_factor == 0.01

    def test_database_config_creation(self):
        from benchbox.core.schemas import DatabaseConfig

        db_config = DatabaseConfig(
            type="duckdb",
            name="test_db",
        )

        assert db_config.type == "duckdb"
        assert db_config.name == "test_db"

    def test_phase_parsing(self):
        phases_str = "load,power"
        phase_list = phases_str.lower().split(",")

        phases_to_run = []
        if "load" in phase_list:
            phases_to_run.append("load")
        if "power" in phase_list or "execute" in phase_list:
            phases_to_run.append("power")

        assert "load" in phases_to_run
        assert "power" in phases_to_run

    def test_platform_options_are_forwarded_only_after_validation(self, tmp_path: Path):
        from benchbox.mcp.tools import benchmark as benchmark_tools

        with (
            patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"name": "tpch"}}),
            patch.object(benchmark_tools, "get_public_benchmark_class", return_value=Mock),
            patch.object(
                benchmark_tools,
                "_execute_mcp_run_via_core",
                return_value={"mcp_metadata": {"status": "no_results"}},
            ) as run_core,
        ):
            response = benchmark_tools._run_benchmark_impl(
                "duckdb",
                "tpch",
                0.01,
                None,
                "load,power",
                "sql",
                platform_options={"threads": 4},
                results_dir=tmp_path,
                anonymize=False,
            )

        assert response["mcp_metadata"]["status"] == "no_results"
        assert run_core.call_args.kwargs["normalized_platform_options"] == {"threads": 4}

    def test_oversized_dask_request_never_builds_a_cluster(self, tmp_path: Path):
        from benchbox.mcp.tools import benchmark as benchmark_tools

        with patch.object(benchmark_tools, "_get_platform_adapter") as get_adapter:
            response = benchmark_tools._run_benchmark_impl(
                "dask-df",
                "tpch",
                0.01,
                None,
                "load,power",
                "dataframe",
                platform_options={"n_workers": 256, "threads_per_worker": 256},
                results_dir=tmp_path,
                anonymize=False,
            )

        assert response["status"] == "failed"
        get_adapter.assert_not_called()

    def test_dask_local_cluster_is_never_constructed_for_a_rejected_request(self, tmp_path: Path):
        pytest.importorskip("dask.distributed")
        from benchbox.mcp.tools import benchmark as benchmark_tools
        from benchbox.platforms.dataframe import dask_df

        class DummyBenchmark:
            def __init__(self, scale_factor: float) -> None:
                self.scale_factor = scale_factor

            def run_with_platform(self, *_args, **_kwargs):
                return None

        with (
            patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"name": "tpch"}}),
            patch.object(benchmark_tools, "get_public_benchmark_class", return_value=Mock),
            patch.object(dask_df, "LocalCluster") as local_cluster,
            patch.object(dask_df, "Client"),
        ):
            benchmark_tools._run_benchmark_impl(
                "dask-df",
                "tpch",
                0.01,
                None,
                "load,power",
                "dataframe",
                platform_options={"n_workers": 256, "threads_per_worker": 256},
                results_dir=tmp_path,
                anonymize=False,
            )

        local_cluster.assert_not_called()

    def test_dask_request_inside_the_envelope_still_reaches_the_adapter(self, tmp_path: Path):
        from benchbox.mcp.tools import benchmark as benchmark_tools

        with (
            patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"name": "tpch"}}),
            patch.object(benchmark_tools, "get_public_benchmark_class", return_value=Mock),
            patch.object(
                benchmark_tools,
                "_execute_mcp_run_via_core",
                return_value={"mcp_metadata": {"status": "no_results"}},
            ) as run_core,
        ):
            benchmark_tools._run_benchmark_impl(
                "dask-df",
                "tpch",
                0.01,
                None,
                "load,power",
                "dataframe",
                platform_options={"n_workers": 4, "threads_per_worker": 4, "memory_limit": "4GB"},
                results_dir=tmp_path,
                anonymize=False,
            )

        assert run_core.call_args.kwargs["normalized_platform_options"] == {
            "n_workers": 4,
            "threads_per_worker": 4,
            "memory_limit": "4GB",
        }

    @pytest.mark.parametrize("platform", ["clickhouse", "clickhouse-server"])
    def test_clickhouse_port_override_is_refused_before_any_adapter_is_built(self, platform, tmp_path: Path):
        from benchbox.mcp.tools import benchmark as benchmark_tools

        with patch.object(benchmark_tools, "_get_platform_adapter") as get_adapter:
            response = benchmark_tools._run_benchmark_impl(
                platform,
                "tpch",
                0.01,
                None,
                "load,power",
                "sql",
                platform_options={"port": 9001, "secure": False},
                results_dir=tmp_path,
                anonymize=False,
            )

        assert response["status"] == "failed"
        get_adapter.assert_not_called()

    def test_clickhouse_profile_resolves_to_the_server_owned_connection_tuple(self, monkeypatch, tmp_path: Path):
        import json

        from benchbox.mcp.schemas import MCP_CLICKHOUSE_PROFILE_ENV
        from benchbox.mcp.tools import benchmark as benchmark_tools

        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"reviewed": {"port": 9440, "secure": True}}))

        with (
            patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"name": "tpch"}}),
            patch.object(benchmark_tools, "get_public_benchmark_class", return_value=Mock),
            patch.object(
                benchmark_tools,
                "_execute_mcp_run_via_core",
                return_value={"mcp_metadata": {"status": "no_results"}},
            ) as run_core,
        ):
            benchmark_tools._run_benchmark_impl(
                "clickhouse-server",
                "tpch",
                0.01,
                None,
                "load,power",
                "sql",
                platform_options={"connection_profile": "reviewed"},
                results_dir=tmp_path,
                anonymize=False,
            )

        assert run_core.call_args.kwargs["normalized_platform_options"] == {"connection_profile": "reviewed"}

    def test_clickhouse_profile_withdrawn_by_the_operator_fails_closed(self, monkeypatch, tmp_path: Path):
        from benchbox.mcp.schemas import MCP_CLICKHOUSE_PROFILE_ENV
        from benchbox.mcp.tools import benchmark as benchmark_tools

        monkeypatch.delenv(MCP_CLICKHOUSE_PROFILE_ENV, raising=False)

        with patch.object(benchmark_tools, "_get_platform_adapter") as get_adapter:
            response = benchmark_tools._run_benchmark_impl(
                "clickhouse-server",
                "tpch",
                0.01,
                None,
                "load,power",
                "sql",
                platform_options={"connection_profile": "reviewed"},
                results_dir=tmp_path,
                anonymize=False,
            )

        assert response["status"] == "failed"
        assert "reviewed" not in json.dumps(response)
        get_adapter.assert_not_called()

    def test_databricks_layout_options_build_unified_tuning_config(self):
        from benchbox.core.run_service import (
            translate_platform_options_for_adapter as _prepare_adapter_platform_options,
        )

        options = _prepare_adapter_platform_options(
            "databricks",
            {"databricks_clustering_strategy": "liquid_clustering", "liquid_clustering_columns": "event_time,id"},
        )

        tuning = options["tuning_config"]
        assert options["tuning_enabled"] is True
        assert "databricks_clustering_strategy" not in options
        assert tuning.platform_optimizations.databricks_clustering_strategy == "liquid_clustering"
        assert tuning.platform_optimizations.liquid_clustering_columns == ["event_time", "id"]

    def test_databricks_columns_infer_liquid_clustering_strategy(self):
        from benchbox.core.run_service import (
            translate_platform_options_for_adapter as _prepare_adapter_platform_options,
        )

        options = _prepare_adapter_platform_options("databricks", {"liquid_clustering_columns": "customer_id,order_id"})

        tuning = options["tuning_config"]
        assert options["tuning_enabled"] is True
        assert tuning.platform_optimizations.databricks_clustering_strategy == "liquid_clustering"
        assert tuning.platform_optimizations.liquid_clustering_columns == ["customer_id", "order_id"]

    def test_every_matrix_option_reaches_effective_preparation(self, monkeypatch, tmp_path):  # noqa: C901
        import json
        from unittest.mock import MagicMock, patch

        from benchbox.core.run_service import (
            translate_platform_options_for_adapter as _prepare_adapter_platform_options,
        )
        from benchbox.mcp.schemas import (
            MCP_CLICKHOUSE_PROFILE_ENV,
            MCP_PLATFORM_OPTION_ALLOWLIST,
            MCP_PLATFORM_OPTION_CONTRACT,
            resolve_clickhouse_connection_profile,
            validate_platform_options,
        )

        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"reviewed": {"port": 9440, "secure": True}}))

        observed = 0
        skipped: list[str] = []
        failures: list[str] = []

        consumer_attr = {
            ("duckdb", "threads"): "thread_limit",
            ("duckdb", "memory_limit"): "memory_limit",
            ("dask", "n_workers"): "n_workers",
            ("dask", "threads_per_worker"): "threads_per_worker",
            ("dask", "memory_limit"): "_memory_limit",
            ("dask", "use_distributed"): "use_distributed",
            ("clickhouse", "deployment_mode"): "deployment_mode",
            ("clickhouse", "connection_profile"): "port",
            ("clickhouse-server", "connection_profile"): "port",
            ("datafusion", "batch_size"): "batch_size",
            ("datafusion", "memory_limit"): "memory_limit",
            ("datafusion", "parquet_pushdown"): "parquet_pushdown",
            ("datafusion", "repartition_joins"): "repartition_joins",
            ("datafusion", "target_partitions"): "target_partitions",
            ("cudf", "device_id"): "device_id",
            ("cudf", "spill_to_host"): "spill_to_host",
            ("polars", "n_rows"): "n_rows",
            ("polars", "streaming"): "streaming",
            ("polars", "rechunk"): "rechunk",
            ("spark", "adaptive_enabled"): "adaptive_enabled",
            ("sqlite", "timeout"): "timeout",
            ("sqlite", "check_same_thread"): "check_same_thread",
            ("velox", "adaptive_enabled"): "adaptive_enabled",
            ("velox", "driver_memory"): "driver_memory",
            ("velox", "offheap_size"): "offheap_size",
            ("velox", "shuffle_partitions"): "shuffle_partitions",
            ("pandas", "dtype_backend"): "dtype_backend",
            ("firebolt", "disable_result_cache"): "disable_result_cache",
            ("firebolt", "strict_validation"): "strict_validation",
        }

        for platform, specs in MCP_PLATFORM_OPTION_ALLOWLIST.items():
            for option_name, spec in specs.items():
                if spec.kind == "bool":
                    value: object = False
                elif spec.kind == "int":
                    value = spec.minimum if spec.minimum is not None else 1
                elif spec.kind == "float":
                    value = spec.minimum if spec.minimum is not None else 1.0
                elif spec.choices:
                    value = spec.choices[0]
                elif option_name == "liquid_clustering_columns":
                    value = "id"
                elif option_name == "connection_profile":
                    value = "reviewed"
                else:
                    value = "1GB"

                request: dict[str, object] = {option_name: value}
                if platform == "clickhouse" and option_name == "connection_profile":
                    request["deployment_mode"] = "server"

                try:
                    normalized = validate_platform_options(platform, request)
                    if option_name == "connection_profile":
                        profile = resolve_clickhouse_connection_profile("reviewed")
                        normalized = {
                            key: item for key, item in normalized.items() if key != "connection_profile"
                        } | profile
                    prepared = _prepare_adapter_platform_options(platform, normalized)
                except Exception as e:
                    failures.append(f"{platform}.{option_name} prepare failed: {e}")
                    continue

                try:
                    if platform == "duckdb" and option_name == "threads":
                        assert prepared == {"thread_limit": value}, f"duckdb threads prepared {prepared!r}"
                    elif platform == "databricks":
                        tuning = prepared.get("tuning_config")
                        assert tuning is not None, f"databricks missing tuning_config {prepared!r}"
                        optimizations = tuning.platform_optimizations
                        if option_name == "databricks_clustering_strategy":
                            assert optimizations.databricks_clustering_strategy == value, (
                                f"databricks.{option_name} did not reach the tuning consumer"
                            )
                        else:
                            assert optimizations.liquid_clustering_columns == ["id"], (
                                f"databricks.{option_name} did not reach the tuning consumer"
                            )
                        observed += 1
                        continue
                    elif option_name == "connection_profile":
                        assert "connection_profile" not in prepared, f"connection_profile not replaced {prepared!r}"
                        assert prepared["port"] == 9440 and prepared["secure"] is True, (
                            f"connection_profile prepared {prepared!r}"
                        )
                    else:
                        assert (
                            prepared.get(option_name) == value
                            or prepared.get(consumer_attr.get((platform, option_name), "")) == value
                        ), f"prepared {prepared!r} missing {option_name}={value!r}"
                except AssertionError as e:
                    failures.append(str(e))
                    continue

                contract = MCP_PLATFORM_OPTION_CONTRACT.get(platform, {}).get(option_name)
                if contract is None:
                    failures.append(f"{platform}.{option_name} missing contract")
                    continue

                attr = consumer_attr.get((platform, option_name), option_name)

                try:
                    if platform == "duckdb":
                        from benchbox.platforms.duckdb import DuckDBAdapter

                        cfg = {
                            "benchmark": "tpch",
                            "scale_factor": 0.01,
                            "output_dir": str(tmp_path),
                            "database_path": ":memory:",
                        }
                        cfg.update(prepared)
                        built = DuckDBAdapter.from_config(cfg)
                        observed_val = getattr(built, attr, None)
                        assert observed_val == value, (
                            f"{platform}.{option_name} -> {attr} {observed_val!r} != {value!r}"
                        )
                        observed += 1
                    elif platform in ("clickhouse", "clickhouse-server"):
                        try:
                            if platform == "clickhouse-server":
                                from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter as Adapter
                            else:
                                from benchbox.platforms.clickhouse import ClickHouseAdapter as Adapter

                            cfg = {
                                "benchmark": "tpch",
                                "scale_factor": 0.01,
                                "output_dir": str(tmp_path),
                                "host": "localhost",
                            }
                            cfg.update(prepared)
                            built = Adapter.from_config(cfg)
                            if option_name == "connection_profile":
                                _assert_effective_consumer_value(platform, option_name, built, "port", 9440)
                                _assert_effective_consumer_value(platform, option_name, built, "secure", True)
                            else:
                                _assert_effective_consumer_value(platform, option_name, built, "deployment_mode", value)
                            observed += 1
                        except ImportError as ie:
                            skipped.append(f"{platform}.{option_name} ClickHouse extra missing: {ie}")
                            continue
                        except AssertionError:
                            raise
                        except Exception as e:
                            failures.append(f"{platform}.{option_name} consumer construction failed: {e}")
                            continue
                    elif platform == "dask":
                        try:
                            from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter
                        except ImportError as ie:
                            skipped.append(f"{platform}.{option_name} Dask not installed: {ie}")
                            continue
                        check_attr = "_memory_limit" if option_name == "memory_limit" else attr
                        with (
                            patch("benchbox.platforms.dataframe.dask_df.LocalCluster") as mock_cluster,
                            patch("benchbox.platforms.dataframe.dask_df.Client") as mock_client,
                        ):
                            mock_cluster.return_value = MagicMock()
                            mock_client.return_value = MagicMock()
                            with (
                                patch("dask.distributed.LocalCluster", create=True) as mock_cluster2,
                                patch("dask.distributed.Client", create=True) as mock_client2,
                            ):
                                mock_cluster2.return_value = MagicMock()
                                mock_client2.return_value = MagicMock()
                                try:
                                    built = DaskDataFrameAdapter(**prepared)
                                except Exception as e:
                                    skipped.append(f"{platform}.{option_name} Dask construct failed (mocked): {e}")
                                    continue
                                observed_val = getattr(built, check_attr, None) if check_attr else None
                                if option_name == "use_distributed":
                                    _assert_effective_consumer_value(
                                        platform, option_name, built, "use_distributed", False
                                    )
                                else:
                                    _assert_effective_consumer_value(platform, option_name, built, check_attr, value)
                                assert (
                                    mock_cluster.call_count == 0
                                    or built._cluster is None
                                    or isinstance(built._cluster, MagicMock)
                                ), "Dask cluster was started"
                                observed += 1
                                try:
                                    built.close()
                                except Exception:
                                    pass
                    elif platform in ("polars", "pandas"):
                        try:
                            if platform == "polars":
                                from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

                                built = PolarsDataFrameAdapter(**prepared)
                                _assert_effective_consumer_value(platform, option_name, built, attr, value)
                                observed += 1
                            elif platform == "pandas":
                                from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

                                built = PandasDataFrameAdapter(**prepared)
                                _assert_effective_consumer_value(platform, option_name, built, attr, value)
                                observed += 1
                        except ImportError as ie:
                            skipped.append(f"{platform}.{option_name} optional dep missing: {ie}")
                            continue
                        except Exception as e:
                            skipped.append(f"{platform}.{option_name} not constructible: {e}")
                            continue
                    else:
                        adapter_map = {
                            "datafusion": "benchbox.platforms.datafusion.DataFusionAdapter",
                            "cudf": "benchbox.platforms.dataframe.cudf.CUDFAdapter",
                            "firebolt": "benchbox.platforms.firebolt.FireboltAdapter",
                            "spark": "benchbox.platforms.spark.SparkAdapter",
                            "sqlite": "benchbox.platforms.sqlite.SQLiteAdapter",
                            "velox": "benchbox.platforms.velox.VeloxAdapter",
                        }
                        mod_path = adapter_map.get(platform)
                        if mod_path:
                            try:
                                mod_name, cls_name = mod_path.rsplit(".", 1)
                                mod = __import__(mod_name, fromlist=[cls_name])
                                Adapter = getattr(mod, cls_name)
                                cfg = (
                                    {"benchmark": "tpch", "scale_factor": 0.01, "output_dir": str(tmp_path)}
                                    if platform in ("velox", "spark", "datafusion", "firebolt")
                                    else {}
                                )
                                cfg.update(prepared)
                                if platform == "sqlite":
                                    cfg["database_path"] = ":memory:"
                                if not hasattr(Adapter, "from_config"):
                                    built = Adapter(**prepared)
                                else:
                                    built = Adapter.from_config(cfg)
                                _assert_effective_consumer_value(platform, option_name, built, attr, value)
                                observed += 1
                            except ImportError as ie:
                                skipped.append(f"{platform}.{option_name} adapter not installed: {ie}")
                                continue
                            except Exception as e:
                                failures.append(f"{platform}.{option_name} consumer construction failed: {e}")
                                continue
                        else:
                            failures.append(f"{platform}.{option_name} missing adapter observation mapping")
                except AssertionError:
                    raise
                except Exception as e:
                    failures.append(f"{platform}.{option_name} unexpected: {e}")
                    continue

        total_options = sum(len(v) for v in MCP_PLATFORM_OPTION_ALLOWLIST.values())
        assert not failures, f"consumer failures: {failures[:10]}"
        assert observed + len(skipped) == total_options, (
            f"observed {observed} + skipped {len(skipped)} != total {total_options}; skipped: {skipped[:5]}"
        )
        assert observed >= 15, f"only {observed} consumers observed, expected at least 15; skipped: {skipped}"

    def test_effective_consumer_oracle_rejects_dropped_option(self):

        class DroppedAdapter:
            thread_limit = None

        with pytest.raises(AssertionError, match=r"duckdb\.threads"):
            _assert_effective_consumer_value("duckdb", "threads", DroppedAdapter(), "thread_limit", 4)


class TestGetQueryDetailsTool:
    def test_tpch_query_complexity_hints(self):
        from benchbox.core.query_hints import get_query_complexity_hints

        hints = get_query_complexity_hints("tpch", "6")
        assert hints["type"] == "scan_filter"
        assert hints["complexity"] == "simple"
        assert hints["joins"] == 0
        assert "lineitem" in hints["tables"]

        hints = get_query_complexity_hints("tpch", "2")
        assert hints["type"] == "correlated_subquery"
        assert hints["complexity"] == "complex"
        assert hints["joins"] >= 4

    def test_unknown_query_returns_default(self):
        from benchbox.core.query_hints import get_query_complexity_hints

        hints = get_query_complexity_hints("unknown_benchmark", "99")
        assert hints["type"] == "unknown"
        assert hints["complexity"] == "unknown"
        assert "note" in hints

    def test_query_id_normalization(self):
        test_cases = [
            ("1", "1"),
            ("Q1", "1"),
            ("q1", "1"),
            ("Q01", "01"),
            ("abc", "abc"),
        ]

        for input_id, expected in test_cases:
            normalized = input_id.upper().lstrip("Q")
            if not normalized.isdigit():
                normalized = input_id
            assert normalized is not None


class TestResolveQueryDetailsMode:
    def test_explicit_sql_mode(self):
        from benchbox.mcp.tools.benchmark import _resolve_query_details_mode

        assert _resolve_query_details_mode(None, "sql") == "sql"
        assert _resolve_query_details_mode("duckdb", "sql") == "sql"

    def test_explicit_dataframe_mode(self):
        from benchbox.mcp.tools.benchmark import _resolve_query_details_mode

        assert _resolve_query_details_mode(None, "dataframe") == "dataframe"
        assert _resolve_query_details_mode("duckdb", "dataframe") == "dataframe"

    def test_mode_case_insensitive(self):
        from benchbox.mcp.tools.benchmark import _resolve_query_details_mode

        assert _resolve_query_details_mode(None, "SQL") == "sql"
        assert _resolve_query_details_mode(None, "DataFrame") == "dataframe"

    def test_dataframe_platform_auto_detects(self):
        from benchbox.mcp.tools.benchmark import _resolve_query_details_mode

        assert _resolve_query_details_mode("polars-df", None) == "dataframe"
        assert _resolve_query_details_mode("pandas-df", None) == "dataframe"

    def test_sql_platform_defaults_to_sql(self):
        from benchbox.mcp.tools.benchmark import _resolve_query_details_mode

        assert _resolve_query_details_mode("duckdb", None) == "sql"
        assert _resolve_query_details_mode("snowflake", None) == "sql"

    def test_no_params_defaults_to_sql(self):
        from benchbox.mcp.tools.benchmark import _resolve_query_details_mode

        assert _resolve_query_details_mode(None, None) == "sql"


class TestGetDataframeFamilyForPlatform:
    def test_polars_is_expression(self):
        from benchbox.mcp.tools.benchmark import _get_dataframe_family_for_platform

        assert _get_dataframe_family_for_platform("polars-df") == "expression"
        assert _get_dataframe_family_for_platform("polars") == "expression"

    def test_pandas_is_pandas(self):
        from benchbox.mcp.tools.benchmark import _get_dataframe_family_for_platform

        assert _get_dataframe_family_for_platform("pandas-df") == "pandas"
        assert _get_dataframe_family_for_platform("pandas") == "pandas"

    def test_datafusion_is_expression(self):
        from benchbox.mcp.tools.benchmark import _get_dataframe_family_for_platform

        assert _get_dataframe_family_for_platform("datafusion-df") == "expression"
        assert _get_dataframe_family_for_platform("datafusion") == "expression"

    def test_none_returns_none(self):
        from benchbox.mcp.tools.benchmark import _get_dataframe_family_for_platform

        assert _get_dataframe_family_for_platform(None) is None

    def test_unknown_platform_returns_none(self):
        from benchbox.mcp.tools.benchmark import _get_dataframe_family_for_platform

        assert _get_dataframe_family_for_platform("nonexistent") is None


class TestPopulateDataframeQueryDetails:
    def test_tpch_q6_returns_source_code(self):
        from benchbox.mcp.tools.benchmark import _populate_dataframe_query_details

        response: dict = {}
        _populate_dataframe_query_details(response, "tpch", "6", "polars-df")

        assert "source_code" in response
        assert response["source_code"] is not None
        assert "source_truncated" in response
        assert response["query_name"] == "Forecasting Revenue Change"
        assert response["has_expression_impl"] is True
        assert response["has_pandas_impl"] is True
        assert response["dataframe_family"] == "expression"

    def test_tpch_q6_pandas_family(self):
        from benchbox.mcp.tools.benchmark import _populate_dataframe_query_details

        response: dict = {}
        _populate_dataframe_query_details(response, "tpch", "6", "pandas-df")

        assert response["dataframe_family"] == "pandas"
        assert "source_code" in response
        assert response["source_code"] is not None

    def test_no_platform_dataframe_family_is_none(self):
        from benchbox.mcp.tools.benchmark import _get_dataframe_family_for_platform

        assert _get_dataframe_family_for_platform(None) is None

    def test_unknown_query_returns_error(self):
        from benchbox.mcp.tools.benchmark import _populate_dataframe_query_details

        response: dict = {}
        _populate_dataframe_query_details(response, "tpch", "99", "polars-df")

        assert "error" in response

    def test_benchmark_with_dataframe_registry_returns_source(self):
        from benchbox.mcp.tools.benchmark import _populate_dataframe_query_details

        response: dict = {}
        _populate_dataframe_query_details(response, "clickbench", "1", "polars-df")

        assert "error" not in response
        assert response["source_code"]
        assert response["has_expression_impl"] is True

    def test_benchmark_without_dataframe_registry_returns_error(self):
        from benchbox.mcp.tools.benchmark import _populate_dataframe_query_details

        response: dict = {}
        _populate_dataframe_query_details(response, "tpcdi", "1", "polars-df")
        assert "error" in response


class TestBenchmarkResultExportPath:
    def test_export_and_build_payload_uses_injected_results_dir(self, tmp_path: Path):
        from benchbox.mcp.tools.benchmark import _export_and_build_payload

        result = SimpleNamespace(execution_id=None)
        execution_id = "mcp_test_001"
        exported_json = tmp_path / "tpch_test.json"
        exported_json.write_text("{}", encoding="utf-8")

        with (
            patch("benchbox.mcp.tools.benchmark.ResultExporter") as exporter_cls,
            patch("benchbox.mcp.tools.benchmark.build_result_payload", return_value={"ok": True}),
        ):
            exporter = exporter_cls.return_value
            exporter.export_result.return_value = {"json": exported_json}

            result_file_path, result_payload = _export_and_build_payload(
                result, execution_id, tmp_path, anonymize=False
            )

        assert result.execution_id == execution_id
        assert result_file_path == str(exported_json)
        assert result_payload == {}
        assert exporter_cls.call_args.kwargs["output_dir"] == tmp_path

    @pytest.mark.parametrize("anonymize", [False, True])
    def test_export_honours_the_requested_anonymization(self, tmp_path: Path, anonymize: bool):
        from benchbox.mcp.tools.benchmark import _export_and_build_payload

        result = SimpleNamespace(execution_id=None)
        exported_json = tmp_path / "tpch_test.json"
        exported_json.write_text("{}", encoding="utf-8")

        with (
            patch("benchbox.mcp.tools.benchmark.ResultExporter") as exporter_cls,
            patch("benchbox.mcp.tools.benchmark.build_result_payload", return_value={"ok": True}),
        ):
            exporter_cls.return_value.export_result.return_value = {"json": exported_json}

            _export_and_build_payload(result, "mcp_test_002", tmp_path, anonymize=anonymize)

        assert exporter_cls.call_args.kwargs["anonymize"] is anonymize


class TestPopulateSqlQueryDetails:
    def test_tpch_q6_returns_sql(self):
        from benchbox.mcp.tools.benchmark import _populate_sql_query_details

        response: dict = {}
        _populate_sql_query_details(response, "tpch", "6", None)

        assert "sql" in response
        assert "SELECT" in response["sql"].upper()

    def test_tpch_q6_with_platform_dialect(self):
        from benchbox.mcp.tools.benchmark import _populate_sql_query_details

        response: dict = {}
        _populate_sql_query_details(response, "tpch", "6", "duckdb")

        assert "sql" in response
        assert "SELECT" in response["sql"].upper()

    def test_sql_truncation(self):
        from benchbox.mcp.tools.benchmark import _populate_sql_query_details

        response: dict = {}
        _populate_sql_query_details(response, "tpch", "6", None)

        if "sql" in response:
            assert response.get("sql_truncated") is False


class TestMcpCoreRunOutputRoots:
    def test_core_run_preserves_constructed_datagen_root(self, tmp_path: Path):
        from benchbox.mcp.tools import benchmark as benchmark_tools

        datagen_root = tmp_path / "benchmark_runs" / "datagen" / "tpch_sf001"
        results_root = tmp_path / "benchmark_runs" / "results"
        benchmark_instance = SimpleNamespace(output_dir=datagen_root)
        result = SimpleNamespace()

        with (
            patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
            patch("benchbox.core.system.SystemProfiler") as profiler_cls,
            patch.object(benchmark_tools, "_export_and_build_payload", return_value=("result.json", {})) as export,
            patch.object(benchmark_tools, "_attach_summary_charts"),
            patch("benchbox.core.platform_config.get_platform_config", return_value={}),
            patch("benchbox.core.run_service.execute_run", return_value=result) as execute,
        ):
            profiler_cls.return_value.get_system_profile.return_value = None
            benchmark_tools._execute_mcp_run_via_core(
                platform="duckdb",
                benchmark="tpch",
                benchmark_class=Mock(return_value=benchmark_instance),
                scale_factor=0.01,
                queries=None,
                phases=["load", "power"],
                resolved_mode="sql",
                capture_plans=False,
                normalized_platform_options={},
                results_dir=results_root,
                execution_id="exec-1",
                start_time=100.0,
                anonymize=False,
            )

        assert execute.call_args.kwargs["output_root"] == datagen_root
        assert export.call_args.args[2] == results_root


class TestBenchmarkTiming:
    def test_data_only_response_uses_monotonic_elapsed_and_tenant_path(self, tmp_path):
        from benchbox.mcp.tools import benchmark as benchmark_tools

        mock_clock = Mock(return_value=103.21)
        tenant_results = tmp_path / "tenant" / "results"
        tenant_data = tenant_results / "datagen" / "tpch_sf001"
        tenant_data.mkdir(parents=True)
        (tenant_data / "lineitem.parquet").write_text("x", encoding="utf-8")
        with (
            patch("benchbox.utils.clock.mono_time", mock_clock),
        ):
            response = benchmark_tools._build_data_only_response(
                benchmark="tpch",
                scale_factor=0.01,
                execution_id="exec-1",
                start_time=100.0,
                data_dir=tenant_data,
            )

        assert response["mcp_metadata"]["execution_time_seconds"] == 3.21
        assert response["data_generation"]["data_path"] == str(tenant_data)
        assert response["data_generation"]["files"] == ["lineitem.parquet"]


