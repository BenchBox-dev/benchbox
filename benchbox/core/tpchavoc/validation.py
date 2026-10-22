# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

import datetime
import hashlib
import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Optional, Union


def _escape_cell_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n")


def _render_cell(value: Any) -> str:
    if value is None:
        return "z:"
    if isinstance(value, str):
        return f"s:{_escape_cell_text(value)}"
    if isinstance(value, bool):
        return f"b:{value}"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, float):
        return f"f:{value!r}"
    if isinstance(value, Decimal):
        return f"d:{value}"
    if isinstance(value, (datetime.date, datetime.time)):
        return f"t:{_escape_cell_text(str(value))}"
    type_name = _escape_cell_text(type(value).__name__).replace(":", "\\:")
    return f"o:{type_name}:{_escape_cell_text(str(value))}"


def calculate_checksum(results: list[tuple[Any, ...]]) -> str:
    result_str = ""
    for row in sorted(results, key=_row_sort_key):
        row_str = "|".join(_render_cell(val) for val in row)
        result_str += row_str + "\n"

    return hashlib.md5(result_str.encode("utf-8")).hexdigest()


def _cell_sort_key(value: Any) -> tuple[Any, ...]:
    if value is None:
        return (0, "", 0.0)
    if isinstance(value, (int, float)):
        return (1, "num", float(value))
    return (1, type(value).__name__, str(value))


