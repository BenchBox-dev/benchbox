# ruff: noqa: SIM905

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from benchbox.core.results.environment import (
    NormalizedExecutionEnvironment,
    PlatformCloudMetadata,
    PlatformComputeMetadata,
    PlatformDeploymentMetadata,
    PlatformRuntimeEnvironment,
    PlatformStorageMetadata,
)
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.platforms.base.docker_runtime import (
    DockerCommandRunner,
    discover_docker_runtime,
    docker_request_from_config,
)

_EMBEDDED_CONNECTION_MODES = {"embedded", "file", "in-memory", "in_memory", "local_process", "memory"}
_LOCAL_PROCESS_CONNECTION_MODES = _EMBEDDED_CONNECTION_MODES | {"local"}
_SERVER_CONNECTION_MODES = {"cluster", "remote", "server", "tcp"}
_SERVERLESS_PLATFORMS = {"athena", "athena_spark", "bigquery", "dataproc_serverless", "emr_serverless"}
_MANAGED_CLOUD_PLATFORMS = set(
    "azure_synapse clickhouse_cloud databricks fabric_lakehouse fabric_warehouse firebolt motherduck onehouse "
    "redshift snowflake starburst".split()
)
_CLOUD_PROVIDER_BY_PLATFORM = dict(  # noqa: C408
    athena="aws",
    athena_spark="aws",
    bigquery="gcp",
    databricks=None,
    dataproc="gcp",
    dataproc_serverless="gcp",
    emr_serverless="aws",
    fabric_lakehouse="azure",
    fabric_warehouse="azure",
    redshift="aws",
    snowflake=None,
    azure_synapse="azure",
)
_STANDARD_PLATFORM_INFO_KEYS = set(
    "client_library_version connection_mode configuration engine_version engine_version_source execution_mode family "
    "host name platform platform_name platform_type platform_version port version".split()
)
_STATUS_RANK = {"not_requested": 0, "unavailable": 0, "error": 1, "partial": 2, "available": 3}
_SOURCE_RANK = {
    **dict.fromkeys(
        ("registered_default", "saved_config", "environment_variable", "cli_option", "runtime_override", "requested"), 2
    ),
    "unavailable": 0,
    "inferred": 1,
    "observed": 3,
}


def build_default_normalized_result_metadata(
    adapter: Any | None = None,
    *,
    connection: Any | None = None,
    platform_info: Mapping[str, Any] | None = None,
    platform_config: Mapping[str, Any] | None = None,
    execution_mode: str | None = None,
    docker_runner: DockerCommandRunner | None = None,
) -> dict[str, Any]:
    info = _collect_platform_info(adapter, connection, platform_info)
    config = _merge_platform_config(adapter, info, platform_config)
    mode = _normalize_token(execution_mode or info.get("execution_mode") or _adapter_execution_mode(adapter))
    platform_key = _platform_key(adapter, info)
    platform_name = _platform_display_name(adapter, info)
    connection_mode = _connection_mode(info, config)
    endpoint_class = _endpoint_class(info, config, mode)
    deployment_type = _deployment_type(platform_key, connection_mode, endpoint_class, info, config, mode)

    deployment = PlatformDeploymentMetadata(
        deployment_type=deployment_type,
        connection_mode=connection_mode,
        endpoint_class=endpoint_class,
        metadata_source="inferred",
        collection_status="partial",
        selected=getattr(adapter, "deployment_selected", None),
        selected_class=getattr(adapter, "deployment_selected_class", None),
    ).to_dict()

    execution_environment = _execution_environment_metadata(
        adapter=adapter,
        platform_name=platform_name,
        platform_key=platform_key,
        connection_mode=connection_mode,
        endpoint_class=endpoint_class,
        deployment_type=deployment_type,
        info=info,
        config=config,
        execution_mode=mode,
        docker_runner=docker_runner,
    )

    metadata: dict[str, Any] = {
        "execution_environment": execution_environment,
        "platform_deployment": deployment,
        "platform_raw_config": sanitize_platform_options(config, exclude_internal=True),
    }

    cloud = _cloud_metadata(platform_key, info, config)
    if cloud:
        metadata["platform_cloud"] = cloud
    compute = _compute_metadata(platform_key, info, config)
    if compute:
        metadata["platform_compute"] = compute
    storage = _storage_metadata(info, config)
    if storage:
        metadata["platform_storage"] = storage
    raw_metadata = _raw_platform_metadata(info)
    if raw_metadata:
        metadata["platform_raw_metadata"] = raw_metadata

    _merge_adapter_client_link(metadata, adapter)
    return _compact_mapping(metadata)


