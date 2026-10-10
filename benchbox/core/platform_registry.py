# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import argparse
import importlib
import os
from collections import Counter
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from benchbox.core.platform_manifest import (
    SUPPORT_STATUS_VALUES,
    SupportStatus,
    get_adapter_imports,
    get_all_platform_aliases,
    get_platform_aliases,
    get_platform_manifest_entry,
    get_platform_metadata,
    is_valid_platform_key,
)
from benchbox.core.schemas import LibraryInfo, PlatformInfo
from benchbox.platforms.base import PlatformAdapter

CostClass = Literal["free", "paid_credits", "paid_compute"]
OptionalAdapterImportStatus = Literal[
    "available",
    "missing_optional_dependency",
    "native_library_load_failure",
    "broken_adapter_import",
    "deprecated_platform",
    "intentionally_disabled",
    "not_configured",
]

SNOWFLAKE_DEFAULT_OUTPUT_LOCATION = "@~/benchbox"

_NATIVE_IMPORT_ERROR_MARKERS = (
    "dlopen",
    "dylib",
    "cannot open shared object file",
    "image not found",
    "library not loaded",
    "undefined symbol",
    "symbol not found",
    "dll load failed",
    "failed to map segment",
)


def _is_internal_module_miss(missing_name: str, module_path: str | None = None) -> bool:
    if module_path is not None and (missing_name == module_path or missing_name.startswith(f"{module_path}.")):
        return True
    return missing_name == "benchbox" or missing_name.startswith("benchbox.")


@dataclass(frozen=True)
class OptionalAdapterDiagnostic:
    platform_name: str
    module_path: str
    class_name: str
    status: OptionalAdapterImportStatus
    support_status: Optional[SupportStatus] = None
    available: bool = False
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_name": self.platform_name,
            "module_path": self.module_path,
            "class_name": self.class_name,
            "status": self.status,
            "support_status": self.support_status,
            "available": self.available,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass
class DeploymentCapability:
    mode: Literal["local", "self-hosted", "managed"]
    requires_credentials: bool = False
    requires_cloud_storage: bool = False
    requires_network: bool = False
    default_for_platform: bool = False
    display_name: str = ""
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    auth_methods: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionEngineCapability:
    engine_class: str
    display_name: str = ""
    description: str = ""
    dependencies: tuple[str, ...] = ()
    selectable: bool = True


@dataclass(frozen=True)
class GatewayRouteCapability:
    engine_class: str


@dataclass(frozen=True)
class GatewayCapability:
    requires_host: bool
    routes_to: dict[str, GatewayRouteCapability] = field(default_factory=dict)


@dataclass
class PlatformCapability:
    supports_sql: bool = False
    supports_dataframe: bool = False
    default_mode: Literal["sql", "dataframe"] = "sql"
    deployment_modes: dict[str, DeploymentCapability] = field(default_factory=dict)
    default_deployment: str = "local"
    execution_engines: dict[str, ExecutionEngineCapability] = field(default_factory=dict)
    platform_family: Optional[str] = None
    inherits_from: Optional[str] = None
    cost_class: CostClass = "free"
    unsupported_benchmarks: dict[str, str] = field(default_factory=dict)
    gateways: dict[str, GatewayCapability] = field(default_factory=dict)


