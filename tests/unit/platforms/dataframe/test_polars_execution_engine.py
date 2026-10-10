from __future__ import annotations

import ast
import functools
import sys
import types
from datetime import date
from pathlib import Path

import pytest

pl = pytest.importorskip("polars")

from benchbox.cli.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.core.dataframe.maintenance_interface import get_maintenance_operations_for_platform
from benchbox.core.execution_engine import ExecutionEngineUnavailableError, UnsupportedExecutionEngineError
from benchbox.core.results.platform_info import build_platform_info
from benchbox.platforms import get_dataframe_adapter, polars_compat
from benchbox.platforms.dataframe import expression_family, polars_df, polars_maintenance, unified_frame
from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TPCH_TABLES = {
    "partsupp": {
        "ps_partkey": [1, 2],
        "ps_suppkey": [1, 1],
        "ps_supplycost": [1.0, 2.0],
        "ps_availqty": [10, 20],
    },
    "supplier": {
        "s_suppkey": [1],
        "s_nationkey": [1],
        "s_name": ["supplier"],
        "s_address": ["address"],
        "s_phone": ["phone"],
    },
    "nation": {"n_nationkey": [1], "n_name": ["GERMANY"]},
    "lineitem": {
        "l_suppkey": [1],
        "l_shipdate": [date(1996, 2, 1)],
        "l_extendedprice": [100.0],
        "l_discount": [0.1],
    },
    "customer": {
        "c_custkey": [1, 2],
        "c_phone": ["13-555-0100", "31-555-0101"],
        "c_acctbal": [100.0, 200.0],
    },
    "orders": {"o_custkey": [1]},
}


CAPABILITY_CACHES = (
    polars_compat.collect_accepts_engine,
    polars_compat.collect_engine_supported,
    polars_compat.default_collect_engine,
)


@pytest.fixture(autouse=True)
def clear_capability_caches():
    for cached in CAPABILITY_CACHES:
        cached.cache_clear()
    yield
    for cached in CAPABILITY_CACHES:
        cached.cache_clear()


@pytest.fixture
def collect_calls(monkeypatch):
    calls: list[dict] = []
    real_collect = pl.LazyFrame.collect

    @functools.wraps(real_collect)
    def recording_collect(self, *args, **kwargs):
        calls.append(dict(kwargs))
        return real_collect(self, *args, **kwargs)

    monkeypatch.setattr(pl.LazyFrame, "collect", recording_collect)
    return calls


def _parse(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _collect_calls_in(node: ast.AST) -> list[ast.Call]:
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "collect"
    ]


def _is_self_call(call: ast.Call) -> bool:
    receiver = call.func.value
    return isinstance(receiver, ast.Name) and receiver.id == "self"


def _polars_branches(tree: ast.Module) -> list[ast.If]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "_is_polars_df"
            for child in ast.walk(node.test)
        )
    ]


def _context_for(adapter: PolarsDataFrameAdapter):
    ctx = adapter.create_context()
    for name, data in TPCH_TABLES.items():
        ctx.register_table(name, pl.DataFrame(data).lazy())
    return ctx


def _expected_collect_kwargs() -> dict:
    if not polars_compat.collect_accepts_engine():
        return {}
    return {"engine": polars_compat.default_collect_engine()}


def _tuning_config(**execution: object):
    from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration

    config = DataFrameTuningConfiguration()
    for key, value in execution.items():
        setattr(config.execution, key, value)
    return config


class TestCollectGuard:
    def test_polars_adapter_never_calls_collect_directly(self):
        assert _collect_calls_in(_parse(polars_df)) == []

    def test_expression_family_only_delegates_to_the_adapter(self):
        calls = _collect_calls_in(_parse(expression_family))

        assert calls
        assert all(_is_self_call(call) for call in calls)

    def test_unified_frame_polars_branches_never_call_collect_directly(self):
        branches = _polars_branches(_parse(unified_frame))

        assert branches
        violations = [call for branch in branches for stmt in branch.body for call in _collect_calls_in(stmt)]
        assert violations == []


