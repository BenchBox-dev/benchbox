# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import unittest
from argparse import Namespace

import pytest

from benchbox.core.config_utils import merge_all_configs

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestVerboseConfig(unittest.TestCase):
    def test_no_verbosity_flag(self):

        args = Namespace(verbose=0)
        config = merge_all_configs("duckdb", "tpch", args)
        self.assertFalse(config["verbose_enabled"])
        self.assertFalse(config["very_verbose"])

    def test_v_flag(self):

        args = Namespace(verbose=1)
        config = merge_all_configs("duckdb", "tpch", args)
        self.assertTrue(config["verbose_enabled"])
        self.assertFalse(config["very_verbose"])

    def test_vv_flag(self):

        args = Namespace(verbose=2)
        config = merge_all_configs("duckdb", "tpch", args)
        self.assertTrue(config["verbose_enabled"])
        self.assertTrue(config["very_verbose"])

    def test_vvv_flag(self):

        args_3 = Namespace(verbose=3)
        config_3 = merge_all_configs("duckdb", "tpch", args_3)
        self.assertTrue(config_3["verbose_enabled"])
        self.assertTrue(config_3["very_verbose"])

        args_5 = Namespace(verbose=5)
        config_5 = merge_all_configs("duckdb", "tpch", args_5)
        self.assertTrue(config_5["verbose_enabled"])
        self.assertTrue(config_5["very_verbose"])


if __name__ == "__main__":
    unittest.main()