def collect_normalized_result_metadata(
    adapter: Any | None,
    *,
    connection: Any | None = None,
    platform_info: Mapping[str, Any] | None = None,
    platform_config: Mapping[str, Any] | None = None,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    hook = getattr(adapter, "get_normalized_result_metadata", None) if adapter is not None else None
    if callable(hook):
        try:
            metadata = hook(connection=connection, platform_info=platform_info)
        except TypeError:
            try:
                metadata = hook()
            except Exception as exc:
                return _metadata_hook_failure(exc)
        except Exception as exc:
            return _metadata_hook_failure(exc)
        if isinstance(metadata, Mapping):
            metadata_dict = dict(metadata)
            _merge_adapter_client_link(metadata_dict, adapter)
            return _compact_mapping(metadata_dict)

    return build_default_normalized_result_metadata(
        adapter,
        connection=connection,
        platform_info=platform_info,
        platform_config=platform_config,
        execution_mode=execution_mode,
    )


def _merge_adapter_client_link(metadata: dict[str, Any], adapter: Any | None) -> None:
    client_link = getattr(adapter, "_client_link_metadata", None)
    if not isinstance(client_link, Mapping) or not client_link:
        return
    exec_env = metadata.setdefault("execution_environment", {})
    if isinstance(exec_env, Mapping) and not isinstance(exec_env, dict):
        exec_env = dict(exec_env)
        metadata["execution_environment"] = exec_env
    if isinstance(exec_env, dict):
        existing = exec_env.get("client_link")
        if isinstance(existing, Mapping):
            exec_env["client_link"] = _merge_metadata_dicts(dict(existing), client_link)
        else:
            exec_env["client_link"] = dict(client_link)


def _metadata_hook_failure(exc: Exception) -> dict[str, Any]:
    return _compact_mapping(
        {
            "execution_environment": NormalizedExecutionEnvironment(
                platform_runtime=PlatformRuntimeEnvironment(
                    runtime_type="unknown",
                    collection_status="unavailable",
                    source="unavailable",
                    collection_error_class=type(exc).__name__,
                    collection_error_message=f"{type(exc).__name__}: result metadata hook failed",
                )
            ).to_dict(),
            "platform_deployment": PlatformDeploymentMetadata(
                deployment_type="unknown",
                endpoint_class="unknown",
                metadata_source="unavailable",
                collection_status="unavailable",
            ).to_dict(),
        }
    )


def apply_normalized_result_metadata(
    result: Any,
    metadata: Mapping[str, Any] | None,
) -> Any:
    if not metadata:
        return result

    _merge_result_attr(result, "execution_environment", metadata.get("execution_environment"))
    _merge_result_attr(result, "platform_deployment", metadata.get("platform_deployment"))
    _merge_result_attr(result, "platform_cloud", metadata.get("platform_cloud"))
    _merge_result_attr(result, "platform_compute", metadata.get("platform_compute"))
    _merge_result_attr(result, "platform_storage", metadata.get("platform_storage"))
    _merge_result_attr(result, "platform_raw_config", metadata.get("platform_raw_config"))
    _merge_result_attr(result, "platform_raw_metadata", metadata.get("platform_raw_metadata"))
    return result


def _execution_environment_metadata(
    *,
    adapter: Any | None,
    platform_name: str,
    platform_key: str,
    connection_mode: str | None,
    endpoint_class: str,
    deployment_type: str,
    info: Mapping[str, Any],
    config: Mapping[str, Any],
    execution_mode: str,
    docker_runner: DockerCommandRunner | None,
) -> dict[str, Any]:
    explicit_runtime = _mapping_value(info.get("platform_runtime")) or _mapping_value(config.get("platform_runtime"))
    explicit_container = _mapping_value(info.get("container")) or _mapping_value(config.get("container"))
    if explicit_runtime:
        return NormalizedExecutionEnvironment(
            platform_runtime=explicit_runtime,
            container=explicit_container,
        ).to_dict()

    if execution_mode == "dataframe":
        runtime = PlatformRuntimeEnvironment(
            runtime_type="dataframe_process",
            collection_status="partial",
            source="inferred",
            engine_host=platform_name,
        ).to_dict()
        return NormalizedExecutionEnvironment(platform_runtime=runtime).to_dict()

    if endpoint_class == "embedded_process" or _normalize_token(connection_mode) in _LOCAL_PROCESS_CONNECTION_MODES:
        runtime = PlatformRuntimeEnvironment(
            runtime_type="local_process",
            collection_status="partial",
            source="inferred",
            engine_host=platform_name,
        ).to_dict()
        return NormalizedExecutionEnvironment(platform_runtime=runtime).to_dict()

    if endpoint_class == "localhost_port":
        docker_metadata = discover_docker_runtime(
            docker_request_from_config(platform_key or platform_name, config),
            runner=docker_runner,
        )
        return NormalizedExecutionEnvironment(
            platform_runtime=docker_metadata.get("platform_runtime", {}),
            container=docker_metadata.get("container"),
        ).to_dict()

    if deployment_type == "serverless":
        runtime = PlatformRuntimeEnvironment(
            runtime_type="serverless",
            collection_status="partial",
            source="inferred",
            engine_host=_engine_host(info, config),
        ).to_dict()
        return NormalizedExecutionEnvironment(platform_runtime=runtime).to_dict()

    if deployment_type == "managed_cloud":
        runtime = PlatformRuntimeEnvironment(
            runtime_type="managed_cloud",
            collection_status="partial",
            source="inferred",
            engine_host=_engine_host(info, config),
        ).to_dict()
        return NormalizedExecutionEnvironment(platform_runtime=runtime).to_dict()

    if endpoint_class == "remote_host" or _normalize_token(connection_mode) in _SERVER_CONNECTION_MODES:
        runtime = PlatformRuntimeEnvironment(
            runtime_type="remote_server",
            collection_status="partial",
            source="inferred",
            engine_host=_engine_host(info, config),
        ).to_dict()
        return NormalizedExecutionEnvironment(platform_runtime=runtime).to_dict()

    return NormalizedExecutionEnvironment().to_dict()


def _cloud_metadata(platform_key: str, info: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    provider = _first_value(
        info,
        config,
        ("cloud_provider", "provider"),
    )
    if provider is None:
        provider = _CLOUD_PROVIDER_BY_PLATFORM.get(platform_key)

    region = _first_value(info, config, ("cloud_region", "region", "aws_region", "current_region"))
    location = _first_value(info, config, ("location", "dataset_location"))
    account = _first_value(info, config, ("account", "account_name", "organization_name"))
    project = _first_value(info, config, ("project", "project_id"))
    workspace = _first_value(info, config, ("workspace", "workspace_id", "server_hostname"))

    if not any((provider, region, location, account, project, workspace)):
        return {}

    return PlatformCloudMetadata(
        provider=str(provider).lower() if provider else None,
        region=str(region) if region else None,
        location=str(location) if location else None,
        account=str(account) if account else None,
        project=str(project) if project else None,
        workspace=str(workspace) if workspace else None,
        source="inferred" if provider and provider == _CLOUD_PROVIDER_BY_PLATFORM.get(platform_key) else "observed",
        collection_status="partial",
    ).to_dict()


def _canonical_compute_fields(platform_key: str, merged: Mapping[str, Any]) -> dict[str, Any]:
    from benchbox.core.compute_resource import compute_size_keys, compute_value_keys, resolve_resource_kind

    resource = _first_present(merged, compute_value_keys(platform_key))
    size = _first_present(merged, compute_size_keys(platform_key))
    kind = resolve_resource_kind(platform_key, merged)
    if resource is None and size is None:
        return {"resource": None, "resource_kind": None, "size": None}
    return {"resource": resource, "resource_kind": kind, "size": size}


def _compute_metadata(platform_key: str, info: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    compute = _mapping_value(info.get("compute_configuration")) or {}
    merged = {**config, **compute}
    canonical = _canonical_compute_fields(platform_key, merged)
    payload = PlatformComputeMetadata(
        warehouse=_first_present(merged, ("warehouse_name", "warehouse", "warehouse_id")),
        warehouse_size=_first_present(merged, ("warehouse_size", "cluster_size")),
        warehouse_type=_first_present(merged, ("warehouse_type",)),
        cluster_id=_first_present(merged, ("cluster_id", "cluster_identifier", "warehouse_id")),
        cluster_name=_first_present(merged, ("cluster_name", "cluster_identifier")),
        serverless_slots=_first_present(merged, ("serverless_slots", "slot_capacity", "slots")),
        rpu=_first_present(merged, ("rpu", "base_capacity")),
        node_type=_first_present(merged, ("node_type",)),
        node_count=_safe_int(_first_present(merged, ("node_count", "num_nodes", "number_of_nodes"))),
        worker_shape=_first_present(merged, ("worker_shape", "worker_node_type")),
        driver_shape=_first_present(merged, ("driver_shape", "driver_node_type")),
        cache_enabled=_first_bool(merged, ("cache_enabled", "query_cache_enabled")),
        result_cache_enabled=_first_bool(merged, ("result_cache_enabled",)),
        resource=canonical["resource"],
        resource_kind=canonical["resource_kind"],
        size=canonical["size"],
        source="observed" if compute else "requested",
        collection_status="partial",
    ).to_dict()
    return payload if _has_payload_value(payload, exclude={"source", "collection_status"}) else {}


def _storage_metadata(info: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    payload = PlatformStorageMetadata(
        table_format=_first_value(info, config, ("table_format", "format")),
        staging_location=_first_value(info, config, ("staging_location", "external_location")),
        bucket=_first_value(info, config, ("bucket", "storage_bucket", "s3_bucket", "gcs_bucket")),
        prefix=_first_value(info, config, ("prefix", "path_prefix", "s3_prefix", "gcs_prefix")),
        storage_tier=_first_value(info, config, ("storage_tier",)),
        compression=_first_value(info, config, ("compression", "compression_type")),
        cleanup_policy=_first_value(info, config, ("cleanup_policy",)),
        source="requested",
        collection_status="partial",
    ).to_dict()
    return payload if _has_payload_value(payload, exclude={"source", "collection_status"}) else {}


def _deployment_type(
    platform_key: str,
    connection_mode: str | None,
    endpoint_class: str,
    info: Mapping[str, Any],
    config: Mapping[str, Any],
    execution_mode: str,
) -> str:
    explicit = _first_value(info, config, ("deployment_type",))
    if explicit:
        explicit_key = _normalize_token(explicit)
        if explicit_key == "serverless":
            return "serverless"
        if explicit_key in {"cloud", "managed_cloud"}:
            return "managed_cloud"
        if explicit_key in {"local", "embedded", "dataframe", "self_hosted"}:
            return explicit_key

    mode = _normalize_token(connection_mode)
    if execution_mode == "dataframe":
        return "dataframe"
    if mode == "serverless" or platform_key in _SERVERLESS_PLATFORMS or _looks_serverless(info, config):
        return "serverless"
    if mode == "cloud" or platform_key in _MANAGED_CLOUD_PLATFORMS or _is_cloud_endpoint(info, config):
        return "managed_cloud"
    if endpoint_class in {"localhost_port", "remote_host"}:
        return "self_hosted"
    if mode in _SERVER_CONNECTION_MODES:
        return "self_hosted"
    if endpoint_class == "embedded_process" or mode in _LOCAL_PROCESS_CONNECTION_MODES:
        return "embedded"
    return "unknown"


def _endpoint_class(
    info: Mapping[str, Any],
    config: Mapping[str, Any],
    execution_mode: str,
) -> str:
    if execution_mode == "dataframe":
        return "embedded_process"

    connection_mode = _normalize_token(_connection_mode(info, config))
    if connection_mode in _EMBEDDED_CONNECTION_MODES:
        return "embedded_process"

    host = _engine_host(info, config)
    if host:
        host_value = str(host).strip().lower()
        host_name = _parse_host_name(host_value)
        if host_name in {"localhost", "127.0.0.1", "::1"}:
            return "localhost_port"
        if _is_cloud_host(host_value):
            return "cloud_endpoint"
        return "remote_host"

    if _is_cloud_endpoint(info, config):
        return "cloud_endpoint"
    return "unknown"


def _collect_platform_info(
    adapter: Any | None,
    connection: Any | None,
    explicit_info: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(explicit_info, Mapping):
        return dict(explicit_info)
    if adapter is None:
        return {}
    get_platform_info = getattr(adapter, "get_platform_info", None)
    if not callable(get_platform_info):
        return {}
    try:
        info = get_platform_info(connection)
    except TypeError:
        info = get_platform_info()
    except Exception:
        return {}
    return dict(info) if isinstance(info, Mapping) else {}


def _merge_platform_config(
    adapter: Any | None,
    info: Mapping[str, Any],
    explicit_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    config: dict[str, Any] = {}
    adapter_config = getattr(adapter, "platform_config", None) if adapter is not None else None
    if isinstance(adapter_config, Mapping):
        config.update(adapter_config)
    info_config = info.get("configuration")
    if isinstance(info_config, Mapping):
        config.update(info_config)
    if isinstance(explicit_config, Mapping):
        config.update(explicit_config)
    for key in ("host", "hostname", "server", "endpoint", "port"):
        if info.get(key) is not None and key not in config:
            config[key] = info[key]
    return config


def _raw_platform_metadata(info: Mapping[str, Any]) -> dict[str, Any]:
    raw = {
        str(key): value for key, value in info.items() if key not in _STANDARD_PLATFORM_INFO_KEYS and value is not None
    }
    return sanitize_platform_options(raw, exclude_internal=True)


def _platform_key(adapter: Any | None, info: Mapping[str, Any]) -> str:
    raw = info.get("platform_type") or info.get("platform") or info.get("platform_name")
    if raw is None and adapter is not None:
        raw = getattr(adapter, "platform_name", None) or type(adapter).__name__
    return _normalize_token(raw)


def _platform_display_name(adapter: Any | None, info: Mapping[str, Any]) -> str:
    raw = info.get("platform_name") or info.get("platform") or info.get("name")
    if raw is None and adapter is not None:
        raw = getattr(adapter, "platform_name", None) or type(adapter).__name__
    return str(raw or "unknown")


def _adapter_execution_mode(adapter: Any | None) -> str:
    if adapter is None:
        return "sql"
    module = type(adapter).__module__
    if ".dataframe." in module or hasattr(adapter, "family") and not hasattr(adapter, "create_connection"):
        return "dataframe"
    return "sql"


def _connection_mode(info: Mapping[str, Any], config: Mapping[str, Any]) -> str | None:
    value = info.get("connection_mode") or config.get("connection_mode") or config.get("deployment_mode")
    return str(value) if value is not None else None


def _engine_host(info: Mapping[str, Any], config: Mapping[str, Any]) -> Any:
    return _first_value(info, config, ("host", "hostname", "server", "endpoint", "server_hostname"))


def _is_cloud_endpoint(info: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    if _first_value(info, config, ("cloud_provider", "cloud_region", "workspace", "project_id")):
        return True
    host = _engine_host(info, config)
    return bool(host and _is_cloud_host(str(host).lower()))


def _is_cloud_host(host_value: str) -> bool:
    return any(
        marker in host_value
        for marker in (
            "amazonaws.com",
            "azuresynapse.net",
            "cloud",
            "databricks.com",
            "database.windows.net",
            "firebolt.io",
            "googleapis.com",
            "snowflakecomputing.com",
            "starburst",
        )
    )


def _looks_serverless(info: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    compute = _mapping_value(info.get("compute_configuration")) or {}
    merged = {**config, **compute}
    values = (
        merged.get("deployment_type"),
        merged.get("warehouse_type"),
        merged.get("cluster_mode"),
        merged.get("compute_type"),
    )
    if any("serverless" in str(value).lower() for value in values if value is not None):
        return True
    return bool(merged.get("enable_serverless_compute"))


def _parse_host_name(host_value: str) -> str:
    if host_value == "::1" or host_value.startswith("[::1]"):
        return "::1"
    parsed = urlparse(host_value if "://" in host_value else f"//{host_value}")
    if parsed.hostname:
        return parsed.hostname.lower()
    return host_value.split("/", 1)[0].split(":", 1)[0].lower()


def _first_value(
    info: Mapping[str, Any],
    config: Mapping[str, Any],
    keys: tuple[str, ...],
) -> Any:
    for key in keys:
        if info.get(key) not in (None, ""):
            return info[key]
        if config.get(key) not in (None, ""):
            return config[key]
    return None


def _first_present(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_bool(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> bool | None:
    value = _first_present(mapping, keys)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _compact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, child in value.items():
        if isinstance(child, Mapping):
            child = _compact_mapping(child)
        if child not in (None, {}, [], ()):
            compacted[str(key)] = child
    return compacted


def _has_payload_value(payload: Mapping[str, Any], *, exclude: set[str]) -> bool:
    return any(value not in (None, "", {}, [], ()) for key, value in payload.items() if key not in exclude)


def _merge_result_attr(result: Any, attr: str, incoming: Any) -> None:
    incoming_mapping = _mapping_value(incoming)
    if not incoming_mapping:
        return
    current = getattr(result, attr, None)
    if isinstance(current, Mapping):
        setattr(result, attr, _merge_metadata_dicts(dict(current), incoming_mapping))
    else:
        setattr(result, attr, incoming_mapping)


def _merge_metadata_dicts(current: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in incoming.items():
        if value in (None, "", {}, [], ()):
            continue
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_metadata_dicts(dict(existing), value)
        elif _should_replace_metadata_value(str(key), existing, value):
            merged[key] = value
    return merged


def _should_replace_metadata_value(key: str, existing: Any, incoming: Any) -> bool:
    if existing in (None, "", "unknown", "unavailable", "not_requested", {}, [], ()):
        return True
    if key == "collection_status":
        return _STATUS_RANK.get(str(incoming), 0) > _STATUS_RANK.get(str(existing), 0)
    if key in {"source", "metadata_source"}:
        return _SOURCE_RANK.get(str(incoming), 0) > _SOURCE_RANK.get(str(existing), 0)
    return False


__all__ = [
    "apply_normalized_result_metadata",
    "build_default_normalized_result_metadata",
    "collect_normalized_result_metadata",
]
