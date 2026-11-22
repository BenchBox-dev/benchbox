# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Any


class BenchBoxError(Exception):
    pass


class InsufficientMemoryError(BenchBoxError):
    pass


class ConfigurationError(BenchBoxError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ReadOnlyPlatformError(BenchBoxError):
    pass


class RunTimeoutError(Exception):
    def __init__(self, timeout_seconds: int, phase: str, query_id: str | None = None, detail: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.phase = phase
        self.query_id = query_id
        self.detail = detail
        self.partial_result: Any = None
        where = phase if query_id is None else f"{phase}, at query {query_id}"
        message = f"The run reached its {timeout_seconds}-second limit (timeout_seconds) during {where}"
        super().__init__(f"{message}: {detail}" if detail else message)
