from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="questdb",
    rule_name="strip_fk_and_pk_constraints",
    transformer_id="translate_ddl:strip_foreign_keys_and_primary_keys",
    description="Remove FOREIGN KEY and PRIMARY KEY constraint clauses from CREATE TABLE; "
    "QuestDB 9.3.4 rejects both with 'unsupported column type: KEY'. "
    "Also strips inline column-level REFERENCES clauses (QuestDB-specific).",
    reason="QuestDB does not support FOREIGN KEY or PRIMARY KEY constraint syntax. "
    "QuestDB 9.3.4 raises 'Schema statement failed: unsupported column type: KEY' "
    "when either is present in a CREATE TABLE statement.",
)
