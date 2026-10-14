# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from benchbox.utils.dependencies import check_platform_dependencies, get_dependency_error_message

from ._dependencies import (
    FLIGHTSQL_AVAILABLE,
    INFLUXDB3_AVAILABLE,
    INFLUXDB_AVAILABLE,
)
from .adapter import InfluxDBAdapter
from .client import InfluxDBConnection, to_line_protocol
from .metadata import InfluxDBMetadataMixin
from .setup import InfluxDBSetupMixin

__all__ = [
    "InfluxDBAdapter",
    "InfluxDBConnection",
    "InfluxDBMetadataMixin",
    "InfluxDBSetupMixin",
    "INFLUXDB_AVAILABLE",
    "INFLUXDB3_AVAILABLE",
    "FLIGHTSQL_AVAILABLE",
    "to_line_protocol",
    "check_platform_dependencies",
    "get_dependency_error_message",
]
