import duckdb
import pytest
import zstandard

from benchbox.core.datavault.etl.transformer import DataVaultETLTransformer

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_compressed_shards_under_memory_pressure(tmp_path):

    for shard in (1, 2):
        data = "".join(
            f"{i}|REGION{i}|{'comment' * 30}|\n" for i in range((shard - 1) * 2_200_000, shard * 2_200_000)
        ).encode()
        (tmp_path / f"region.tbl.{shard}").write_bytes(data)
        (tmp_path / f"region.tbl.{shard}.zst").write_bytes(zstandard.ZstdCompressor().compress(data))

    identity_sql = "SELECT count(*), sum(r_regionkey), bit_xor(hash(r_regionkey, r_name, r_comment)) FROM region"
    with duckdb.connect(config={"memory_limit": "512MB", "threads": 10}) as conn:
        conn.execute(
            f"CREATE TABLE region AS SELECT * FROM read_csv('{tmp_path}/region.tbl.[12]', "
            "delim='|', header=false, names=['r_regionkey', 'r_name', 'r_comment'])"
        )
        expected = conn.execute(identity_sql).fetchone()
    with duckdb.connect(config={"memory_limit": "512MB", "threads": 10}) as conn:
        DataVaultETLTransformer()._load_tpch_tables(conn, tmp_path, tables=["hub_region"])
        assert conn.execute(identity_sql).fetchone() == expected
        assert expected[:2] == (4_400_000, 9_679_997_800_000)


