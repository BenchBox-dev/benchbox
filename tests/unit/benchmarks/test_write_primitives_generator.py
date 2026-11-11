# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.write_primitives.generator import WritePrimitivesDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


_TPCH_TABLE_FILES = (
    "customer.tbl",
    "lineitem.tbl",
    "nation.tbl",
    "orders.tbl",
    "part.tbl",
    "partsupp.tbl",
    "region.tbl",
    "supplier.tbl",
)


def _write_mock_tpch_data(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)

    orders_path = directory / "orders.tbl"
    with open(orders_path, "w", encoding="utf-8") as f:
        for order_key in range(1, 4001):
            row = (
                f"{order_key}|{order_key}|O|{1000.0 + order_key:.2f}|1995-01-01|"
                f"1-URGENT|Clerk#000000001|0|sample order {order_key}|\n"
            )
            f.write(row)

    lineitem_path = directory / "lineitem.tbl"
    with open(lineitem_path, "w", encoding="utf-8") as f:
        for line_key in range(1, 6001):
            row = (
                f"{line_key}|{line_key}|{line_key}|1|1.0|10.0|0.0|0.0|N|O|1995-01-02|"
                f"1995-01-03|1995-01-04|DELIVER IN PERSON|AIR|sample lineitem {line_key}|\n"
            )
            f.write(row)

    tiny_row = "1|1|\n"
    for filename in _TPCH_TABLE_FILES:
        table_path = directory / filename
        if table_path.exists():
            continue
        table_path.write_text(tiny_row, encoding="utf-8")


@pytest.fixture(scope="module")
def shared_tpch_seed_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    seed_dir = tmp_path_factory.mktemp("write_primitives_tpch_seed")
    _write_mock_tpch_data(seed_dir)
    return seed_dir