def _row_sort_key(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(_cell_sort_key(value) for value in row)


_PLAIN_CELL_TYPES = frozenset({str, bytes, bool, type(None), datetime.date, datetime.datetime, datetime.time})
_MAX_EXACT_INT = 1 << 53


def _cell_is_exact_safe(value: Any) -> bool:
    cell_type = type(value)
    if cell_type in _PLAIN_CELL_TYPES:
        return True
    if cell_type is int:
        if -_MAX_EXACT_INT <= value <= _MAX_EXACT_INT:
            return True
        try:
            float(value)
        except OverflowError:
            return False
        return True
    if cell_type is float:
        return value == value
    if cell_type is Decimal:
        return value.is_finite()
    if isinstance(value, (list, tuple)):
        return all(_cell_is_exact_safe(item) for item in value)
    if isinstance(value, dict):
        return all(_cell_is_exact_safe(item) for item in value.values())
    if isinstance(value, int):
        return _cell_is_exact_safe(int(value))
    try:
        return bool(value == value)
    except (TypeError, ValueError):
        return False


def _rows_are_exact_safe(rows: list[tuple[Any, ...]]) -> bool:
    return all(_cell_is_exact_safe(value) for row in rows for value in row)


class ValidationError(Exception):
    pass


class ResultValidator:
    def __init__(
        self,
        tolerance: float = 1e-10,
        *,
        treat_nan_as_null: bool = False,
        strip_strings: bool = False,
    ) -> None:
        self.tolerance = tolerance
        self.treat_nan_as_null = treat_nan_as_null
        self.strip_strings = strip_strings

    def validate_results_exact(
        self,
        original_results: list[tuple[Any, ...]],
        variant_results: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
        *,
        tie_aware: bool = False,
        order_aware: bool = False,
        order_by: Sequence[int] | None = None,
        final_key_tied_beyond_limit: bool = False,
    ) -> bool:
        if len(original_results) != len(variant_results):
            raise ValidationError(
                f"Q{query_id}.{variant_id}: Row count mismatch. "
                f"Original: {len(original_results)}, Variant: {len(variant_results)}"
            )

        if self._exact_fast_path_accepts(original_results, variant_results, ordered=bool(order_aware and order_by)):
            return True

        if order_aware and order_by and self._order_key_in_range(order_by, original_results, variant_results):
            return self._validate_order_aware(
                original_results,
                variant_results,
                query_id,
                variant_id,
                order_by,
                tie_aware=tie_aware,
                final_key_tied_beyond_limit=final_key_tied_beyond_limit,
            )

        original_sorted = sorted(original_results, key=self._pairing_sort_key)
        variant_sorted = sorted(variant_results, key=self._pairing_sort_key)

        detail = self._first_positional_mismatch(original_sorted, variant_sorted, query_id, variant_id)
        if detail is None:
            return True

        paired_variant = self._pair_variant_rows(original_sorted, variant_sorted)
        if paired_variant is not None:
            detail = self._first_positional_mismatch(original_sorted, paired_variant, query_id, variant_id)
            if detail is None:
                return True

        if tie_aware and self._is_boundary_tie_equivalent(original_results, variant_results, query_id, variant_id):
            return True

        raise ValidationError(detail)

    @staticmethod
    def _exact_fast_path_accepts(
        original: list[tuple[Any, ...]],
        variant: list[tuple[Any, ...]],
        *,
        ordered: bool,
    ) -> bool:
        try:
            if original != variant:
                if ordered or sorted(original) != sorted(variant):
                    return False
            return _rows_are_exact_safe(original) and _rows_are_exact_safe(variant)
        except (TypeError, ValueError, ArithmeticError):
            return False

    def _first_column_count_mismatch(
        self,
        original: list[tuple[Any, ...]],
        variant: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
    ) -> str | None:
        for i, (orig_row, var_row) in enumerate(zip(original, variant)):
            if len(orig_row) != len(var_row):
                return (
                    f"Q{query_id}.{variant_id}: Column count mismatch at row {i}. "
                    f"Original: {len(orig_row)}, Variant: {len(var_row)}"
                )
        return None

    @staticmethod
    def _order_key_in_range(
        key_columns: Sequence[int],
        original: list[tuple[Any, ...]],
        variant: list[tuple[Any, ...]],
    ) -> bool:
        if not key_columns:
            return False
        max_index = max(key_columns)
        if max_index < 0:
            return False
        return all(len(row) > max_index for row in original) and all(len(row) > max_index for row in variant)

    def _validate_order_aware(
        self,
        original: list[tuple[Any, ...]],
        variant: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
        key_columns: Sequence[int],
        *,
        tie_aware: bool,
        final_key_tied_beyond_limit: bool = False,
    ) -> bool:
        detail = self._first_column_count_mismatch(original, variant, query_id, variant_id)
        if detail is not None:
            raise ValidationError(detail)

        original_groups = self._group_by_order_key(original, key_columns)
        variant_groups = self._group_by_order_key(variant, key_columns)

        if len(original_groups) != len(variant_groups):
            raise ValidationError(
                f"Q{query_id}.{variant_id}: ORDER BY tie-group count mismatch. "
                f"Original: {len(original_groups)}, Variant: {len(variant_groups)} "
                f"(order-key columns {key_columns}) - the returned order differs."
            )

        last_index = len(original_groups) - 1
        for i, ((orig_key, orig_rows), (var_key, var_rows)) in enumerate(zip(original_groups, variant_groups)):
            if not self._order_keys_equal(orig_key, var_key):
                raise ValidationError(
                    f"Q{query_id}.{variant_id}: ORDER BY key mismatch at position {i}. "
                    f"Original key: {orig_key}, Variant key: {var_key} "
                    f"(order-key columns {key_columns}) - the returned order differs "
                    f"(e.g. a reversed ORDER BY)."
                )
            if self._multisets_equal(orig_rows, var_rows):
                continue
            if i == last_index and tie_aware and final_key_tied_beyond_limit:
                continue
            detail = self._first_positional_mismatch(
                sorted(orig_rows, key=self._pairing_sort_key),
                sorted(var_rows, key=self._pairing_sort_key),
                query_id,
                variant_id,
            )
            raise ValidationError(
                detail
                or (
                    f"Q{query_id}.{variant_id}: ORDER BY tie group at position {i} differs as a multiset "
                    f"(order-key {orig_key})."
                )
            )
        return True

    def _group_by_order_key(
        self, rows: list[tuple[Any, ...]], key_columns: list[int]
    ) -> list[tuple[tuple[Any, ...], list[tuple[Any, ...]]]]:
        groups: list[tuple[tuple[Any, ...], list[tuple[Any, ...]]]] = []
        for row in rows:
            key = tuple(row[c] for c in key_columns)
            if groups and self._order_keys_equal(groups[-1][0], key):
                groups[-1][1].append(row)
            else:
                groups.append((key, [row]))
        return groups

    def _order_keys_equal(self, a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
        return len(a) == len(b) and all(self._values_equal(x, y) for x, y in zip(a, b))

    def _multisets_equal(self, left: list[tuple[Any, ...]], right: list[tuple[Any, ...]]) -> bool:
        if len(left) != len(right):
            return False
        left_sorted = sorted(left, key=self._pairing_sort_key)
        right_sorted = sorted(right, key=self._pairing_sort_key)
        if all(self._order_keys_equal(lhs, rhs) for lhs, rhs in zip(left_sorted, right_sorted)):
            return True
        return self._pair_variant_rows(left_sorted, right_sorted) is not None

    def _row_sort_key(self, row: tuple[Any, ...]) -> tuple[Any, ...]:
        if self.treat_nan_as_null:
            return tuple(self._cell_sort_key(value) for value in row)
        return _row_sort_key(row)

    def _pairing_sort_key(self, row: tuple[Any, ...]) -> tuple[Any, ...]:
        return self._row_sort_key(row)

    def _cell_sort_key(self, value: Any) -> tuple[Any, ...]:
        if self.treat_nan_as_null and isinstance(value, float) and math.isnan(value):
            return _cell_sort_key(None)
        return _cell_sort_key(value)

    def _row_value_mismatch_columns(
        self,
        orig_row: tuple[Any, ...],
        var_row: tuple[Any, ...],
        aggregation_columns: Optional[Sequence[int]] = None,
    ) -> list[int]:
        agg = set(aggregation_columns or ())
        mismatched: list[int] = []
        for j, (orig_val, var_val) in enumerate(zip(orig_row, var_row)):
            if j in agg:
                try:
                    equal = self._numeric_values_equal(orig_val, var_val)
                except (TypeError, ValueError):
                    equal = False
            else:
                equal = self._values_equal(orig_val, var_val)
            if not equal:
                mismatched.append(j)
        return mismatched

    @staticmethod
    def _also_columns_suffix(mismatched: list[int]) -> str:
        extra = mismatched[1:]
        return f"; also columns {extra}" if extra else ""

    def _rows_match(
        self,
        orig_row: tuple[Any, ...],
        var_row: tuple[Any, ...],
        aggregation_columns: Optional[Sequence[int]] = None,
    ) -> bool:
        if len(orig_row) != len(var_row):
            return False
        return not self._row_value_mismatch_columns(orig_row, var_row, aggregation_columns)

    def _pair_variant_rows(
        self,
        original_sorted: list[tuple[Any, ...]],
        variant_sorted: list[tuple[Any, ...]],
        aggregation_columns: Optional[Sequence[int]] = None,
    ) -> list[tuple[Any, ...]] | None:
        n = len(original_sorted)
        if n != len(variant_sorted):
            return None
        if n == 0:
            return []

        if all(self._rows_match(original_sorted[i], variant_sorted[i], aggregation_columns) for i in range(n)):
            return variant_sorted

        var_match = [-1] * n

        for u in range(n):
            if var_match[u] == -1 and self._rows_match(original_sorted[u], variant_sorted[u], aggregation_columns):
                var_match[u] = u
                continue
            matched = False
            for offset in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
                v = u + offset
                if (
                    0 <= v < n
                    and var_match[v] == -1
                    and self._rows_match(original_sorted[u], variant_sorted[v], aggregation_columns)
                ):
                    var_match[v] = u
                    matched = True
                    break
            if matched:
                continue
            for v in range(n):
                if var_match[v] == -1 and self._rows_match(original_sorted[u], variant_sorted[v], aggregation_columns):
                    var_match[v] = u
                    break

        if not self._augmenting_path_match(original_sorted, variant_sorted, var_match, aggregation_columns):
            return None

        paired: list[Optional[tuple[Any, ...]]] = [None] * n
        for v, u in enumerate(var_match):
            paired[u] = variant_sorted[v]
        return paired

    def _augmenting_path_match(
        self,
        original_sorted: list[tuple[Any, ...]],
        variant_sorted: list[tuple[Any, ...]],
        var_match: list[int],
        aggregation_columns: Optional[Sequence[int]] = None,
    ) -> bool:
        n = len(original_sorted)
        matched_u = {u for u in var_match if u != -1}
        for u in range(n):
            if u in matched_u:
                continue
            visited = [False] * n

            def dfs(curr_u: int) -> bool:
                for v in range(n):
                    if visited[v]:
                        continue
                    if self._rows_match(original_sorted[curr_u], variant_sorted[v], aggregation_columns):
                        visited[v] = True
                        if var_match[v] == -1 or dfs(var_match[v]):
                            var_match[v] = curr_u
                            return True
                return False

            if not dfs(u):
                return False
        return True

    def _first_positional_mismatch(
        self,
        original_sorted: list[tuple[Any, ...]],
        variant_sorted: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
    ) -> str | None:
        for i, (orig_row, var_row) in enumerate(zip(original_sorted, variant_sorted)):
            if len(orig_row) != len(var_row):
                return (
                    f"Q{query_id}.{variant_id}: Column count mismatch at row {i}. "
                    f"Original: {len(orig_row)}, Variant: {len(var_row)}"
                )
            mismatched = self._row_value_mismatch_columns(orig_row, var_row)
            if mismatched:
                j = mismatched[0]
                return (
                    f"Q{query_id}.{variant_id}: Value mismatch at row {i}, column {j}. "
                    f"Original: {orig_row[j]}, Variant: {var_row[j]}"
                    f"{self._also_columns_suffix(mismatched)}"
                )
        return None

    def _is_boundary_tie_equivalent(
        self,
        original: list[tuple[Any, ...]],
        variant: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
    ) -> bool:
        if not original or not variant or len(original[0]) != len(variant[0]):
            return False

        from collections import Counter

        try:
            only_original = list((Counter(original) - Counter(variant)).elements())
            only_variant = list((Counter(variant) - Counter(original)).elements())
        except TypeError:
            return False

        if not only_original or not only_variant:
            return False

        swapped = only_original + only_variant
        candidate_cols = self._monotonic_columns(original)
        varying_cols = [c for c in candidate_cols if not self._column_is_constant(original, c)]
        boundary_cols = varying_cols or candidate_cols
        for c in boundary_cols:
            boundary_value = original[-1][c]
            if boundary_value is None:
                continue
            column = [row[c] for row in original]
            if sum(1 for v in column if self._values_equal(v, boundary_value)) < 2:
                continue
            if any(not self._values_equal(row[c], boundary_value) for row in swapped):
                continue
            present = [v for v in column if v is not None]
            try:
                at_extreme = self._values_equal(boundary_value, min(present)) or self._values_equal(
                    boundary_value, max(present)
                )
            except TypeError:
                continue
            if at_extreme:
                return True
        return False

    def _monotonic_columns(self, rows: list[tuple[Any, ...]]) -> list[int]:
        if len(rows) < 2:
            return []
        width = len(rows[0])
        result: list[int] = []
        for c in range(width):
            non_decreasing = True
            non_increasing = True
            for left, right in zip(rows, rows[1:]):
                order = self._safe_compare(left[c], right[c])
                if order is None:
                    non_decreasing = non_increasing = False
                    break
                if order < 0:
                    non_increasing = False
                elif order > 0:
                    non_decreasing = False
            if non_decreasing or non_increasing:
                result.append(c)
        return result

    def _column_is_constant(self, rows: list[tuple[Any, ...]], c: int) -> bool:
        if not rows:
            return True
        first = rows[0][c]
        return all(self._values_equal(row[c], first) for row in rows)

    def _safe_compare(self, a: Any, b: Any) -> int | None:
        if self._values_equal(a, b):
            return 0
        if a is None or b is None:
            return None
        try:
            return -1 if a < b else 1
        except TypeError:
            return None

    def validate_results_checksum(
        self,
        original_results: list[tuple[Any, ...]],
        variant_results: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
    ) -> bool:
        original_checksum = self._calculate_checksum(original_results)
        variant_checksum = self._calculate_checksum(variant_results)

        if original_checksum != variant_checksum:
            raise ValidationError(
                f"Q{query_id}.{variant_id}: Checksum mismatch. "
                f"Original: {original_checksum}, Variant: {variant_checksum}"
            )

        return True

    def validate_aggregation_results(
        self,
        original_results: list[tuple[Any, ...]],
        variant_results: list[tuple[Any, ...]],
        query_id: int,
        variant_id: int,
        aggregation_columns: Optional[list[int]] = None,
    ) -> bool:
        if len(original_results) != len(variant_results):
            raise ValidationError(
                f"Q{query_id}.{variant_id}: Row count mismatch in aggregation. "
                f"Original: {len(original_results)}, Variant: {len(variant_results)}"
            )

        original_sorted = sorted(original_results, key=self._pairing_sort_key)
        variant_sorted = sorted(variant_results, key=self._pairing_sort_key)
        paired_variant = self._pair_variant_rows(original_sorted, variant_sorted, aggregation_columns)
        if paired_variant is not None:
            variant_sorted = paired_variant

        agg_columns = set(aggregation_columns or ())
        for i, (orig_row, var_row) in enumerate(zip(original_sorted, variant_sorted)):
            if len(orig_row) != len(var_row):
                raise ValidationError(
                    f"Q{query_id}.{variant_id}: Column count mismatch at row {i}. "
                    f"Original: {len(orig_row)}, Variant: {len(var_row)}"
                )

            mismatched = self._row_value_mismatch_columns(orig_row, var_row, aggregation_columns)
            if not mismatched:
                continue
            j = mismatched[0]
            suffix = self._also_columns_suffix(mismatched)
            if j in agg_columns:
                raise ValidationError(
                    f"Q{query_id}.{variant_id}: Aggregation value mismatch at row {i}, column {j}. "
                    f"Original: {orig_row[j]}, Variant: {var_row[j]}, Tolerance: {self.tolerance}{suffix}"
                )
            raise ValidationError(
                f"Q{query_id}.{variant_id}: Value mismatch at row {i}, column {j}. "
                f"Original: {orig_row[j]}, Variant: {var_row[j]}{suffix}"
            )

        return True

    def _values_equal(self, val1: Any, val2: Any) -> bool:
        if val1 is None or val2 is None:
            if self.treat_nan_as_null:
                val1_nullish = val1 is None or (isinstance(val1, float) and math.isnan(val1))
                val2_nullish = val2 is None or (isinstance(val2, float) and math.isnan(val2))
                return val1_nullish and val2_nullish
            return val1 is None and val2 is None

        if isinstance(val1, (int, float, Decimal)) and isinstance(val2, (int, float, Decimal)):
            return self._numeric_values_equal(val1, val2)

        if isinstance(val1, (list, tuple)) and isinstance(val2, (list, tuple)):
            return len(val1) == len(val2) and all(self._values_equal(x, y) for x, y in zip(val1, val2))
        if isinstance(val1, dict) and isinstance(val2, dict):
            return val1.keys() == val2.keys() and all(self._values_equal(val1[k], val2[k]) for k in val1)

        if isinstance(val1, str) and isinstance(val2, str):
            if self.strip_strings:
                return val1.strip() == val2.strip()
            return val1 == val2

        return val1 == val2

    def _numeric_values_equal(self, val1: Union[int, float], val2: Union[int, float]) -> bool:
        val1_is_none = val1 is None
        val2_is_none = val2 is None
        val1_is_nan = isinstance(val1, float) and math.isnan(val1)
        val2_is_nan = isinstance(val2, float) and math.isnan(val2)
        if val1_is_none or val2_is_none:
            if self.treat_nan_as_null:
                return (val1_is_none or val1_is_nan) and (val2_is_none or val2_is_nan)
            return val1_is_none and val2_is_none
        if val1_is_nan or val2_is_nan:
            if self.treat_nan_as_null:
                return (val1_is_none or val1_is_nan) and (val2_is_none or val2_is_nan)
            return False

        val1 = float(val1)
        val2 = float(val2)
        if val1 == val2:
            return True

        if abs(val1) < 1e-10 and abs(val2) < 1e-10:
            return abs(val1 - val2) < self.tolerance

        try:
            relative_diff = abs(val1 - val2) / max(abs(val1), abs(val2))
            return relative_diff < self.tolerance
        except ZeroDivisionError:
            return abs(val1 - val2) < self.tolerance

    def _calculate_checksum(self, results: list[tuple[Any, ...]]) -> str:
        return calculate_checksum(results)

    def validate_query1_results(
        self,
        original_results: list[tuple[Any, ...]],
        variant_results: list[tuple[Any, ...]],
        variant_id: int,
    ) -> bool:

        aggregation_columns = [
            2,
            3,
            4,
            5,
            6,
            7,
            8,
        ]

        return self.validate_aggregation_results(
            original_results,
            variant_results,
            query_id=1,
            variant_id=variant_id,
            aggregation_columns=aggregation_columns,
        )


class ValidationReport:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    def add_validation_result(
        self,
        query_id: int,
        variant_id: int,
        success: bool,
        error_message: Optional[str] = None,
        execution_time_original: Optional[float] = None,
        execution_time_variant: Optional[float] = None,
    ) -> None:
        key = f"Q{query_id}.{variant_id}"
        self.results[key] = {
            "query_id": query_id,
            "variant_id": variant_id,
            "success": success,
            "error_message": error_message,
            "execution_time_original": execution_time_original,
            "execution_time_variant": execution_time_variant,
            "performance_ratio": (
                execution_time_variant / execution_time_original
                if execution_time_original and execution_time_variant and execution_time_original > 0
                else None
            ),
        }

    def get_summary(self) -> dict[str, Any]:
        total_tests = len(self.results)
        successful_tests = sum(1 for result in self.results.values() if result["success"])
        failed_tests = total_tests - successful_tests

        return {
            "total_tests": total_tests,
            "successful_tests": successful_tests,
            "failed_tests": failed_tests,
            "success_rate": successful_tests / total_tests if total_tests > 0 else 0.0,
            "failed_queries": [
                f"Q{result['query_id']}.{result['variant_id']}"
                for result in self.results.values()
                if not result["success"]
            ],
        }

    def get_performance_summary(self) -> dict[str, Any]:
        performance_ratios = [
            result["performance_ratio"] for result in self.results.values() if result["performance_ratio"] is not None
        ]

        if not performance_ratios:
            return {"message": "No performance data available"}

        return {
            "min_ratio": min(performance_ratios),
            "max_ratio": max(performance_ratios),
            "avg_ratio": sum(performance_ratios) / len(performance_ratios),
            "variants_faster": sum(1 for ratio in performance_ratios if ratio < 1.0),
            "variants_slower": sum(1 for ratio in performance_ratios if ratio > 1.0),
            "variants_similar": sum(1 for ratio in performance_ratios if 0.9 <= ratio <= 1.1),
        }

    def generate_report(self) -> str:
        summary = self.get_summary()
        perf_summary = self.get_performance_summary()

        report = f"""
TPC-Havoc Validation Report
===========================

Summary:
--------
Total Tests: {summary["total_tests"]}
Successful: {summary["successful_tests"]}
Failed: {summary["failed_tests"]}
Success Rate: {summary["success_rate"]:.2%}

"""

        if summary["failed_tests"] > 0:
            report += f"Failed Queries: {', '.join(summary['failed_queries'])}\n\n"

        if "message" not in perf_summary:
            report += f"""Performance Summary:
-------------------
Fastest Variant Ratio: {perf_summary["min_ratio"]:.2f}x
Slowest Variant Ratio: {perf_summary["max_ratio"]:.2f}x
Average Ratio: {perf_summary["avg_ratio"]:.2f}x
Variants Faster: {perf_summary["variants_faster"]}
Variants Slower: {perf_summary["variants_slower"]}
Variants Similar: {perf_summary["variants_similar"]}

"""

        report += "Detailed Results:\n"
        report += "-----------------\n"
        for key, result in sorted(self.results.items()):
            status = "✅" if result["success"] else "❌"
            report += f"{status} {key}: {result.get('error_message', 'Success')}\n"

        return report
