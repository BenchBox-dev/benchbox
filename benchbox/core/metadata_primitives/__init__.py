# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from .benchmark import (
    AclBenchmarkResult,
    AclMutationResult,
    ComplexityBenchmarkResult,
    MetadataBenchmarkResult,
    MetadataPrimitivesBenchmark,
    MetadataQueryResult,
)
from .complexity import (
    COMPLEXITY_PRESETS,
    AclGrant,
    ConstraintDensity,
    GeneratedMetadata,
    MetadataComplexityConfig,
    PermissionDensity,
    RoleHierarchyDepth,
    TypeComplexity,
    get_complexity_preset,
)
from .dataframe_operations import (
    DASK_METADATA_CAPABILITIES,
    DATAFUSION_METADATA_CAPABILITIES,
    OPERATION_CATEGORIES,
    PANDAS_METADATA_CAPABILITIES,
    POLARS_METADATA_CAPABILITIES,
    PYSPARK_METADATA_CAPABILITIES,
    DataFrameMetadataCapabilities,
    DataFrameMetadataOperationsManager,
    DataFrameMetadataResult,
    MetadataOperationCategory,
    MetadataOperationType,
    UnsupportedOperationError,
    get_dataframe_metadata_manager,
    get_platform_capabilities,
    get_unsupported_message,
)
from .generator import MetadataGenerator
from .queries import MetadataPrimitivesQueryManager

__all__ = [
    "AclBenchmarkResult",
    "AclMutationResult",
    "ComplexityBenchmarkResult",
    "MetadataBenchmarkResult",
    "MetadataPrimitivesBenchmark",
    "MetadataPrimitivesQueryManager",
    "MetadataQueryResult",
    "COMPLEXITY_PRESETS",
    "AclGrant",
    "ConstraintDensity",
    "GeneratedMetadata",
    "MetadataComplexityConfig",
    "MetadataGenerator",
    "PermissionDensity",
    "RoleHierarchyDepth",
    "TypeComplexity",
    "get_complexity_preset",
    "DASK_METADATA_CAPABILITIES",
    "DATAFUSION_METADATA_CAPABILITIES",
    "DataFrameMetadataCapabilities",
    "DataFrameMetadataOperationsManager",
    "DataFrameMetadataResult",
    "MetadataOperationCategory",
    "MetadataOperationType",
    "OPERATION_CATEGORIES",
    "PANDAS_METADATA_CAPABILITIES",
    "POLARS_METADATA_CAPABILITIES",
    "PYSPARK_METADATA_CAPABILITIES",
    "UnsupportedOperationError",
    "get_dataframe_metadata_manager",
    "get_platform_capabilities",
    "get_unsupported_message",
]