@pytest.fixture(autouse=True)
def mock_tpch_generator(shared_tpch_seed_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    def _mock_generate(self: TPCHDataGenerator) -> dict[str, Path]:
        output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        table_paths: dict[str, Path] = {}
        for filename in _TPCH_TABLE_FILES:
            source = shared_tpch_seed_dir / filename
            target = output_dir / filename
            shutil.copy2(source, target)
            table_paths[filename.removesuffix(".tbl")] = target
        return table_paths

    monkeypatch.setattr(TPCHDataGenerator, "generate", _mock_generate)


def test_generation_lock_target_does_not_materialize_remote_path() -> None:
    class RemotePath:
        def __str__(self) -> str:
            return "s3://benchbox-example/primitives"

        def __fspath__(self) -> str:
            raise AssertionError("remote lock selection must not materialize the cloud path")

    generator = object.__new__(WritePrimitivesDataGenerator)
    generator.output_dir = RemotePath()
    generator._auxiliary_dir = "write_primitives_auxiliary"

    lock_target = generator._generation_lock_target()

    assert lock_target.parent.name == "benchbox-primitives-locks"
    assert len(lock_target.name) == 64


@pytest.mark.slow
class TestFileLocking:
    def test_lock_prevents_concurrent_generation(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)

            assert gen1._acquire_bulk_load_lock(timeout=1)

            try:
                gen2 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
                assert not gen2._acquire_bulk_load_lock(timeout=1)
            finally:
                gen1._release_bulk_load_lock()

    def test_lock_is_released_after_generation(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()

            lock_file = output_dir / "write_primitives_auxiliary" / ".bulk_load_generation.lock"
            assert not lock_file.exists()

    def test_lock_contains_pid(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)

            assert gen._acquire_bulk_load_lock(timeout=1)

            try:
                lock_file = output_dir / "write_primitives_auxiliary" / ".bulk_load_generation.lock"
                assert lock_file.exists()

                content = lock_file.read_text()
                assert content.startswith("pid:")

                pid = int(content.split(":")[1])
                assert pid == os.getpid()
            finally:
                gen._release_bulk_load_lock()

    def test_stale_lock_detection_by_pid(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            files_dir = output_dir / "write_primitives_auxiliary"
            files_dir.mkdir(parents=True, exist_ok=True)

            lock_file = files_dir / ".bulk_load_generation.lock"
            fake_pid = 999999
            lock_file.write_text(f"pid:{fake_pid}\n")

            old_time = time.time() - 400
            os.utime(lock_file, (old_time, old_time))

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)

            assert gen._acquire_bulk_load_lock(timeout=1)
            gen._release_bulk_load_lock()

    def test_stale_lock_detection_by_age(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            files_dir = output_dir / "write_primitives_auxiliary"
            files_dir.mkdir(parents=True, exist_ok=True)

            lock_file = files_dir / ".bulk_load_generation.lock"
            lock_file.write_text(f"pid:{os.getpid()}\n")

            old_time = time.time() - 400
            os.utime(lock_file, (old_time, old_time))

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)

            assert gen._acquire_bulk_load_lock(timeout=1)
            gen._release_bulk_load_lock()

    def test_process_liveness_check(self):

        gen = WritePrimitivesDataGenerator(scale_factor=0.01, verbose=False)

        assert gen._is_process_running(os.getpid())

        fake_pid = 999999
        assert not gen._is_process_running(fake_pid)


@pytest.mark.slow
class TestScaleFactorValidation:
    def test_files_reused_when_scale_factor_matches(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen1.generate()

            test_file = output_dir / "write_primitives_auxiliary" / "csv_small_1k.csv"
            assert test_file.exists()
            original_mtime = test_file.stat().st_mtime

            frozen_mtime = original_mtime - 1
            os.utime(test_file, (frozen_mtime, frozen_mtime))

            gen2 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen2.generate()

            assert test_file.stat().st_mtime == frozen_mtime

    def test_files_regenerated_when_scale_factor_changes(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen1 = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen1.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen1.generate()

            metadata_file = output_dir / "write_primitives_auxiliary" / ".bulk_load_metadata.json"
            assert metadata_file.exists()
            with open(metadata_file, encoding="utf-8") as f:
                metadata = json.load(f)
            assert metadata["scale_factor"] == 0.01

            tpch_gen2 = TPCHDataGenerator(
                scale_factor=0.02, output_dir=output_dir, verbose=False, force_regenerate=True
            )
            tpch_gen2.generate()

            gen2 = WritePrimitivesDataGenerator(scale_factor=0.02, output_dir=output_dir, verbose=False)
            gen2.generate()

            with open(metadata_file, encoding="utf-8") as f:
                metadata = json.load(f)
            assert metadata["scale_factor"] == 0.02

    def test_metadata_file_written_correctly(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()

            metadata_file = output_dir / "write_primitives_auxiliary" / ".bulk_load_metadata.json"
            assert metadata_file.exists()

            with open(metadata_file, encoding="utf-8") as f:
                metadata = json.load(f)

            assert "scale_factor" in metadata
            assert metadata["scale_factor"] == 0.01
            assert "generated_at" in metadata
            assert "file_count" in metadata
            assert metadata["file_count"] > 0

    def test_check_bulk_load_files_exist_validates_scale_factor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen1.generate()

            gen2 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            assert gen2.check_bulk_load_files_exist()

            gen3 = WritePrimitivesDataGenerator(scale_factor=0.02, output_dir=output_dir, verbose=False)
            assert not gen3.check_bulk_load_files_exist()


@pytest.mark.slow
class TestSmallDatasetHandling:
    def test_parallel_parts_with_small_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()

            files_dir = output_dir / "write_primitives_auxiliary"
            for part_num in range(1, 5):
                part_file = files_dir / f"csv_parallel_part{part_num}.csv"
                assert part_file.exists()

                with open(part_file, encoding="utf-8") as f:
                    lines = f.readlines()
                assert len(lines) > 1

    def test_error_file_with_small_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()

            error_file = output_dir / "write_primitives_auxiliary" / "csv_with_errors.csv"
            assert error_file.exists()

            with open(error_file, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) > 1

    def test_all_parallel_parts_have_roughly_equal_rows(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()

            files_dir = output_dir / "write_primitives_auxiliary"
            part_sizes = []
            for part_num in range(1, 5):
                with open(files_dir / f"csv_parallel_part{part_num}.csv", encoding="utf-8") as f:
                    lines = f.readlines()
                part_sizes.append(len(lines) - 1)

            assert all(size > 0 for size in part_sizes)

            avg_size = sum(part_sizes) / len(part_sizes)
            for size in part_sizes:
                deviation = abs(size - avg_size) / avg_size if avg_size > 0 else 0
                assert deviation <= 0.5

    def test_no_index_error_with_very_small_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()


@pytest.mark.slow
class TestConcurrentGeneration:
    def test_double_check_locking_pattern(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen1.generate()

            test_file = output_dir / "write_primitives_auxiliary" / "csv_small_1k.csv"
            original_mtime = test_file.stat().st_mtime

            frozen_mtime = original_mtime - 1
            os.utime(test_file, (frozen_mtime, frozen_mtime))

            gen2 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen2.generate()

            assert test_file.stat().st_mtime == frozen_mtime

    def test_force_regenerate_bypasses_existing_files(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen1.generate()

            test_file = output_dir / "write_primitives_auxiliary" / "csv_small_1k.csv"
            original_mtime = test_file.stat().st_mtime

            frozen_mtime = original_mtime - 1
            os.utime(test_file, (frozen_mtime, frozen_mtime))

            gen2 = WritePrimitivesDataGenerator(
                scale_factor=0.01, output_dir=output_dir, verbose=False, force_regenerate=True
            )
            gen2.generate()

            assert test_file.stat().st_mtime > frozen_mtime


class TestErrorHandling:
    def test_corrupted_metadata_file_handled_gracefully(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            files_dir = output_dir / "write_primitives_auxiliary"
            files_dir.mkdir(parents=True, exist_ok=True)

            metadata_file = files_dir / ".bulk_load_metadata.json"
            metadata_file.write_text("{ corrupted json")

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen.generate()

            assert (files_dir / "csv_small_1k.csv").exists()

    def test_missing_metadata_file_handled_gracefully(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            gen1.generate()

            metadata_file = output_dir / "write_primitives_auxiliary" / ".bulk_load_metadata.json"
            metadata_file.unlink()

            gen2 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            result = gen2.check_bulk_load_files_exist()
            assert isinstance(result, bool)

    def test_no_tpch_data_skips_bulk_load_generation(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            gen = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            files = gen.generate_bulk_load_files()

            assert files == {}

    def test_lock_timeout_returns_false(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            files_dir = output_dir / "write_primitives_auxiliary"
            files_dir.mkdir(parents=True, exist_ok=True)

            tpch_gen = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
            tpch_gen.generate()

            gen1 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)

            assert gen1._acquire_bulk_load_lock(timeout=0.1)

            try:
                gen2 = WritePrimitivesDataGenerator(scale_factor=0.01, output_dir=output_dir, verbose=False)
                result = gen2._acquire_bulk_load_lock(timeout=0.1)
                assert result is False
            finally:
                gen1._release_bulk_load_lock()


