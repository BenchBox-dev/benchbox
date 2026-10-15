from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

from benchbox.cli.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionError,
    BenchmarkOptionSpec,
    parse_bool,
    parse_datetime,
    parse_enum_list,
    parse_int,
    parse_int_list,
    parse_str_list,
)


class TestParsers:
    def test_parse_int(self):
        assert parse_int("42") == 42
        assert parse_int(" 7 ") == 7
        with pytest.raises(BenchmarkOptionError, match="Invalid integer"):
            parse_int("abc")

    def test_parse_bool(self):
        assert parse_bool("true") is True
        assert parse_bool("False") is False
        assert parse_bool("1") is True
        assert parse_bool("no") is False
        with pytest.raises(BenchmarkOptionError, match="Invalid boolean"):
            parse_bool("maybe")

    def test_parse_int_list(self):
        assert parse_int_list("1,2,3") == [1, 2, 3]
        assert parse_int_list(" 4 , 5 ") == [4, 5]
        with pytest.raises(BenchmarkOptionError, match="Empty list"):
            parse_int_list("")
        with pytest.raises(BenchmarkOptionError, match="Invalid integer list"):
            parse_int_list("1,abc,3")

    def test_parse_str_list(self):
        assert parse_str_list("a,b,c") == ["a", "b", "c"]
        assert parse_str_list(" store , web ") == ["store", "web"]
        with pytest.raises(BenchmarkOptionError, match="Empty list"):
            parse_str_list("")

    def test_parse_enum_list(self):
        from enum import Enum

        class Color(Enum):
            RED = "red"
            BLUE = "blue"

        parser = parse_enum_list(Color)
        assert parser("red,blue") == [Color.RED, Color.BLUE]
        assert parser("red") == [Color.RED]
        with pytest.raises(BenchmarkOptionError, match="Invalid value 'green'"):
            parser("red,green")
        with pytest.raises(BenchmarkOptionError, match="Empty list"):
            parser("")

    def test_parse_enum_list_case_insensitive(self):
        from enum import Enum

        class Fruit(Enum):
            APPLE = "apple"

        parser = parse_enum_list(Fruit)
        assert parser("APPLE") == [Fruit.APPLE]
        assert parser("Apple") == [Fruit.APPLE]

    def test_parse_datetime(self):
        dt = parse_datetime("2019-02-01T00:00:00")
        assert dt.year == 2019
        assert dt.month == 2
        assert dt.day == 1

        dt2 = parse_datetime("2024-12-31")
        assert dt2.year == 2024

        with pytest.raises(BenchmarkOptionError, match="Invalid datetime"):
            parse_datetime("not-a-date")


class TestBenchmarkOptionSpec:
    def test_parse_identity(self):
        spec = BenchmarkOptionSpec(name="foo")
        assert spec.parse("bar") == "bar"

    def test_parse_with_parser(self):
        spec = BenchmarkOptionSpec(name="count", parser=parse_int)
        assert spec.parse("42") == 42

    def test_parse_with_choices_valid(self):
        spec = BenchmarkOptionSpec(name="mode", choices=("full", "minimal"))
        assert spec.parse("full") == "full"

    def test_parse_with_choices_invalid(self):
        spec = BenchmarkOptionSpec(name="mode", choices=("full", "minimal"))
        with pytest.raises(BenchmarkOptionError, match="Invalid value"):
            spec.parse("partial")


@pytest.fixture(autouse=True, scope="module")
def _register_real_benchmark_specs():
    import benchbox.core.datavault.benchmark
    import benchbox.core.flightdata.benchmark
    import benchbox.core.joinorder.benchmark
    import benchbox.core.joinorder_synthetic.benchmark
    import benchbox.core.nyctaxi.benchmark
    import benchbox.core.tpcdi.benchmark
    import benchbox.core.tpcds_obt.benchmark
    import benchbox.core.tpch_skew.benchmark
    import benchbox.core.tsbs_devops.benchmark
    import benchbox.core.vector_search.benchmark


@pytest.fixture(autouse=True)
def _clean_registry():
    import copy

    saved_specs = copy.deepcopy(BenchmarkHookRegistry._option_specs)
    saved_aliases = copy.deepcopy(BenchmarkHookRegistry._alias_index)
    saved_validated = set(BenchmarkHookRegistry._validated_benchmarks)
    yield
    BenchmarkHookRegistry._option_specs = saved_specs
    BenchmarkHookRegistry._alias_index = saved_aliases
    BenchmarkHookRegistry._validated_benchmarks = saved_validated