class PlatformRegistry:
    _adapters: dict[str, type[PlatformAdapter]] = {}
    _availability_cache: Optional[dict[str, bool]] = None
    _platform_metadata: dict[str, dict[str, Any]] = {}
    _auto_registered: bool = False
    _platform_aliases: dict[str, str] = get_platform_aliases("registry")

    @classmethod
    def resolve_platform_name(cls, platform_name: str) -> str:
        normalized = platform_name.lower()
        return cls._platform_aliases.get(normalized, normalized)

    @classmethod
    def get_all_aliases(cls) -> dict[str, str]:
        return cls._platform_aliases.copy()

    @classmethod
    def _build_platform_metadata(cls) -> dict[str, dict[str, Any]]:
        return get_platform_metadata()

    @classmethod
    def _ensure_registered(cls) -> None:
        if not cls._auto_registered:
            cls._auto_registered = True
            auto_register_platforms()

    @classmethod
    def register_adapter(cls, platform_name: str, adapter_class: type[PlatformAdapter]) -> None:
        normalized = platform_name.lower()
        if not is_valid_platform_key(normalized):
            raise ValueError(f"Platform {platform_name!r} is not a valid canonical adapter key")
        if normalized in get_all_platform_aliases():
            raise ValueError(f"Platform alias {platform_name!r} cannot be used as an adapter registration key")
        canonical_name = normalized
        entry = get_platform_manifest_entry(canonical_name)
        if entry is not None and entry.adapter is None:
            raise ValueError(f"Built-in platform {platform_name!r} has no runtime adapter registration")
        if not isinstance(adapter_class, type) or not issubclass(adapter_class, PlatformAdapter):
            raise TypeError(f"Adapter registered for {canonical_name!r} must subclass PlatformAdapter")

        existing = cls._adapters.get(canonical_name)
        if existing is not None and existing is not adapter_class:
            raise ValueError(
                f"Platform {canonical_name!r} is already registered with {existing.__module__}.{existing.__name__}"
            )
        cls._adapters[canonical_name] = adapter_class
        cls._availability_cache = None
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

    @classmethod
    def get_adapter_class(cls, platform_name: str) -> type[PlatformAdapter]:
        cls._ensure_registered()
        canonical_name = cls.resolve_platform_name(platform_name)

        if canonical_name not in cls._adapters:
            available = ", ".join(cls.get_available_platforms())
            raise ValueError(f"Platform '{platform_name}' not registered. Available: {available}")
        return cls._adapters[canonical_name]

    @classmethod
    def create_adapter(cls, platform_name: str, config: dict[str, Any]) -> PlatformAdapter:
        adapter_class = cls.get_adapter_class(platform_name)
        return adapter_class.from_config(config)

    @classmethod
    def add_platform_arguments(cls, parser: argparse.ArgumentParser, platform_name: str) -> None:
        adapter_class = cls.get_adapter_class(platform_name)
        adapter_class.add_cli_arguments(parser)

    @classmethod
    def get_available_platforms(cls) -> list[str]:
        cls._ensure_registered()
        return list(cls._adapters.keys())

    @classmethod
    def _detect_library(cls, lib_spec: dict[str, Any]) -> LibraryInfo:
        lib_name = lib_spec["name"]
        import_name = lib_spec.get("import_name", lib_name)

        original_cwd = os.getcwd()
        try:
            module = importlib.import_module(import_name)
            version = getattr(module, "__version__", None)
            if version is not None and not isinstance(version, str):
                if hasattr(version, "version"):
                    version = version.version
                elif hasattr(version, "VERSION"):
                    version = version.VERSION
                else:
                    version = None
            if version is not None and not isinstance(version, str):
                version = str(version) if version else None
            return LibraryInfo(name=lib_name, version=version, installed=True)
        except (ImportError, OSError) as e:
            return LibraryInfo(name=lib_name, version=None, installed=False, import_error=str(e))
        finally:
            os.chdir(original_cwd)

    @staticmethod
    def _extract_requirement_package(requirement: str) -> Optional[str]:

        if not requirement:
            return None

        requirement = requirement.strip()
        if "(" in requirement and ")" in requirement and " " in requirement:
            return requirement.split(" ", 1)[0]

        separators = [" ", "<", ">", "=", "!", "~"]
        package = requirement
        for sep in separators:
            if sep in package:
                package = package.split(sep, 1)[0]
        package = package.strip()
        return package or None

    @classmethod
    def get_platform_availability(cls) -> dict[str, bool]:
        cls._ensure_registered()
        if cls._availability_cache is not None:
            return cls._availability_cache.copy()

        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        availability = {}
        for platform_name in cls._adapters:
            if platform_name in cls._platform_metadata:
                platform_spec = cls._platform_metadata[platform_name]
                available = True

                for lib_spec in platform_spec.get("libraries", []):
                    lib_info = cls._detect_library(lib_spec)
                    if (
                        lib_spec.get("required", True)
                        and not lib_info.installed
                        and not lib_spec.get("alternative", False)
                    ):
                        available = False
                        break

                availability[platform_name] = available
            else:
                try:
                    adapter_class = cls._adapters[platform_name]
                    test_config = {"database_path": ":memory:"} if platform_name == "duckdb" else {}
                    adapter_class(**test_config)
                    availability[platform_name] = True
                except (ImportError, OSError):
                    availability[platform_name] = False
                except Exception:
                    availability[platform_name] = True

        cls._availability_cache = availability
        return availability.copy()

    @classmethod
    def is_platform_available(cls, platform_name: str) -> bool:
        availability = cls.get_platform_availability()
        return availability.get(platform_name, False)

    @classmethod
    def get_platform_info(cls, platform_name: str) -> Optional[PlatformInfo]:
        cls._ensure_registered()
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        canonical_name = cls.resolve_platform_name(platform_name)

        if canonical_name not in cls._platform_metadata:
            return None

        platform_spec = cls._platform_metadata[canonical_name]

        libraries = []
        available = True

        for lib_spec in platform_spec.get("libraries", []):
            lib_info = cls._detect_library(lib_spec)
            libraries.append(lib_info)

            if lib_spec.get("required", True) and not lib_info.installed and not lib_spec.get("alternative", False):
                available = False

        if "driver_package" in platform_spec:
            driver_package = platform_spec["driver_package"]
        else:
            requirements = platform_spec.get("requirements", [])
            driver_package = cls._extract_requirement_package(requirements[0]) if requirements else None

        return PlatformInfo(
            name=canonical_name,
            display_name=platform_spec["display_name"],
            description=platform_spec["description"],
            libraries=libraries,
            available=available,
            enabled=available and canonical_name in cls._adapters,
            requirements=deepcopy(platform_spec["requirements"]),
            installation_command=platform_spec["installation_command"],
            adoption=platform_spec.get("adoption", "niche"),
            support_status=platform_spec.get("support_status"),
            category=platform_spec.get("category", "database"),
            supports=deepcopy(platform_spec.get("supports", [])),
            driver_package=driver_package,
        )

    @classmethod
    def get_platform_requirements(cls, platform_name: str) -> str:
        info = cls.get_platform_info(platform_name)
        if info:
            return info.installation_command

        requirements_map = {
            "duckdb": "uv add duckdb",
            "databricks": "uv add databricks-sql-connector",
            "clickhouse": "uv add benchbox --extra clickhouse",
            "clickhouse-local": "uv add benchbox --extra clickhouse-local",
            "clickhouse-server": "uv add benchbox --extra clickhouse-server",
            "clickhouse-cloud": "uv add benchbox --extra clickhouse-cloud",
            "sqlite": "Built-in (no additional requirements)",
            "bigquery": "uv add google-cloud-bigquery",
            "redshift": "uv add redshift-connector",
            "snowflake": "uv add snowflake-connector-python",
        }
        return requirements_map.get(platform_name, "Unknown requirements")

    @classmethod
    def get_platforms_by_category(cls, category: str) -> list[str]:
        cls._ensure_registered()
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        return [
            name
            for name, spec in cls._platform_metadata.items()
            if spec.get("category") == category and name in cls._adapters
        ]

    @classmethod
    def get_platforms_by_adoption(cls, tier: str) -> list[str]:
        cls._ensure_registered()
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        return [
            name
            for name, spec in cls._platform_metadata.items()
            if spec.get("adoption", "niche") == tier and name in cls._adapters
        ]

    @classmethod
    def requires_cloud_storage(cls, platform_name: str) -> bool:
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        metadata = cls._platform_metadata.get(platform_name.lower(), {})
        return metadata.get("category") == "cloud"

    @classmethod
    def get_cloud_path_examples(cls, platform_name: str) -> list[str]:
        examples = {
            "databricks": [
                "dbfs:/Volumes/catalog/schema/volume/benchbox",
                "s3://my-bucket/benchbox/data",
                "abfss://container@storage.dfs.core.windows.net/benchbox",
                "gs://my-bucket/benchbox/data",
            ],
            "bigquery": [
                "gs://my-bucket/benchbox/data",
            ],
            "snowflake": [
                SNOWFLAKE_DEFAULT_OUTPUT_LOCATION,
                "s3://my-bucket/benchbox/data",
                "azure://my-container/benchbox/data",
                "gcs://my-bucket/benchbox/data",
            ],
            "redshift": [
                "s3://my-bucket/benchbox/data",
            ],
            "trino": [
                "s3://my-bucket/benchbox/data",
                "gs://my-bucket/benchbox/data",
                "abfss://container@storage.dfs.core.windows.net/benchbox",
            ],
        }
        return examples.get(platform_name.lower(), [])

    @classmethod
    def clear_cache(cls) -> None:
        cls._availability_cache = None

    @classmethod
    def _get_cached_platform_metadata(cls) -> dict[str, dict[str, Any]]:
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()
        return cls._platform_metadata

    @classmethod
    def get_all_platform_metadata(cls) -> dict[str, dict[str, Any]]:
        return deepcopy(cls._get_cached_platform_metadata())

    @classmethod
    def get_platform_names(cls) -> list[str]:
        return list(cls._get_cached_platform_metadata())

    @classmethod
    def get_platform_support_status(cls, platform_name: str) -> Optional[SupportStatus]:
        metadata = cls._get_cached_platform_metadata()
        canonical_name = cls.resolve_platform_name(platform_name)
        platform_spec = metadata.get(canonical_name)
        if platform_spec is None:
            return None
        return platform_spec["support_status"]

    @classmethod
    def get_platforms_by_support_status(cls, status: SupportStatus) -> list[str]:
        if status not in SUPPORT_STATUS_VALUES:
            raise ValueError(f"Unknown support_status {status!r}. Expected one of: {', '.join(SUPPORT_STATUS_VALUES)}")

        metadata = cls._get_cached_platform_metadata()
        return sorted(name for name, spec in metadata.items() if spec["support_status"] == status)

    @classmethod
    def get_platform_count_summary(cls) -> dict[str, Any]:
        metadata = cls._get_cached_platform_metadata()
        status_counts = Counter(spec["support_status"] for spec in metadata.values())
        category_counts = Counter(spec.get("category", "unknown") for spec in metadata.values())
        sql_capable = sum(1 for spec in metadata.values() if spec.get("capabilities", {}).get("supports_sql", False))
        dataframe_capable = sum(
            1 for spec in metadata.values() if spec.get("capabilities", {}).get("supports_dataframe", False)
        )
        dual_mode = sum(
            1
            for spec in metadata.values()
            if spec.get("capabilities", {}).get("supports_sql", False)
            and spec.get("capabilities", {}).get("supports_dataframe", False)
        )
        dataframe_only = sum(
            1
            for spec in metadata.values()
            if not spec.get("capabilities", {}).get("supports_sql", False)
            and spec.get("capabilities", {}).get("supports_dataframe", False)
        )

        return {
            "total": len(metadata),
            "sql_capable": sql_capable,
            "dataframe_capable": dataframe_capable,
            "dual_mode": dual_mode,
            "dataframe_only": dataframe_only,
            "support_status": {status: status_counts.get(status, 0) for status in SUPPORT_STATUS_VALUES},
            "category": dict(sorted(category_counts.items())),
        }

    @classmethod
    def classify_optional_import_error(
        cls,
        exc: BaseException,
        *,
        module_path: str | None = None,
    ) -> OptionalAdapterImportStatus:
        message = str(exc).lower()
        if isinstance(exc, ModuleNotFoundError):
            missing_name = exc.name or ""
            if _is_internal_module_miss(missing_name, module_path):
                return "broken_adapter_import"
            return "missing_optional_dependency"
        if "no module named" in message:
            return "missing_optional_dependency"
        if isinstance(exc, OSError) or any(marker in message for marker in _NATIVE_IMPORT_ERROR_MARKERS):
            return "native_library_load_failure"
        return "broken_adapter_import"

    @classmethod
    def diagnose_optional_adapter_imports(
        cls,
        platform_names: Optional[Iterable[str]] = None,
    ) -> dict[str, dict[str, Any]]:
        requested = None
        if platform_names is not None:
            requested = {cls.resolve_platform_name(platform_name) for platform_name in platform_names}

        diagnostics: dict[str, dict[str, Any]] = {}
        for name, module_path, class_name in _OPTIONAL_ADAPTERS:
            if requested is not None and name not in requested:
                continue
            diagnostics[name] = _diagnose_optional_adapter_entry(name, module_path, class_name).to_dict()

        if requested is not None:
            missing = requested - set(diagnostics)
            for name in sorted(missing):
                diagnostics[name] = OptionalAdapterDiagnostic(
                    platform_name=name,
                    module_path="",
                    class_name="",
                    status="not_configured",
                    support_status=cls.get_platform_support_status(name),
                    error_message="Platform is not configured for optional adapter registration.",
                ).to_dict()

        return diagnostics

    @classmethod
    def detect_library(cls, lib_spec: dict[str, Any]) -> LibraryInfo:
        return cls._detect_library(lib_spec)

    @classmethod
    def get_platform_capabilities(cls, platform_name: str) -> Optional[PlatformCapability]:
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        canonical_name = cls.resolve_platform_name(platform_name)
        metadata = cls._platform_metadata.get(canonical_name)
        if metadata is None:
            return None

        caps = metadata.get("capabilities", {})

        deployment_modes: dict[str, DeploymentCapability] = {}
        deployment_data = caps.get("deployment_modes", {})
        for mode_name, mode_spec in deployment_data.items():
            deployment_modes[mode_name] = DeploymentCapability(
                mode=mode_spec.get("mode", "local"),
                requires_credentials=mode_spec.get("requires_credentials", False),
                requires_cloud_storage=mode_spec.get("requires_cloud_storage", False),
                requires_network=mode_spec.get("requires_network", False),
                default_for_platform=mode_spec.get("default_for_platform", False),
                display_name=mode_spec.get("display_name", ""),
                description=mode_spec.get("description", ""),
                dependencies=deepcopy(mode_spec.get("dependencies", [])),
                auth_methods=deepcopy(mode_spec.get("auth_methods", [])),
            )

        execution_engines = {
            name: ExecutionEngineCapability(
                engine_class=spec["class"],
                display_name=spec["display_name"],
                description=spec["description"],
                dependencies=tuple(spec["dependencies"]),
                selectable=spec["selectable"],
            )
            for name, spec in caps.get("execution_engines", {}).items()
        }
        gateways = {
            name: GatewayCapability(
                requires_host=spec["requires_host"],
                routes_to={
                    engine_name: GatewayRouteCapability(engine_class=engine_spec["class"])
                    for engine_name, engine_spec in spec.get("routes_to", {}).items()
                },
            )
            for name, spec in caps.get("gateways", {}).items()
        }

        import benchbox.sql_compat.rules.benchmark_gate.clickhouse_local_gate  # noqa: F401
        import benchbox.sql_compat.rules.benchmark_gate.lakesail_gate  # noqa: F401
        import benchbox.sql_compat.rules.benchmark_gate.pg_family_gate  # noqa: F401
        import benchbox.sql_compat.rules.benchmark_gate.questdb_gate  # noqa: F401
        from benchbox.sql_compat.actions import CompatAction
        from benchbox.sql_compat.context import Phase
        from benchbox.sql_compat.registry import REGISTRY

        unsupported: dict[str, str] = {}
        for (phase, platform, benchmark, _query_id), entry in REGISTRY.all_rules():
            if (
                phase is Phase.BENCHMARK_GATE
                and platform == canonical_name
                and benchmark is not None
                and entry.decision.action is CompatAction.BLOCK_BENCHMARK
            ):
                reason = getattr(entry.decision.payload, "reason", None) or entry.decision.reason or ""
                unsupported[benchmark] = reason

        return PlatformCapability(
            supports_sql=caps.get("supports_sql", False),
            supports_dataframe=caps.get("supports_dataframe", False),
            default_mode=caps.get("default_mode", "sql"),
            deployment_modes=deployment_modes,
            default_deployment=caps.get("default_deployment", "local"),
            execution_engines=execution_engines,
            platform_family=caps.get("platform_family"),
            inherits_from=caps.get("inherits_from"),
            cost_class=caps.get("cost_class", "free"),
            gateways=gateways,
            unsupported_benchmarks=unsupported,
        )

    @classmethod
    def get_platform_conflicts(cls, platform_name: str) -> list[str]:
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        canonical_name = cls.resolve_platform_name(platform_name)
        metadata = cls._platform_metadata.get(canonical_name)
        if metadata is None:
            return []

        caps = metadata.get("capabilities", {})
        return list(caps.get("conflicts_with", []))

    @classmethod
    def supports_mode(cls, platform_name: str, mode: str) -> bool:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None:
            return False

        if mode == "sql":
            return caps.supports_sql
        elif mode == "dataframe":
            return caps.supports_dataframe
        return False

    @classmethod
    def get_default_mode(cls, platform_name: str) -> str:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None:
            return "sql"
        return caps.default_mode

    @classmethod
    def get_unsupported_benchmarks(cls, platform_name: str) -> dict[str, str]:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None:
            return {}
        return dict(getattr(caps, "unsupported_benchmarks", None) or {})

    @classmethod
    def get_benchmark_block_reason(cls, platform_name: str, benchmark: str) -> str | None:
        return cls.get_unsupported_benchmarks(platform_name).get(benchmark)

    @classmethod
    def is_benchmark_supported(cls, platform_name: str, benchmark: str) -> bool:
        return cls.get_benchmark_block_reason(platform_name, benchmark) is None

    @classmethod
    def get_dual_mode_platforms(cls) -> list[str]:
        if not cls._platform_metadata:
            cls._platform_metadata = cls._build_platform_metadata()

        dual_mode = []
        for name, metadata in cls._platform_metadata.items():
            caps = metadata.get("capabilities", {})
            if caps.get("supports_sql") and caps.get("supports_dataframe"):
                dual_mode.append(name)
        return dual_mode

    @classmethod
    def get_sql_platforms(cls, *, include_deprecated: bool = False) -> list[str]:
        return cls._get_platforms_matching_capability("supports_sql", include_deprecated=include_deprecated)

    @classmethod
    def get_dataframe_platforms(cls, *, include_deprecated: bool = False) -> list[str]:
        return cls._get_platforms_matching_capability("supports_dataframe", include_deprecated=include_deprecated)

    @classmethod
    def get_self_hosted_platforms(cls, *, include_deprecated: bool = False) -> list[str]:
        metadata = cls._get_cached_platform_metadata()
        out: list[str] = []
        for name, spec in metadata.items():
            if not include_deprecated and spec.get("support_status") in {"deprecated", "document_only"}:
                continue
            deployment_modes = spec.get("capabilities", {}).get("deployment_modes", {})
            if any(mode.get("mode") == "self-hosted" for mode in deployment_modes.values()):
                out.append(name)
        return out

    @classmethod
    def _get_platforms_matching_capability(
        cls,
        capability: str,
        *,
        include_deprecated: bool = False,
    ) -> list[str]:
        metadata = cls._get_cached_platform_metadata()
        out: list[str] = []
        for name, spec in metadata.items():
            if not include_deprecated and spec.get("support_status") in {"deprecated", "document_only"}:
                continue
            if spec.get("capabilities", {}).get(capability, False):
                out.append(name)
        return out

    @classmethod
    def get_deployment_capability(cls, platform_name: str, deployment_mode: str) -> Optional[DeploymentCapability]:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None or not caps.deployment_modes:
            return None
        return caps.deployment_modes.get(deployment_mode)

    @classmethod
    def get_default_deployment(cls, platform_name: str) -> Optional[str]:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None or not caps.deployment_modes:
            return None
        return caps.default_deployment

    @classmethod
    def get_platform_family(cls, platform_name: str) -> Optional[str]:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None:
            return None
        return caps.platform_family

    @classmethod
    def get_inherited_platform(cls, platform_name: str) -> Optional[str]:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None:
            return None
        return caps.inherits_from

    @classmethod
    def requires_cloud_storage_for_deployment(cls, platform_name: str, deployment_mode: Optional[str] = None) -> bool:
        if deployment_mode is None:
            deployment_mode = cls.get_default_deployment(platform_name)

        if deployment_mode is None:
            return cls.requires_cloud_storage(platform_name)

        dep_cap = cls.get_deployment_capability(platform_name, deployment_mode)
        if dep_cap is not None:
            return dep_cap.requires_cloud_storage

        return cls.requires_cloud_storage(platform_name)

    @classmethod
    def get_available_deployment_modes(cls, platform_name: str) -> list[str]:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None or not caps.deployment_modes:
            return []
        return list(caps.deployment_modes.keys())

    @classmethod
    def supports_deployment_mode(cls, platform_name: str, deployment_mode: str) -> bool:
        caps = cls.get_platform_capabilities(platform_name)
        if caps is None or not caps.deployment_modes:
            return deployment_mode == "local"
        return deployment_mode in caps.deployment_modes

    @classmethod
    def get_available_execution_engines(cls, platform_name: str) -> list[str]:
        caps = cls.get_platform_capabilities(platform_name)
        return [] if caps is None else list(caps.execution_engines)

    @classmethod
    def supports_execution_engine(cls, platform_name: str, engine: str) -> bool:
        caps = cls.get_platform_capabilities(platform_name)
        return caps is not None and engine in caps.execution_engines

    @classmethod
    def get_available_gateways(cls, platform_name: str) -> dict[str, GatewayCapability]:
        caps = cls.get_platform_capabilities(platform_name)
        return {} if caps is None else caps.gateways

    @classmethod
    def supports_gateway(cls, platform_name: str, gateway: str) -> bool:
        caps = cls.get_platform_capabilities(platform_name)
        return caps is not None and gateway in caps.gateways


