from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_NO_ZSTD = (
    "SingleStore 10.8.1 LOAD DATA ingests only gzip (.gz) and lz4 (.lz4) compressed files "
    "(docs: LOAD DATA > Handling Data Compression, COMPRESSION {{ AUTO | NONE | LZ4 | GZIP }}); a .zst file is read "
    "as raw text and fails with \"Leaf Error (127.0.0.1:3307): In the input file, row 1 doesn't contain data for "
    'all columns" (error 1261), so a {size} zstd load cannot be timed without decompressing it outside the engine.'
)
_NO_BZIP2 = (
    "SingleStore 10.8.1 LOAD DATA ingests only gzip (.gz) and lz4 (.lz4) compressed files "
    "(docs: LOAD DATA > Handling Data Compression, COMPRESSION {{ AUTO | NONE | LZ4 | GZIP }}); a .bz2 file is "
    "accepted without an error but loads 0 rows, so a {size} bzip2 load cannot be timed without decompressing it "
    "outside the engine."
)

SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS = {
    f"bulk_load_csv_{size}_{codec}": template.format(size=size)
    for size in ("small", "medium", "large")
    for codec, template in (("zstd", _NO_ZSTD), ("bzip2", _NO_BZIP2))
}


def singlestore_write_primitive_skip_reason(operation) -> str | None:
    return SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS.get(operation.id)


for _query_id, _reason in SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.singlestore.write_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "singlestore",
        benchmark="write_primitives",
        query_id=_query_id,
    )
