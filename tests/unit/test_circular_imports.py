# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

import importlib

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestCircularImports:
    def test_no_circular_imports_core_tpch(self):
        modules = [
            "benchbox.core.tpch.queries",
            "benchbox.core.tpch.generator",
            "benchbox.core.tpch.benchmark",
        ]

        for module_name in modules:
            importlib.import_module(module_name)

    def test_key_modules_importable(self):
        key_modules = [
            "benchbox",
            "benchbox.base",
            "benchbox.tpch",
            "benchbox.tpcds",
            "benchbox.read_primitives",
            "benchbox.write_primitives",
            "benchbox.ssb",
            "benchbox.clickbench",
            "benchbox.h2odb",
            "benchbox.amplab",
            "benchbox.joinorder",
            "benchbox.tpchavoc",
        ]

        for module_name in key_modules:
            importlib.import_module(module_name)

    def test_core_benchmark_classes_importable(self):
        benchmark_imports = [
            ("benchbox.core.tpch", "TPCHBenchmark"),
            ("benchbox.core.tpcds", "TPCDSBenchmark"),
            ("benchbox.core.read_primitives", "ReadPrimitivesBenchmark"),
            ("benchbox.core.write_primitives.benchmark", "WritePrimitivesBenchmark"),
            ("benchbox.core.ssb", "SSBBenchmark"),
            ("benchbox.core.clickbench", "ClickBenchBenchmark"),
            ("benchbox.core.h2odb", "H2OBenchmark"),
            ("benchbox.core.amplab", "AMPLabBenchmark"),
            ("benchbox.core.joinorder", "JoinOrderBenchmark"),
            ("benchbox.core.tpchavoc", "TPCHavocBenchmark"),
        ]

        for module_name, class_name in benchmark_imports:
            if "tpcdi" in module_name and importlib.util.find_spec("pandas") is None:
                continue

            module = importlib.import_module(module_name)
            cls = getattr(module, class_name, None)
            assert cls is not None, f"{module_name} should export {class_name}"
            assert callable(cls), f"{class_name} should be callable (a class)"

    def test_relative_imports_in_init_files(self):
        init_modules = [
            "benchbox.core.ssb",
            "benchbox.core.amplab",
            "benchbox.core.tpcdi",
            "benchbox.core.tpch",
            "benchbox.core.clickbench",
            "benchbox.core.h2odb",
            "benchbox.core.tpchavoc",
        ]

        for module_name in init_modules:
            if "tpcdi" in module_name and importlib.util.find_spec("pandas") is None:
                pytest.skip("pandas not available for TPCDI tests")

            module = importlib.import_module(module_name)

            if "tpchavoc" not in module_name:
                tables = getattr(module, "TABLES", None)
                create_fn = getattr(module, "get_create_table_sql", None)
                assert tables is not None or create_fn is not None, (
                    f"{module_name} should have TABLES or get_create_table_sql"
                )

    def test_platform_adapters_importable(self):
        platform_modules = [
            "benchbox.platforms.base",
            "benchbox.platforms.duckdb",
            "benchbox.platforms.sqlite",
        ]

        for module_name in platform_modules:
            importlib.import_module(module_name)

    def test_import_validation_script_functions(self):

        critical_modules = [
            "benchbox",
            "benchbox.base",
            "benchbox.core.tpch.benchmark",
            "benchbox.core.tpch.generator",
            "benchbox.core.tpch.queries",
            "benchbox.core.tpcds.benchmark",
            "benchbox.core.tpcds.generator",
            "benchbox.core.tpcds.queries",
        ]

        for module_name in critical_modules:
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                pytest.fail(f"Failed to import {module_name}: {e}")

    def test_cross_package_imports_valid(self):
        cross_imports = [
            ("benchbox.core.read_primitives.generator", "benchbox.core.tpch"),
            ("benchbox.core.tpchavoc.benchmark", "benchbox.core.tpch"),
            ("benchbox.core.tpch.schema", "benchbox.core.tuning"),
            ("benchbox.core.ssb.schema", "benchbox.core.tuning"),
        ]

        for importer_module, imported_package in cross_imports:
            importlib.import_module(importer_module)
            importlib.import_module(imported_package)

    def test_no_import_errors_on_reload(self):
        test_modules = [
            "benchbox.core.tpch",
            "benchbox.core.tpcds",
            "benchbox.core.ssb",
        ]

        for module_name in test_modules:
            module = importlib.import_module(module_name)
            reloaded_module = importlib.reload(module)
            assert reloaded_module is module


@pytest.mark.unit
class TestImportStructure:
    def test_benchbox_package_importable(self):
        import benchbox

        assert isinstance(benchbox.__version__, str)
        assert len(benchbox.__version__) > 0

    def test_base_benchmark_importable(self):
        from benchbox.base import BaseBenchmark

        assert callable(BaseBenchmark)

    def test_utility_modules_importable(self):
        utility_modules = [
            "benchbox.utils.scale_factor",
            "benchbox.utils.cloud_storage",
            "benchbox.utils.compression_mixin",
            "benchbox.utils.data_validation",
        ]

        for module_name in utility_modules:
            importlib.import_module(module_name)

    def test_top_level_benchmark_interfaces(self):
        from benchbox import SSB, TPCDS, TPCH, ReadPrimitives, WritePrimitives

        tpch = TPCH(scale_factor=0.01)
        tpcds = TPCDS(scale_factor=1.0)
        read_primitives = ReadPrimitives(scale_factor=0.01)
        write_primitives = WritePrimitives(scale_factor=0.01)
        ssb = SSB(scale_factor=0.01, compress_data=False, compression_type="none")

        for benchmark in [tpch, tpcds, read_primitives, write_primitives, ssb]:
            assert benchmark.scale_factor > 0, f"{type(benchmark).__name__} should have positive scale_factor"