_OPTIONAL_ADAPTERS: tuple[tuple[str, str, str], ...] = get_adapter_imports()

_OPTIONAL_ADAPTER_REGISTRATION_DIAGNOSTICS: dict[str, dict[str, Any]] = {}


def _diagnose_optional_adapter_entry(
    name: str,
    module_path: str,
    class_name: str,
) -> OptionalAdapterDiagnostic:
    support_status = PlatformRegistry.get_platform_support_status(name)
    if support_status == "deprecated":
        return OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status="deprecated_platform",
            support_status=support_status,
            error_message="Platform selector is deprecated; use the documented replacement.",
        )
    if support_status in {"repo_only", "document_only"}:
        return OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status="intentionally_disabled",
            support_status=support_status,
            error_message=f"Platform support_status is {support_status}; it is not a default runtime adapter.",
        )

    try:
        module = importlib.import_module(module_path)
        adapter_cls = getattr(module, class_name)
    except AttributeError as exc:
        return OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status="broken_adapter_import",
            support_status=support_status,
            error_type=type(exc).__name__,
            error_message=f"{module_path} does not expose {class_name}",
        )
    except (ImportError, OSError) as exc:
        return OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status=PlatformRegistry.classify_optional_import_error(exc, module_path=module_path),
            support_status=support_status,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    return OptionalAdapterDiagnostic(
        platform_name=name,
        module_path=module_path,
        class_name=class_name,
        status="available",
        support_status=support_status,
        available=adapter_cls is not None,
    )


