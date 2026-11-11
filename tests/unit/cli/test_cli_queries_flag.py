# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueriesFlagBasic:
    def test_queries_flag_parsing_single(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1", "--check-platforms"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "❌" not in result.output or "query" not in result.output.lower()

    def test_queries_flag_parsing_multiple(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1,6,17", "--check-platforms"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "❌" not in result.output or "query" not in result.output.lower()

    def test_queries_flag_preserves_order(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "17,6,1", "--check-platforms"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "Invalid query" not in result.output


class TestQueriesFlagEdgeCases:
    def test_queries_flag_empty_string(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", ""],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "no valid query IDs found" in result.output

    def test_queries_flag_whitespace_handling(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "  1  , 6  , 17  ", "--check-platforms"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "Invalid query ID format" not in result.output

    def test_queries_flag_trailing_comma(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1,2,3,", "--check-platforms"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "Invalid query" not in result.output

    def test_queries_flag_double_comma(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1,,2", "--check-platforms"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "Invalid query" not in result.output


class TestQueriesFlagValidation:
    def test_queries_too_many(self):
        runner = CliRunner()
        many_queries = ",".join(str(i) for i in range(1, 102))
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", many_queries],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "Too many queries" in result.output
        assert "max 100" in result.output

    def test_query_id_too_long(self):

        from benchbox.utils.input_validation import MAX_QUERY_ID_LENGTH

        runner = CliRunner()
        long_id = "a" * (MAX_QUERY_ID_LENGTH + 1)
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", long_id],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "Query ID too long" in result.output
        assert f"max {MAX_QUERY_ID_LENGTH} chars" in result.output

    def test_invalid_format_special_chars(self):

        runner = CliRunner()
        invalid_cases = [
            "1;DROP TABLE",
            "1 OR 1=1",
            "../../../etc/passwd",
            "$(whoami)",
            "1%s%s%s",
        ]

        for invalid_query in invalid_cases:
            result = runner.invoke(
                run,
                ["--platform", "duckdb", "--benchmark", "tpch", "--queries", invalid_query],
                obj={"config": ConfigManager()},
                catch_exceptions=False,
            )
            assert result.exit_code != 0, f"Should reject: {invalid_query}"
            assert "Invalid query ID format" in result.output, f"Should show format error for: {invalid_query}"

    def test_valid_format_alphanumeric(self):

        runner = CliRunner()
        valid_cases = [
            "1",
            "22",
            "query1",
            "Q1",
            "q1-variant",
            "q1_v2",
            "Q1.1",
            "Q3.4",
        ]

        for valid_query in valid_cases:
            result = runner.invoke(
                run,
                ["--platform", "duckdb", "--benchmark", "tpch", "--queries", valid_query, "--check-platforms"],
                obj={"config": ConfigManager()},
                catch_exceptions=False,
            )
            assert "Invalid query ID format" not in result.output, f"Should accept: {valid_query}"


class TestQueriesFlagPhaseInteraction:
    def test_queries_with_power_phase(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--queries",
                "1,6",
                "--phases",
                "power",
                "--check-platforms",
            ],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "only works with power/standard" not in result.output

    def test_queries_with_warmup_only_errors(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1,6", "--phases", "warmup"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "--queries only works with power/standard phases" in result.output

    def test_queries_with_mixed_phases_warns(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--queries",
                "1,6",
                "--phases",
                "power,warmup",
                "--validation",
                "full",
            ],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert "⚠" in result.output
        assert "ignored for" in result.output.lower()

    def test_queries_with_throughput_only_errors(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1,6", "--phases", "throughput"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "--queries only works with power/standard phases" in result.output

    def test_queries_with_maintenance_only_errors(self):

        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--queries", "1,6", "--phases", "maintenance"],
            obj={"config": ConfigManager()},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "--queries only works with power/standard phases" in result.output


@pytest.mark.integration
class TestQueriesFlagHelp:
    def test_help_text_includes_constraints(self):

        runner = CliRunner()
        result = runner.invoke(run, ["--help"])
        assert result.exit_code == 0
        assert "--queries" in result.output
        assert "power" in result.output.lower() or "standard" in result.output.lower()


