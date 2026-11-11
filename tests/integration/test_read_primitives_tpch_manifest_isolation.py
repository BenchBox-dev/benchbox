# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.generator import ReadPrimitivesDataGenerator
from benchbox.core.tpch.generator import TPCHDataGenerator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


@pytest.mark.integration
class TestPrimitivesTpchManifestIsolation:
    def test_primitives_does_not_rewrite_tpch_manifest(self, temp_dir, small_scale_factor):

        shared_dir = temp_dir / "shared_tpch_data"
        shared_dir.mkdir()

        tpch_gen = TPCHDataGenerator(scale_factor=small_scale_factor, output_dir=str(shared_dir))
        tpch_gen.generate()

        manifest_path = shared_dir / "_datagen_manifest.json"
        assert manifest_path.exists(), "TPC-H should create a manifest"

        with manifest_path.open("r") as f:
            original_manifest = json.load(f)

        assert original_manifest["benchmark"] == "tpch", "Original manifest should have benchmark='tpch'"
        json.dumps(original_manifest, sort_keys=True)

        primitives_gen = ReadPrimitivesDataGenerator(scale_factor=small_scale_factor, output_dir=str(shared_dir))

        primitives_gen.generate_data()

        with manifest_path.open("r") as f:
            after_manifest = json.load(f)

        assert after_manifest["benchmark"] == "tpch", "Manifest should still have benchmark='tpch' after Primitives"

        assert after_manifest["benchmark"] == original_manifest["benchmark"], "Benchmark field must not change"
        assert after_manifest["scale_factor"] == original_manifest["scale_factor"], "Scale factor must not change"
        assert after_manifest["tables"] == original_manifest["tables"], "Tables must not change"

    def test_tpch_then_primitives_then_tpch(self, temp_dir, small_scale_factor):

        shared_dir = temp_dir / "shared_data"
        shared_dir.mkdir()

        tpch_gen1 = TPCHDataGenerator(scale_factor=small_scale_factor, output_dir=str(shared_dir))
        tpch_gen1.generate()

        manifest_path = shared_dir / "_datagen_manifest.json"
        with manifest_path.open("r") as f:
            tpch_manifest_1 = json.load(f)

        assert tpch_manifest_1["benchmark"] == "tpch"

        primitives_gen = ReadPrimitivesDataGenerator(scale_factor=small_scale_factor, output_dir=str(shared_dir))
        primitives_gen.generate_data()

        with manifest_path.open("r") as f:
            after_primitives = json.load(f)

        assert after_primitives["benchmark"] == "tpch", "Primitives should not change benchmark field"

        tpch_gen2 = TPCHDataGenerator(scale_factor=small_scale_factor, output_dir=str(shared_dir))
        tpch_gen2.generate()

        with manifest_path.open("r") as f:
            tpch_manifest_2 = json.load(f)

        assert tpch_manifest_2["benchmark"] == "tpch"
        assert tpch_manifest_2["scale_factor"] == small_scale_factor

    def test_primitives_benchmark_uses_tpch_path(self, temp_dir):

        benchmark = ReadPrimitivesBenchmark(scale_factor=1.0)

        output_path = str(benchmark.output_dir)
        assert "tpch_sf1" in output_path, f"Expected tpch_sf1 in path, got: {output_path}"
        assert "primitives_sf" not in output_path, f"Should not have primitives_sf in path: {output_path}"

    def test_primitives_with_custom_path(self, temp_dir):

        custom_path = temp_dir / "custom_primitives"

        benchmark = ReadPrimitivesBenchmark(scale_factor=1.0, output_dir=str(custom_path))

        assert str(benchmark.output_dir) == str(custom_path)


