import pytest

from benchbox.utils.datagen_manifest import get_table_files

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_manifest(format_preference, table_formats):
    return {
        "version": 2,
        "benchmark": "tpch",
        "scale_factor": 1.0,
        "format_preference": format_preference,
        "tables": {
            "orders": {
                "formats": table_formats,
            }
        },
    }


_DELTA_ENTRIES = [
    {"path": "orders", "size_bytes": 4096, "row_count": 100, "is_directory": True},
]

_PARQUET_ENTRIES = [
    {"path": "orders.parquet", "size_bytes": 2048, "row_count": 100},
]

_TBL_ENTRIES = [
    {"path": "orders.tbl", "size_bytes": 1024, "row_count": 100},
]

_ICEBERG_ENTRIES = [
    {"path": "orders_iceberg", "size_bytes": 8192, "row_count": 100, "is_directory": True},
]


class TestGetTableFilesDefaultSelection:
    def test_default_respects_directory_preference(self):
        manifest = _make_manifest(
            ["delta", "parquet", "tbl"],
            {"delta": _DELTA_ENTRIES, "parquet": _PARQUET_ENTRIES, "tbl": _TBL_ENTRIES},
        )
        result = get_table_files(manifest, "orders")
        assert result == _DELTA_ENTRIES

    def test_default_returns_first_available_without_preference(self):
        manifest = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "tables": {
                "orders": {
                    "formats": {
                        "delta": _DELTA_ENTRIES,
                        "tbl": _TBL_ENTRIES,
                    }
                }
            },
        }
        result = get_table_files(manifest, "orders")
        assert result == _DELTA_ENTRIES

    def test_non_directory_format_selected_first(self):
        manifest = _make_manifest(
            ["parquet", "delta", "tbl"],
            {"parquet": _PARQUET_ENTRIES, "delta": _DELTA_ENTRIES, "tbl": _TBL_ENTRIES},
        )
        result = get_table_files(manifest, "orders")
        assert result == _PARQUET_ENTRIES

    def test_explicit_format_returns_directory_entries(self):
        manifest = _make_manifest(
            ["delta", "parquet"],
            {"delta": _DELTA_ENTRIES, "parquet": _PARQUET_ENTRIES},
        )
        result = get_table_files(manifest, "orders", format="delta")
        assert result == _DELTA_ENTRIES

    def test_empty_table_returns_empty_list(self):
        manifest = _make_manifest(["tbl"], {"tbl": _TBL_ENTRIES})
        result = get_table_files(manifest, "nonexistent")
        assert result == []


class TestGetTableFilesDirectorySkipping:
    def test_skips_delta_selects_parquet(self):
        manifest = _make_manifest(
            ["delta", "parquet", "tbl"],
            {"delta": _DELTA_ENTRIES, "parquet": _PARQUET_ENTRIES, "tbl": _TBL_ENTRIES},
        )
        result = get_table_files(manifest, "orders", skip_directory_only_formats=True)
        assert result == _PARQUET_ENTRIES

    def test_skips_delta_selects_tbl(self):
        manifest = _make_manifest(
            ["delta", "tbl"],
            {"delta": _DELTA_ENTRIES, "tbl": _TBL_ENTRIES},
        )
        result = get_table_files(manifest, "orders", skip_directory_only_formats=True)
        assert result == _TBL_ENTRIES

    def test_skips_all_directory_formats(self):
        manifest = _make_manifest(
            ["delta", "iceberg", "tbl"],
            {"delta": _DELTA_ENTRIES, "iceberg": _ICEBERG_ENTRIES, "tbl": _TBL_ENTRIES},
        )
        result = get_table_files(manifest, "orders", skip_directory_only_formats=True)
        assert result == _TBL_ENTRIES

    def test_last_resort_returns_directory_entries(self):
        manifest = _make_manifest(
            ["delta", "iceberg"],
            {"delta": _DELTA_ENTRIES, "iceberg": _ICEBERG_ENTRIES},
        )
        result = get_table_files(manifest, "orders", skip_directory_only_formats=True)
        assert result == _DELTA_ENTRIES

    def test_fallback_without_format_preference(self):
        manifest = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "tables": {
                "orders": {
                    "formats": {
                        "delta": _DELTA_ENTRIES,
                        "tbl": _TBL_ENTRIES,
                    }
                }
            },
        }
        result = get_table_files(manifest, "orders", skip_directory_only_formats=True)
        assert result == _TBL_ENTRIES

    def test_mixed_directory_and_file_entries_not_skipped(self):
        mixed_entries = [
            {"path": "orders_part1", "size_bytes": 4096, "row_count": 50, "is_directory": True},
            {"path": "orders_part2.parquet", "size_bytes": 2048, "row_count": 50},
        ]
        manifest = _make_manifest(
            ["delta", "tbl"],
            {"delta": mixed_entries, "tbl": _TBL_ENTRIES},
        )
        result = get_table_files(manifest, "orders", skip_directory_only_formats=True)
        assert result == mixed_entries


class TestGetTableFilesV1Manifest:
    def test_v1_manifest_returns_entries(self):
        manifest = {
            "benchmark": "tpcds",
            "scale_factor": 1.0,
            "tables": {
                "orders": [
                    {"path": "orders_1_10.dat.zst", "size_bytes": 1024, "row_count": 100},
                    {"path": "orders_2_10.dat.zst", "size_bytes": 2048, "row_count": 200},
                ],
            },
        }
        result = get_table_files(manifest, "orders")
        assert len(result) == 2
        assert result[0]["path"] == "orders_1_10.dat.zst"

    def test_v1_manifest_missing_table(self):
        manifest = {
            "tables": {
                "orders": [{"path": "orders.dat.zst", "size_bytes": 100, "row_count": 10}],
            },
        }
        assert get_table_files(manifest, "nonexistent") == []


