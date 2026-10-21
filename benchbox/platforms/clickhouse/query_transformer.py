from __future__ import annotations

import logging
import re

from benchbox.utils.dialect_utils import apply_rewrite
from benchbox.utils.sql_parsing import find_matching_parenthesis

logger = logging.getLogger(__name__)


class ClickHouseQueryTransformer:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.transformations_applied = []

    def transform(self, query: str) -> str:
        self.transformations_applied = []

        query = self.normalize_case(query)
        query = self.fix_type_casts(query)
        query = self.apply_fork_safe_division(query)

        if self.verbose and self.transformations_applied:
            logger.debug(f"Applied transformations: {', '.join(self.transformations_applied)}")

        return query

    def normalize_case(self, query: str) -> str:
        uppercase_patterns = [
            r"\bSR_RETURN_AMT\b",
            r"\bSR_CUSTOMER_SK\b",
            r"\bSR_STORE_SK\b",
            r"\bSR_RETURNED_DATE_SK\b",
            r"\b[A-Z][A-Z_]+_SK\b",
            r"\b[A-Z][A-Z_]+_AMT\b",
            r"\b[A-Z][A-Z_]+_QTY\b",
            r"\b[A-Z][A-Z_]+_FEE\b",
            r"\b[A-Z][A-Z_]+_CASH\b",
            r"\b[A-Z][A-Z_]+_CREDIT\b",
            r"\b[A-Z][A-Z_]+_TAX\b",
            r"\b[A-Z][A-Z_]+_CHARGE\b",
            r"\bD_YEAR\b",
            r"\bD_MOY\b",
            r"\bD_DATE_SK\b",
        ]

        original_query = query
        for pattern in uppercase_patterns:

            def replace_outside_strings(match):
                return match.group(0).lower()

            query = re.sub(pattern, lambda m: m.group(0).lower(), query)

        if query != original_query:
            self.transformations_applied.append("case_normalization")

        return query

    def fix_type_casts(self, query: str) -> str:
        original_query = query

        query = re.sub(
            r"COALESCE\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*,\s*(0\.0*)\s*\)",
            r"COALESCE(\1, CAST(0 AS Decimal(15,2)))",
            query,
            flags=re.IGNORECASE,
        )

        query = re.sub(
            r"([a-zA-Z_][a-zA-Z0-9_.]*)\s*([-+*/])\s*(0\.0*)(?![0-9\)])",
            r"\1 \2 CAST(0 AS Decimal(15,2))",
            query,
        )

        query = re.sub(
            r"\bTHEN\s+(0\.0*)(?![0-9])",
            r"THEN CAST(0 AS Decimal(15,2))",
            query,
            flags=re.IGNORECASE,
        )
        query = re.sub(
            r"\bELSE\s+(0\.0*)(?![0-9])",
            r"ELSE CAST(0 AS Decimal(15,2))",
            query,
            flags=re.IGNORECASE,
        )

        if query != original_query:
            self.transformations_applied.append("type_casting")

        return query

    def add_subquery_aliases(self, query: str) -> str:
        original_query = query

        alias_counter = [0]

        def add_alias(match):
            alias_counter[0] += 1
            subquery = match.group(1)
            following = match.group(2)
            return f"FROM ({subquery}) AS subquery_{alias_counter[0]} {following}"

        pattern = r"\bFROM\s*(\([^)]*(?:\([^)]*\))*[^)]*\))\s+(?!AS\s)(\b(?:WHERE|JOIN|GROUP|ORDER|UNION|INTERSECT|EXCEPT|LIMIT|;))"
        query = re.sub(pattern, add_alias, query, flags=re.IGNORECASE)

        if query != original_query:
            self.transformations_applied.append("subquery_aliasing")

        return query

    def apply_fork_safe_division(self, query: str) -> str:
        rewritten = apply_rewrite("safe_division", query, "clickhouse")
        if rewritten.lower().count("nullif(") > query.lower().count("nullif("):
            self.transformations_applied.append("safe_division")
        return rewritten

    def safe_division(self, query: str) -> str:
        original_query = query
        identifier_or_func = r"[a-zA-Z_][a-zA-Z0-9_.]*(?:\([^)]*\))?"
        paren_expr = r"\([^)]+\)"
        numerator_pattern = re.compile(rf"((?:{identifier_or_func}|{paren_expr}))\s*/\s*", re.IGNORECASE)

        transformed_segments: list[str] = []
        last_emitted = 0
        search_start = 0

        while match := numerator_pattern.search(query, search_start):
            if self._inside_string_literal(query, match.start()):
                search_start = match.end()
                continue

            divisor_start = match.end()
            divisor_end = self._scan_sql_operand(query, divisor_start)
            if divisor_end is None:
                search_start = divisor_start
                continue

            numerator = match.group(1).strip()
            divisor = query[divisor_start:divisor_end].strip()
            replacement = self._wrap_divisor_with_nullif(numerator, divisor)

            transformed_segments.append(query[last_emitted : match.start()])
            transformed_segments.append(replacement)
            last_emitted = divisor_end
            search_start = divisor_end

        transformed_segments.append(query[last_emitted:])
        query = "".join(transformed_segments)

        if query != original_query:
            self.transformations_applied.append("safe_division")

        return query

    def _wrap_divisor_with_nullif(self, numerator: str, divisor: str) -> str:
        normalized_divisor = self._strip_outer_parentheses(divisor).strip()

        if re.fullmatch(r"\d+(?:\.\d+)?", normalized_divisor):
            return f"{numerator} / {divisor}"

        if normalized_divisor.lower().startswith("nullif"):
            return f"{numerator} / {divisor}"

        return f"{numerator} / NULLIF({divisor}, 0)"

    @classmethod
    def _scan_sql_operand(cls, text: str, start_index: int) -> int | None:
        index = start_index
        while index < len(text) and text[index].isspace():
            index += 1

        if index >= len(text):
            return None

        first = text[index]
        if first == "(":
            return cls._find_matching_parenthesis(text, index) + 1

        if first.isdigit():
            end = index + 1
            while end < len(text) and (text[end].isdigit() or text[end] == "."):
                end += 1
            return end

        if not (first.isalpha() or first == "_"):
            return None

        end = index + 1
        while end < len(text) and (text[end].isalnum() or text[end] in "._"):
            end += 1

        if end < len(text) and text[end] == "(":
            call_end = cls._find_matching_parenthesis(text, end) + 1
            return cls._extend_over_clause(text, call_end)

        return end

    @classmethod
    def _extend_over_clause(cls, text: str, index: int) -> int:
        probe = index
        while probe < len(text) and text[probe].isspace():
            probe += 1
        if text[probe : probe + 4].upper() != "OVER":
            return index
        after_kw = probe + 4
        if after_kw < len(text) and (text[after_kw].isalnum() or text[after_kw] == "_"):
            return index
        while after_kw < len(text) and text[after_kw].isspace():
            after_kw += 1
        if after_kw < len(text) and text[after_kw] == "(":
            return cls._find_matching_parenthesis(text, after_kw) + 1
        return index

    @classmethod
    def _strip_outer_parentheses(cls, expression: str) -> str:
        stripped = expression.strip()
        while stripped.startswith("("):
            try:
                close_index = cls._find_matching_parenthesis(stripped, 0)
            except ValueError:
                break
            if close_index != len(stripped) - 1:
                break
            stripped = stripped[1:-1].strip()
        return stripped

    @staticmethod
    def _find_matching_parenthesis(text: str, open_index: int) -> int:
        return find_matching_parenthesis(text, open_index)

    @staticmethod
    def _inside_string_literal(text: str, position: int) -> bool:
        in_quote = False
        index = 0
        while index < position:
            if text[index] == "'":
                if in_quote and index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                in_quote = not in_quote
            index += 1
        return in_quote

    def fix_decimal_division_by_zero(self, query: str) -> str:
        original_query = query

        pattern = r"/\s*(CAST\([^)]+\bAS\s+Nullable\s*\(\s*Decimal\s*\(\s*\d+\s*,\s*\d+\s*\)\s*\)\s*\))"
        query = re.sub(
            pattern,
            lambda m: f"/ NULLIF({m.group(1)}, 0)",
            query,
            flags=re.IGNORECASE,
        )

        if query != original_query:
            self.transformations_applied.append("decimal_division_fix")

        return query

    def get_transformations_applied(self) -> list[str]:
        return self.transformations_applied

    def add_query_settings(
        self,
        query: str,
        additional_settings: tuple[tuple[str, str | int], ...] = (),
    ) -> str:
        stripped = query.strip()
        if not stripped.upper().startswith("SELECT") and not stripped.upper().startswith("WITH"):
            return query

        cleaned = stripped.rstrip(";").rstrip()
        settings = ["joined_subquery_requires_alias = 0"]
        for name, value in additional_settings:
            if isinstance(value, str):
                formatted = f"'{value.replace(chr(39), chr(39) * 2)}'"
            else:
                formatted = str(value)
            settings.append(f"{name} = {formatted}")
        return f"{cleaned} SETTINGS {', '.join(settings)}"


def transform_query_for_clickhouse(query: str, verbose: bool = False) -> str:
    transformer = ClickHouseQueryTransformer(verbose=verbose)
    return transformer.transform(query)


__all__ = ["ClickHouseQueryTransformer", "transform_query_for_clickhouse"]
