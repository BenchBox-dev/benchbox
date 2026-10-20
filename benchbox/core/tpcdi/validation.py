# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from benchbox.utils.dialect_utils import translate_sql_query
from benchbox.utils.printing import emit

logger = logging.getLogger(__name__)


@dataclass
class ValidationRule:
    name: str
    sql: str
    expected: Union[int, float, str]
    description: str
    category: str = "integrity"
    severity: str = "error"


@dataclass
class ValidationResult:
    name: str
    description: str
    sql: str
    violations: Union[int, float, str] = 0
    expected: Union[int, float, str] = 0
    passed: bool = False
    status: str = "pending"
    error: Optional[str] = None
    category: str = "integrity"
    severity: str = "error"


@dataclass
class DataQualityResult:
    validations: list[ValidationResult] = field(default_factory=list)
    total_validations: int = 0
    passed_validations: int = 0
    failed_validations: int = 0
    quality_score: float = 0.0
    error_count: int = 0
    warning_count: int = 0
    categories: dict[str, dict[str, int]] = field(default_factory=dict)


class TPCDIValidator:
    def __init__(self, connection: Any, dialect: str = "duckdb"):
        self.connection = connection
        self.dialect = dialect
        self.validation_rules = self._get_default_validation_rules()

    def _get_default_validation_rules(self) -> list[ValidationRule]:
        return [
            ValidationRule(
                name="Customer Primary Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM (SELECT SK_CustomerID, COUNT(*) as cnt
                            FROM DimCustomer
                            GROUP BY SK_CustomerID
                            HAVING COUNT(*) > 1)""",
                expected=0,
                description="No duplicate customer surrogate keys",
                category="primary_key",
            ),
            ValidationRule(
                name="Account Primary Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM (SELECT SK_AccountID, COUNT(*) as cnt
                            FROM DimAccount
                            GROUP BY SK_AccountID
                            HAVING COUNT(*) > 1)""",
                expected=0,
                description="No duplicate account surrogate keys",
                category="primary_key",
            ),
            ValidationRule(
                name="Security Primary Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM (SELECT SK_SecurityID, COUNT(*) as cnt
                            FROM DimSecurity
                            GROUP BY SK_SecurityID
                            HAVING COUNT(*) > 1)""",
                expected=0,
                description="No duplicate security surrogate keys",
                category="primary_key",
            ),
            ValidationRule(
                name="Customer Foreign Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM FactTrade t
                      LEFT JOIN DimCustomer c ON t.SK_CustomerID = c.SK_CustomerID
                      WHERE c.SK_CustomerID IS NULL""",
                expected=0,
                description="All trades reference valid customers",
                category="foreign_key",
            ),
            ValidationRule(
                name="Account Foreign Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM FactTrade t
                      LEFT JOIN DimAccount a ON t.SK_AccountID = a.SK_AccountID
                      WHERE a.SK_AccountID IS NULL""",
                expected=0,
                description="All trades reference valid accounts",
                category="foreign_key",
            ),
            ValidationRule(
                name="Security Foreign Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM FactTrade t
                      LEFT JOIN DimSecurity s ON t.SK_SecurityID = s.SK_SecurityID
                      WHERE s.SK_SecurityID IS NULL""",
                expected=0,
                description="All trades reference valid securities",
                category="foreign_key",
            ),
            ValidationRule(
                name="Date Foreign Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM FactTrade t
                      LEFT JOIN DimDate d ON t.SK_CreateDateID = d.SK_DateID
                      WHERE d.SK_DateID IS NULL""",
                expected=0,
                description="All trades reference valid create dates",
                category="foreign_key",
            ),
            ValidationRule(
                name="Time Foreign Key Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM FactTrade t
                      LEFT JOIN DimTime tm ON t.SK_CreateTimeID = tm.SK_TimeID
                      WHERE tm.SK_TimeID IS NULL""",
                expected=0,
                description="All trades reference valid create times",
                category="foreign_key",
            ),
            ValidationRule(
                name="Trade Price Reasonableness",
                sql="SELECT COUNT(*) as violations FROM FactTrade WHERE TradePrice <= 0 OR TradePrice > 10000",
                expected=0,
                description="Trade prices within reasonable bounds ($0-$10,000)",
                category="business_logic",
            ),
            ValidationRule(
                name="Trade Quantity Reasonableness",
                sql="SELECT COUNT(*) as violations FROM FactTrade WHERE Quantity <= 0 OR Quantity > 1000000",
                expected=0,
                description="Trade quantities within reasonable bounds (1-1,000,000)",
                category="business_logic",
            ),
            ValidationRule(
                name="Customer Status Validity",
                sql="SELECT COUNT(*) as violations FROM DimCustomer WHERE Status NOT IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')",
                expected=0,
                description="Customer status values are valid",
                category="business_logic",
            ),
            ValidationRule(
                name="Account Status Validity",
                sql="SELECT COUNT(*) as violations FROM DimAccount WHERE Status NOT IN ('ACTIVE', 'INACTIVE', 'CLOSED')",
                expected=0,
                description="Account status values are valid",
                category="business_logic",
            ),
            ValidationRule(
                name="Customer Data Completeness",
                sql="SELECT COUNT(*) as violations FROM DimCustomer WHERE LastName IS NULL OR FirstName IS NULL OR Status IS NULL",
                expected=0,
                description="Required customer fields are not null",
                category="completeness",
            ),
            ValidationRule(
                name="Trade Data Completeness",
                sql="SELECT COUNT(*) as violations FROM FactTrade WHERE TradeID IS NULL OR SK_CustomerID IS NULL OR SK_SecurityID IS NULL",
                expected=0,
                description="Required trade fields are not null",
                category="completeness",
            ),
            ValidationRule(
                name="Customer SCD Type 2 Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM (
                          SELECT CustomerID
                          FROM DimCustomer
                          WHERE IsCurrent = TRUE
                          GROUP BY CustomerID
                          HAVING COUNT(*) > 1
                      ) as duplicate_customers""",
                expected=0,
                description="Only one current record per customer",
                category="scd_type2",
            ),
            ValidationRule(
                name="Account SCD Type 2 Integrity",
                sql="""SELECT COUNT(*) as violations
                      FROM (
                          SELECT AccountID
                          FROM DimAccount
                          WHERE IsCurrent = TRUE
                          GROUP BY AccountID
                          HAVING COUNT(*) > 1
                      ) as duplicate_accounts""",
                expected=0,
                description="Only one current record per account",
                category="scd_type2",
            ),
        ]

    def add_validation_rule(self, rule: ValidationRule) -> None:
        self.validation_rules.append(rule)

    def run_validation(self, rule: ValidationRule) -> ValidationResult:
        result = ValidationResult(
            name=rule.name,
            description=rule.description,
            sql=rule.sql,
            expected=rule.expected,
            category=rule.category,
            severity=rule.severity,
        )

        try:
            sql = rule.sql
            if self.dialect != "standard":
                sql = translate_sql_query(rule.sql, self.dialect, source_dialect="postgres", identify=False)

            if hasattr(self.connection, "execute"):
                cursor_result = self.connection.execute(sql).fetchone()
            elif hasattr(self.connection, "query"):
                cursor_result = self.connection.query(sql).fetchone()
            else:
                raise ValueError(f"Unsupported connection type: {type(self.connection)}")

            violations = cursor_result[0] if cursor_result else -1
            result.violations = violations

            if isinstance(rule.expected, (int, float)):
                result.passed = violations == rule.expected
            else:
                result.passed = str(violations) == str(rule.expected)

            result.status = "PASSED" if result.passed else f"FAILED ({violations} violations)"

        except Exception as e:
            result.passed = False
            result.error = str(e)
            result.status = f"ERROR: {str(e)[:50]}..."
            result.violations = -1
            logger.error(f"Validation {rule.name} failed: {e}")

        return result

    def run_all_validations(self) -> DataQualityResult:
        logger.info(f"Running {len(self.validation_rules)} TPC-DI validation rules")

        quality_result = DataQualityResult()
        quality_result.total_validations = len(self.validation_rules)

        category_stats = {}

        for rule in self.validation_rules:
            validation_result = self.run_validation(rule)
            quality_result.validations.append(validation_result)

            if validation_result.passed:
                quality_result.passed_validations += 1
            else:
                quality_result.failed_validations += 1

                if validation_result.severity == "error":
                    quality_result.error_count += 1
                elif validation_result.severity == "warning":
                    quality_result.warning_count += 1

            category = validation_result.category
            if category not in category_stats:
                category_stats[category] = {"total": 0, "passed": 0, "failed": 0}

            category_stats[category]["total"] += 1
            if validation_result.passed:
                category_stats[category]["passed"] += 1
            else:
                category_stats[category]["failed"] += 1

        quality_result.categories = category_stats

        if quality_result.total_validations > 0:
            quality_result.quality_score = quality_result.passed_validations / quality_result.total_validations

        logger.info(
            f"Validation complete: {quality_result.passed_validations}/{quality_result.total_validations} passed"
        )
        logger.info(f"Data quality score: {quality_result.quality_score:.1%}")

        return quality_result

    def run_category_validations(self, category: str) -> DataQualityResult:
        category_rules = [rule for rule in self.validation_rules if rule.category == category]

        if not category_rules:
            logger.warning(f"No validation rules found for category: {category}")
            return DataQualityResult()

        original_rules = self.validation_rules
        self.validation_rules = category_rules

        try:
            result = self.run_all_validations()
            return result
        finally:
            self.validation_rules = original_rules

    def validate_foreign_keys(self) -> list[ValidationResult]:
        fk_results = []
        for rule in self.validation_rules:
            if rule.category == "foreign_key":
                result = self.run_validation(rule)
                fk_results.append(result)
        return fk_results

    def print_validation_summary(self, result: DataQualityResult) -> None:
        emit("\n" + "=" * 60)
        emit("TPC-DI DATA QUALITY VALIDATION RESULTS")
        emit("=" * 60)

        emit(f"Total Validations: {result.total_validations}")
        emit(f"Passed: {result.passed_validations}")
        emit(f"Failed: {result.failed_validations}")
        emit(f"Overall Quality Score: {result.quality_score:.1%}")

        if result.error_count > 0:
            emit(f"Errors: {result.error_count}")
        if result.warning_count > 0:
            emit(f"Warnings: {result.warning_count}")

        emit("\nResults by Category:")
        for category, stats in result.categories.items():
            score = stats["passed"] / stats["total"] if stats["total"] > 0 else 0
            emit(f"  {category}: {stats['passed']}/{stats['total']} ({score:.1%})")

        emit("\nFailed Validations:")
        failed_validations = [v for v in result.validations if not v.passed]
        if not failed_validations:
            emit("  None - All validations passed!")
        else:
            for validation in failed_validations:
                emit(f"  ❌ {validation.name}: {validation.status}")
                emit(f"     {validation.description}")
                if hasattr(validation, "violations") and validation.violations > 0:
                    emit(f"     Violations found: {validation.violations}")
                if hasattr(validation, "error") and validation.error:
                    emit(f"     Error: {validation.error}")

        emit("=" * 60)
