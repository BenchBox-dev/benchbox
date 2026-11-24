# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from benchbox.platforms.base.config_utils import make_platform_config_builder
from benchbox.platforms.databend.adapter import DatabendAdapter

_build_databend_config = make_platform_config_builder(
    "databend",
    __name__,
    "Databend",
    "databend-driver",
    [
        "host",
        "port",
        "username",
        "password",
        "database",
        "dsn",
        "compute_resource",
        "ssl",
        "disable_result_cache",
    ],
)


__all__ = ["DatabendAdapter", "_build_databend_config"]
