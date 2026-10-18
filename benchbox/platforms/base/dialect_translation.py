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

        from benchbox.utils.dialect_utils import (
            NO_IDENTIFY_DIALECTS,
            SQLTranslationError,
            SqlTranslationOutcome,
            _fingerprint_sql,
            _fix_sqlite_unsupported_syntax,
            _query_has_group_or_order_by_all,
            _restore_group_order_by_all_keyword,
            current_sql_translation_strict_mode,
            normalize_dialect_for_sqlglot,
            record_sql_translation_outcome,
            translation_collection_active,
        )

        strict_mode = current_sql_translation_strict_mode() if strict is None else strict
        src = normalize_dialect_for_sqlglot(source_dialect)
        tgt = normalize_dialect_for_sqlglot(self.dialect)
        fingerprint = _fingerprint_sql(sql) if translation_collection_active() else None

        try:
            from benchbox._vendor import sqlglot

            should_identify = tgt not in NO_IDENTIFY_DIALECTS

            translated_statements = sqlglot.transpile(sql, read=src, write=tgt, identify=should_identify)

            has_all = _query_has_group_or_order_by_all(sql)
            fixed = []
            for stmt in translated_statements:
                if tgt == "duckdb" and has_all:
                    stmt = _restore_group_order_by_all_keyword(stmt)
                elif tgt == "sqlite":
                    stmt = _fix_sqlite_unsupported_syntax(stmt)
                fixed.append(stmt)

            record_sql_translation_outcome(
                SqlTranslationOutcome(
                    source_dialect=source_dialect,
                    target_dialect=self.dialect or "",
                    normalized_source_dialect=src,
                    normalized_target_dialect=tgt,
                    translator="sqlglot",
                    status="success",
                    strict_mode=strict_mode,
                    scope=scope,
                    query_fingerprint=fingerprint,
                )
            )
            return ";\n\n".join(fixed) + ";"

        except ImportError:
            outcome = SqlTranslationOutcome(
                source_dialect=source_dialect,
                target_dialect=self.dialect or "",
                normalized_source_dialect=src,
                normalized_target_dialect=tgt,
                translator="sqlglot",
                status="failed" if strict_mode else "fallback",
                strict_mode=strict_mode,
                warning_category=None if strict_mode else "translator_unavailable",
                error_category="translator_unavailable" if strict_mode else None,
                message="SQLGlot not available",
                scope=scope,
                query_fingerprint=fingerprint,
            )
            record_sql_translation_outcome(outcome)
            if strict_mode:
                raise SQLTranslationError("SQLGlot not available for strict SQL translation", outcome) from None
            self.logger.warning("sqlglot not available for SQL translation")
            return sql
        except Exception as e:
            outcome = SqlTranslationOutcome(
                source_dialect=source_dialect,
                target_dialect=self.dialect or "",
                normalized_source_dialect=src,
                normalized_target_dialect=tgt,
                translator="sqlglot",
                status="failed" if strict_mode else "fallback",
                strict_mode=strict_mode,
                warning_category=None if strict_mode else "translation_failed",
                error_category="translation_failed" if strict_mode else None,
                message=str(e),
                scope=scope,
                query_fingerprint=fingerprint,
            )
            record_sql_translation_outcome(outcome)
            if strict_mode:
                raise SQLTranslationError(f"Failed to translate SQL: {e}", outcome) from e
            self.logger.warning(f"Failed to translate SQL: {e}")
            return sql

    def get_tpc_base_dialect(self, benchmark_name: str) -> str:
        benchmark_lower = benchmark_name.lower()
        if benchmark_lower == "tpcds":
            return "netezza"
        else:
            return "netezza"
