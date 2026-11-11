# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.write_primitives.catalog import ValidationQuery

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestValidationRangeLogic:
    def test_validation_query_with_exact_match(self):

        val_query = ValidationQuery(
            id="test",
            sql="SELECT * FROM test",
            expected_rows=5,
        )

        assert val_query.expected_rows == 5
        assert val_query.expected_rows_min is None
        assert val_query.expected_rows_max is None

    def test_validation_query_with_min_only(self):

        val_query = ValidationQuery(
            id="test",
            sql="SELECT * FROM test",
            expected_rows=None,
            expected_rows_min=1,
        )

        assert val_query.expected_rows is None
        assert val_query.expected_rows_min == 1
        assert val_query.expected_rows_max is None

    def test_validation_query_with_max_only(self):

        val_query = ValidationQuery(
            id="test",
            sql="SELECT * FROM test",
            expected_rows=None,
            expected_rows_max=100,
        )

        assert val_query.expected_rows is None
        assert val_query.expected_rows_min is None
        assert val_query.expected_rows_max == 100

    def test_validation_query_with_min_and_max(self):

        val_query = ValidationQuery(
            id="test",
            sql="SELECT * FROM test",
            expected_rows=None,
            expected_rows_min=1,
            expected_rows_max=100,
        )

        assert val_query.expected_rows is None
        assert val_query.expected_rows_min == 1
        assert val_query.expected_rows_max == 100

    def test_validation_query_no_expectations(self):

        val_query = ValidationQuery(
            id="test",
            sql="SELECT * FROM test",
        )

        assert val_query.expected_rows is None
        assert val_query.expected_rows_min is None
        assert val_query.expected_rows_max is None

    def test_range_validation_logic_in_range(self):

        actual_rows = 50
        expected_rows_min = 1
        expected_rows_max = 100

        min_val = expected_rows_min if expected_rows_min is not None else 0
        max_val = expected_rows_max if expected_rows_max is not None else float("inf")
        passed = min_val <= actual_rows <= max_val

        assert passed is True

    def test_range_validation_logic_below_min(self):

        actual_rows = 0
        expected_rows_min = 1
        expected_rows_max = 100

        min_val = expected_rows_min if expected_rows_min is not None else 0
        max_val = expected_rows_max if expected_rows_max is not None else float("inf")
        passed = min_val <= actual_rows <= max_val

        assert passed is False

    def test_range_validation_logic_above_max(self):

        actual_rows = 101
        expected_rows_min = 1
        expected_rows_max = 100

        min_val = expected_rows_min if expected_rows_min is not None else 0
        max_val = expected_rows_max if expected_rows_max is not None else float("inf")
        passed = min_val <= actual_rows <= max_val

        assert passed is False

    def test_range_validation_logic_min_only(self):

        actual_rows = 1000
        expected_rows_min = 1
        expected_rows_max = None

        min_val = expected_rows_min if expected_rows_min is not None else 0
        max_val = expected_rows_max if expected_rows_max is not None else float("inf")
        passed = min_val <= actual_rows <= max_val

        assert passed is True

    def test_range_validation_logic_max_only(self):

        actual_rows = 5
        expected_rows_min = None
        expected_rows_max = 10

        min_val = expected_rows_min if expected_rows_min is not None else 0
        max_val = expected_rows_max if expected_rows_max is not None else float("inf")
        passed = min_val <= actual_rows <= max_val

        assert passed is True

    def test_range_validation_logic_at_boundaries(self):

        actual_rows = 1
        expected_rows_min = 1
        expected_rows_max = 10

        min_val = expected_rows_min if expected_rows_min is not None else 0
        max_val = expected_rows_max if expected_rows_max is not None else float("inf")
        passed = min_val <= actual_rows <= max_val

        assert passed is True

        actual_rows = 10
        passed = min_val <= actual_rows <= max_val

        assert passed is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


