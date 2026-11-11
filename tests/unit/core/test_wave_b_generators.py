from __future__ import annotations

import pytest

from benchbox.core.clickbench.generator import ClickBenchDataGenerator
from benchbox.core.h2odb.generator import H2ODataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_clickbench_generator_record_and_local_generation(tmp_path):
    gen = ClickBenchDataGenerator(scale_factor=0.00001, output_dir=tmp_path, compression_enabled=False)
    gen.base_records = 2

    rec = gen._generate_hit_record(1, __import__("datetime").datetime(2013, 7, 1))
    assert len(rec) == 105

    paths = gen._generate_data_local(tmp_path, tables=["hits"])
    assert "hits" in paths
    assert gen._table_row_counts["hits"] == 2


def test_clickbench_generator_scales_record_count() -> None:
    gen = ClickBenchDataGenerator(scale_factor=10.0)

    assert gen.base_records == 10_000_000


def test_h2odb_generator_trips_and_manifest_tracking(tmp_path):
    gen = H2ODataGenerator(scale_factor=1.0, output_dir=tmp_path, compression_enabled=False)
    gen.base_trips = 3
    gen.output_dir = tmp_path

    path = gen._generate_trips_data()
    assert path.exists()
    assert gen._manifest_row_counts["trips"] == 3

    table_paths = {"trips": path}
    gen._write_manifest(table_paths)
    manifest = tmp_path / "_datagen_manifest.json"
    assert manifest.exists()


