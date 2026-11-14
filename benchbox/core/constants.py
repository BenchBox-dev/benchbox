# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

DEFAULT_OUTPUT_COMPRESSION_TYPE = "zstd"
NO_COMPRESSION_ENV_VAR = "BENCHBOX_NO_COMPRESSION"

TPCH_POWER_DEFAULT_WARMUP_ITERATIONS = 1
TPCH_POWER_DEFAULT_MEASUREMENT_ITERATIONS = 3

TPCDS_POWER_DEFAULT_WARMUP_ITERATIONS = 1
TPCDS_POWER_DEFAULT_MEASUREMENT_ITERATIONS = 3

TPCH_THROUGHPUT_DEFAULT_STREAMS = 2

TPCDS_THROUGHPUT_DEFAULT_STREAMS = 2

GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS = 1
GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS = 3

GENERIC_THROUGHPUT_DEFAULT_STREAMS = 2


RUN_MODES = ("sql", "dataframe")

VALID_PHASES = (
    "generate",
    "load",
    "statistics",
    "warmup",
    "power",
    "throughput",
    "maintenance",
)

QUERY_PHASES = ("power", "throughput", "maintenance")

EXECUTION_TYPES = (
    "standard",
    "power",
    "throughput",
    "maintenance",
    "combined",
    "load_only",
    "data_only",
)

EXPORT_FORMATS = ("json", "csv", "html")

MCP_RESULT_FORMATS = ("list", "details", *EXPORT_FORMATS, "text", "markdown")

MCP_MODE_CHOICES = (*RUN_MODES, "data_only")

MCP_DATA_ONLY_ALIASES = ("datagen", "generate")