def _try_register_adapter(name: str, module_path: str, class_name: str) -> None:
    support_status = PlatformRegistry.get_platform_support_status(name)
    try:
        module = importlib.import_module(module_path)
        adapter_cls = getattr(module, class_name)
        PlatformRegistry.register_adapter(name, adapter_cls)
        _OPTIONAL_ADAPTER_REGISTRATION_DIAGNOSTICS[name] = OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status="available",
            support_status=support_status,
            available=True,
        ).to_dict()
    except AttributeError as exc:
        _OPTIONAL_ADAPTER_REGISTRATION_DIAGNOSTICS[name] = OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status="broken_adapter_import",
            support_status=support_status,
            error_type=type(exc).__name__,
            error_message=f"{module_path} does not expose {class_name}",
        ).to_dict()
    except (ImportError, OSError) as exc:
        _OPTIONAL_ADAPTER_REGISTRATION_DIAGNOSTICS[name] = OptionalAdapterDiagnostic(
            platform_name=name,
            module_path=module_path,
            class_name=class_name,
            status=PlatformRegistry.classify_optional_import_error(exc, module_path=module_path),
            support_status=support_status,
            error_type=type(exc).__name__,
            error_message=str(exc),
        ).to_dict()


def auto_register_platforms() -> None:
    for name, module_path, class_name in _OPTIONAL_ADAPTERS:
        _try_register_adapter(name, module_path, class_name)
