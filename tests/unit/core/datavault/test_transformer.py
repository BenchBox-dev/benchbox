from datetime import datetime
from unittest.mock import MagicMock

import pytest

from benchbox.core.datavault.etl.transformer import DataVaultETLTransformer

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataVaultTransformer:
    def test_required_sources_subset(self):
        transformer = DataVaultETLTransformer()

        required = transformer._get_required_sources(["sat_customer", "hub_region"])

        assert required == {"customer", "region"}

    def test_link_lineitem_hash_key_alias(self):
        transformer = DataVaultETLTransformer()
        sql = transformer._get_link_sql("link_lineitem", "2025-01-01 00:00:00")

        assert "AS hk_lineitem_link" in sql

        assert "md5(CAST(l_orderkey AS VARCHAR) || '|' || CAST(l_linenumber AS VARCHAR) || '|' ||" in sql

    def test_generate_dv_table_respects_output_format(self, tmp_path):
        transformer = DataVaultETLTransformer()
        mock_conn = MagicMock()
        from datetime import datetime

        output_path = transformer._generate_dv_table(
            mock_conn,
            table_name="hub_region",
            load_dts=datetime(2025, 1, 1, 0, 0, 0),
            output_dir=tmp_path,
            output_format="csv",
        )

        copy_call = mock_conn.execute.call_args_list[-1][0][0]
        assert "DELIMITER ','" in copy_call
        assert output_path.suffix == ".csv"

    def test_write_manifest(self, tmp_path):
        transformer = DataVaultETLTransformer()

        table_path = tmp_path / "hub_region.tbl"
        table_path.write_text("a\nb\n")

        transformer._write_manifest(
            output_dir=tmp_path,
            table_paths={"hub_region": table_path},
            table_row_counts={"hub_region": 2},
            output_format="tbl",
            load_timestamp=datetime(2025, 1, 1, 0, 0, 0),
        )

        manifest_path = tmp_path / "_datagen_manifest.json"
        assert manifest_path.exists()

        import json

        manifest = json.loads(manifest_path.read_text())
        assert manifest["benchmark"] == "datavault"
        entry = manifest["tables"]["hub_region"]["formats"]["tbl"][0]
        assert entry["path"] == table_path.name
        assert entry["row_count"] == 2
        assert entry["size_bytes"] == table_path.stat().st_size

    def test_end_to_end_transform_minimal_tpch(self, tmp_path):
        tpch_dir = tmp_path / "tpch"
        output_dir = tmp_path / "out"
        tpch_dir.mkdir()

        def write_tbl(name: str, rows: list[list[str]]):
            path = tpch_dir / f"{name}.tbl"
            with path.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write("|".join(str(v) for v in row) + "|\n")

        write_tbl("region", [[0, "AFRICA", "comment"]])
        write_tbl("nation", [[0, "ALGERIA", 0, "comment"]])
        write_tbl("customer", [[1, "Customer#1", "Address", 0, "11-111-1111", 1000.0, "BUILDING", "comment"]])
        write_tbl("supplier", [[1, "Supplier#1", "Address", 0, "11-111-1111", 500.0, "comment"]])
        write_tbl(
            "part",
            [[1, "Part#1", "MFGR#1", "Brand#1", "ECONOMY ANODIZED STEEL", 1, "SM BOX", 10.0, "comment"]],
        )
        write_tbl("partsupp", [[1, 1, 10, 100.0, "ps comment"]])
        write_tbl(
            "orders",
            [[1, 1, "O", 100.0, "1992-01-01", "1-URGENT", "Clerk#1", 0, "order comment"]],
        )
        write_tbl(
            "lineitem",
            [
                [
                    1,
                    1,
                    1,
                    1,
                    1.0,
                    100.0,
                    0.0,
                    0.0,
                    "N",
                    "O",
                    "1992-02-02",
                    "1992-02-01",
                    "1992-02-03",
                    "DELIVER IN PERSON",
                    "AIR",
                    "l comment",
                ]
            ],
        )

        transformer = DataVaultETLTransformer(scale_factor=0.01)
        outputs = transformer.transform(tpch_dir=tpch_dir, output_dir=output_dir)

        assert len(outputs) == 21
        assert (output_dir / "_datagen_manifest.json").exists()

        import json

        manifest = json.loads((output_dir / "_datagen_manifest.json").read_text())
        assert manifest["version"] == 2

        for table_name in ("hub_customer", "link_lineitem", "sat_lineitem"):
            entries = manifest["tables"][table_name]["formats"]["tbl"]
            assert entries, f"{table_name} missing manifest entries"


