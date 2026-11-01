from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import BlockBenchmarkPayload, CompatibilityDecision, FailureMode, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_GATES = {
    "metadata_primitives": (
        "Metadata primitives relies on INFORMATION_SCHEMA catalog tables such as schemata, tables, columns, views, "
        "and table_constraints. Apache Spark does not expose those catalog relations, failing metadata queries "
        "with `TABLE_OR_VIEW_NOT_FOUND`.",
        "Apache Spark does not expose the INFORMATION_SCHEMA catalog contract required by metadata_primitives.",
    ),
}

for _benchmark, (_block_reason, _reason) in _GATES.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"benchmark_gate.spark.{_benchmark}.unsupported",
            action=CompatAction.BLOCK_BENCHMARK,
            support_level=SupportLevel.BLOCKED,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=BlockBenchmarkPayload(reason=_block_reason),
            reason=_reason,
        ),
        Phase.BENCHMARK_GATE,
        "spark",
        benchmark=_benchmark,
    )
