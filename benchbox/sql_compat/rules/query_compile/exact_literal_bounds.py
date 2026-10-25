from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, RewriteQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_ENGINE_LITERAL_TYPE = {"sqlite": "REAL", "clickhouse": "Float64"}

for _platform, _literal_type in _ENGINE_LITERAL_TYPE.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_compile.{_platform}.all.exact_literal_bounds",
            action=CompatAction.REWRITE_QUERY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.SILENT_CORRUPTION,
            payload=RewriteQueryPayload(
                transformer_id="fold_between_literal_arithmetic",
                description="Fold literal-only Add/Sub BETWEEN bounds and comparison operands to exact decimal literals",
            ),
            reason=(
                f"{_platform} evaluates decimal literals as {_literal_type}, so literal arithmetic in BETWEEN bounds "
                "and comparisons drops rows equal to a bound; normalize to exact decimal literals for any table and column"
            ),
        ),
        Phase.QUERY_COMPILE,
        _platform,
    )
