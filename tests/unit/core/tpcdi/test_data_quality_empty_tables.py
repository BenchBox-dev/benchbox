import re
import sqlite3

import pytest

from benchbox.core.tpcdi.etl.data_quality_monitor import DataQualityRule, DataQualityRuleEngine

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_empty_accuracy_and_validity_checks_report_zero_records() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.create_function("REGEXP", 2, lambda pattern, value: bool(re.search(pattern, value)))
        connection.execute("CREATE TABLE Sample (Value TEXT)")
        engine = DataQualityRuleEngine(connection, dialect="sqlite")
        rules = [
            DataQualityRule("allowed", "Allowed value", "ACCURACY", "Sample", "Value", expected_values=["A"]),
            DataQualityRule("present", "Present value", "ACCURACY", "Sample", "Value"),
            DataQualityRule(
                "pattern",
                "Valid pattern",
                "VALIDITY",
                "Sample",
                "Value",
                threshold_operator="REGEX",
                threshold_value="A",
            ),
        ]

        for rule in rules:
            engine.register_rule(rule)
            result = engine.execute_rule(rule.rule_id)
            assert result.error_message is None
            assert result.records_checked == 0
            assert result.records_failed == 0
            assert result.actual_value == 0.0
            assert result.failure_rate == 0.0


