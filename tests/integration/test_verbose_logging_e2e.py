# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import unittest

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


class TestVerboseLoggingE2E(unittest.TestCase):
    def _run_benchbox_command(self, args):

        return run_cli_command(args)

    def test_e2e_no_verbosity(self):

        args = ["run", "--platform", "sqlite", "--benchmark", "ssb", "--scale", "0.001"]
        result = self._run_benchbox_command(args)

        self.assertNotIn("INFO -", result.stderr)
        self.assertNotIn("DEBUG -", result.stderr)
        self.assertNotIn("Starting", result.stderr)
        self.assertNotIn("completed in", result.stderr)

    def test_e2e_level_one_verbosity(self):

        args = ["run", "-v", "--platform", "sqlite", "--benchmark", "ssb", "--scale", "0.001"]
        result = self._run_benchbox_command(args)

        self.assertIn("INFO - Verbose logging enabled", result.stderr)
        self.assertIn("INFO - Starting direct benchmark execution", result.stderr)
        self.assertNotIn("DEBUG", result.stderr)

    def test_e2e_level_two_verbosity(self):

        args = ["run", "-vv", "--platform", "sqlite", "--benchmark", "ssb", "--scale", "0.001"]
        result = self._run_benchbox_command(args)

        self.assertIn("DEBUG - Very verbose logging enabled", result.stderr)
        self.assertIn("DEBUG - Starting BenchBox CLI run command", result.stderr)
        self.assertIn("INFO - Starting direct benchmark execution", result.stderr)


if __name__ == "__main__":
    unittest.main()


