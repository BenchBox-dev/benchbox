from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Literal, TypeAlias
from urllib.parse import urlparse

from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.utils.environment import detect_execution_class

MetadataSource: TypeAlias = Literal[
    "registered_default",
    "saved_config",
    "environment_variable",
    "cli_option",
    "runtime_override",
    "requested",
    "observed",
    "inferred",
    "unavailable",
]
CPUIdentityProvenance: TypeAlias = Literal["measured", "user_attested", "inferred"]
CollectionStatus: TypeAlias = Literal["available", "partial", "unavailable", "error", "not_requested"]
RuntimeType: TypeAlias = Literal[
    "local_process",
    "docker_container",
    "remote_server",
    "managed_cloud",
    "serverless",
    "dataframe_process",
    "unknown",
]
EndpointClass: TypeAlias = Literal["localhost_port", "remote_host", "cloud_endpoint", "embedded_process", "unknown"]


def _is_empty(value: Any) -> bool:
    return value is None or value == {} or value == [] or value == ()


def _compact(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        compacted = {}
        for key, child in value.items():
            child_value = _compact(child)
            if not _is_empty(child_value):
                compacted[key] = child_value
        return compacted
    if isinstance(value, list):
        compacted_list = []
        for item in value:
            compacted_item = _compact(item)
            if not _is_empty(compacted_item):
                compacted_list.append(compacted_item)
        return compacted_list
    return value


def _metadata_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return result if isinstance(result, dict) else {}
    if is_dataclass(value) and not isinstance(value, type):
        return _compact(asdict(value))
    if isinstance(value, Mapping):
        return _compact(value)
    return {}


def system_profile_snapshot(system_profile: Any | None) -> dict[str, Any]:
    if isinstance(system_profile, Mapping):
        return dict(system_profile)
    if hasattr(system_profile, "model_dump"):
        dumped = system_profile.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


@dataclass
class ClientHostEnvironment:
    os: str | None = None
    arch: str | None = None
    cpu_count: int | None = None
    memory_gb: float | int | None = None
    python: str | None = None
    machine_id: str | None = None

    cpu_model: str | None = None
    cpu_vendor: str | None = None
    cpu_identity_provenance: CPUIdentityProvenance | None = None

    execution_class: str | None = None
    container_runtime: str | None = None
    container_host_os: str | None = None
    execution_class_source: str | None = None

    @classmethod
    def from_system_profile(cls, system_profile: Any | None) -> ClientHostEnvironment:
        profile = system_profile_snapshot(system_profile)
        os_type = profile.get("os_type") or profile.get("os") or profile.get("os_name")
        os_release = profile.get("os_release") or profile.get("os_version")
        os_name = f"{os_type} {os_release}".strip() if os_type and os_release else os_type
        cpu_model = profile.get("cpu_model")
        cpu_vendor = profile.get("cpu_vendor")
        cpu_identity_provenance = profile.get("cpu_identity_provenance")
        if system_profile is None and (not cpu_model or not cpu_vendor):
            from benchbox.utils.environment import detect_cpu_info

            detected_model, detected_vendor = detect_cpu_info()
            if not cpu_model:
                cpu_model = detected_model
                if detected_model:
                    cpu_identity_provenance = "measured"
            if not cpu_vendor:
                cpu_vendor = detected_vendor

        recorded_class = {
            key: profile.get(key)
            for key in ("execution_class", "container_runtime", "container_host_os", "execution_class_source")
            if profile.get(key)
        }
        execution = detect_execution_class() if system_profile is None else recorded_class

        return cls(
            os=os_name,
            arch=profile.get("architecture") or profile.get("arch"),
            cpu_count=profile.get("cpu_count") or profile.get("cpu_cores_logical") or profile.get("cpu_cores"),
            memory_gb=profile.get("memory_gb") or profile.get("memory_total_gb") or profile.get("total_memory_gb"),
            python=profile.get("python_version") or profile.get("python"),
            machine_id=profile.get("machine_id") or profile.get("anonymous_machine_id"),
            cpu_model=cpu_model,
            cpu_vendor=cpu_vendor,
            cpu_identity_provenance=cpu_identity_provenance,
            execution_class=execution.get("execution_class"),
            container_runtime=execution.get("container_runtime"),
            container_host_os=execution.get("container_host_os"),
            execution_class_source=execution.get("execution_class_source"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class PlatformRuntimeEnvironment:
    runtime_type: RuntimeType = "unknown"
    collection_status: CollectionStatus = "unavailable"
    source: MetadataSource = "unavailable"
    os: str | None = None
    os_release: str | None = None
    arch: str | None = None
    engine_host: str | None = None
    collection_error_class: str | None = None
    collection_error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ContainerEnvironment:
    collection_status: CollectionStatus = "unavailable"
    source: MetadataSource = "unavailable"
    docker_engine_version: str | None = None
    image: str | None = None
    image_digest: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    os: str | None = None
    arch: str | None = None
    requested_platform: str | None = None
    cpu_limit: str | int | float | None = None
    memory_limit: str | int | float | None = None
    port_mappings: dict[str, Any] = field(default_factory=dict)
    bind_mounts: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)
    collection_errors: list[dict[str, Any]] = field(default_factory=list)
    host_port_matched: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ClientLinkEnvironment:
    collection_status: CollectionStatus = "unavailable"
    source: MetadataSource = "unavailable"
    client_region: str | None = None
    client_cloud: str | None = None
    statement_overhead_ms: dict[str, Any] | None = None
    probed_at: str | None = None
    collection_error_class: str | None = None
    collection_error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class NormalizedExecutionEnvironment:
    client_host: ClientHostEnvironment | dict[str, Any] | None = None
    platform_runtime: PlatformRuntimeEnvironment | dict[str, Any] = field(default_factory=PlatformRuntimeEnvironment)
    container: ContainerEnvironment | dict[str, Any] | None = None
    client_link: ClientLinkEnvironment | dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.client_host is not None:
            payload["client_host"] = _metadata_dict(self.client_host)
        payload["platform_runtime"] = _metadata_dict(self.platform_runtime)
        if self.container is not None:
            payload["container"] = _metadata_dict(self.container)
        if self.client_link is not None:
            payload["client_link"] = _metadata_dict(self.client_link)
        return _compact(payload)


@dataclass
class PlatformDeploymentMetadata:
    deployment_type: str = "unknown"
    connection_mode: str | None = None
    endpoint_class: EndpointClass = "unknown"
    metadata_source: MetadataSource = "unavailable"
    collection_status: CollectionStatus = "unavailable"
    selected: str | None = None
    selected_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class PlatformCloudMetadata:
    provider: str | None = None
    region: str | None = None
    location: str | None = None
    account: str | None = None
    project: str | None = None
    workspace: str | None = None
    source: MetadataSource = "unavailable"
    collection_status: CollectionStatus = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class PlatformComputeMetadata:
    warehouse: str | None = None
    warehouse_size: str | None = None
    warehouse_type: str | None = None
    cluster_id: str | None = None
    cluster_name: str | None = None
    serverless_slots: int | float | None = None
    rpu: int | float | None = None
    node_type: str | None = None
    node_count: int | None = None
    worker_shape: str | None = None
    driver_shape: str | None = None
    cache_enabled: bool | None = None
    result_cache_enabled: bool | None = None
    source: MetadataSource = "unavailable"
    collection_status: CollectionStatus = "unavailable"
    resource: str | None = None
    resource_kind: str | None = None
    size: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class PlatformStorageMetadata:
    table_format: str | None = None
    staging_location: str | None = None
    bucket: str | None = None
    prefix: str | None = None
    storage_tier: str | None = None
    compression: str | None = None
    cleanup_policy: str | None = None
    source: MetadataSource = "unavailable"
    collection_status: CollectionStatus = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


_VM_BACKED_DOCKER_HOSTS = {"Darwin": "macOS", "Windows": "Windows"}


def _docker_hosted_execution_class(
    platform_runtime: Mapping[str, Any], container: Mapping[str, Any], client_host: Mapping[str, Any]
) -> dict[str, str]:
    host_family = str(client_host.get("os") or "").split(maxsplit=1)[0] if client_host.get("os") else ""
    if (
        platform_runtime.get("runtime_type") != "docker_container"
        or platform_runtime.get("collection_status") != "available"
        or platform_runtime.get("source") != "observed"
        or container.get("host_port_matched") is not True
        or host_family not in _VM_BACKED_DOCKER_HOSTS
        or client_host.get("execution_class") == "container"
    ):
        return {}
    return {
        "execution_class": "container",
        "container_runtime": "docker",
        "container_host_os": _VM_BACKED_DOCKER_HOSTS[host_family],
        "execution_class_source": "observed",
    }


def build_environment_payload(
    *,
    system_profile: Any | None,
    execution_environment: NormalizedExecutionEnvironment | Mapping[str, Any] | None,
) -> dict[str, Any]:
    explicit = _metadata_dict(execution_environment)
    profile_client_host = ClientHostEnvironment.from_system_profile(system_profile).to_dict()
    explicit_client_host = _metadata_dict(explicit.get("client_host"))
    client_host = {**profile_client_host, **explicit_client_host}
    platform_runtime = _metadata_dict(explicit.get("platform_runtime")) or PlatformRuntimeEnvironment().to_dict()
    container = _metadata_dict(explicit.get("container"))
    client_link = _metadata_dict(explicit.get("client_link"))
    client_host = {**client_host, **_docker_hosted_execution_class(platform_runtime, container, client_host)}

    payload: dict[str, Any] = dict(client_host)
    if client_host:
        payload["client_host"] = client_host
    if platform_runtime:
        payload["platform_runtime"] = platform_runtime
    if container:
        payload["container"] = container
    if client_link:
        payload["client_link"] = client_link
    return _compact(payload)


def _sanitize_metadata_block(
    value: Any,
    *,
    default: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    converted = _metadata_dict(value)
    if not converted and default is not None:
        converted = dict(default)
    if not converted:
        return {}
    return sanitize_platform_options(converted)


def build_platform_metadata_payload(
    *,
    platform_info: Mapping[str, Any] | None,
    platform_config: Mapping[str, Any] | None,
    deployment: PlatformDeploymentMetadata | Mapping[str, Any] | None,
    cloud: PlatformCloudMetadata | Mapping[str, Any] | None,
    compute: PlatformComputeMetadata | Mapping[str, Any] | None,
    storage: PlatformStorageMetadata | Mapping[str, Any] | None,
    raw_config: Mapping[str, Any] | None,
    raw_metadata: Mapping[str, Any] | None,
    sanitize_raw_config: bool = True,
) -> dict[str, Any]:
    _ = sanitize_raw_config
    default_deployment = _default_deployment_metadata(platform_info or {}, platform_config or {})
    payload: dict[str, Any] = {
        "deployment": _sanitize_metadata_block(deployment, default=default_deployment.to_dict()),
        "cloud": _sanitize_metadata_block(cloud, default=PlatformCloudMetadata().to_dict()),
        "compute": _sanitize_metadata_block(compute, default=PlatformComputeMetadata().to_dict()),
        "storage": _sanitize_metadata_block(storage, default=PlatformStorageMetadata().to_dict()),
    }
    raw_config_payload = _sanitize_metadata_block(raw_config)
    if raw_config_payload:
        payload["raw_config"] = raw_config_payload
    raw_metadata_payload = _sanitize_metadata_block(raw_metadata)
    if raw_metadata_payload:
        payload["raw_metadata"] = raw_metadata_payload
    return _compact(payload)


def _default_deployment_metadata(
    platform_info: Mapping[str, Any],
    platform_config: Mapping[str, Any],
) -> PlatformDeploymentMetadata:
    connection_mode = platform_info.get("connection_mode") or platform_config.get("connection_mode")
    endpoint_class = _classify_endpoint(connection_mode, platform_config)
    has_partial_metadata = bool(connection_mode) or endpoint_class != "unknown"
    return PlatformDeploymentMetadata(
        connection_mode=str(connection_mode) if connection_mode is not None else None,
        endpoint_class=endpoint_class,
        metadata_source="inferred" if has_partial_metadata else "unavailable",
        collection_status="partial" if has_partial_metadata else "unavailable",
    )


def _classify_endpoint(
    connection_mode: Any,
    platform_config: Mapping[str, Any],
) -> EndpointClass:
    mode = str(connection_mode).lower() if connection_mode is not None else ""
    if mode in {"embedded", "in-memory", "in_memory", "file", "local_process", "memory"}:
        return "embedded_process"

    host = _first_config_value(platform_config, ("host", "hostname", "server", "endpoint"))
    if host is None:
        return "unknown"

    host_value = str(host).strip().lower()
    host_name = _parse_host_name(host_value)
    if host_name in {"localhost", "127.0.0.1", "::1"}:
        return "localhost_port"
    if any(marker in host_value for marker in ("cloud", "snowflakecomputing.com", "databricks.com")):
        return "cloud_endpoint"
    return "remote_host"


def _parse_host_name(host_value: str) -> str:
    if host_value == "::1" or host_value.startswith("[::1]"):
        return "::1"
    parsed = urlparse(host_value if "://" in host_value else f"//{host_value}")
    if parsed.hostname:
        return parsed.hostname.lower()
    return host_value.split("/", 1)[0].split(":", 1)[0].lower()


def _first_config_value(platform_config: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = platform_config.get(key)
        if value:
            return value
    return None


__all__ = [
    "ClientHostEnvironment",
    "ClientLinkEnvironment",
    "CollectionStatus",
    "ContainerEnvironment",
    "EndpointClass",
    "MetadataSource",
    "NormalizedExecutionEnvironment",
    "PlatformCloudMetadata",
    "PlatformComputeMetadata",
    "PlatformDeploymentMetadata",
    "PlatformRuntimeEnvironment",
    "PlatformStorageMetadata",
    "RuntimeType",
    "build_environment_payload",
    "build_platform_metadata_payload",
]
