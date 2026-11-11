import pytest

from benchbox.utils.output_path import normalize_output_root

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_normalize_local_path():
    path = normalize_output_root("/data/benchbox", "tpch", 0.1)
    assert path.endswith("tpch_sf01")


def test_normalize_dbfs_volume_root():
    path = normalize_output_root("dbfs:/Volumes/workspace/raw/source/", "tpch", 0.01)
    assert path == "dbfs:/Volumes/workspace/raw/source/tpch_sf001"


def test_normalize_s3_root():
    path = normalize_output_root("s3://my-bucket/prefix", "tpcds", 1.0)
    assert path == "s3://my-bucket/prefix/tpcds_sf1"


def test_idempotent_when_suffix_present():
    path = normalize_output_root("s3://bucket/p/tpch_sf01", "tpch", 0.1)
    assert path == "s3://bucket/p/tpch_sf01"


