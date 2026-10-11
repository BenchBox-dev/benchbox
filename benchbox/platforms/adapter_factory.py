# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import warnings
from collections.abc import Mapping
from typing import Any, Literal, Optional

from benchbox.core.deployment import DEPLOYMENT_ALIAS_KEYS, resolve_deployment
from benchbox.core.execution_engine import DEFAULT_EXECUTION_ENGINE, resolve_requested
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base.adapter import check_isolation_capability
from benchbox.platforms.clickhouse.deployment_mode import (
    CLICKHOUSE_CANONICAL_PLATFORM_NAMES,
    CLICKHOUSE_LEGACY_SELECTOR_MAP,
    clickhouse_legacy_selector_warning,
)
from benchbox.utils.runtime_env import ensure_driver_version


def _resolve_clickhouse_legacy(platform: str) -> tuple[str, Optional[str]]:
    lp = platform.lower().strip()

    if lp in CLICKHOUSE_CANONICAL_PLATFORM_NAMES:
        return platform, None

    if lp in CLICKHOUSE_LEGACY_SELECTOR_MAP:
        target = CLICKHOUSE_LEGACY_SELECTOR_MAP[lp]
        return target, clickhouse_legacy_selector_warning(platform, target)

    if lp == "clickhouse":
        raise ValueError(
            "Platform 'clickhouse' has been removed. Use 'clickhouse-local' "
            "(embedded chDB), 'clickhouse-server' (self-hosted), or "
            "'clickhouse-cloud' (managed). The explicit 'clickhouse:local', "
            "'clickhouse:server', and 'clickhouse:cloud' selectors also remain available."
        )

    return platform, None


def _normalize_platform_name(platform: str) -> tuple[str, bool, Optional[str]]:
    platform_lower = platform.lower()
    deployment_mode: Optional[str] = None
    df_mode_implied = False

    if ":" in platform_lower:
        base_part, deployment_mode = platform_lower.rsplit(":", 1)
        platform_lower = base_part

    if platform_lower.endswith("-df"):
        platform_lower = platform_lower[:-3]
        df_mode_implied = True

    return platform_lower, df_mode_implied, deployment_mode


def reject_removed_platform(platform: str) -> None:
    selector = platform.lower().strip().split(":", 1)[0]
    if selector in {"modin", "modin-df"}:
        raise ValueError(
            "Platform 'modin' has been removed because no compatible Modin release is available. "
            "Use 'pandas-df' for pandas-compatible execution or 'dask-df' for distributed DataFrames."
        )


_reject_removed_platform = reject_removed_platform


def get_adapter(
    platform: str,
    mode: Optional[Literal["sql", "dataframe"]] = None,
    deployment: Optional[str] = None,
    **config: Any,
) -> Any:
    _reject_removed_platform(platform)

    resolved_platform, migration_warning = _resolve_clickhouse_legacy(platform)
    if migration_warning:
        warnings.warn(migration_warning, DeprecationWarning, stacklevel=2)
    if resolved_platform != platform:
        platform = resolved_platform
        deployment = None

    base_platform, df_mode_implied, deployment_from_name = _normalize_platform_name(platform)

    caps = PlatformRegistry.get_platform_capabilities(base_platform)
    if caps is None:
        raise ValueError(f"Unknown platform: {platform}")

    if mode is not None:
        resolved_mode = mode
    elif df_mode_implied:
        resolved_mode = "dataframe"
    else:
        resolved_mode = caps.default_mode

    if not PlatformRegistry.supports_mode(base_platform, resolved_mode):
        mode_support = []
        if caps.supports_sql:
            mode_support.append("sql")
        if caps.supports_dataframe:
            mode_support.append("dataframe")
        supported = ", ".join(mode_support) if mode_support else "none"
        raise ValueError(f"Platform '{platform}' does not support {resolved_mode} mode. Supported modes: {supported}")

    if base_platform in DEPLOYMENT_ALIAS_KEYS:
        resolution = _resolve_family_deployment(base_platform, deployment, deployment_from_name, config)
        if resolution.explicit and resolution.selected is not None:
            config["deployment_mode"] = resolution.selected
    else:
        resolved_deployment = _resolve_deployment_mode(base_platform, deployment, deployment_from_name, caps)

        if resolved_deployment is not None:
            if not PlatformRegistry.supports_deployment_mode(base_platform, resolved_deployment):
                available = PlatformRegistry.get_available_deployment_modes(base_platform)
                if available:
                    available_str = ", ".join(available)
                    raise ValueError(
                        f"Platform '{base_platform}' does not support deployment mode '{resolved_deployment}'. "
                        f"Available: {available_str}"
                    )
                else:
                    raise ValueError(
                        f"Platform '{base_platform}' does not support deployment modes. "
                        f"Remove the ':{resolved_deployment}' suffix."
                    )

        if resolved_deployment is not None:
            config["deployment_mode"] = resolved_deployment

    requested_engine = config.pop("execution_engine", None) or "default"
    resolve_requested(base_platform, requested_engine)

    if resolved_mode == "sql":
        if requested_engine != DEFAULT_EXECUTION_ENGINE:
            config["execution_engine"] = requested_engine
        return _get_sql_adapter(base_platform, **config)
    return _get_dataframe_adapter(base_platform, execution_engine=requested_engine, **config)


