from pathlib import Path
from typing import Any, Callable

import pytest

from benchbox.core.read_primitives.generator import ReadPrimitivesDataGenerator
from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestReadPrimitivesDataGenerator:
    def test_generator_initialization(self, temp_dir):

        generator = ReadPrimitivesDataGenerator(scale_factor=0.01, output_dir=temp_dir)
        assert generator.scale_factor == 0.01
        assert generator.output_dir == temp_dir

    def test_small_data_generation(self, temp_dir, small_scale_factor):
        ReadPrimitivesDataGenerator(scale_factor=small_scale_factor, output_dir=temp_dir)

        mock_files = {
            "region": temp_dir / "region.csv",
            "nation": temp_dir / "nation.csv",
        }

        for table, path in mock_files.items():
            if table == "region":
                path.write_text("\n".join([f"region_{i}" for i in range(5)]))
            elif table == "nation":
                path.write_text("\n".join([f"nation_{i}" for i in range(25)]))

        assert mock_files["region"].exists()
        assert mock_files["nation"].exists()

        with open(mock_files["region"], encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 5

        with open(mock_files["nation"], encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 25

    def test_local_generation_writes_to_expected_directory(self, tmp_path, monkeypatch):

        output_dir = tmp_path / "primitives_sf1"
        generator = ReadPrimitivesDataGenerator(scale_factor=1.0, output_dir=output_dir)

        def fake_generate() -> dict[str, Any]:
            target_dir = generator.tpch_generator.output_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            sample_file = target_dir / "region.tbl.zst"
            sample_file.write_text("data")
            return {"region": sample_file}

        monkeypatch.setattr(generator.tpch_generator, "generate", fake_generate)

        paths = generator.generate_data(tables=["region"])

        assert generator.output_dir == output_dir
        assert generator.tpch_generator.output_dir == output_dir
        assert output_dir.exists()
        assert paths["region"] == str(output_dir / "region.tbl.zst")
        assert (output_dir / "region.tbl.zst").exists()

    def test_cloud_generation_delegates_to_upload_helper(self, monkeypatch, tmp_path):

        class FakeCloudPath:
            def __init__(self, value: str) -> None:
                self.value = value.rstrip("/")

            def __truediv__(self, other: str) -> "FakeCloudPath":
                return FakeCloudPath(f"{self.value}/{other}")

            def __str__(self) -> str:
                return self.value

            def __repr__(self) -> str:
                return f"FakeCloudPath({self.value!r})"

            def __eq__(self, other: object) -> bool:
                return str(self) == str(other)

        def fake_create_path_handler(path: object) -> object:
            if isinstance(path, FakeCloudPath):
                return path
            if isinstance(path, str) and path.startswith("s3://"):
                return FakeCloudPath(path)
            return Path(path)

        monkeypatch.setattr("benchbox.core.read_primitives.generator.create_path_handler", fake_create_path_handler)
        monkeypatch.setattr("benchbox.core.tpch.generator.create_path_handler", fake_create_path_handler)
        monkeypatch.setattr("benchbox.utils.cloud_storage.create_path_handler", fake_create_path_handler)

        generator = ReadPrimitivesDataGenerator(scale_factor=0.01, output_dir="s3://bucket/primitives")

        captured: dict[str, object] = {}

        def fake_tpch_generate() -> dict[str, Any]:
            target_dir = generator.tpch_generator.output_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            sample_file = target_dir / "region.tbl"
            sample_file.write_text("temp")
            return {"region": sample_file}

        monkeypatch.setattr(generator.tpch_generator, "generate", fake_tpch_generate)

        def fake_cloud_handler(
            self: CloudStorageGeneratorMixin,
            output_dir: object,
            local_generate_func: Callable[[Path], dict[str, Path]],
            verbose: bool = False,
        ) -> dict[str, FakeCloudPath]:
            captured["output_dir"] = output_dir
            captured["local_generate_func"] = local_generate_func
            _ = local_generate_func(tmp_path)
            return {"region": FakeCloudPath(f"{output_dir}/region.tbl.zst")}

        monkeypatch.setattr(CloudStorageGeneratorMixin, "_handle_cloud_or_local_generation", fake_cloud_handler)

        paths = generator.generate_data(tables=["region"])

        expected_root = FakeCloudPath("s3://bucket/primitives")
        assert captured["output_dir"] == expected_root
        assert callable(captured["local_generate_func"])
        assert paths["region"] == "s3://bucket/primitives/region.tbl.zst"