class TestExecutionEngineReachesWriteAndMaintenance:
    @pytest.mark.parametrize("execution_engine", ["auto", "in-memory", "streaming"])
    @pytest.mark.parametrize("source_format", ["parquet", "csv"])
    def test_write_benchmark_manager_uses_resolved_engine(
        self, tmp_path, collect_calls, execution_engine, source_format
    ):
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        adapter = PolarsDataFrameAdapter(execution_engine=execution_engine)
        benchmark = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        manager = benchmark.get_dataframe_operations("polars-df", execution_engine=adapter.collect_engine)
        assert manager is not None
        source = tmp_path / f"source.{source_format}"
        dataframe = pl.DataFrame({"value": [1]})
        if source_format == "parquet":
            dataframe.write_parquet(source)
        else:
            dataframe.write_csv(source)
        collect_calls.clear()

        result = manager.execute_bulk_load(source, tmp_path / "target", source_format=source_format)

        assert result.success
        assert collect_calls == [{"engine": execution_engine}]

    @pytest.mark.parametrize("execution_engine", ["auto", "in-memory", "streaming"])
    def test_maintenance_uses_requested_engine(self, tmp_path, collect_calls, execution_engine):
        adapter = PolarsDataFrameAdapter(execution_engine=execution_engine)
        operations = get_maintenance_operations_for_platform("polars-df", adapter.collect_engine)
        assert operations is not None
        collect_calls.clear()

        assert operations._do_insert(tmp_path / "table", pl.LazyFrame({"value": [1]}), None, "append") == 1

        assert collect_calls == [{"engine": execution_engine}]

    @pytest.mark.parametrize("execution_engine", ["auto", "in-memory", "streaming"])
    def test_delete_and_update_use_requested_engine(self, tmp_path, collect_calls, execution_engine):
        table_path = tmp_path / "table"
        table_path.mkdir()
        pl.DataFrame({"id": [1, 2], "value": [10, 20]}).write_parquet(table_path / "part-00000.parquet")
        adapter = PolarsDataFrameAdapter(execution_engine=execution_engine)
        operations = get_maintenance_operations_for_platform("polars-df", adapter.collect_engine)
        assert operations is not None
        expected_kwargs = [{"engine": execution_engine}] * 3

        collect_calls.clear()
        assert operations._do_update(table_path, "id = 1", {"value": "99"}) == 1
        assert pl.read_parquet(table_path).sort("id")["value"].to_list() == [99, 20]
        assert collect_calls == expected_kwargs

        collect_calls.clear()
        assert operations._do_delete(table_path, "id = 2") == 1
        assert pl.read_parquet(table_path).select("id").to_series().to_list() == [1]
        assert collect_calls == expected_kwargs

    @pytest.mark.parametrize("execution_engine", ["auto", "in-memory", "streaming"])
    def test_merge_uses_requested_engine(self, tmp_path, collect_calls, execution_engine):
        table_path = tmp_path / "table"
        table_path.mkdir()
        pl.DataFrame({"id": [1], "value": [10]}).write_parquet(table_path / "part-00000.parquet")
        adapter = PolarsDataFrameAdapter(execution_engine=execution_engine)
        operations = get_maintenance_operations_for_platform("polars-df", adapter.collect_engine)
        assert operations is not None
        source = pl.LazyFrame({"id": [1, 2], "value": [99, 20]})
        collect_calls.clear()

        assert (
            operations._do_merge(
                table_path,
                source,
                "target.id = source.id",
                {"value": "source.value"},
                {"id": "source.id"},
            )
            == 2
        )
        assert pl.read_parquet(table_path).sort("id").to_dict(as_series=False) == {"id": [1, 2], "value": [99, 20]}
        assert collect_calls == [{"engine": execution_engine}] * 2