class TestDataVaultTransformerCompression:
    @pytest.mark.parametrize("suffix", ["", ".gz", ".zst"])
    @pytest.mark.parametrize("sharded", [False, True])
    def test_source_read_parallelism_and_identity(self, tmp_path, suffix, sharded):
        import gzip

        import duckdb
        import zstandard

        rows = [b"0|AFRICA|comment|\n", b"1|ASIA|other comment|\n"]
        shards = rows if sharded else [b"".join(rows)]
        for index, data in enumerate(shards, 1):
            if suffix == ".gz":
                data = gzip.compress(data)
            elif suffix == ".zst":
                data = zstandard.ZstdCompressor().compress(data)
            name = f"region.tbl.{index}" if sharded else "region.tbl"
            (tmp_path / f"{name}{suffix}").write_bytes(data)

        transformer = DataVaultETLTransformer()
        mock_conn = MagicMock()
        transformer._load_tpch_tables(mock_conn, tmp_path, tables=["hub_region"])
        sql = mock_conn.execute.call_args.args[0]
        assert f"parallel={'false' if suffix else 'true'}" in sql
        with duckdb.connect() as conn:
            threads = conn.execute("SELECT current_setting('threads')").fetchone()
            transformer._load_tpch_tables(conn, tmp_path, tables=["hub_region"])
            assert conn.execute("SELECT * FROM region ORDER BY r_regionkey").fetchall() == [
                (0, "AFRICA", "comment", None),
                (1, "ASIA", "other comment", None),
            ]
            assert conn.execute(
                "SELECT md5(CAST(r_regionkey AS VARCHAR)) FROM region ORDER BY r_regionkey"
            ).fetchall() == [
                ("cfcd208495d565ef66e7dff9f98764da",),
                ("c4ca4238a0b923820dcc509a6f75849b",),
            ]
            assert conn.execute("SELECT current_setting('threads')").fetchone() == threads

    def test_invalid_compressed_source_is_rejected(self, tmp_path):
        import duckdb

        (tmp_path / "region.tbl.gz").write_bytes(b"not a gzip stream")
        with duckdb.connect() as conn, pytest.raises(duckdb.Error):
            DataVaultETLTransformer()._load_tpch_tables(conn, tmp_path, tables=["hub_region"])

    def test_compression_mixin_inherited(self):
        from benchbox.utils.compression_mixin import CompressionMixin

        assert issubclass(DataVaultETLTransformer, CompressionMixin)

    def test_compression_defaults_to_disabled(self):
        transformer = DataVaultETLTransformer()
        assert not transformer.should_use_compression()

    def test_compression_enabled_with_flag(self):
        transformer = DataVaultETLTransformer(compress_data=True)
        assert transformer.should_use_compression()

        assert transformer.compression_type == "zstd"

    def test_compression_type_configurable(self):
        transformer = DataVaultETLTransformer(
            compress_data=True,
            compression_type="gzip",
        )
        assert transformer.compression_type == "gzip"

    def test_manifest_includes_compression_metadata(self, tmp_path):
        transformer = DataVaultETLTransformer(compress_data=True, compression_type="zstd")

        table_path = tmp_path / "hub_region.tbl"
        table_path.write_text("test\n")

        transformer._write_manifest(
            output_dir=tmp_path,
            table_paths={"hub_region": table_path},
            table_row_counts={"hub_region": 1},
            output_format="tbl",
            load_timestamp=datetime(2025, 1, 1),
        )

        import json

        manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
        assert manifest["compression"]["enabled"] is True
        assert manifest["compression"]["type"] == "zstd"


