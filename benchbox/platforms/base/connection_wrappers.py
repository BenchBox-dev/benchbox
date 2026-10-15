from __future__ import annotations

import inspect
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchbox.core.expected_results.models import ValidationMode
    from benchbox.platforms.base.adapter import PlatformAdapter

logger = logging.getLogger(__name__)


class DriverIsolationCapability(Enum):
    SUPPORTED = "supported"
    NOT_APPLICABLE = "not_applicable"
    NOT_FEASIBLE = "not_feasible"
    FEASIBLE_CLIENT_ONLY = "feasible_client_only"


class StreamConnectionCapability(Enum):
    SHARED_CURSOR = "shared_cursor"
    INDEPENDENT_CONNECTION = "independent_connection"
    UNSUPPORTED = "unsupported"


_ISOLATION_REMEDIATION = {
    DriverIsolationCapability.NOT_APPLICABLE: (
        "This platform has no versioned driver package. Driver version isolation is not applicable."
    ),
    DriverIsolationCapability.NOT_FEASIBLE: (
        "This platform's driver has technical constraints that prevent isolated runtime binding "
        "(e.g. JVM dependency, native C library, shared library conflicts). "
        "Install the requested driver version into the active environment instead."
    ),
    DriverIsolationCapability.FEASIBLE_CLIENT_ONLY: (
        "This platform's Python client can be version-isolated, but the client version is independent "
        "from the engine/service version. Install the requested client version into the active "
        "environment, or omit driver_version to use the current client."
    ),
}


def check_isolation_capability(
    adapter_class: type,
    platform_name: str,
    runtime_strategy: str,
) -> None:
    capability = getattr(adapter_class, "driver_isolation_capability", DriverIsolationCapability.NOT_APPLICABLE)
    if runtime_strategy != "isolated-site-packages" or capability == DriverIsolationCapability.SUPPORTED:
        return

    remediation = _ISOLATION_REMEDIATION.get(capability, "Driver version isolation is not supported.")
    raise RuntimeError(
        f"Platform '{platform_name}' requested runtime strategy '{runtime_strategy}', "
        f"but adapter '{adapter_class.__name__}' does not support isolated driver runtime binding "
        f"(capability: {capability.value}). {remediation}"
    )


def resolve_stream_connection_capability(adapter: Any) -> tuple[StreamConnectionCapability, bool]:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST
    from benchbox.platforms.base.adapter import PlatformAdapter

    cls = adapter if isinstance(adapter, type) else type(adapter)
    manifest_entries = [entry.adapter for entry in PLATFORM_MANIFEST if entry.adapter is not None]
    for klass in cls.__mro__:
        if klass is PlatformAdapter:
            break
        for spec in manifest_entries:
            if spec.class_name == klass.__name__ and (
                klass.__module__ == spec.module or klass.__module__.startswith(f"{spec.module}.")
            ):
                return StreamConnectionCapability(spec.stream_connection_capability), True
        if "stream_connection_capability" in klass.__dict__:
            value = klass.__dict__["stream_connection_capability"]
            if not isinstance(value, StreamConnectionCapability):
                raise RuntimeError(
                    f"Adapter '{cls.__name__}' declares stream_connection_capability={value!r}, "
                    "which is not a StreamConnectionCapability. Declare one of SHARED_CURSOR, "
                    "INDEPENDENT_CONNECTION, or UNSUPPORTED."
                )
            return value, True
    return StreamConnectionCapability.SHARED_CURSOR, False


def require_throughput_stream_capability(
    adapter: Any, *, platform_name: str | None = None, connection: Any = None
) -> StreamConnectionCapability:
    from benchbox.platforms.base.adapter import PlatformAdapter

    if platform_name is None:
        adapter_name = getattr(adapter, "platform_name", None)
        platform_name = (
            adapter_name
            if isinstance(adapter_name, str)
            else getattr(adapter, "__name__", None) or type(adapter).__name__
        )
    capability, declared = resolve_stream_connection_capability(adapter)
    if not declared:
        raise RuntimeError(
            f"Platform '{platform_name}' has no explicit stream_connection_capability declaration. "
            "Throughput is refused before stream submission; set SHARED_CURSOR in the platform manifest "
            "only after reviewing the driver's concurrent-session guarantees, or set "
            "INDEPENDENT_CONNECTION/UNSUPPORTED as appropriate."
        )
    if capability is StreamConnectionCapability.UNSUPPORTED:
        raise RuntimeError(
            f"Platform '{platform_name}' declares stream_connection_capability=UNSUPPORTED: "
            "this engine cannot provide concurrent throughput streams with isolated sessions. "
            "Throughput is refused before stream submission instead of silently sharing one "
            "connection across streams. To support throughput, implement per-stream sessions via "
            "PlatformAdapter.new_stream_connection() and set INDEPENDENT_CONNECTION in the platform "
            "manifest (see StreamConnectionCapability equivalence dimensions); to keep throughput "
            "unavailable, leave this declaration in place and run power/single-stream tests instead."
        )
    if capability is StreamConnectionCapability.INDEPENDENT_CONNECTION:
        adapter_cls = adapter if isinstance(adapter, type) else type(adapter)
        if adapter_cls.new_stream_connection is PlatformAdapter.new_stream_connection:
            raise RuntimeError(
                f"Platform '{platform_name}' declares stream_connection_capability="
                "INDEPENDENT_CONNECTION but does not override new_stream_connection() to open an "
                "independent per-stream connection/session. Declaring the capability without the "
                "override would fail on the first stream; fix the adapter (not the runner) by "
                "overriding new_stream_connection() per the StreamConnectionCapability contract."
            )
    ensure_supported = getattr(adapter, "ensure_stream_sessions_supported", None)
    if callable(ensure_supported):
        ensure_supported(connection)
    return capability


