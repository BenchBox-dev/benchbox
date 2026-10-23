from __future__ import annotations

from typing import Any

from benchbox.utils.dialect_utils import translate_ddl

TPCDS_FOREIGN_KEY_DROP_REASON = (
    "TPC-DS generated data does not satisfy every declared foreign key, so the tables are created without them"
)


def is_tpcds_benchmark(benchmark: object) -> bool:
    name = getattr(benchmark, "_name", "") or type(benchmark).__name__
    return "TPC-DS" in str(name) or "TPCDS" in str(name)


def translate_schema_with_tpcds_fk_strip(adapter: Any, benchmark: object, target_dialect: str) -> str:
    schema_sql = adapter._create_schema_with_tuning(benchmark, source_dialect=target_dialect)
    strip = is_tpcds_benchmark(benchmark)
    if strip:
        config = adapter.get_effective_tuning_configuration() if adapter.tuning_enabled else None
        ledger = getattr(adapter, "_applied_tuning_ledger", None)
        if config is not None and config.foreign_keys.enabled and ledger is not None:
            ledger.record_dropped("foreign_keys", TPCDS_FOREIGN_KEY_DROP_REASON)
    statements = translate_ddl(schema_sql, target_dialect, "standard", strip_foreign_keys=strip)
    return ";\n\n".join(statements) + ";"
