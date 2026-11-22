# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Any

from benchbox.utils.run_deadline import RunTimeoutError as RunTimeoutError


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