class TestEngineReachesEveryCollect:
    @pytest.mark.parametrize(
        ("execution_engine", "expected"),
        [("default", None), ("auto", "auto"), ("in-memory", "in-memory"), ("streaming", "streaming")],
    )
    @pytest.mark.parametrize("query_id", [11, 15, 22])
    def test_tpch_scalar_queries(self, collect_calls, query_id, execution_engine, expected):
        from benchbox.core.tpch import dataframe_queries

        adapter = PolarsDataFrameAdapter(execution_engine=execution_engine)
        ctx = _context_for(adapter)
        collect_calls.clear()

        result = getattr(dataframe_queries, f"q{query_id}_expression_impl")(ctx)
        frame = getattr(result, "native", result)
        adapter.get_row_count(frame)
        adapter._get_first_row(frame)
        adapter.collect(frame)

        if expected is None:
            expected_kwargs = _expected_collect_kwargs()
        else:
            expected_kwargs = {"engine": expected}
        assert len(collect_calls) >= 4
        assert all(call == expected_kwargs for call in collect_calls)

    def test_scalar_and_row_count_use_the_requested_engine(self, collect_calls):
        adapter = PolarsDataFrameAdapter(execution_engine="in-memory")
        frame = pl.LazyFrame({"a": [1, 2, 3]})
        collect_calls.clear()

        assert adapter.scalar(frame.select(pl.col("a").sum())) == 6
        assert adapter.get_row_count(frame) == 3
        assert adapter._get_first_row(frame) == (1,)

        assert collect_calls == [{"engine": "in-memory"}] * 3

    def test_unified_frame_collects_use_the_adapter_engine(self, collect_calls):
        adapter = PolarsDataFrameAdapter(execution_engine="in-memory")
        unified = UnifiedLazyFrame(pl.LazyFrame({"a": [1, 2]}), adapter)
        collect_calls.clear()

        unified.collect()
        assert unified.collect_column_as_list("a") == [1, 2]
        assert unified.scalar() == 1

        assert collect_calls == [{"engine": "in-memory"}] * 3

    def test_unified_frame_without_a_polars_adapter_uses_the_default_engine(self, collect_calls):
        unified = UnifiedLazyFrame(pl.LazyFrame({"a": [1]}), object())
        collect_calls.clear()

        assert unified.scalar() == 1

        assert collect_calls == [{}]

    def test_streaming_flag_selects_the_streaming_engine(self, collect_calls, recwarn):
        adapter = PolarsDataFrameAdapter(streaming=True)
        collect_calls.clear()

        adapter.collect(pl.LazyFrame({"a": [1]}))

        assert adapter.collect_engine == "streaming"
        assert adapter.get_platform_info()["execution_engine"]["resolution"] == "legacy_option"
        assert collect_calls == [{"engine": "streaming"}]
        assert any(issubclass(warning.category, DeprecationWarning) for warning in recwarn.list)

    def test_default_on_a_version_without_an_engine_parameter_resolves_in_memory(self, monkeypatch):
        monkeypatch.setattr(polars_df, "collect_accepts_engine", lambda: False)
        monkeypatch.setattr(polars_df, "default_collect_engine", lambda: "in-memory")
        monkeypatch.setattr(polars_compat, "collect_accepts_engine", lambda: False)

        adapter = PolarsDataFrameAdapter()

        assert adapter.get_platform_info()["execution_engine"]["applied"] == "in-memory"
        assert adapter.collect_engine == "default"

    def test_default_engine_resolves_to_the_installed_default(self, collect_calls):
        adapter = PolarsDataFrameAdapter()
        collect_calls.clear()

        adapter.collect(pl.LazyFrame({"a": [1]}))
        adapter.get_row_count(pl.LazyFrame({"a": [1]}))

        assert adapter.collect_engine == polars_compat.default_collect_engine()
        assert collect_calls == [_expected_collect_kwargs(), _expected_collect_kwargs()]