def open_stream_connection(adapter: Any, connection: Any, benchmark_type: str) -> Any:
    method = adapter.new_stream_connection
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_keyword = any(
        parameter.name == "benchmark_type" or parameter.kind is parameter.VAR_KEYWORD for parameter in parameters
    )
    if accepts_keyword:
        return method(connection, benchmark_type=benchmark_type)
    return method(connection)


class _NoCloseProxy:
    __slots__ = ("_conn",)

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def close(self) -> None:
        pass


def _make_stream_cursor(connection: Any) -> Any:
    if hasattr(connection, "cursor"):
        return connection.cursor()
    return _NoCloseProxy(connection)


class PlatformAdapterConnection:
    def __init__(
        self,
        connection: Any,
        platform_adapter: PlatformAdapter,
        maintenance_mode: bool = False,
        validation_mode: ValidationMode | None = None,
    ):
        self.connection = connection
        self.platform_adapter = platform_adapter
        self.connection_string = getattr(connection, "connection_string", "platform_adapter_connection")
        self.dialect = getattr(platform_adapter, "get_target_dialect", lambda: "standard")()
        self._maintenance_mode = maintenance_mode
        self._validate_row_count = True
        self.validation_mode = validation_mode

        self.benchmark_type: str | None = None
        self.scale_factor: float | None = None
        self._current_query_id: str = "unknown_query"
        self._current_stream_id: int | None = None

    def set_query_context(self, query_id: str, stream_id: int | None = None) -> None:
        self._current_query_id = query_id
        self._current_stream_id = stream_id

    def execute(self, query: str, parameters: tuple | list | None = None):
        if self._maintenance_mode:
            if parameters:
                return self.connection.execute(query, parameters)
            else:
                return self.connection.execute(query)

        if parameters:
            return self.connection.execute(query, parameters)

        from benchbox.core.validation.query_validation import validation_mode_context

        with validation_mode_context(self.validation_mode):
            result = self.platform_adapter.execute_query(
                self.connection,
                query,
                self._current_query_id,
                benchmark_type=self.benchmark_type,
                scale_factor=self.scale_factor,
                validate_row_count=self._validate_row_count,
                stream_id=self._current_stream_id,
            )
        return PlatformAdapterCursor(result)

    def commit(self):
        transaction_connection = self.connection
        if not hasattr(transaction_connection, "commit"):
            transaction_connection = getattr(transaction_connection, "connection", transaction_connection)
        if hasattr(transaction_connection, "commit"):
            transaction_connection.commit()

    def rollback(self):
        transaction_connection = self.connection
        if not hasattr(transaction_connection, "rollback"):
            transaction_connection = getattr(transaction_connection, "connection", transaction_connection)
        if hasattr(transaction_connection, "rollback"):
            transaction_connection.rollback()

    def close(self):
        if hasattr(self.connection, "close"):
            self.connection.close()


class PlatformAdapterCursor:
    def __init__(self, platform_result: dict):
        self.platform_result = platform_result
        self._rows: list | None = None
        self._has_placeholder_padding: bool = False
        self._placeholder_kind: str | None = None
        self._warned_placeholder_materialization: bool = False

    def _extract_rows(self):
        explicit_rows = self.platform_result.get("rows")
        if isinstance(explicit_rows, (list, tuple)):
            return list(explicit_rows)

        first_row = self.platform_result.get("first_row")
        row_count = self.platform_result.get("rows_returned")
        if isinstance(row_count, int):
            if row_count <= 0:
                return []
            if first_row is not None:
                padding = row_count - 1
                if padding > 0:
                    self._has_placeholder_padding = True
                    self._placeholder_kind = "first_row+padding"
                return [first_row] + [(None,)] * padding
            self._has_placeholder_padding = True
            self._placeholder_kind = "rows_returned_only"
            return [(None,)] * row_count

        if first_row is not None:
            return [first_row]

        return []

    @property
    def has_real_rows(self) -> bool:
        return isinstance(self.platform_result.get("rows"), (list, tuple))

    @property
    def rows(self):
        if self._rows is None:
            self._rows = self._extract_rows()
        self._warn_if_placeholder_materialized()
        return self._rows

    def _warn_if_placeholder_materialized(self) -> None:
        if not self._has_placeholder_padding or self._warned_placeholder_materialization:
            return
        self._warned_placeholder_materialization = True
        query_id = self.platform_result.get("query_id", "unknown")
        logger.warning(
            "PlatformAdapterCursor materialized placeholder rows for query_id=%s "
            "(kind=%s): the platform result carried only a row count (and, in the "
            "first_row+padding case, one sampled row), so fetchall()/fetchone()/.rows "
            "fabricated (None,) placeholders to preserve cardinality. These are NOT "
            "real platform values - check has_real_rows before trusting them, or use "
            "row_count()/count_query_rows() if only the count is needed. "
            "(This warning fires once per cursor.)",
            query_id,
            self._placeholder_kind,
        )

    def fetchall(self):
        return self.rows

    def fetchone(self):
        rows = self.rows
        return rows[0] if rows else None

    def row_count(self) -> int:
        return count_query_rows(self)


def count_query_rows(cursor: Any) -> int:
    platform_result = getattr(cursor, "platform_result", None)
    if isinstance(platform_result, dict):
        explicit_rows = platform_result.get("rows")
        if isinstance(explicit_rows, (list, tuple)):
            return len(explicit_rows)
        reported = platform_result.get("rows_returned")
        if isinstance(reported, int) and reported >= 0:
            return reported

    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount >= 0:
        return rowcount

    if hasattr(cursor, "__iter__"):
        return sum(1 for _ in cursor)

    if hasattr(cursor, "fetchall"):
        return len(cursor.fetchall())

    return 0
