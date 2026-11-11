from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.cloud_import,
]


def _parse_snowflake_cli_args(argv: list[str]) -> dict[str, object]:

    script = """
import argparse
import json
from benchbox.platforms.snowflake import SnowflakeAdapter

parser = argparse.ArgumentParser()
SnowflakeAdapter.add_cli_arguments(parser)
print(json.dumps(vars(parser.parse_args(ARGV))))
"""

    result = subprocess.run(
        [sys.executable, "-c", script.replace("ARGV", repr(argv))],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestSnowflakeAddCliArguments:
    def test_adds_snowflake_arguments(self):

        args = _parse_snowflake_cli_args([])

        assert args["warehouse"] == "COMPUTE_WH"
        assert args["schema"] == "PUBLIC"
        assert args["authenticator"] == "snowflake"
        assert args["modify_warehouse_settings"] is False
        assert args["suppress_nondeterministic_errors"] is False
        assert args["disable_result_cache"] is True

    def test_cli_no_disable_result_cache_flag(self):

        args = _parse_snowflake_cli_args(["--no-disable-result-cache"])
        assert args["disable_result_cache"] is False

    def test_cli_modify_warehouse_settings_flag(self):

        args = _parse_snowflake_cli_args(["--modify-warehouse-settings"])
        assert args["modify_warehouse_settings"] is True


