# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json

import pytest

from benchbox.mcp.schemas import (
    MAX_QUERY_ID_LENGTH,
    MAX_QUERY_IDS,
    MCP_CLICKHOUSE_PROFILE_ENV,
    MCP_DASK_MAX_TOTAL_MEMORY_ENV,
    MCP_DASK_MAX_TOTAL_THREADS_ENV,
    MCP_DASK_MAX_WORKERS_ENV,
    MCP_PLATFORM_OPTION_ALLOWLIST,
    MCP_PLATFORM_OPTION_CONTRACT,
    MCPValidationError,
    build_databricks_clustering_intent,
    load_dask_resource_envelope,
    resolve_clickhouse_connection_profile,
    validate_benchmark_name,
    validate_filename,
    validate_platform_name,
    validate_platform_options,
    validate_query_id,
    validate_query_list,
    validate_scale_factor,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidateQueryId:
    def test_valid_query_ids(self):
        assert validate_query_id("1") == "1"
        assert validate_query_id("Q1") == "Q1"
        assert validate_query_id("query-1") == "query-1"
        assert validate_query_id("query_1") == "query_1"
        assert validate_query_id("  Q1  ") == "Q1"

    def test_empty_query_id_rejected(self):
        with pytest.raises(MCPValidationError, match="cannot be empty"):
            validate_query_id("")
        with pytest.raises(MCPValidationError, match="cannot be empty"):
            validate_query_id("   ")

    def test_too_long_query_id_rejected(self):
        long_id = "a" * (MAX_QUERY_ID_LENGTH + 1)
        with pytest.raises(MCPValidationError, match="too long"):
            validate_query_id(long_id)

    def test_invalid_characters_rejected(self):
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_query_id("query@1")
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_query_id("query 1")
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_query_id("../query")


class TestValidateQueryList:
    def test_valid_query_list(self):
        assert validate_query_list("1,2,3") == ["1", "2", "3"]
        assert validate_query_list("Q1, Q2, Q3") == ["Q1", "Q2", "Q3"]
        assert validate_query_list("1") == ["1"]

    def test_none_and_empty(self):
        assert validate_query_list(None) is None
        assert validate_query_list("") is None
        assert validate_query_list("   ") is None

    def test_too_many_queries_rejected(self):
        query_list = ",".join([str(i) for i in range(MAX_QUERY_IDS + 1)])
        with pytest.raises(MCPValidationError, match="Too many query IDs"):
            validate_query_list(query_list)

    def test_invalid_query_in_list_rejected(self):
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_query_list("1,2,query@3")


class TestValidatePlatformName:
    def test_valid_platform_names(self):
        assert validate_platform_name("duckdb") == "duckdb"
        assert validate_platform_name("DuckDB") == "duckdb"
        assert validate_platform_name("polars-df") == "polars-df"
        assert validate_platform_name("  snowflake  ") == "snowflake"

    def test_empty_platform_rejected(self):
        with pytest.raises(MCPValidationError, match="cannot be empty"):
            validate_platform_name("")

    def test_too_long_platform_rejected(self):
        long_name = "a" * 51
        with pytest.raises(MCPValidationError, match="too long"):
            validate_platform_name(long_name)

    def test_invalid_characters_rejected(self):
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_platform_name("platform@test")

    def test_a_single_deployment_selector_is_allowed(self):
        assert validate_platform_name("ClickHouse:Server") == "clickhouse:server"

    @pytest.mark.parametrize("name", ["a:b:c", ":server", "clickhouse:", "clickhouse::server", "click house:server"])
    def test_malformed_selectors_are_rejected(self, name):
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_platform_name(name)
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_platform_name("../platform")


class TestValidateBenchmarkName:
    def test_valid_benchmark_names(self):
        assert validate_benchmark_name("tpch") == "tpch"
        assert validate_benchmark_name("TPCH") == "tpch"
        assert validate_benchmark_name("tpc-ds") == "tpc-ds"
        assert validate_benchmark_name("  tpch  ") == "tpch"

    def test_empty_benchmark_rejected(self):
        with pytest.raises(MCPValidationError, match="cannot be empty"):
            validate_benchmark_name("")

    def test_too_long_benchmark_rejected(self):
        long_name = "a" * 51
        with pytest.raises(MCPValidationError, match="too long"):
            validate_benchmark_name(long_name)


class TestValidateFilename:
    def test_valid_filenames(self):
        assert validate_filename("results.json") == "results.json"
        assert validate_filename("tpch_sf001_duckdb_20231201.json") == "tpch_sf001_duckdb_20231201.json"
        assert validate_filename("test-file.json") == "test-file.json"

    def test_empty_filename_rejected(self):
        with pytest.raises(MCPValidationError, match="cannot be empty"):
            validate_filename("")

    def test_too_long_filename_rejected(self):
        long_name = "a" * 256
        with pytest.raises(MCPValidationError, match="too long"):
            validate_filename(long_name)

    def test_path_traversal_rejected(self):
        with pytest.raises(MCPValidationError, match="path components"):
            validate_filename("../secret.json")
        with pytest.raises(MCPValidationError, match="path components"):
            validate_filename("dir/file.json")
        with pytest.raises(MCPValidationError, match="path components"):
            validate_filename("..\\file.json")

    def test_invalid_characters_rejected(self):
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_filename("file@name.json")
        with pytest.raises(MCPValidationError, match="invalid characters"):
            validate_filename("file name.json")


class TestValidateScaleFactor:
    def test_valid_scale_factors(self):
        assert validate_scale_factor(0.01) == 0.01
        assert validate_scale_factor(1.0) == 1.0
        assert validate_scale_factor(10.0) == 10.0
        assert validate_scale_factor(100.0) == 100.0

    def test_zero_rejected(self):
        with pytest.raises(MCPValidationError, match="must be positive"):
            validate_scale_factor(0)

    def test_negative_rejected(self):
        with pytest.raises(MCPValidationError, match="must be positive"):
            validate_scale_factor(-1)

    def test_too_small_rejected(self):
        with pytest.raises(MCPValidationError, match="too small"):
            validate_scale_factor(0.0001)

    def test_too_large_rejected(self):
        with pytest.raises(MCPValidationError, match="too large"):
            validate_scale_factor(20000)


class TestPlatformOptionAdmission:
    def test_platform_options_are_typed_and_normalized(self):
        assert validate_platform_options("duckdb", {"threads": 4, "memory_limit": "2GB"}) == {
            "threads": 4,
            "memory_limit": "2GB",
        }

    def test_every_allowlisted_option_has_an_explicit_contract(self):
        assert set(MCP_PLATFORM_OPTION_ALLOWLIST) == set(MCP_PLATFORM_OPTION_CONTRACT)
        for platform, specs in MCP_PLATFORM_OPTION_ALLOWLIST.items():
            contracts = MCP_PLATFORM_OPTION_CONTRACT[platform]
            assert set(specs) == set(contracts)
            for option_name, contract in contracts.items():
                assert contract.consumer
                assert contract.security_class in {"execution", "resource", "device", "layout", "connection"}
                assert all(alias != option_name for alias in contract.aliases)

    def test_clickhouse_mode_alias_is_neither_advertised_nor_accepted(self):
        assert MCP_PLATFORM_OPTION_CONTRACT["clickhouse"]["deployment_mode"].aliases == ()
        for platform in ("clickhouse", "clickhouse-server"):
            with pytest.raises(MCPValidationError, match="not authorized"):
                validate_platform_options(platform, {"mode": "server"})

    def test_option_without_contract_is_rejected_fail_closed(self, monkeypatch):
        monkeypatch.delitem(MCP_PLATFORM_OPTION_CONTRACT["duckdb"], "threads")
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options("duckdb", {"threads": 2})

    def test_secret_and_unknown_platform_options_are_rejected(self):
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options("duckdb", {"password": "secret"})
        with pytest.raises(MCPValidationError):
            validate_platform_options("snowflake", {"warehouse": "secret"})

    def test_platform_option_bounds_and_types_are_rejected(self):
        with pytest.raises(MCPValidationError):
            validate_platform_options("duckdb", {"threads": 0})
        with pytest.raises(MCPValidationError):
            validate_platform_options("duckdb", {"threads": "4"})
        with pytest.raises(MCPValidationError):
            validate_platform_options("duckdb", {"memory_limit": "/tmp/secret"})
        with pytest.raises(MCPValidationError):
            validate_platform_options("sqlite", {"timeout": float("nan")})

    def test_dataframe_aliases_and_identifier_options_are_bounded(self):
        assert validate_platform_options("polars-df", {"streaming": True}) == {"streaming": True}
        assert validate_platform_options("databricks", {"liquid_clustering_columns": "event_time,customer_id"}) == {
            "liquid_clustering_columns": "event_time,customer_id"
        }
        with pytest.raises(MCPValidationError):
            validate_platform_options("databricks", {"liquid_clustering_columns": "event_time;DROP"})

    def test_mcp_platform_choices_match_adapter_contracts(self):
        with pytest.raises(MCPValidationError):
            validate_platform_options("velox", {"deployment": "docker"})
        with pytest.raises(MCPValidationError):
            validate_platform_options("velox", {"deployment": "remote"})
        with pytest.raises(MCPValidationError):
            validate_platform_options("velox", {"deployment": "local"})

    def test_velox_remote_cannot_reach_an_unapproved_endpoint(self):
        for deployment in ("remote", "local", "docker"):
            with pytest.raises(MCPValidationError, match="not authorized"):
                validate_platform_options("velox", {"deployment": deployment})

    def test_df_suffixed_allowlist_key_resolves_to_itself(self, monkeypatch):
        from benchbox.core.run_service import (
            translate_platform_options_for_adapter as _prepare_adapter_platform_options,
        )
        from benchbox.mcp.schemas import (
            MCP_PLATFORM_OPTION_ALLOWLIST,
            MCP_PLATFORM_OPTION_CONTRACT,
            MCPPlatformOptionContract,
            MCPPlatformOptionSpec,
            resolve_platform_policy_key,
            validate_platform_options,
        )

        fake_platform = "polars-df-synthetic-test"
        assert fake_platform not in MCP_PLATFORM_OPTION_ALLOWLIST
        opt_name = "synthetic_opt"
        monkeypatch.setitem(
            MCP_PLATFORM_OPTION_ALLOWLIST,
            fake_platform,
            {opt_name: MCPPlatformOptionSpec(kind="bool", choices=(), minimum=None, maximum=None)},
        )
        monkeypatch.setitem(
            MCP_PLATFORM_OPTION_CONTRACT,
            fake_platform,
            {opt_name: MCPPlatformOptionContract(consumer="test consumer", security_class="execution")},
        )
        assert resolve_platform_policy_key(fake_platform) == fake_platform
        normalized = validate_platform_options(fake_platform, {opt_name: True})
        assert normalized[opt_name] is True
        prepared = _prepare_adapter_platform_options(fake_platform, normalized)
        assert prepared[opt_name] is True

    def test_connection_class_covers_every_destination_changing_option(self):
        must_be_connection = {
            ("clickhouse", "deployment_mode"),
            ("clickhouse", "connection_profile"),
            ("clickhouse-server", "connection_profile"),
        }
        for platform, option in must_be_connection:
            assert MCP_PLATFORM_OPTION_CONTRACT[platform][option].security_class == "connection", (
                f"{platform}.{option} must be 'connection' per the security_class rule"
            )
        for platform, contracts in MCP_PLATFORM_OPTION_CONTRACT.items():
            for option, contract in contracts.items():
                if (platform, option) not in must_be_connection:
                    assert contract.security_class != "connection" or (platform, option) in must_be_connection, (
                        f"Unexpected 'connection' classification for {platform}.{option}"
                    )


class TestDatabricksClusteringOptions:
    @pytest.mark.parametrize(
        "options",
        [
            {"databricks_clustering_strategy": "z_order", "liquid_clustering_columns": "a,b"},
            {"databricks_clustering_strategy": "liquid_clustering_auto", "liquid_clustering_columns": "a"},
            {"databricks_clustering_strategy": "none", "liquid_clustering_columns": "a"},
        ],
    )
    def test_contradictory_layout_combinations_are_rejected(self, options):
        with pytest.raises(MCPValidationError, match="clustering options conflict"):
            validate_platform_options("databricks", options)

    @pytest.mark.parametrize(
        "options",
        [
            {"databricks_clustering_strategy": "liquid_clustering", "liquid_clustering_columns": "a,b"},
            {"liquid_clustering_columns": "a,b"},
            {"databricks_clustering_strategy": "z_order"},
            {"databricks_clustering_strategy": "liquid_clustering_auto"},
            {"databricks_clustering_strategy": "none"},
        ],
    )
    def test_coherent_layout_combinations_are_accepted(self, options):
        assert validate_platform_options("databricks", options) == options

    def test_intent_translation_is_shared_with_adapter_preparation(self):
        normalized = validate_platform_options(
            "databricks",
            {"databricks_clustering_strategy": "liquid_clustering", "liquid_clustering_columns": "a,b"},
        )
        tuning_config = build_databricks_clustering_intent(normalized)
        assert tuning_config is not None
        platform_opts = tuning_config.platform_optimizations
        assert platform_opts.databricks_clustering_strategy == "liquid_clustering"
        assert platform_opts.liquid_clustering_columns == ["a", "b"]
        assert platform_opts.liquid_clustering_enabled is True

    def test_no_clustering_request_builds_no_intent(self):
        assert build_databricks_clustering_intent({}) is None


class TestDaskResourceEnvelope:
    def test_independent_maxima_cannot_be_multiplied_into_a_thread_bomb(self):
        with pytest.raises(MCPValidationError, match="total thread budget"):
            validate_platform_options("dask", {"n_workers": 16, "threads_per_worker": 256})

    def test_worker_count_alone_is_bounded(self):
        with pytest.raises(MCPValidationError, match="worker budget"):
            validate_platform_options("dask", {"n_workers": 256})

    def test_per_worker_memory_is_multiplied_by_the_worker_count(self):
        assert validate_platform_options("dask", {"n_workers": 8, "memory_limit": "8GB"}) == {
            "n_workers": 8,
            "memory_limit": "8GB",
        }
        with pytest.raises(MCPValidationError, match="total memory budget"):
            validate_platform_options("dask", {"n_workers": 8, "memory_limit": "9GB"})

    @pytest.mark.parametrize(
        ("options", "expected"),
        [
            ({"n_workers": 16, "threads_per_worker": 4}, True),
            ({"n_workers": 16, "threads_per_worker": 5}, False),
            ({"n_workers": 17, "threads_per_worker": 1}, False),
            ({"n_workers": 1, "threads_per_worker": 64}, True),
            ({"n_workers": 1, "threads_per_worker": 65}, False),
        ],
    )
    def test_aggregate_boundaries_are_exact(self, options, expected):
        if expected:
            assert validate_platform_options("dask", options) == options
        else:
            with pytest.raises(MCPValidationError):
                validate_platform_options("dask", options)

    def test_omitted_fields_use_the_adapters_own_conservative_caps(self):
        assert validate_platform_options("dask", {"threads_per_worker": 32}) == {"threads_per_worker": 32}
        with pytest.raises(MCPValidationError, match="total thread budget"):
            validate_platform_options("dask", {"threads_per_worker": 33})

    def test_dataframe_alias_is_covered_by_the_same_envelope(self):
        with pytest.raises(MCPValidationError, match="worker budget"):
            validate_platform_options("dask-df", {"n_workers": 256})

    def test_operator_can_widen_or_narrow_the_budget(self, monkeypatch):
        monkeypatch.setenv(MCP_DASK_MAX_WORKERS_ENV, "4")
        monkeypatch.setenv(MCP_DASK_MAX_TOTAL_THREADS_ENV, "8")
        monkeypatch.setenv(MCP_DASK_MAX_TOTAL_MEMORY_ENV, "8GB")
        envelope = load_dask_resource_envelope()
        assert envelope.max_workers == 4
        assert envelope.max_total_threads == 8
        assert envelope.max_total_memory_bytes == float(8 << 30)
        with pytest.raises(MCPValidationError, match="worker budget"):
            validate_platform_options("dask", {"n_workers": 5})

    @pytest.mark.parametrize(
        ("env_name", "value"),
        [
            (MCP_DASK_MAX_WORKERS_ENV, "not-a-number"),
            (MCP_DASK_MAX_WORKERS_ENV, "0"),
            (MCP_DASK_MAX_WORKERS_ENV, "-5"),
            (MCP_DASK_MAX_WORKERS_ENV, "100000"),
            (MCP_DASK_MAX_TOTAL_THREADS_ENV, "nonsense"),
            (MCP_DASK_MAX_TOTAL_MEMORY_ENV, "/etc/passwd"),
            (MCP_DASK_MAX_TOTAL_MEMORY_ENV, "lots"),
        ],
    )
    def test_malformed_operator_budget_falls_back_to_the_reviewed_default(self, monkeypatch, env_name, value):
        monkeypatch.setenv(env_name, value)
        envelope = load_dask_resource_envelope()
        assert envelope.max_workers == 16
        assert envelope.max_total_threads == 64
        assert envelope.max_total_memory_bytes == float(64 << 30)

    @pytest.mark.parametrize("value", ["999999TB", "17TB", "1000000GB"])
    def test_an_out_of_range_memory_override_cannot_disable_the_ceiling(self, monkeypatch, value):
        monkeypatch.setenv(MCP_DASK_MAX_TOTAL_MEMORY_ENV, value)
        assert load_dask_resource_envelope().max_total_memory_bytes == float(64 << 30)
        with pytest.raises(MCPValidationError, match="total memory budget"):
            validate_platform_options("dask", {"n_workers": 16, "memory_limit": "1024GB"})

    def test_an_in_range_memory_override_is_still_honoured(self, monkeypatch):
        monkeypatch.setenv(MCP_DASK_MAX_TOTAL_MEMORY_ENV, "16TB")
        assert load_dask_resource_envelope().max_total_memory_bytes == float(16 << 40)

    def test_an_omitted_request_is_held_to_the_same_envelope_as_an_empty_one(self, monkeypatch):
        monkeypatch.setenv(MCP_DASK_MAX_WORKERS_ENV, "1")
        for omitted in (None, {}):
            with pytest.raises(MCPValidationError, match="worker budget"):
                validate_platform_options("dask", omitted)

    def test_an_optionless_request_still_passes_under_the_default_budget(self):
        assert validate_platform_options("dask", None) == {}
        assert validate_platform_options("duckdb", None) == {}


CLICKHOUSE_PLATFORM_SPELLINGS = ("clickhouse", "clickhouse-server")
CLICKHOUSE_UNCONFIGURED_SPELLINGS = ("clickhouse-local", "clickhouse-cloud", "chdb", "clickhouse_server")


class TestClickHouseConnectionBoundary:
    @pytest.mark.parametrize("platform", CLICKHOUSE_PLATFORM_SPELLINGS)
    @pytest.mark.parametrize("options", [{"port": 9001}, {"secure": False}, {"port": 9001, "secure": True}])
    def test_port_and_tls_overrides_are_rejected(self, platform, options):
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options(platform, options)

    @pytest.mark.parametrize("platform", CLICKHOUSE_UNCONFIGURED_SPELLINGS)
    def test_other_clickhouse_spellings_expose_no_options(self, platform):
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options(platform, {"port": 9001})

    def test_unknown_profile_is_rejected_and_is_not_a_probe_oracle(self, monkeypatch):
        monkeypatch.delenv(MCP_CLICKHOUSE_PROFILE_ENV, raising=False)
        with pytest.raises(MCPValidationError) as excinfo:
            validate_platform_options("clickhouse-server", {"connection_profile": "analytics"})
        assert "analytics" not in str(excinfo.value)

    def test_configured_profile_admits_only_its_name(self, monkeypatch):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440, "secure": True}}))
        normalized = validate_platform_options("clickhouse-server", {"connection_profile": "analytics"})
        assert normalized == {"connection_profile": "analytics"}

    def test_local_deployment_cannot_acquire_a_network_path(self, monkeypatch):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440, "secure": True}}))
        with pytest.raises(MCPValidationError, match="requires the server deployment"):
            validate_platform_options("clickhouse", {"connection_profile": "analytics"})
        with pytest.raises(MCPValidationError, match="requires the server deployment"):
            validate_platform_options("clickhouse", {"connection_profile": "analytics", "deployment_mode": "local"})
        assert validate_platform_options(
            "clickhouse", {"connection_profile": "analytics", "deployment_mode": "server"}
        ) == {"connection_profile": "analytics", "deployment_mode": "server"}

    @pytest.mark.parametrize(
        "registry",
        [
            "not json",
            json.dumps(["analytics"]),
            json.dumps({"Analytics": {"port": 9440}}),
            json.dumps({"analytics": {"port": 0}}),
            json.dumps({"analytics": {"port": 70000}}),
            json.dumps({"analytics": {"port": True}}),
            json.dumps({"analytics": {"port": "9440"}}),
            json.dumps({"analytics": {"port": 9440, "secure": "yes"}}),
            json.dumps({"analytics": {"port": 9440, "host": "10.0.0.5"}}),
            json.dumps({"analytics": {"port": 9440, "password": "hunter2"}}),
        ],
    )
    def test_malformed_server_registry_fails_closed(self, monkeypatch, registry):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, registry)
        with pytest.raises(MCPValidationError, match="not configured"):
            validate_platform_options("clickhouse-server", {"connection_profile": "analytics"})

    def test_profile_name_shape_is_bounded(self, monkeypatch):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440}}))
        for bad_name in ("Analytics", "../etc/passwd", "a" * 65, "9analytics", ""):
            with pytest.raises(MCPValidationError):
                validate_platform_options("clickhouse-server", {"connection_profile": bad_name})

    def test_dataframe_alias_fallback_does_not_bypass_the_policy(self, monkeypatch):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440, "secure": True}}))
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options("clickhouse-df", {"port": 9001})
        with pytest.raises(MCPValidationError, match="requires the server deployment"):
            validate_platform_options("clickhouse-df", {"connection_profile": "analytics"})

    def test_resolution_returns_the_server_owned_tuple(self, monkeypatch):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440, "secure": True}}))
        assert resolve_clickhouse_connection_profile("analytics") == {"port": 9440, "secure": True}

    def test_profile_defaults_to_plaintext_only_when_the_operator_says_so(self, monkeypatch):
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"plain": {"port": 9000}}))
        assert resolve_clickhouse_connection_profile("plain") == {"port": 9000, "secure": False}
