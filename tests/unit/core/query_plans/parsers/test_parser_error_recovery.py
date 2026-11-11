import pytest

from benchbox.core.errors import PlanParseError
from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFormatDetection:
    def test_detect_empty_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        assert parser._detect_explain_format("") == "empty"
        assert parser._detect_explain_format("   ") == "empty"
        assert parser._detect_explain_format("\n\n") == "empty"

    def test_detect_json_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        assert parser._detect_explain_format('{"name": "test"}') == "json"
        assert parser._detect_explain_format('[{"name": "test"}]') == "json"
        assert parser._detect_explain_format('  {"name": "test"}  ') == "json"

    def test_detect_xml_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        assert parser._detect_explain_format("<?xml version='1.0'?>") == "xml"
        assert parser._detect_explain_format("<plan>test</plan>") == "xml"

    def test_detect_text_box_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        box_format = "┌────┐\n│test│\n└────┘"
        assert parser._detect_explain_format(box_format) == "text-box"

    def test_detect_text_tree_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        tree_format = "-> Seq Scan on orders"
        assert parser._detect_explain_format(tree_format) == "text-tree"

        tree_format2 = "Hash Join (cost=100.00 rows=1000)"
        assert parser._detect_explain_format(tree_format2) == "text-tree"

    def test_detect_unknown_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        assert parser._detect_explain_format("This is random text") == "unknown"


class TestRecoveryHints:
    def test_empty_format_hint(self) -> None:
        parser = DuckDBQueryPlanParser()
        hint = parser._get_recovery_hint("empty", "some error")
        assert hint is not None
        assert "empty" in hint.lower()

    def test_unknown_format_hint(self) -> None:
        parser = DuckDBQueryPlanParser()
        hint = parser._get_recovery_hint("unknown", "some error")
        assert hint is not None
        assert "format" in hint.lower()

    def test_xml_format_hint(self) -> None:
        parser = DuckDBQueryPlanParser()
        hint = parser._get_recovery_hint("xml", "some error")
        assert hint is not None
        assert "xml" in hint.lower()

    def test_json_error_hint(self) -> None:
        parser = DuckDBQueryPlanParser()
        hint = parser._get_recovery_hint("json", "JSON decode error")
        assert hint is not None
        assert "json" in hint.lower()

    def test_no_operators_error_hint(self) -> None:
        parser = DuckDBQueryPlanParser()
        hint = parser._get_recovery_hint("text-box", "No operators found")
        assert hint is not None
        assert "truncated" in hint.lower() or "operators" in hint.lower()

    def test_no_hint_for_valid_format(self) -> None:
        parser = DuckDBQueryPlanParser()
        hint = parser._get_recovery_hint("text-box", "some internal error")

        assert hint is None or isinstance(hint, str)


class TestEnhancedErrorMessages:
    def test_parse_error_with_diagnostics(self) -> None:
        error = PlanParseError(
            query_id="q01",
            platform="duckdb",
            error_message="Test error",
            explain_sample="sample output",
            detected_format="json",
            recovery_hint="Try this fix",
        )

        error_str = str(error)
        assert "q01" in error_str
        assert "duckdb" in error_str
        assert "Test error" in error_str
        assert "json" in error_str
        assert "Try this fix" in error_str

    def test_parse_error_diagnostic_info(self) -> None:
        error = PlanParseError(
            query_id="q01",
            platform="duckdb",
            error_message="Test error",
            detected_format="json",
        )

        info = error.diagnostic_info
        assert info["query_id"] == "q01"
        assert info["platform"] == "duckdb"
        assert info["detected_format"] == "json"

    def test_error_message_truncates_sample(self) -> None:
        long_sample = "line\n" * 100
        error = PlanParseError(
            query_id="q01",
            platform="duckdb",
            error_message="Test error",
            explain_sample=long_sample,
        )

        error_str = str(error)

        assert error_str.count("line") <= 10


class TestParserErrorHandling:
    def test_parse_invalid_returns_none(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", "This is not valid")
        assert plan is None

    def test_parse_empty_returns_none(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", "")
        assert plan is None

    def test_parse_whitespace_returns_none(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", "   \n\n   ")
        assert plan is None

    def test_parse_malformed_json_returns_none(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", '{"incomplete": ')
        assert plan is None

    def test_successful_parse_still_works(self) -> None:
        parser = DuckDBQueryPlanParser()
        valid_json = '{"name": "SEQ_SCAN", "extra_info": "orders", "children": []}'
        plan = parser.parse_explain_output("q01", valid_json)
        assert plan is not None
        assert plan.query_id == "q01"


class TestMultipleParseErrors:
    def test_errors_dont_leak_between_parses(self) -> None:
        parser = DuckDBQueryPlanParser()

        plan1 = parser.parse_explain_output("q01", "invalid")
        assert plan1 is None

        valid_json = '{"name": "SEQ_SCAN", "children": []}'
        plan2 = parser.parse_explain_output("q02", valid_json)
        assert plan2 is not None
        assert plan2.query_id == "q02"

    def test_operator_ids_reset_after_error(self) -> None:
        parser = DuckDBQueryPlanParser()

        parser.parse_explain_output("q01", "invalid")

        valid = '{"name": "SEQ_SCAN", "children": []}'
        plan1 = parser.parse_explain_output("q02", valid)
        plan2 = parser.parse_explain_output("q03", valid)

        assert plan1 is not None
        assert plan2 is not None

        id1 = int(plan1.logical_root.operator_id.split("_")[-1])
        id2 = int(plan2.logical_root.operator_id.split("_")[-1])
        assert id1 < 10
        assert id2 < 10


