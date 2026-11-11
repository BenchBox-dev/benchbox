import json

import pytest

from benchbox.core.manifest import (
    ConvertedFileEntry,
    FileEntry,
    ManifestV1,
    ManifestV2,
    TableFormats,
    detect_version,
    get_files_for_format,
    get_preferred_format,
    load_manifest,
    upgrade_v1_to_v2,
    write_manifest,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestManifestModels:
    def test_file_entry_creation(self):

        entry = FileEntry(path="customer.tbl", size_bytes=100, row_count=10)
        assert entry.path == "customer.tbl"
        assert entry.size_bytes == 100
        assert entry.row_count == 10

    def test_converted_file_entry_creation(self):

        entry = ConvertedFileEntry(
            path="customer.parquet",
            size_bytes=50,
            row_count=10,
            converted_from="tbl",
            converted_at="2025-11-24T10:00:00Z",
            compression="snappy",
            row_groups=1,
            conversion_options={"merge_shards": True},
        )
        assert entry.path == "customer.parquet"
        assert entry.converted_from == "tbl"
        assert entry.compression == "snappy"

    def test_table_formats_creation(self):

        formats = TableFormats(
            formats={
                "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                "parquet": [ConvertedFileEntry(path="customer.parquet", size_bytes=50, row_count=10)],
            }
        )
        assert "tbl" in formats.formats
        assert "parquet" in formats.formats
        assert len(formats.formats["tbl"]) == 1

    def test_manifest_v1_creation(self):
        manifest = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={"customer": [FileEntry(path="customer.tbl", size_bytes=100, row_count=10)]},
        )
        assert manifest.benchmark == "tpch"
        assert manifest.scale_factor == 0.01
        assert "customer" in manifest.tables

    def test_manifest_v2_creation(self):
        manifest = ManifestV2(
            version=2,
            benchmark="tpch",
            scale_factor=0.01,
            format_preference=["parquet", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={"tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)]}
                )
            },
        )
        assert manifest.version == 2
        assert manifest.format_preference == ["parquet", "tbl"]
        assert "customer" in manifest.tables


class TestVersionDetection:
    def test_detect_v1_no_version_field(self):
        data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {"customer": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}]},
        }
        assert detect_version(data) == 1

    def test_detect_v2_with_version_field(self):
        data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {
                "customer": {"formats": {"tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}]}}
            },
        }
        assert detect_version(data) == 2

    def test_detect_v2_by_structure(self):
        data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {
                "customer": {"formats": {"tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}]}}
            },
        }
        assert detect_version(data) == 2

    def test_detect_empty_tables(self):

        data = {"benchmark": "tpch", "scale_factor": 0.01, "tables": {}}
        assert detect_version(data) == 1


class TestManifestIO:
    def test_load_v1_manifest(self, tmp_path):
        manifest_data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {"customer": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}]},
        }

        manifest_path = tmp_path / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)

        manifest = load_manifest(manifest_path)
        assert isinstance(manifest, ManifestV1)
        assert manifest.benchmark == "tpch"
        assert "customer" in manifest.tables

    def test_load_v2_manifest(self, tmp_path):
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {"formats": {"tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}]}}
            },
        }

        manifest_path = tmp_path / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)

        manifest = load_manifest(manifest_path)
        assert isinstance(manifest, ManifestV2)
        assert manifest.version == 2
        assert manifest.format_preference == ["parquet", "tbl"]
        assert "customer" in manifest.tables

    def test_write_v1_manifest(self, tmp_path):
        manifest = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={"customer": [FileEntry(path="customer.tbl", size_bytes=100, row_count=10)]},
        )

        manifest_path = tmp_path / "manifest.json"
        write_manifest(manifest, manifest_path)

        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["benchmark"] == "tpch"
        assert "customer" in data["tables"]
        assert "version" not in data

    def test_write_v2_manifest(self, tmp_path):
        manifest = ManifestV2(
            version=2,
            benchmark="tpch",
            scale_factor=0.01,
            format_preference=["parquet", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={"tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)]}
                )
            },
        )

        manifest_path = tmp_path / "manifest.json"
        write_manifest(manifest, manifest_path)

        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["version"] == 2
        assert data["format_preference"] == ["parquet", "tbl"]
        assert "customer" in data["tables"]
        assert "formats" in data["tables"]["customer"]

    def test_write_v2_with_conversion_metadata(self, tmp_path):
        manifest = ManifestV2(
            version=2,
            benchmark="tpch",
            scale_factor=0.01,
            format_preference=["parquet", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "parquet": [
                            ConvertedFileEntry(
                                path="customer.parquet",
                                size_bytes=50,
                                row_count=10,
                                converted_from="tbl",
                                converted_at="2025-11-24T10:00:00Z",
                                compression="snappy",
                                row_groups=1,
                                conversion_options={"merge_shards": True},
                            )
                        ],
                    }
                )
            },
        )

        manifest_path = tmp_path / "manifest.json"
        write_manifest(manifest, manifest_path)

        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)

        parquet_files = data["tables"]["customer"]["formats"]["parquet"]
        assert parquet_files[0]["converted_from"] == "tbl"
        assert parquet_files[0]["compression"] == "snappy"
        assert parquet_files[0]["conversion_options"]["merge_shards"] is True


