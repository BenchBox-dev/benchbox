# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ValidationMode(str, Enum):
    EXACT = "exact"
    RANGE = "range"
    LOOSE = "loose"
    SKIP = "skip"


@dataclass
class ExpectedQueryResult:
    query_id: str
    scale_factor: float | None = None
    expected_row_count: int | None = None
    expected_row_count_min: int | None = None
    expected_row_count_max: int | None = None
    row_count_formula: str | None = None
    validation_mode: ValidationMode = ValidationMode.EXACT
    scale_independent: bool = False
    loose_tolerance_percent: float = 50.0
    notes: str | None = None
    value_digest: str | None = None
    reference_parameters_only: bool = False

    def __post_init__(self):
        if self.validation_mode == ValidationMode.EXACT:
            if self.expected_row_count is None and self.row_count_formula is None:
                raise ValueError(f"Query {self.query_id}: EXACT mode requires expected_row_count or row_count_formula")
        elif self.validation_mode == ValidationMode.RANGE:
            if self.expected_row_count_min is None or self.expected_row_count_max is None:
                raise ValueError(
                    f"Query {self.query_id}: RANGE mode requires expected_row_count_min and expected_row_count_max"
                )
            if self.expected_row_count_min > self.expected_row_count_max:
                raise ValueError(
                    f"Query {self.query_id}: expected_row_count_min ({self.expected_row_count_min}) "
                    f"must be <= expected_row_count_max ({self.expected_row_count_max})"
                )
        elif self.validation_mode == ValidationMode.LOOSE:
            if self.expected_row_count is None and self.row_count_formula is None:
                raise ValueError(f"Query {self.query_id}: LOOSE mode requires expected_row_count or row_count_formula")
            if self.loose_tolerance_percent <= 0:
                raise ValueError(
                    f"Query {self.query_id}: loose_tolerance_percent must be positive, got {self.loose_tolerance_percent}"
                )

    def get_expected_count(self, scale_factor: float | None = None) -> int | None:
        if self.validation_mode == ValidationMode.SKIP:
            return None

        if self.expected_row_count is not None:
            return self.expected_row_count

        if self.row_count_formula:
            return self._evaluate_formula(self.row_count_formula, scale_factor or self.scale_factor or 1.0)

        return None

    def _evaluate_formula(self, formula: str, scale_factor: float) -> int:
        import ast
        import operator

        formula_str = formula.replace("SF", str(scale_factor))

        try:
            node = ast.parse(formula_str, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Invalid formula syntax '{formula}': {e}") from e

        allowed_ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.FloorDiv: operator.floordiv,
            ast.Div: operator.truediv,
            ast.Mod: operator.mod,
        }

        def eval_node(node):
            if isinstance(node, ast.Expression):
                return eval_node(node.body)
            elif isinstance(node, ast.Constant):
                return node.value
            elif isinstance(node, ast.BinOp):
                if type(node.op) not in allowed_ops:
                    raise ValueError(
                        f"Operation {type(node.op).__name__} not allowed in formula. "
                        f"Only +, -, *, /, //, % are permitted."
                    )
                left = eval_node(node.left)
                right = eval_node(node.right)
                return allowed_ops[type(node.op)](left, right)
            elif isinstance(node, ast.UnaryOp):
                if isinstance(node.op, ast.UAdd):
                    return eval_node(node.operand)
                elif isinstance(node.op, ast.USub):
                    return -eval_node(node.operand)
                else:
                    raise ValueError(f"Unary operation {type(node.op).__name__} not allowed in formula")
            else:
                raise ValueError(
                    f"AST node type {type(node).__name__} not allowed in formula. "
                    f"Formula must contain only numeric constants and safe arithmetic operations."
                )

        try:
            result = eval_node(node)
            return int(result)
        except (ValueError, TypeError, ZeroDivisionError) as e:
            raise ValueError(f"Failed to evaluate formula '{formula}' with SF={scale_factor}: {e}") from e

    def validate_loose(self, actual_count: int, scale_factor: float | None = None) -> ValidationResult:
        expected_count = self.get_expected_count(scale_factor)

        if expected_count is None:
            return ValidationResult(
                is_valid=False,
                query_id=self.query_id,
                expected_row_count=None,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.LOOSE,
                error_message="Cannot perform loose validation without expected row count",
            )

        if expected_count > 0 and actual_count == 0:
            return ValidationResult(
                is_valid=False,
                query_id=self.query_id,
                expected_row_count=expected_count,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.LOOSE,
                error_message=(
                    f"Query returned 0 rows when expecting {expected_count:,} rows. "
                    f"This indicates a critical query failure."
                ),
            )

        tolerance_multiplier = self.loose_tolerance_percent / 100.0
        min_acceptable = int(expected_count * (1.0 - tolerance_multiplier))
        max_acceptable = int(expected_count * (1.0 + tolerance_multiplier))

        if min_acceptable <= actual_count <= max_acceptable:
            return ValidationResult(
                is_valid=True,
                query_id=self.query_id,
                expected_row_count=expected_count,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.LOOSE,
            )
        else:
            difference = actual_count - expected_count
            difference_percent = (difference / expected_count * 100.0) if expected_count > 0 else 0.0

            return ValidationResult(
                is_valid=False,
                query_id=self.query_id,
                expected_row_count=expected_count,
                actual_row_count=actual_count,
                validation_mode=ValidationMode.LOOSE,
                error_message=(
                    f"Row count outside tolerance range. "
                    f"Expected: {expected_count:,} ±{self.loose_tolerance_percent}% "
                    f"({min_acceptable:,} to {max_acceptable:,}), "
                    f"Actual: {actual_count:,} ({difference_percent:+.1f}%)"
                ),
            )


@dataclass
class BenchmarkExpectedResults:
    benchmark_name: str
    scale_factor: float
    query_results: dict[str, ExpectedQueryResult]
    metadata: dict[str, Any] | None = None

    def get_expected_result(self, query_id: str) -> ExpectedQueryResult | None:
        return self.query_results.get(query_id)


@dataclass
class ValidationResult:
    is_valid: bool
    query_id: str
    expected_row_count: int | None
    actual_row_count: int
    validation_mode: ValidationMode
    error_message: str | None = None
    warning_message: str | None = None
    difference: int | None = None
    difference_percent: float | None = None

    def __post_init__(self):
        if self.expected_row_count is not None and self.actual_row_count is not None:
            self.difference = self.actual_row_count - self.expected_row_count
            if self.expected_row_count > 0:
                self.difference_percent = (self.difference / self.expected_row_count) * 100.0
