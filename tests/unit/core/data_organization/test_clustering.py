from __future__ import annotations

import pyarrow as pa
import pytest

from benchbox.core.data_organization.clustering import (
    HilbertClusterer,
    ZOrderClusterer,
    hilbert_index_2d,
    z_order_key,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestZOrderKey:
    def test_interleaves_two_dimensions(self):
        assert z_order_key([0, 0], bits=2) == 0
        assert z_order_key([1, 0], bits=2) == 1
        assert z_order_key([0, 1], bits=2) == 2
        assert z_order_key([1, 1], bits=2) == 3

    def test_rejects_invalid_bits(self):
        with pytest.raises(ValueError, match="bits must be positive"):
            z_order_key([1, 2], bits=0)


class TestZOrderClusterer:
    def test_clusters_table_by_z_order_key(self):
        table = pa.table(
            {
                "x": pa.array([1, 0, 1, 0], type=pa.int32()),
                "y": pa.array([1, 1, 0, 0], type=pa.int32()),
                "payload": pa.array(["d", "c", "b", "a"]),
            }
        )
        clusterer = ZOrderClusterer(bits=2)

        clustered = clusterer.cluster_table(table, ["x", "y"])

        assert clustered["x"].to_pylist() == [0, 1, 0, 1]
        assert clustered["y"].to_pylist() == [0, 0, 1, 1]
        assert clustered["payload"].to_pylist() == ["a", "b", "c", "d"]

    def test_missing_cluster_column_raises(self):
        table = pa.table({"x": pa.array([1], type=pa.int32())})
        clusterer = ZOrderClusterer(bits=2)

        with pytest.raises(ValueError, match="Cluster columns not found"):
            clusterer.cluster_table(table, ["x", "y"])

    def test_non_integer_column_raises(self):
        table = pa.table({"x": pa.array(["a", "b"])})
        clusterer = ZOrderClusterer(bits=2)

        with pytest.raises(TypeError, match="integer/boolean columns only"):
            clusterer.cluster_table(table, ["x"])


class TestHilbertIndex2D:
    def test_known_2x2_order(self):
        assert hilbert_index_2d(0, 0, bits=1) == 0
        assert hilbert_index_2d(0, 1, bits=1) == 1
        assert hilbert_index_2d(1, 1, bits=1) == 2
        assert hilbert_index_2d(1, 0, bits=1) == 3

    def test_rejects_out_of_range_coordinate(self):
        with pytest.raises(ValueError, match="must be in"):
            hilbert_index_2d(4, 0, bits=2)


class TestHilbertClusterer:
    def test_clusters_table_by_hilbert_key(self):
        table = pa.table(
            {
                "x": pa.array([1, 0, 1, 0], type=pa.int32()),
                "y": pa.array([0, 1, 1, 0], type=pa.int32()),
                "payload": pa.array(["d", "b", "c", "a"]),
            }
        )
        clusterer = HilbertClusterer(bits=1)

        clustered = clusterer.cluster_table(table, ["x", "y"])

        assert clustered["x"].to_pylist() == [0, 0, 1, 1]
        assert clustered["y"].to_pylist() == [0, 1, 1, 0]
        assert clustered["payload"].to_pylist() == ["a", "b", "c", "d"]

    def test_requires_exactly_two_cluster_columns(self):
        table = pa.table({"x": pa.array([1], type=pa.int32())})
        clusterer = HilbertClusterer(bits=4)
        with pytest.raises(ValueError, match="exactly 2 cluster columns"):
            clusterer.cluster_table(table, ["x"])