class TestBenchmarkHookRegistry:
    def test_register_and_list(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="opt_a", parser=parse_int, help="An option"),
        )
        specs = BenchmarkHookRegistry.list_option_specs("test_bench")
        assert "opt_a" in specs
        assert specs["opt_a"].help == "An option"

    def test_register_with_alias(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="my_opt", aliases=("my-opt",)),
        )
        result = BenchmarkHookRegistry.parse_options("test_bench", [("my-opt", "val")])
        assert result == {"my_opt": "val"}

    def test_parse_options_unknown_key_rejected(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="known"),
        )
        with pytest.raises(BenchmarkOptionError, match="Unknown benchmark option 'bad'"):
            BenchmarkHookRegistry.parse_options("test_bench", [("bad", "val")])

    def test_parse_options_duplicate_rejected(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="dupe"),
        )
        with pytest.raises(BenchmarkOptionError, match="Duplicate"):
            BenchmarkHookRegistry.parse_options("test_bench", [("dupe", "a"), ("dupe", "b")])

    def test_parse_options_no_specs_rejected(self):
        with pytest.raises(BenchmarkOptionError, match="does not accept"):
            BenchmarkHookRegistry.parse_options("no_specs_bench", [("key", "val")])

    def test_parse_options_returns_only_provided(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="a", default="default_a"),
            BenchmarkOptionSpec(name="b", parser=parse_int),
        )
        result = BenchmarkHookRegistry.parse_options("test_bench", [("b", "5")])
        assert result == {"b": 5}
        assert "a" not in result

    def test_describe_options(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="dim", parser=parse_int, default=128, help="Dimensions"),
        )
        lines = BenchmarkHookRegistry.describe_options("test_bench")
        assert len(lines) == 1
        assert "dim" in lines[0]
        assert "128" in lines[0]
        assert "Dimensions" in lines[0]

    def test_has_specs(self):
        assert not BenchmarkHookRegistry.has_specs("nonexistent")
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="x"),
        )
        assert BenchmarkHookRegistry.has_specs("test_bench")

    def test_re_registration_is_idempotent(self):
        spec = BenchmarkOptionSpec(name="x", help="first")
        BenchmarkHookRegistry.register_option_specs("test_bench", spec)
        spec2 = BenchmarkOptionSpec(name="x", help="second")
        BenchmarkHookRegistry.register_option_specs("test_bench", spec2)
        assert BenchmarkHookRegistry.list_option_specs("test_bench")["x"].help == "first"

    def test_duplicate_alias_rejected(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="opt_a", aliases=("shared-alias",)),
        )
        with pytest.raises(BenchmarkOptionError, match="Alias 'shared-alias' already used"):
            BenchmarkHookRegistry.register_option_specs(
                "test_bench",
                BenchmarkOptionSpec(name="opt_b", aliases=("shared-alias",)),
            )

    def test_case_insensitive_benchmark_name(self):
        BenchmarkHookRegistry.register_option_specs(
            "TestBench",
            BenchmarkOptionSpec(name="x"),
        )
        assert BenchmarkHookRegistry.has_specs("testbench")
        assert BenchmarkHookRegistry.has_specs("TESTBENCH")


