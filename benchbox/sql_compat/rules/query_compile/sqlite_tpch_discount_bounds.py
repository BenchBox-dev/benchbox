from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_compile.sqlite.tpch.q6_exact_discount_bounds",
        action=CompatAction.REWRITE_QUERY,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.SILENT_CORRUPTION,
        payload=RewriteQueryPayload(
            transformer_id="fold_between_literal_arithmetic",
            description="Fold isolated numeric literal Add/Sub l_discount BETWEEN bounds to exact numeric literals",
        ),
        reason="SQLite REAL arithmetic can exclude inclusive TPC-H Q6 discount endpoints; normalize exact literal bounds",
    ),
    Phase.QUERY_COMPILE,
    "sqlite",
    benchmark="tpch",
    query_id="6",
)
