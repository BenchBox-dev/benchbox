# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_adapter(**kwargs):

    with (
        patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.bigquery.bigquery"),
    ):
        from benchbox.platforms.bigquery import BigQueryAdapter

        defaults = {
            "project_id": "test-project",
            "dataset_id": "test_dataset",
        }
        defaults.update(kwargs)
        return BigQueryAdapter(**defaults)


class TestBigQueryConvertToBigQueryTable:
    def test_three_part_name_in_output(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._convert_to_bigquery_table("CREATE TABLE orders (id INT64)")
        assert "my-proj.my_dataset.ORDERS" in result

    def test_partition_by_added_when_configured(self):
        adapter = _make_adapter(partitioning_field="order_date")
        result = adapter._convert_to_bigquery_table("CREATE TABLE orders (id INT64)")
        assert "PARTITION BY" in result
        assert "order_date" in result

    def test_cluster_by_added_when_configured(self):
        adapter = _make_adapter(clustering_fields=["customer_id", "order_id"])
        result = adapter._convert_to_bigquery_table("CREATE TABLE orders (id INT64)")
        assert "CLUSTER BY" in result
        assert "customer_id" in result

    def test_no_partition_when_not_configured(self):
        adapter = _make_adapter()
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64)")
        assert "PARTITION BY" not in result

    def test_no_cluster_when_not_configured(self):
        adapter = _make_adapter()
        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64)")
        assert "CLUSTER BY" not in result

    def test_lowercase_table_normalized_to_uppercase(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._convert_to_bigquery_table("CREATE TABLE hits (WatchID INT64)")
        assert result == "CREATE OR REPLACE TABLE `my-proj.my_dataset.HITS` (WatchID INT64)"


class TestBigQueryQualifyTableNames:
    def test_parser_qualifies_non_tpch_tables(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._qualify_table_names("SELECT COUNT(*) FROM hits")
        assert result == "SELECT COUNT(*) FROM `my-proj.my_dataset.HITS`"

    def test_cte_names_are_not_qualified(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._qualify_table_names("WITH recent AS (SELECT * FROM hits) SELECT COUNT(*) FROM recent")
        assert "`my-proj.my_dataset.HITS`" in result
        assert "FROM recent" in result

    def test_unparseable_query_falls_back_to_tpch_list(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        with patch("benchbox._vendor.sqlglot.parse_one", side_effect=ValueError("no parse")):
            result = adapter._qualify_table_names("SELECT * FROM lineitem")
        assert result == "SELECT * FROM `my-proj.my_dataset.LINEITEM`"

    def test_string_literal_matching_table_is_not_qualified(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._qualify_table_names("SELECT * FROM hits WHERE note = 'hits'")
        assert result == "SELECT * FROM `my-proj.my_dataset.HITS` WHERE note = 'hits'"

    def test_select_column_matching_table_is_not_qualified(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._qualify_table_names("SELECT orders FROM orders")
        assert result == "SELECT orders FROM `my-proj.my_dataset.ORDERS`"

    def test_comma_separated_from_items_are_qualified(self):

        adapter = _make_adapter(project_id="my-proj", dataset_id="my_dataset")
        result = adapter._qualify_table_names("SELECT * FROM customer, orders")
        assert result == "SELECT * FROM `my-proj.my_dataset.CUSTOMER`, `my-proj.my_dataset.ORDERS`"


class TestBigQueryGenerateTuningClause:
    def test_none_returns_empty(self):
        adapter = _make_adapter()
        assert adapter.generate_tuning_clause(None) == ""

    def test_no_tuning_returns_empty(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False
        assert adapter.generate_tuning_clause(mock_tuning) == ""

    def test_partition_col_returns_partition_by(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        col = Mock()
        col.name = "order_date"
        col.type = "DATE"
        col.order = 1

        def _cols(t):
            if t == TuningType.PARTITIONING:
                return [col]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "PARTITION BY" in clause
        assert "order_date" in clause

    def test_cluster_col_returns_cluster_by(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        col = Mock()
        col.name = "customer_id"
        col.order = 1

        def _cols(t):
            if t == TuningType.CLUSTERING:
                return [col]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "CLUSTER BY customer_id" in clause


class TestBigQueryGetTargetDialect:
    def test_returns_bigquery(self):
        adapter = _make_adapter()
        assert adapter.get_target_dialect() == "bigquery"


class TestBigQueryPlatformName:
    def test_platform_name_is_bigquery(self):
        adapter = _make_adapter()
        assert adapter.platform_name.lower() == "bigquery"


class TestBigQueryTypeMapping:
    def test_adapter_accepts_int64_in_schema(self):

        adapter = _make_adapter()

        result = adapter._convert_to_bigquery_table("CREATE TABLE t (id INT64, score FLOAT64)")
        assert "INT64" in result
        assert "FLOAT64" in result
