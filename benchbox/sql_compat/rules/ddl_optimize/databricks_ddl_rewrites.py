from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    RewriteDDLPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY
from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="databricks",
    rule_name="convert_to_delta_table",
    transformer_id="translate_ddl:default_layout",
    description="Convert DuckDB-style DDL to Databricks Delta Lake or Apache Hudi format: "
    "CREATE OR REPLACE TABLE, USING DELTA or USING HUDI, with TBLPROPERTIES settings",
    reason="Databricks requires Delta Lake or Hudi DDL: CREATE TABLE must use CREATE OR REPLACE TABLE "
    "for idempotency, tables must declare USING DELTA or USING HUDI, and auto-optimize TBLPROPERTIES "
    "improve write performance when delta_auto_optimize is enabled (Delta only).",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="ddl_optimize.databricks.transaction_primitives.txn_staging_catalog_managed",
        action=CompatAction.REWRITE_DDL,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=RewriteDDLPayload(
            transformer_id="txn_staging_catalog_managed",
            description="Transaction Primitives staging DDL declares the catalogManaged Delta table "
            "feature: USING DELTA TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')",
            governance_only=True,
        ),
        reason="Databricks multi-statement transactions only write Delta tables with the catalogManaged "
        "feature (TRANSACTION_NOT_SUPPORTED.WRITE_NON_CATALOG_MANAGED_TABLE otherwise); the txn_* staging "
        "tables are the only tables these transactions write, so the schema emitter declares the feature "
        "when the registry resolves this benchmark-scoped rule.",
    ),
    Phase.DDL_OPTIMIZE,
    "databricks",
    benchmark="transaction_primitives",
)