class TestManifestUpgrade:
    def test_upgrade_v1_to_v2(self):
        v1 = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={"customer": [FileEntry(path="customer.tbl", size_bytes=100, row_count=10)]},
        )

        v2 = upgrade_v1_to_v2(v1)

        assert isinstance(v2, ManifestV2)
        assert v2.version == 2
        assert v2.benchmark == "tpch"
        assert v2.scale_factor == 0.01
        assert "customer" in v2.tables
        assert "tbl" in v2.tables["customer"].formats
        assert "tbl" in v2.format_preference

    def test_upgrade_preserves_metadata(self):

        v1 = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={"customer": [FileEntry(path="customer.tbl", size_bytes=100, row_count=10)]},
            compression={"type": "zstd", "level": 3},
            parallel=4,
            created_at="2025-11-24T09:00:00Z",
            generator_version="0.1.0",
        )

        v2 = upgrade_v1_to_v2(v1)

        assert v2.compression == {"type": "zstd", "level": 3}
        assert v2.parallel == 4
        assert v2.created_at == "2025-11-24T09:00:00Z"
        assert v2.generator_version == "0.1.0"

    def test_upgrade_detects_parquet_format(self):

        v1 = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={"customer": [FileEntry(path="customer.parquet", size_bytes=100, row_count=10)]},
        )

        v2 = upgrade_v1_to_v2(v1)

        assert "parquet" in v2.tables["customer"].formats
        assert "parquet" in v2.format_preference

    def test_upgrade_detects_csv_format(self):

        v1 = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={"customer": [FileEntry(path="customer.csv", size_bytes=100, row_count=10)]},
        )

        v2 = upgrade_v1_to_v2(v1)

        assert "csv" in v2.tables["customer"].formats