class TestExecutionEngineResolution:
    def test_simulated_version_without_an_engine_parameter_fails_explicit_selection(self, monkeypatch):
        monkeypatch.setattr(polars_df, "collect_accepts_engine", lambda: False)
        monkeypatch.setattr(polars_compat, "collect_accepts_engine", lambda: False)

        for engine in ("in-memory", "streaming"):
            with pytest.raises(ExecutionEngineUnavailableError, match=rf"Polars {pl.__version__}.*{engine!r}"):
                PolarsDataFrameAdapter(execution_engine=engine)

        with pytest.raises(ExecutionEngineUnavailableError, match="'streaming'"):
            PolarsDataFrameAdapter(streaming=True)

    def test_value_rejected_by_polars_fails_the_run(self, monkeypatch):
        def rejecting_collect(self, *, engine="auto"):
            raise ValueError(f"unsupported engine {engine}")

        monkeypatch.setattr(pl.LazyFrame, "collect", rejecting_collect)

        with pytest.raises(ExecutionEngineUnavailableError, match=rf"Polars {pl.__version__}.*'in-memory'"):
            PolarsDataFrameAdapter(execution_engine="in-memory")

    def test_unknown_value_is_rejected(self):
        with pytest.raises(UnsupportedExecutionEngineError, match="Allowed: default, auto, in-memory, streaming$"):
            PolarsDataFrameAdapter(execution_engine="gpu")

    def test_removed_engine_option_is_rejected(self):
        with pytest.raises(TypeError):
            PolarsDataFrameAdapter(engine="in-memory")

    def test_conflicting_explicit_and_legacy_sources_fail(self):
        with pytest.raises(
            ValueError, match="explicit setting requests 'in-memory'.*legacy option requests 'streaming'"
        ):
            PolarsDataFrameAdapter(execution_engine="in-memory", streaming=True)

    def test_conflicting_explicit_and_tuning_sources_fail(self):
        tuning = _tuning_config(engine_affinity="in-memory")

        with pytest.raises(ValueError, match="explicit setting requests 'streaming'.*tuning file requests 'in-memory'"):
            PolarsDataFrameAdapter(execution_engine="streaming", tuning_config=tuning)

    def test_agreeing_sources_resolve_to_the_highest_precedence(self):
        tuning = _tuning_config(engine_affinity="streaming")

        adapter = PolarsDataFrameAdapter(execution_engine="streaming", tuning_config=tuning, streaming=True)

        assert adapter.get_platform_info()["execution_engine"]["resolution"] == "explicit"

    def test_tuning_source_resolves_with_tuning_profile(self, caplog):
        tuning = _tuning_config(engine_affinity="in-memory")

        with caplog.at_level("WARNING", logger="benchbox.platforms.dataframe.polars_df"):
            adapter = PolarsDataFrameAdapter(tuning_config=tuning)

        receipt = adapter.get_platform_info()["execution_engine"]
        assert receipt["requested"] == "in-memory"
        assert receipt["applied"] == "in-memory"
        assert receipt["resolution"] == "tuning_profile"
        assert any("may not be comparable" in message for message in caplog.messages)

    def test_tuning_streaming_mode_selects_streaming(self):
        tuning = _tuning_config(streaming_mode=True)

        adapter = PolarsDataFrameAdapter(tuning_config=tuning)

        receipt = adapter.get_platform_info()["execution_engine"]
        assert receipt["requested"] == "streaming"
        assert receipt["applied"] == "streaming"
        assert receipt["resolution"] == "tuning_profile"

    def test_conflicting_tuning_engine_selections_fail(self):
        tuning = _tuning_config(engine_affinity="in-memory", streaming_mode=True)

        with pytest.raises(ValueError, match="Conflicting tuning execution engines"):
            PolarsDataFrameAdapter(tuning_config=tuning)

    def test_affinity_records_native_collect_kwargs_and_environment(self, monkeypatch, caplog):
        monkeypatch.setenv("POLARS_ENGINE_AFFINITY", "streaming")

        with caplog.at_level("WARNING", logger="benchbox.platforms.dataframe.polars_df"):
            adapter = PolarsDataFrameAdapter(execution_engine="streaming")

        native = adapter.get_platform_info()["execution_engine"]["applied_native"]
        assert native["collect_kwargs"] == {"engine": "streaming"}
        assert native["POLARS_ENGINE_AFFINITY"] == "streaming"
        assert any("POLARS_ENGINE_AFFINITY" in message for message in caplog.messages)


