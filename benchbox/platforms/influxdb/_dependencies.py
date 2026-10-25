# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

try:
    from influxdb_client_3 import InfluxDBClient3

    INFLUXDB3_AVAILABLE = True
except ImportError:
    InfluxDBClient3 = None
    INFLUXDB3_AVAILABLE = False

try:
    from flightsql import FlightSQLClient

    FLIGHTSQL_AVAILABLE = True
except ImportError:
    FlightSQLClient = None
    FLIGHTSQL_AVAILABLE = False

try:
    import pyarrow

    PYARROW_AVAILABLE = True
except ImportError:
    pyarrow = None
    PYARROW_AVAILABLE = False

INFLUXDB_AVAILABLE = INFLUXDB3_AVAILABLE or FLIGHTSQL_AVAILABLE

__all__ = [
    "InfluxDBClient3",
    "FlightSQLClient",
    "pyarrow",
    "INFLUXDB3_AVAILABLE",
    "FLIGHTSQL_AVAILABLE",
    "PYARROW_AVAILABLE",
    "INFLUXDB_AVAILABLE",
]
