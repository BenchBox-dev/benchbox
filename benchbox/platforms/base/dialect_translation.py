from __future__ import annotations

import logging


class DialectTranslationMixin:
    _dialect: str | None
    logger: logging.Logger

    @property
    def dialect(self) -> str | None:
        return self._dialect

    def translate_sql(
        self,
        sql: str,
        source_dialect: str = "standard",
        strict: bool | None = None,
        scope: str | None = None,
    ) -> str:
        from benchbox.utils.dialect_utils import SCHEMA_DDL_SCOPE

        if scope is None:
            scope = SCHEMA_DDL_SCOPE
        if not self.dialect or self.dialect == source_dialect:
            return sql

        from benchbox.utils.dialect_utils import split_sql_statements, translate_sql_query

        statements = split_sql_statements(sql, source_dialect)
        translated_statements = [
            translate_sql_query(
                statement,
                self.dialect,
                source_dialect=source_dialect,
                identify=True,
                strict=strict,
                scope=scope,
            )
            for statement in statements
        ]
        if any(translated is statement for translated, statement in zip(translated_statements, statements)):
            return sql
        return ";\n\n".join(translated_statements) + ";"

    def get_tpc_base_dialect(self, benchmark_name: str) -> str:
        benchmark_lower = benchmark_name.lower()
        if benchmark_lower == "tpcds":
            return "netezza"
        else:
            return "netezza"
