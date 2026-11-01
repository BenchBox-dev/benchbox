from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    SetSessionPolicyPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

_P = Phase.QUERY_ADAPTER
_B = "tpcds"

_SETTING: tuple[tuple[str, str], ...] = (("joined_subquery_requires_alias", "0"),)
_REASON = (
    "ClickHouse requires explicit aliases on all FROM/JOIN subqueries; "
    "AST-based injection is unsafe for these queries (corrupts Q23 GROUP BY, "
    "inserts aliases inside Q87 EXCEPT/INTERSECT). "
    "Session setting joined_subquery_requires_alias=0 disables the requirement. "
    "This is a limitation of the SQL parser's AST rewriting, not of ClickHouse, so no upstream "
    "issue is linked (issue_url is unset). The affected queries are TPC-DS Q23 and Q87."
)

for _qid in ("23a", "23b", "87"):
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_adapter.clickhouse.tpcds.q{_qid}_joined_subquery_alias_policy",
            action=CompatAction.SET_SESSION_POLICY,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SetSessionPolicyPayload(
                settings=_SETTING,
                issue_url=None,
            ),
            reason=_REASON,
        ),
        _P,
        "clickhouse",
        benchmark=_B,
        query_id=_qid,
    )