def _resolve_family_deployment(
    platform: str,
    explicit_deployment: Optional[str],
    deployment_from_name: Optional[str],
    config: dict[str, Any],
):
    nested = config.get("options")
    nested_options = dict(nested) if isinstance(nested, Mapping) else {}
    values = {**nested_options, **config}
    marker = values.get("_explicit_platform_options")
    explicit = set(marker.keys()) if isinstance(marker, Mapping) else None
    selector = explicit_deployment if explicit_deployment is not None else deployment_from_name
    return resolve_deployment(platform, selector, values, explicit)


def _resolve_deployment_mode(
    platform: str,
    explicit_deployment: Optional[str],
    deployment_from_name: Optional[str],
    caps: Any,
) -> Optional[str]:
    if not caps.deployment_modes:
        if explicit_deployment or deployment_from_name:
            return explicit_deployment or deployment_from_name
        return None

    if explicit_deployment is not None:
        return explicit_deployment

    if deployment_from_name is not None:
        return deployment_from_name

    return caps.default_deployment


def _get_sql_adapter(platform: str, **config: Any) -> Any:
    from benchbox.platforms import get_platform_adapter

    return get_platform_adapter(platform, **config)


def _get_dataframe_adapter(platform: str, **config: Any) -> Any:
    import benchbox.platforms.dataframe as _df

    requested_engine = config.pop("execution_engine", None) or DEFAULT_EXECUTION_ENGINE

    if platform in {"polars", "dask"}:
        config["execution_engine"] = requested_engine
    driver_package = config.pop("driver_package", None)
    driver_version = config.pop("driver_version", None) or config.pop("driver_version_requested", None)
    driver_version_resolved = config.pop("driver_version_resolved", None)
    driver_auto_install = bool(config.pop("driver_auto_install", False))
    config.pop("driver_auto_install_used", None)

    platform_info = PlatformRegistry.get_platform_info(platform)
    package_hint = driver_package or (platform_info.driver_package if platform_info else None)
    requested_version = driver_version
    resolution = ensure_driver_version(
        package_name=package_hint,
        requested_version=requested_version,
        auto_install=driver_auto_install,
        install_hint=platform_info.installation_command if platform_info else None,
    )
    resolved_version = resolution.resolved or driver_version_resolved
    requested = driver_version or resolution.requested

    from benchbox.platforms import _DATAFRAME_PLATFORM_INFO

    dataframe_spelling = f"{platform}-df"
    adapter_info = _DATAFRAME_PLATFORM_INFO.get(dataframe_spelling)
    if adapter_info is None:
        available = ", ".join(sorted(name.removesuffix("-df") for name in _DATAFRAME_PLATFORM_INFO))
        raise ValueError(f"Unknown DataFrame platform: {platform}. Available: {available}")

    adapter_name, availability_name, install_cmd = adapter_info
    adapter_class = getattr(_df, adapter_name, None)
    is_available = bool(getattr(_df, availability_name, False))

    if not is_available or adapter_class is None:
        raise ImportError(
            f"DataFrame platform '{platform}' is not available. Install required dependencies: {install_cmd}"
        )

    check_isolation_capability(adapter_class, platform, resolution.runtime_strategy)

    adapter = adapter_class(**config)
    adapter.driver_package = resolution.package or package_hint
    adapter.driver_version_requested = requested
    adapter.driver_version_resolved = resolved_version
    adapter.driver_version_actual = resolution.actual
    adapter.driver_runtime_strategy = resolution.runtime_strategy
    adapter.driver_runtime_path = resolution.runtime_path
    adapter.driver_runtime_python_executable = resolution.runtime_python_executable
    adapter.driver_auto_install_used = resolution.auto_install_used

    return adapter


def is_dataframe_mode(platform: str, mode: Optional[str] = None) -> bool:
    if mode is not None:
        return mode == "dataframe"

    base_platform, df_mode_implied, _ = _normalize_platform_name(platform)
    if df_mode_implied:
        return True

    return PlatformRegistry.get_default_mode(base_platform) == "dataframe"


def get_available_modes(platform: str) -> list[str]:
    base_platform, _, _ = _normalize_platform_name(platform)
    caps = PlatformRegistry.get_platform_capabilities(base_platform)
    if caps is None:
        return []

    modes = []
    if caps.supports_sql:
        modes.append("sql")
    if caps.supports_dataframe:
        modes.append("dataframe")
    return modes


def get_available_deployments(platform: str) -> list[str]:
    base_platform, _, _ = _normalize_platform_name(platform)
    return PlatformRegistry.get_available_deployment_modes(base_platform)


def get_default_deployment(platform: str) -> Optional[str]:
    base_platform, _, _ = _normalize_platform_name(platform)
    return PlatformRegistry.get_default_deployment(base_platform)