class TestEngineMetadata:
    @pytest.mark.parametrize(
        ("module_name", "module_version", "package", "version"),
        [
            ("polars.polars", None, "absent", "absent"),
            ("_polars_runtime_32._polars_runtime", "1.44.2", "polars-runtime-32", "1.44.2"),
            ("_polars_runtime_64._polars_runtime", "1.44.2", "polars-runtime-64", "1.44.2"),
            ("_polars_runtime_compat._polars_runtime", "1.44.2", "polars-runtime-compat", "1.44.2"),
        ],
        ids=["pre-1.35-layout", "rt32", "rt64", "rtcompat"],
    )
    def test_platform_info_records_the_active_runtime_package(
        self, monkeypatch, module_name, module_version, package, version
    ):
        loaded = types.ModuleType(module_name)
        if module_version is not None:
            loaded.__version__ = module_version
        monkeypatch.setitem(sys.modules, "polars._plr", loaded)

        info = PolarsDataFrameAdapter(execution_engine="in-memory").get_platform_info()

        assert info["version"] == pl.__version__
        assert info["polars_runtime_package"] == package
        assert info["polars_runtime_version"] == version
        receipt = info["execution_engine"]
        assert receipt["requested"] == "in-memory"
        assert receipt["applied"] == "in-memory"
        assert receipt["applied_class"] == "in-memory"
        assert receipt["resolution"] == "explicit"
        assert receipt["observed"] == "not_captured"
        assert receipt["observed_source"] == "none"

    def test_active_runtime_ignores_other_installed_runtime_distributions(self, monkeypatch):
        loaded = types.ModuleType("_polars_runtime_64._polars_runtime")
        loaded.__version__ = "1.44.2"
        monkeypatch.setitem(sys.modules, "polars._plr", loaded)
        monkeypatch.setattr(polars_compat.metadata, "version", lambda name: "0.0.1")

        assert polars_compat.active_runtime() == ("polars-runtime-64", "1.44.2")

    def test_active_runtime_falls_back_to_distribution_metadata_without_a_module_version(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "polars._plr", types.ModuleType("_polars_runtime_32._polars_runtime"))
        monkeypatch.setattr(polars_compat.metadata, "version", lambda name: f"{name}-metadata")

        assert polars_compat.active_runtime() == ("polars-runtime-32", "polars-runtime-32-metadata")

    def test_active_runtime_matches_the_loaded_module(self):
        package, version = polars_compat.active_runtime()
        loaded = sys.modules["polars._plr"].__name__

        if loaded.startswith("_polars_runtime_"):
            assert package == loaded.split(".", 1)[0].removeprefix("_").replace("_", "-")
            assert version == sys.modules["polars._plr"].__version__
        else:
            assert (package, version) == ("absent", "absent")

    @pytest.mark.parametrize(
        ("kwargs", "requested", "resolution", "argument"),
        [
            ({}, "default", "version_default", "installed_default"),
            ({"streaming": True}, "streaming", "legacy_option", "streaming"),
            ({"execution_engine": "streaming"}, "streaming", "explicit", "streaming"),
        ],
    )
    def test_requested_engine_is_distinct_from_the_applied_collect_argument(
        self, kwargs, requested, resolution, argument, recwarn
    ):
        info = PolarsDataFrameAdapter(**kwargs).get_platform_info()

        if argument == "installed_default":
            argument = polars_compat.default_collect_engine()
        assert info["execution_engine"]["requested"] == requested
        assert info["execution_engine"]["resolution"] == resolution
        assert info["execution_engine"]["applied_native"]["collect_kwargs"] == {"engine": argument}
        assert "collect_engine_argument" not in info

    def test_result_bundle_config_carries_the_engine_fields(self):
        platform_info = build_platform_info(
            PolarsDataFrameAdapter(execution_engine="in-memory"), execution_mode="dataframe"
        )

        assert platform_info.platform_version == pl.__version__
        assert platform_info.config["execution_engine"]["requested"] == "in-memory"
        assert platform_info.config["execution_engine"]["applied"] == "in-memory"
        assert platform_info.config["execution_engine"]["applied_native"]["collect_kwargs"] == {"engine": "in-memory"}
        assert "collect_engine_argument" not in platform_info.config
        assert "polars_runtime_version" in platform_info.config
        assert "polars_runtime_package" in platform_info.config


class TestEngineOption:
    def test_option_is_removed(self):
        specs = PlatformHookRegistry.list_option_specs("polars")

        assert "engine" not in specs

    def test_option_is_rejected(self):
        with pytest.raises(PlatformOptionError):
            PlatformHookRegistry.parse_options("polars", [("engine", "in-memory")])

    def test_factory_passes_the_engine_to_the_adapter(self):
        assert get_dataframe_adapter("polars-df", execution_engine="in-memory").engine == "in-memory"


class TestCliMcpParity:
    @pytest.mark.parametrize("execution_engine", ["default", "streaming"])
    def test_same_request_produces_identical_receipts(self, execution_engine):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
        from benchbox.core.schemas import DatabaseConfig
        from benchbox.mcp.schemas import validate_execution_engine
        from benchbox.platforms import get_adapter

        cli_config = PlatformHookRegistry.build_database_config("polars-df", {}, {"execution_engine": execution_engine})
        mcp_config = DatabaseConfig(
            type="polars-df",
            name="mcp_polars-df",
            execution_mode="dataframe",
            execution_engine=validate_execution_engine("polars-df", execution_engine),
        )

        cli_receipt = get_adapter(
            cli_config.type, mode="dataframe", execution_engine=cli_config.execution_engine
        ).get_platform_info()["execution_engine"]
        mcp_receipt = get_adapter(
            mcp_config.type, mode="dataframe", execution_engine=mcp_config.execution_engine
        ).get_platform_info()["execution_engine"]

        assert cli_receipt == mcp_receipt
        assert cli_receipt["requested"] == execution_engine
