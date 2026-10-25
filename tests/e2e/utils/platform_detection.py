from __future__ import annotations

import functools
import importlib
import os
import shutil
from typing import TYPE_CHECKING, Callable, TypeVar

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

LOCAL_PLATFORMS = frozenset({"duckdb", "sqlite", "datafusion", "postgresql", "timescaledb", "motherduck"})

CLOUD_PLATFORMS = frozenset(
    {
        "snowflake",
        "bigquery",
        "redshift",
        "athena",
        "databricks",
        "firebolt",
        "azure_synapse",
        "fabric_warehouse",
        "presto",
        "trino",
        "starburst",
        "clickhouse",
    }
)

DATAFRAME_PLATFORMS = frozenset(
    {
        "pandas-df",
        "polars-df",
        "dask-df",
        "pyspark-df",
        "cudf-df",
        "datafusion-df",
        "vaex-df",
        "ray-df",
    }
)

_PLATFORM_MODULES: dict[str, str | Sequence[str]] = {
    "duckdb": "duckdb",
    "sqlite": "sqlite3",
    "datafusion": "datafusion",
    "postgresql": "psycopg",
    "timescaledb": "psycopg",
    "motherduck": "duckdb",
    "snowflake": "snowflake.connector",
    "bigquery": "google.cloud.bigquery",
    "redshift": "redshift_connector",
    "athena": "pyathena",
    "databricks": "databricks.sql",
    "firebolt": "firebolt_db",
    "clickhouse": ("chdb", "clickhouse_driver"),
    "pandas-df": "pandas",
    "polars-df": "polars",
    "dask-df": "dask",
    "pyspark-df": "pyspark",
    "cudf-df": "cudf",
    "datafusion-df": "datafusion",
    "vaex-df": "vaex",
    "ray-df": "ray",
}

_CLOUD_CREDENTIAL_VARS: dict[str, Sequence[str]] = {
    "snowflake": ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"),
    "bigquery": ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"),
    "redshift": ("REDSHIFT_HOST", "REDSHIFT_USER"),
    "athena": ("AWS_ACCESS_KEY_ID", "AWS_REGION"),
    "databricks": ("DATABRICKS_HOST", "DATABRICKS_TOKEN"),
    "firebolt": ("FIREBOLT_CLIENT_ID", "FIREBOLT_CLIENT_SECRET"),
    "motherduck": ("MOTHERDUCK_TOKEN",),
    "starburst": ("STARBURST_HOST", "STARBURST_USER"),
}

F = TypeVar("F", bound=Callable[..., object])


def _check_module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def is_platform_available(platform: str) -> bool:
    modules = _PLATFORM_MODULES.get(platform)
    if modules is None:
        return False

    if isinstance(modules, str):
        return _check_module_available(modules)

    return any(_check_module_available(m) for m in modules)


def is_dataframe_available(platform: str) -> bool:
    if platform not in DATAFRAME_PLATFORMS:
        return False
    if not is_platform_available(platform):
        return False
    if platform == "pyspark-df" and shutil.which("java") is None:
        return False
    return True


def is_gpu_available() -> bool:
    if shutil.which("nvidia-smi") is None:
        return False

    try:
        importlib.import_module("cudf")

        return True
    except ImportError:
        return False


def has_cloud_credentials(platform: str) -> bool:
    try:
        from benchbox.security.credentials import CredentialManager

        creds = CredentialManager().get_platform_credentials(platform)
        metadata_keys = {"last_updated", "status", "error_message"}
        if creds and any(k not in metadata_keys for k in creds):
            return True
    except Exception:
        pass

    required_vars = _CLOUD_CREDENTIAL_VARS.get(platform)
    if required_vars is None:
        return False

    return all(os.environ.get(var) for var in required_vars)


def requires_platform(platform: str, reason: str | None = None) -> Callable[[F], F]:
    skip_reason = reason or f"Platform '{platform}' dependencies not available"

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if not is_platform_available(platform):
                pytest.skip(skip_reason)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def requires_dataframe(platform: str, reason: str | None = None) -> Callable[[F], F]:
    skip_reason = reason or f"DataFrame platform '{platform}' not available"

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if not is_dataframe_available(platform):
                pytest.skip(skip_reason)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def requires_gpu(reason: str | None = None) -> Callable[[F], F]:
    skip_reason = reason or "NVIDIA GPU with CUDA not available"

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if not is_gpu_available():
                pytest.skip(skip_reason)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def requires_cloud_credentials(platform: str, reason: str | None = None) -> Callable[[F], F]:
    skip_reason = reason or f"Cloud credentials for '{platform}' not configured"

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if not has_cloud_credentials(platform):
                pytest.skip(skip_reason)
            return func(*args, **kwargs)

        return wrapper

    return decorator


mark_e2e_local = pytest.mark.e2e_local
mark_e2e_cloud = pytest.mark.e2e_cloud
mark_e2e_dataframe = pytest.mark.e2e_dataframe