class TestFormatPreferences:
    def test_get_preferred_format_manifest_preference_wins_by_default(self):
        manifest = ManifestV2(
            version=2,
            format_preference=["parquet", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "parquet": [ConvertedFileEntry(path="customer.parquet", size_bytes=50, row_count=10)],
                    }
                )
            },
        )

        preferred = get_preferred_format(manifest, "customer", "duckdb")
        assert preferred == "parquet"

        preferred = get_preferred_format(manifest, "customer", "redshift")
        assert preferred == "parquet"

    def test_get_preferred_format_can_prefer_platform_defaults_when_requested(self):

        manifest = ManifestV2(
            version=2,
            format_preference=["parquet", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "parquet": [ConvertedFileEntry(path="customer.parquet", size_bytes=50, row_count=10)],
                    }
                )
            },
        )

        preferred = get_preferred_format(
            manifest,
            "customer",
            "redshift",
            prefer_platform_defaults=True,
        )
        assert preferred == "tbl"

    def test_get_preferred_format_platform_preference_no_manifest_pref(self):

        manifest = ManifestV2(
            version=2,
            format_preference=[],
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "parquet": [ConvertedFileEntry(path="customer.parquet", size_bytes=50, row_count=10)],
                    }
                )
            },
        )

        preferred = get_preferred_format(manifest, "customer", "duckdb")
        assert preferred == "tbl"

    def test_get_preferred_format_external_mode_uses_external_capabilities(self):

        manifest = ManifestV2(
            version=2,
            format_preference=[],
            tables={
                "lineitem": TableFormats(
                    formats={
                        "parquet": [ConvertedFileEntry(path="lineitem.parquet", size_bytes=50, row_count=10)],
                        "delta": [ConvertedFileEntry(path="lineitem", size_bytes=0, row_count=10)],
                    }
                )
            },
        )

        assert get_preferred_format(manifest, "lineitem", "bigquery") == "parquet"
        assert get_preferred_format(manifest, "lineitem", "bigquery", table_mode="external") == "parquet"
        assert (
            get_preferred_format(
                manifest,
                "lineitem",
                "bigquery",
                table_mode="external",
                platform_config={
                    "staging_root": "gs://bucket/prefix",
                    "biglake_connection": "project.us.conn",
                },
            )
            == "delta"
        )

    def test_get_preferred_format_fallback(self):

        manifest = ManifestV2(
            version=2,
            format_preference=[],
            tables={
                "customer": TableFormats(
                    formats={"tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)]}
                )
            },
        )

        preferred = get_preferred_format(manifest, "customer", "duckdb")
        assert preferred == "tbl"

    def test_get_preferred_format_unlisted_platform_falls_back_to_first_available(self):

        manifest = ManifestV2(
            version=2,
            format_preference=["delta", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "delta": [ConvertedFileEntry(path="customer", size_bytes=50, row_count=10)],
                    }
                )
            },
        )

        preferred = get_preferred_format(manifest, "customer", "some-unlisted-platform")
        assert preferred == "tbl"

    def test_get_preferred_format_missing_table(self):

        manifest = ManifestV2(version=2, tables={})

        preferred = get_preferred_format(manifest, "customer", "duckdb")
        assert preferred is None

    def test_get_files_for_format(self):

        manifest = ManifestV2(
            version=2,
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "parquet": [
                            ConvertedFileEntry(path="customer.parquet", size_bytes=50, row_count=10),
                            ConvertedFileEntry(path="customer_2.parquet", size_bytes=50, row_count=10),
                        ],
                    }
                )
            },
        )

        files = get_files_for_format(manifest, "customer", "parquet")
        assert len(files) == 2
        assert "customer.parquet" in files
        assert "customer_2.parquet" in files

    def test_get_files_for_missing_format(self):

        manifest = ManifestV2(
            version=2,
            tables={
                "customer": TableFormats(
                    formats={"tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)]}
                )
            },
        )

        files = get_files_for_format(manifest, "customer", "delta")
        assert files == []

    def test_get_files_for_missing_table(self):

        manifest = ManifestV2(version=2, tables={})

        files = get_files_for_format(manifest, "customer", "tbl")
        assert files == []


class TestManifestRoundTrip:
    def test_v1_roundtrip(self, tmp_path):
        original = ManifestV1(
            benchmark="tpch",
            scale_factor=0.01,
            tables={
                "customer": [FileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                "orders": [FileEntry(path="orders.tbl", size_bytes=200, row_count=20)],
            },
        )

        manifest_path = tmp_path / "manifest.json"
        write_manifest(original, manifest_path)
        loaded = load_manifest(manifest_path)

        assert isinstance(loaded, ManifestV1)
        assert loaded.benchmark == original.benchmark
        assert loaded.scale_factor == original.scale_factor
        assert len(loaded.tables) == 2

    def test_v2_roundtrip(self, tmp_path):
        original = ManifestV2(
            version=2,
            benchmark="tpch",
            scale_factor=0.01,
            format_preference=["parquet", "tbl"],
            tables={
                "customer": TableFormats(
                    formats={
                        "tbl": [ConvertedFileEntry(path="customer.tbl", size_bytes=100, row_count=10)],
                        "parquet": [
                            ConvertedFileEntry(
                                path="customer.parquet",
                                size_bytes=50,
                                row_count=10,
                                converted_from="tbl",
                                converted_at="2025-11-24T10:00:00Z",
                            )
                        ],
                    }
                )
            },
        )

        manifest_path = tmp_path / "manifest.json"
        write_manifest(original, manifest_path)
        loaded = load_manifest(manifest_path)

        assert isinstance(loaded, ManifestV2)
        assert loaded.version == 2
        assert loaded.format_preference == ["parquet", "tbl"]
        assert "parquet" in loaded.tables["customer"].formats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