class TestRegistrationTimeConstructorValidation:
    def test_omitted_class_skips_validation(self):
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="anything_goes_here"),
        )
        assert "anything_goes_here" in BenchmarkHookRegistry.list_option_specs("test_bench")
        assert "test_bench" not in BenchmarkHookRegistry._validated_benchmarks

    def test_mismatched_spec_rejected(self):
        class FakeBench:
            def __init__(self, ok_param=None, **kwargs):
                self.ok_param = ok_param

        with pytest.raises(BenchmarkOptionError, match="does not match any constructor parameter"):
            BenchmarkHookRegistry.register_option_specs(
                "test_bench",
                BenchmarkOptionSpec(name="definitely_not_a_ctor_param"),
                benchmark_class=FakeBench,
            )
        assert "test_bench" not in BenchmarkHookRegistry._validated_benchmarks

    def test_matching_spec_accepted_and_recorded(self):
        class FakeBench:
            def __init__(self, ok_param=None, **kwargs):
                self.ok_param = ok_param

        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="ok_param", parser=parse_int),
            benchmark_class=FakeBench,
        )
        assert "ok_param" in BenchmarkHookRegistry.list_option_specs("test_bench")
        assert "test_bench" in BenchmarkHookRegistry._validated_benchmarks

    def test_alias_is_not_validated_against_constructor(self):
        class FakeBench:
            def __init__(self, ok_param=None):
                self.ok_param = ok_param

        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="ok_param", aliases=("not-a-ctor-param",)),
            benchmark_class=FakeBench,
        )
        assert "ok_param" in BenchmarkHookRegistry.list_option_specs("test_bench")

    def test_positional_only_param_rejected(self):
        class FakeBench:
            def __init__(self, pos_only, /, ok_param=None):
                self.pos_only = pos_only
                self.ok_param = ok_param

        with pytest.raises(BenchmarkOptionError, match="does not match any constructor parameter"):
            BenchmarkHookRegistry.register_option_specs(
                "test_bench",
                BenchmarkOptionSpec(name="pos_only"),
                benchmark_class=FakeBench,
            )
        BenchmarkHookRegistry.register_option_specs(
            "test_bench",
            BenchmarkOptionSpec(name="ok_param"),
            benchmark_class=FakeBench,
        )
        assert "ok_param" in BenchmarkHookRegistry.list_option_specs("test_bench")

    def test_uninspectable_signature_raises(self, monkeypatch):
        import inspect as inspect_module

        def _no_signature(cls):
            raise ValueError("no signature found")

        monkeypatch.setattr(inspect_module, "signature", _no_signature)

        class FakeBench:
            def __init__(self, ok_param=None):
                self.ok_param = ok_param

        with pytest.raises(BenchmarkOptionError, match="signature of FakeBench is unavailable"):
            BenchmarkHookRegistry.register_option_specs(
                "test_bench",
                BenchmarkOptionSpec(name="ok_param"),
                benchmark_class=FakeBench,
            )

    def test_all_registered_specs_match_constructors(self):
        import inspect

        from benchbox.core.benchmark_loader import get_core_benchmark_class

        for benchmark, specs in BenchmarkHookRegistry._option_specs.items():
            try:
                benchmark_class = get_core_benchmark_class(benchmark)
            except Exception:
                continue
            explicit = {
                name
                for name, parameter in inspect.signature(benchmark_class).parameters.items()
                if parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            for spec_name in specs:
                assert spec_name in explicit, f"{benchmark}.{spec_name}"

    def test_all_resolvable_benchmarks_validated_at_registration(self):
        from benchbox.core.benchmark_loader import get_core_benchmark_class

        for benchmark in BenchmarkHookRegistry._option_specs:
            try:
                get_core_benchmark_class(benchmark)
            except Exception:
                continue
            assert benchmark in BenchmarkHookRegistry._validated_benchmarks, benchmark


class TestBenchmarkOptionParamType:
    def test_valid_key_value(self):
        from benchbox.cli.commands.run import BenchmarkOptionParamType

        param_type = BenchmarkOptionParamType()
        result = param_type.convert("key=value", None, None)
        assert result == ("key", "value")

    def test_value_with_equals(self):
        from benchbox.cli.commands.run import BenchmarkOptionParamType

        param_type = BenchmarkOptionParamType()
        result = param_type.convert("key=a=b", None, None)
        assert result == ("key", "a=b")

    def test_missing_equals_fails(self):
        from click.exceptions import BadParameter

        from benchbox.cli.commands.run import BenchmarkOptionParamType

        param_type = BenchmarkOptionParamType()
        with pytest.raises(BadParameter):
            param_type.convert("noequals", None, None)


class TestRealBenchmarkSpecs:
    def test_nyctaxi_specs_registered(self):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        specs = BenchmarkHookRegistry.list_option_specs("nyctaxi")
        assert "taxi_types" in specs
        assert "year" in specs
        assert "months" in specs
        assert "seed" in specs

    def test_nyctaxi_taxi_types_parser(self):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark
        from benchbox.core.nyctaxi.schema import TaxiType

        specs = BenchmarkHookRegistry.list_option_specs("nyctaxi")
        result = specs["taxi_types"].parse("yellow,green,hvfhv")
        assert result == [TaxiType.YELLOW, TaxiType.GREEN, TaxiType.HVFHV]

    def test_tsbs_devops_specs_registered(self):
        from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark

        specs = BenchmarkHookRegistry.list_option_specs("tsbs_devops")
        assert "num_hosts" in specs
        assert "duration_days" in specs
        assert "interval_seconds" in specs
        assert "start_time" in specs

    def test_tpch_skew_specs_registered(self):
        from benchbox.core.tpch_skew.benchmark import TPCHSkewBenchmark

        specs = BenchmarkHookRegistry.list_option_specs("tpch_skew")
        assert "skew_preset" in specs
        assert specs["skew_preset"].default == "moderate"

    def test_vector_search_specs_registered(self):
        from benchbox.core.vector_search.benchmark import VectorSearchBenchmark

        specs = BenchmarkHookRegistry.list_option_specs("vector_search")
        assert "dimensions" in specs
        assert specs["dimensions"].default == 128

    def test_joinorder_force_regenerate_documents_download_cost(self):
        from benchbox.core.joinorder.benchmark import JoinOrderBenchmark

        specs = BenchmarkHookRegistry.list_option_specs("joinorder")
        help_text = specs["force_regenerate"].help
        assert "re-downloading" in help_text
        assert "~1.2 GB" in help_text

    def test_benchmarks_without_specs_still_work(self):
        assert not BenchmarkHookRegistry.has_specs("tpcds")

    def test_tpch_refresh_specs_registered(self):
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        specs = BenchmarkHookRegistry.list_option_specs("tpch")
        assert "tpch_refresh_functions" in specs
        assert "tpch_refresh_first_set" in specs


class TestBenchmarkOptionShellCompletion:
    def _ctx(self, benchmark=None, pairs=()):
        from click import Context
        from click.core import Command

        ctx = Context(Command("run"))
        ctx.params = {"benchmark": benchmark, "benchmark_option_pairs": pairs}
        return ctx

    def _completer(self):
        from benchbox.cli.commands.run import BenchmarkOptionParamType

        return BenchmarkOptionParamType()

    def test_completes_keys_for_selected_benchmark(self):
        items = self._completer().shell_complete(self._ctx("tpch_skew"), None, "")
        assert "skew_preset=" in [item.value for item in items]

    def test_key_prefix_filters(self):
        items = self._completer().shell_complete(self._ctx("tpch_skew"), None, "sk")
        values = [item.value for item in items]
        assert values
        assert all(value.startswith("sk") for value in values)
        assert "skew_preset=" in values

    def test_completes_declared_choice_values(self):
        items = self._completer().shell_complete(self._ctx("tpch_skew"), None, "skew_preset=h")
        assert [item.value for item in items] == ["heavy"]

    def test_choice_prefix_filters(self):
        items = self._completer().shell_complete(self._ctx("tpch_skew"), None, "skew_preset=")
        assert {item.value for item in items} == {"none", "light", "moderate", "heavy", "extreme", "realistic"}

    def test_spec_without_choices_completes_no_values(self):
        items = self._completer().shell_complete(self._ctx("nyctaxi"), None, "year=20")
        assert items == []

    def test_unknown_or_missing_benchmark_completes_nothing(self):
        assert self._completer().shell_complete(self._ctx("tpcds"), None, "") == []
        assert self._completer().shell_complete(self._ctx(None), None, "") == []

    def test_tpch_refresh_options_complete(self):
        items = self._completer().shell_complete(self._ctx("tpch"), None, "tpch_refresh_")
        assert {item.value for item in items} == {"tpch_refresh_functions=", "tpch_refresh_first_set="}

    def test_used_keys_omitted(self):
        items = self._completer().shell_complete(self._ctx("tpch_skew", pairs=(("skew_preset", "heavy"),)), None, "")
        assert "skew_preset=" not in [item.value for item in items]

    def test_completion_imports_lazy_benchmark(self, monkeypatch):
        import sys

        from benchbox.core.hooks.benchmark_hooks import BenchmarkHookRegistry

        module_name = "benchbox.core.tpch_skew.benchmark"
        assert module_name in sys.modules
        monkeypatch.delitem(sys.modules, module_name)
        monkeypatch.delitem(BenchmarkHookRegistry._option_specs, "tpch_skew")
        monkeypatch.delitem(BenchmarkHookRegistry._alias_index, "tpch_skew", raising=False)

        assert BenchmarkHookRegistry.list_option_specs("tpch_skew") == {}
        items = self._completer().shell_complete(self._ctx("tpch_skew"), None, "")

        assert "skew_preset=" in [item.value for item in items]

    def test_completion_unknown_benchmark_is_silent(self):
        items = self._completer().shell_complete(self._ctx("no_such_bench"), None, "")

        assert items == []
