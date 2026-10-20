# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import unittest
from unittest import mock

import pytest

from benchbox import TPCH

pytestmark = pytest.mark.medium


@pytest.mark.unit
class TestBenchBox(unittest.TestCase):
    def setUp(self) -> None:
        with mock.patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen") as mock_build:
            mock_build.return_value = "/tmp/dbgen"
            self.tpch = TPCH(scale_factor=0.01)

    @mock.patch("benchbox.core.tpch.benchmark.TPCHBenchmark.generate_data")
    def test_generate_data(self, mock_generate_data) -> None:
        mock_paths = ["/tmp/data/customer.tbl", "/tmp/data/lineitem.tbl"]
        mock_generate_data.return_value = mock_paths

        data_paths = self.tpch.generate_data()

        self.assertIsInstance(data_paths, list)
        self.assertTrue(all(isinstance(path, str) for path in data_paths))
        mock_generate_data.assert_called_once()

    def test_get_queries(self) -> None:
        queries = self.tpch.get_queries()
        self.assertIsInstance(queries, dict)
        self.assertGreater(len(queries), 0)

    def test_get_query(self) -> None:
        query1 = self.tpch.get_query(1)
        self.assertIsInstance(query1, str)
        self.assertIn("SELECT", query1.upper())

    def test_translate_query(self) -> None:
        query = "SELECT * FROM customer WHERE c_custkey = 1"

        with mock.patch.object(self.tpch, "get_query", return_value=query):
            translated_query = self.tpch.translate_query(1, dialect="postgres")

        self.assertEqual(translated_query, query)


if __name__ == "__main__":
    unittest.main()
