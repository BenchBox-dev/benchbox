from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

SupportStatus = Literal["stable", "beta", "experimental", "repo_only", "deprecated", "document_only"]
DefaultMode = Literal["sql", "dataframe"]
AliasScope = Literal["cli", "registry"]
StreamConnectionCapabilityName = Literal["shared_cursor", "independent_connection", "unsupported"]

SUPPORT_STATUS_VALUES: tuple[SupportStatus, ...] = (
    "stable",
    "beta",
    "experimental",
    "repo_only",
    "deprecated",
    "document_only",
)

EXECUTION_ENGINE_CLASSES: tuple[str, ...] = (
    "adaptive",
    "in-memory",
    "streaming",
    "gpu",
    "native-vectorized",
    "standard",
    "distributed",
    "jit",
    "delegated",
)
DEFAULT_EXECUTION_ENGINE = "default"
_EXECUTION_ENGINE_FIELDS = frozenset({"class", "display_name", "description", "dependencies", "selectable"})
_EXECUTION_ENGINE_FIELD_LIST = ", ".join(sorted(_EXECUTION_ENGINE_FIELDS))

_PLATFORM_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REQUIRED_METADATA_FIELDS = frozenset(
    {
        "display_name",
        "description",
        "category",
        "libraries",
        "requirements",
        "installation_command",
        "adoption",
        "supports",
        "support_status",
        "capabilities",
    }
)


@dataclass(frozen=True)
class AdapterImportSpec:
    module: str
    class_name: str
    registration_order: int
    stream_connection_capability: StreamConnectionCapabilityName


@dataclass(frozen=True)
class PlatformAliasSpec:
    name: str
    scopes: tuple[AliasScope, ...]
    implied_mode: DefaultMode | None = None


@dataclass(frozen=True)
class PlatformManifestEntry:
    key: str
    aliases: tuple[PlatformAliasSpec, ...]
    metadata: Mapping[str, Any]
    adapter: AdapterImportSpec | None

    @property
    def support_status(self) -> SupportStatus:
        return cast(SupportStatus, self.metadata["support_status"])

    @property
    def capabilities(self) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], self.metadata["capabilities"])

    def metadata_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _thaw_json(self.metadata))


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _value in pairs]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate platform manifest object keys: {', '.join(duplicates)}")
    return dict(pairs)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_capabilities(key: str, capabilities: object) -> None:
    if not isinstance(capabilities, dict):
        raise ValueError(f"Platform {key!r} capabilities must be an object")

    supports_sql = capabilities.get("supports_sql")
    supports_dataframe = capabilities.get("supports_dataframe")
    default_mode = capabilities.get("default_mode")
    if not isinstance(supports_sql, bool) or not isinstance(supports_dataframe, bool):
        raise ValueError(f"Platform {key!r} must declare boolean SQL and DataFrame capabilities")
    if not supports_sql and not supports_dataframe:
        raise ValueError(f"Platform {key!r} must support at least one execution mode")
    if default_mode not in {"sql", "dataframe"}:
        raise ValueError(f"Platform {key!r} has invalid default_mode {default_mode!r}")
    if default_mode == "sql" and not supports_sql:
        raise ValueError(f"Platform {key!r} defaults to unsupported SQL mode")
    if default_mode == "dataframe" and not supports_dataframe:
        raise ValueError(f"Platform {key!r} defaults to unsupported DataFrame mode")

    deployment_modes = capabilities.get("deployment_modes", {})
    if not isinstance(deployment_modes, dict):
        raise ValueError(f"Platform {key!r} deployment_modes must be an object")
    if deployment_modes:
        invalid_deployments = sorted(name for name, value in deployment_modes.items() if not isinstance(value, dict))
        if invalid_deployments:
            raise ValueError(f"Platform {key!r} has invalid deployment objects: {', '.join(invalid_deployments)}")
        default_deployment = capabilities.get("default_deployment")
        if default_deployment not in deployment_modes:
            raise ValueError(f"Platform {key!r} default_deployment must name a declared deployment mode")
        defaults = [name for name, value in deployment_modes.items() if value.get("default_for_platform")]
        if defaults != [default_deployment]:
            raise ValueError(f"Platform {key!r} must mark exactly its default deployment: {default_deployment!r}")

    _validate_compute(key, capabilities.get("compute", {}))
    _validate_execution_engines(key, capabilities.get("execution_engines", {}))


def _validate_execution_engines(key: str, engines: object) -> None:
    if not isinstance(engines, dict):
        raise ValueError(f"Platform {key!r} execution_engines must be an object")
    for name, engine in engines.items():
        if name == DEFAULT_EXECUTION_ENGINE or not _PLATFORM_KEY_PATTERN.fullmatch(name):
            raise ValueError(f"Platform {key!r} cannot declare execution engine {name!r}")
        if not isinstance(engine, dict) or set(engine) != _EXECUTION_ENGINE_FIELDS:
            raise ValueError(
                f"Platform {key!r} execution engine {name!r} must have exactly: {_EXECUTION_ENGINE_FIELD_LIST}"
            )
        if engine["class"] not in EXECUTION_ENGINE_CLASSES:
            raise ValueError(f"Platform {key!r} execution engine {name!r} has unknown class {engine['class']!r}")
        if not isinstance(engine["display_name"], str) or not isinstance(engine["description"], str):
            raise ValueError(f"Platform {key!r} execution engine {name!r} needs string display_name and description")
        dependencies = engine["dependencies"]
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            raise ValueError(f"Platform {key!r} execution engine {name!r} dependencies must be a list of strings")
        if not isinstance(engine["selectable"], bool):
            raise ValueError(f"Platform {key!r} execution engine {name!r} selectable must be a boolean")


COMPUTE_RESOURCE_KINDS: tuple[str, ...] = (
    "warehouse",
    "engine",
    "workgroup",
    "cluster",
    "pool",
    "application",
    "service",
)


def _validate_compute(key: str, compute: object) -> None:
    if not isinstance(compute, dict):
        raise ValueError(f"Platform {key!r} compute must be an object")
    if not compute:
        return
    required = {"resource_kind", "native_resource_keys", "native_size_keys"}
    allowed = required | {"size_choices", "size_default"}
    if not required <= set(compute) <= allowed:
        raise ValueError(
            f"Platform {key!r} compute must have exactly: resource_kind, native_resource_keys, native_size_keys"
        )
    if "size_choices" in compute and (
        not isinstance(compute["size_choices"], list)
        or not all(isinstance(item, str) and item for item in compute["size_choices"])
    ):
        raise ValueError(f"Platform {key!r} compute size_choices must be a list of strings")
    if "size_default" in compute and not isinstance(compute["size_default"], str):
        raise ValueError(f"Platform {key!r} compute size_default must be a string")
    kind = compute["resource_kind"]
    if (
        kind is not None
        and kind not in COMPUTE_RESOURCE_KINDS
        and not (
            isinstance(kind, dict)
            and kind
            and all(isinstance(alias, str) and value in COMPUTE_RESOURCE_KINDS for alias, value in kind.items())
        )
    ):
        raise ValueError(f"Platform {key!r} compute resource_kind must name a known kind")
    for field in ("native_resource_keys", "native_size_keys"):
        keys = compute[field]
        if not isinstance(keys, list) or not all(isinstance(item, str) and item for item in keys):
            raise ValueError(f"Platform {key!r} compute {field} must be a list of strings")
    if isinstance(kind, dict) and set(kind) - set(compute["native_resource_keys"]):
        raise ValueError(f"Platform {key!r} compute resource_kind keys must be native resource keys")


def _parse_adapter(key: str, adapter_data: object) -> AdapterImportSpec | None:
    if adapter_data is None:
        return None
    if not isinstance(adapter_data, dict) or not set(adapter_data) <= {
        "module",
        "class_name",
        "registration_order",
        "stream_connection_capability",
    }:
        raise ValueError(f"Platform {key!r} adapter contains unsupported fields")
    required = {"module", "class_name", "registration_order"}
    if not required <= set(adapter_data):
        raise ValueError(f"Platform {key!r} adapter must contain module, class_name, and registration_order")
    module = adapter_data["module"]
    class_name = adapter_data["class_name"]
    registration_order = adapter_data["registration_order"]
    stream_capability = adapter_data.get("stream_connection_capability", "unsupported")
    if not isinstance(module, str) or not module.startswith("benchbox.platforms."):
        raise ValueError(f"Platform {key!r} has invalid adapter module {module!r}")
    if not isinstance(class_name, str) or not class_name.endswith("Adapter"):
        raise ValueError(f"Platform {key!r} has invalid adapter class {class_name!r}")
    if not isinstance(registration_order, int) or isinstance(registration_order, bool) or registration_order < 0:
        raise ValueError(f"Platform {key!r} has invalid adapter registration_order {registration_order!r}")
    if stream_capability not in {"shared_cursor", "independent_connection", "unsupported"}:
        raise ValueError(f"Platform {key!r} has invalid stream_connection_capability {stream_capability!r}")
    return AdapterImportSpec(
        module=module,
        class_name=class_name,
        registration_order=registration_order,
        stream_connection_capability=cast(StreamConnectionCapabilityName, stream_capability),
    )


def _parse_aliases(key: str, aliases_data: object) -> tuple[PlatformAliasSpec, ...]:
    if not isinstance(aliases_data, list):
        raise ValueError(f"Platform {key!r} aliases must be a list")

    aliases: list[PlatformAliasSpec] = []
    for alias_data in aliases_data:
        if not isinstance(alias_data, dict) or not set(alias_data) <= {"name", "scopes", "implied_mode"}:
            raise ValueError(f"Platform {key!r} aliases must contain typed name and scopes objects")
        name = alias_data.get("name")
        scopes = alias_data.get("scopes")
        implied_mode = alias_data.get("implied_mode")
        if not isinstance(name, str) or not _PLATFORM_KEY_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid alias {name!r} for platform {key!r}")
        if (
            not isinstance(scopes, list)
            or not scopes
            or not all(scope in {"cli", "registry"} for scope in scopes)
            or scopes != sorted(set(scopes))
        ):
            raise ValueError(f"Platform alias {name!r} must have sorted, unique CLI/registry scopes")
        if implied_mode not in {None, "sql", "dataframe"}:
            raise ValueError(f"Platform alias {name!r} has invalid implied_mode {implied_mode!r}")
        if implied_mode is not None and "cli" not in scopes:
            raise ValueError(f"Platform alias {name!r} mode semantics require CLI scope")
        if name.endswith("-df") and implied_mode != "dataframe":
            raise ValueError(f"DataFrame alias {name!r} must declare implied_mode='dataframe'")
        aliases.append(
            PlatformAliasSpec(
                name=name,
                scopes=cast(tuple[AliasScope, ...], tuple(scopes)),
                implied_mode=cast(DefaultMode | None, implied_mode),
            )
        )
    return tuple(aliases)


def _parse_manifest_entry(raw_entry: object) -> PlatformManifestEntry:
    if not isinstance(raw_entry, dict):
        raise ValueError("Every platform manifest entry must be an object")
    entry = raw_entry.copy()
    key = entry.pop("key", None)
    aliases = entry.pop("aliases", [])
    adapter_data = entry.pop("adapter", None)
    if not isinstance(key, str) or not _PLATFORM_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Invalid canonical platform key: {key!r}")
    if missing := sorted(_REQUIRED_METADATA_FIELDS - entry.keys()):
        raise ValueError(f"Platform {key!r} is missing metadata fields: {', '.join(missing)}")
    if entry["support_status"] not in SUPPORT_STATUS_VALUES:
        raise ValueError(f"Platform {key!r} has invalid support_status {entry['support_status']!r}")
    _validate_capabilities(key, entry["capabilities"])
    return PlatformManifestEntry(
        key=key,
        aliases=_parse_aliases(key, aliases),
        metadata=cast(Mapping[str, Any], _freeze_json(entry)),
        adapter=_parse_adapter(key, adapter_data),
    )


def _validate_manifest_set(entries: list[PlatformManifestEntry]) -> None:
    keys = [entry.key for entry in entries]
    duplicate_keys = sorted(key for key, count in Counter(keys).items() if count > 1)
    if duplicate_keys:
        raise ValueError(f"Duplicate canonical platform keys: {', '.join(duplicate_keys)}")

    canonical_keys = set(keys)
    alias_targets: dict[str, str] = {}
    for entry in entries:
        if tuple(sorted(entry.aliases, key=lambda alias: alias.name)) != entry.aliases:
            raise ValueError(f"Platform {entry.key!r} aliases must be sorted and unique")
        for alias in entry.aliases:
            if alias.name in canonical_keys:
                raise ValueError(f"Platform alias {alias.name!r} collides with a canonical key")
            previous = alias_targets.get(alias.name)
            if previous is not None:
                raise ValueError(f"Duplicate platform alias {alias.name!r} on {previous!r} and {entry.key!r}")
            alias_targets[alias.name] = entry.key

    engine_classes: dict[str, tuple[str, str]] = {}
    for entry in entries:
        for name, engine in entry.capabilities.get("execution_engines", {}).items():
            first_key, first_class = engine_classes.setdefault(name, (entry.key, engine["class"]))
            if first_class != engine["class"]:
                raise ValueError(
                    f"Execution engine {name!r} is {first_class!r} on {first_key!r} "
                    f"but {engine['class']!r} on {entry.key!r}"
                )

    adapter_coordinates = [
        (entry.adapter.module, entry.adapter.class_name) for entry in entries if entry.adapter is not None
    ]
    duplicate_adapters = sorted(
        f"{module}:{class_name}" for (module, class_name), count in Counter(adapter_coordinates).items() if count > 1
    )
    if duplicate_adapters:
        raise ValueError(f"Duplicate adapter registrations: {', '.join(duplicate_adapters)}")

    registration_orders = [entry.adapter.registration_order for entry in entries if entry.adapter is not None]
    duplicate_orders = sorted(order for order, count in Counter(registration_orders).items() if count > 1)
    expected_orders = set(range(len(registration_orders)))
    missing_orders = sorted(expected_orders - set(registration_orders))
    unexpected_orders = sorted(set(registration_orders) - expected_orders)
    if duplicate_orders or missing_orders or unexpected_orders:
        raise ValueError(
            "Adapter registration_order values must be unique and contiguous from zero: "
            f"duplicates={duplicate_orders}, missing={missing_orders}, unexpected={unexpected_orders}"
        )


def _load_manifest(payload: str) -> tuple[PlatformManifestEntry, ...]:
    raw_entries = json.loads(payload, object_pairs_hook=_reject_duplicate_object_keys)
    if not isinstance(raw_entries, list):
        raise ValueError("Platform manifest payload must contain a list")
    entries = [_parse_manifest_entry(raw_entry) for raw_entry in raw_entries]
    _validate_manifest_set(entries)

    return tuple(entries)


_PLATFORM_MANIFEST_JSON = """[
  {
    "key": "duckdb",
    "aliases": [
      {
        "name": "duck",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.duckdb",
      "class_name": "DuckDBAdapter",
      "registration_order": 0,
      "stream_connection_capability": "shared_cursor"
    },
    "display_name": "DuckDB",
    "description": "Columnar OLAP engine • Single-node • In-memory",
    "category": "analytical",
    "libraries": [
      {
        "name": "duckdb",
        "required": true
      }
    ],
    "requirements": [
      "duckdb>=1.5.0,<2.0.0"
    ],
    "installation_command": "uv add 'duckdb>=1.5,<2'",
    "adoption": "mainstream",
    "supports": [
      "olap",
      "in_memory",
      "columnar"
    ],
    "driver_package": "duckdb",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "duckdb",
      "default_deployment": "local",
      "deployment_modes": {
        "local": {
          "mode": "local",
          "display_name": "DuckDB Local",
          "description": "Embedded in-process DuckDB",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "duckdb"
          ],
          "auth_methods": []
        }
      }
    },
    "support_status": "stable"
  },
  {
    "key": "datafusion",
    "aliases": [
      {
        "name": "datafusion-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      },
      {
        "name": "fusion",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.datafusion",
      "class_name": "DataFusionAdapter",
      "registration_order": 3
    },
    "display_name": "DataFusion",
    "description": "Arrow-based SQL • Single-node • In-memory",
    "category": "analytical",
    "libraries": [
      {
        "name": "datafusion",
        "required": true
      }
    ],
    "requirements": [
      "datafusion>=54.0.0"
    ],
    "installation_command": "uv add datafusion",
    "adoption": "emerging",
    "supports": [
      "olap",
      "in_memory",
      "columnar",
      "arrow",
      "dataframe"
    ],
    "driver_package": "datafusion",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql"
    },
    "support_status": "stable"
  },
  {
    "key": "sqlite",
    "aliases": [
      {
        "name": "sqlite3",
        "scopes": ["registry"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.sqlite",
      "class_name": "SQLiteAdapter",
      "registration_order": 11,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "SQLite",
    "description": "Row-based OLTP database • Single-node • File-based",
    "category": "embedded",
    "libraries": [
      {
        "name": "sqlite3",
        "required": true
      }
    ],
    "requirements": [
      "sqlite3 (built-in)"
    ],
    "installation_command": "Built-in Python library",
    "adoption": "niche",
    "supports": [
      "transactional",
      "file_based"
    ],
    "driver_package": null,
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql"
    },
    "support_status": "stable"
  },
  {
    "key": "polars",
    "aliases": [
      {
        "name": "polars-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.polars_platform",
      "class_name": "PolarsAdapter",
      "registration_order": 44
    },
    "display_name": "Polars",
    "description": "DataFrame engine • In-memory • Columnar",
    "category": "analytical",
    "libraries": [
      {
        "name": "polars",
        "required": true
      }
    ],
    "requirements": [
      "polars>=0.20.0"
    ],
    "installation_command": "uv add polars",
    "adoption": "established",
    "supports": [
      "olap",
      "in_memory",
      "columnar",
      "dataframe"
    ],
    "driver_package": "polars",
    "capabilities": {
      "supports_sql": false,
      "supports_dataframe": true,
      "default_mode": "dataframe",
      "execution_engines": {
        "auto": {
          "class": "adaptive",
          "display_name": "Auto",
          "description": "Polars chooses the engine for each query",
          "dependencies": ["polars"],
          "selectable": true
        },
        "in-memory": {
          "class": "in-memory",
          "display_name": "In-memory",
          "description": "Materializes each query in memory",
          "dependencies": ["polars"],
          "selectable": true
        },
        "streaming": {
          "class": "streaming",
          "display_name": "Streaming",
          "description": "Processes each query in batches to bound memory",
          "dependencies": ["polars"],
          "selectable": true
        }
      }
    },
    "support_status": "stable"
  },
  {
    "key": "motherduck",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.motherduck",
      "class_name": "MotherDuckAdapter",
      "registration_order": 1
    },
    "display_name": "MotherDuck",
    "description": "Serverless DuckDB cloud • Managed • Cloud storage",
    "category": "cloud",
    "libraries": [
      {
        "name": "duckdb",
        "required": true
      }
    ],
    "requirements": [
      "duckdb>=0.9.0"
    ],
    "installation_command": "uv add duckdb",
    "adoption": "emerging",
    "supports": [
      "olap",
      "cloud",
      "columnar",
      "serverless"
    ],
    "driver_package": "duckdb",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "duckdb",
      "inherits_from": "duckdb",
      "cost_class": "paid_credits",
      "default_deployment": "managed",
      "deployment_modes": {
        "managed": {
          "mode": "managed",
          "display_name": "MotherDuck Cloud",
          "description": "Serverless DuckDB in MotherDuck cloud",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "duckdb"
          ],
          "auth_methods": [
            "token"
          ]
        }
      }
    },
    "required_credentials": [
      "MOTHERDUCK_TOKEN",
      "database"
    ],
    "support_status": "beta"
  },
  {
    "key": "ducklake",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.ducklake",
      "class_name": "DuckLakeAdapter",
      "registration_order": 2,
      "stream_connection_capability": "shared_cursor"
    },
    "display_name": "DuckLake",
    "description": "Open lakehouse format • DuckDB engine • Parquet + SQL catalog",
    "category": "olap",
    "libraries": [
      {
        "name": "duckdb",
        "required": true
      }
    ],
    "requirements": [
      "duckdb>=1.5.0"
    ],
    "installation_command": "uv add 'benchbox[ducklake]'",
    "adoption": "emerging",
    "supports": [
      "olap",
      "lakehouse",
      "columnar",
      "parquet"
    ],
    "driver_package": "duckdb",
    "notes": "Two independent axes, not a fixed set of deployments: catalog metadata backend (DuckDB-file default, SQLite, or self-hosted PostgreSQL) x Parquet DATA_PATH location (local or S3). Any combination is valid. Requires a live DuckDB runtime >= 1.3 for the ducklake extension; install via the ducklake extra, which pins that floor.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "duckdb",
      "inherits_from": "duckdb",
      "default_deployment": "local",
      "deployment_modes": {
        "local": {
          "mode": "local",
          "display_name": "DuckLake Local",
          "description": "Local catalog (DuckDB-file or SQLite) + Parquet on local disk. Descriptive capability metadata: the adapter selects the catalog backend from --platform-option catalog=<duckdb|sqlite|postgres> and the storage location from the data_path scheme.",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "duckdb"
          ],
          "auth_methods": []
        },
        "local_catalog_s3": {
          "mode": "local",
          "display_name": "DuckLake Local Catalog + S3 Storage",
          "description": "Local catalog (DuckDB-file or SQLite) + Parquet DATA_PATH on S3 via DuckDB's httpfs extension. Descriptive capability metadata: the adapter selects the catalog backend from --platform-option catalog=<duckdb|sqlite|postgres> and the storage location from the data_path scheme.",
          "requires_credentials": false,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "duckdb"
          ],
          "auth_methods": [
            "credential_chain",
            "access_key"
          ]
        },
        "postgres_catalog": {
          "mode": "self-hosted",
          "display_name": "DuckLake PostgreSQL Catalog",
          "description": "Self-hosted PostgreSQL catalog metadata (via DuckDB's postgres extension) + Parquet on local disk. Descriptive capability metadata: the adapter selects the catalog backend from --platform-option catalog=<duckdb|sqlite|postgres> and the storage location from the data_path scheme.",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "duckdb"
          ],
          "auth_methods": [
            "password"
          ]
        },
        "postgres_catalog_s3": {
          "mode": "self-hosted",
          "display_name": "DuckLake PostgreSQL Catalog + S3 Storage",
          "description": "Self-hosted PostgreSQL catalog metadata + Parquet DATA_PATH on S3. Previously unrepresentable: the old three-mode model forced a choice between the catalog axis and the storage axis. Descriptive capability metadata: the adapter selects the catalog backend from --platform-option catalog=<duckdb|sqlite|postgres> and the storage location from the data_path scheme.",
          "requires_credentials": true,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "duckdb"
          ],
          "auth_methods": [
            "password",
            "credential_chain",
            "access_key"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "clickhouse",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.clickhouse",
      "class_name": "ClickHouseAdapter",
      "registration_order": 6
    },
    "display_name": "ClickHouse",
    "description": "Columnar OLAP database • Local/server • Distributed",
    "category": "analytical",
    "libraries": [
      {
        "name": "clickhouse_driver",
        "required": true,
        "import_name": "clickhouse_driver"
      },
      {
        "name": "chdb",
        "required": false,
        "description": "Local ClickHouse"
      }
    ],
    "requirements": [
      "clickhouse-driver>=0.2.0"
    ],
    "installation_command": "uv add clickhouse-driver",
    "adoption": "established",
    "supports": [
      "olap",
      "columnar",
      "distributed"
    ],
    "driver_package": "clickhouse-driver",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "clickhouse",
      "default_deployment": "local",
      "deployment_modes": {
        "local": {
          "mode": "local",
          "display_name": "ClickHouse Local (chDB)",
          "description": "Embedded ClickHouse via chDB library",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "chdb"
          ],
          "auth_methods": []
        },
        "server": {
          "mode": "self-hosted",
          "display_name": "ClickHouse Server",
          "description": "Self-hosted ClickHouse server or cluster",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "clickhouse-driver"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "deprecated"
  },
  {
    "key": "clickhouse-local",
    "aliases": [
      {
        "name": "ch",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.clickhouse_local",
      "class_name": "ClickHouseLocalAdapter",
      "registration_order": 7
    },
    "display_name": "ClickHouse Local (chDB)",
    "description": "Embedded ClickHouse via chDB • In-process • Zero network",
    "category": "analytical",
    "libraries": [
      {
        "name": "chdb",
        "required": true,
        "import_name": "chdb"
      }
    ],
    "requirements": [
      "chdb>=0.10.0"
    ],
    "installation_command": "uv add benchbox --extra clickhouse-local",
    "adoption": "established",
    "supports": [
      "olap",
      "columnar",
      "embedded",
      "in-process"
    ],
    "driver_package": "chdb",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "clickhouse",
      "inherits_from": "clickhouse"
    },
    "support_status": "beta"
  },
  {
    "key": "clickhouse-server",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.clickhouse_server",
      "class_name": "ClickHouseServerAdapter",
      "registration_order": 8
    },
    "display_name": "ClickHouse Server",
    "description": "Self-hosted ClickHouse • Docker/dedicated • High-performance columnar",
    "category": "analytical",
    "libraries": [
      {
        "name": "clickhouse_driver",
        "required": true,
        "import_name": "clickhouse_driver"
      }
    ],
    "requirements": [
      "clickhouse-driver>=0.2.0"
    ],
    "installation_command": "uv add benchbox --extra clickhouse-server",
    "adoption": "established",
    "supports": [
      "olap",
      "columnar",
      "distributed",
      "self-hosted"
    ],
    "driver_package": "clickhouse-driver",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "clickhouse",
      "inherits_from": "clickhouse",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "ClickHouse Server Self-Hosted",
          "description": "Self-hosted ClickHouse Server server",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "clickhouse_driver"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "clickhouse-cloud",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.clickhouse_cloud",
      "class_name": "ClickHouseCloudAdapter",
      "registration_order": 9
    },
    "display_name": "ClickHouse Cloud",
    "description": "Managed ClickHouse • Serverless/dedicated • Cloud analytics",
    "category": "cloud",
    "libraries": [
      {
        "name": "clickhouse_connect",
        "required": true,
        "import_name": "clickhouse_connect"
      }
    ],
    "requirements": [
      "clickhouse-connect>=0.10.0"
    ],
    "installation_command": "uv add benchbox --extra clickhouse-cloud",
    "adoption": "emerging",
    "supports": [
      "olap",
      "columnar",
      "distributed",
      "serverless",
      "cloud"
    ],
    "driver_package": "clickhouse-connect",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "clickhouse",
      "inherits_from": "clickhouse",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "service",
        "native_resource_keys": ["service_id"],
        "native_size_keys": ["compute_size"]
      },
      "default_deployment": "managed",
      "deployment_modes": {
        "managed": {
          "mode": "managed",
          "display_name": "ClickHouse Cloud",
          "description": "ClickHouse Cloud managed service",
          "requires_credentials": true,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "clickhouse-connect"
          ],
          "auth_methods": [
            "password",
            "oauth"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "bigquery",
    "aliases": [
      {
        "name": "bq",
        "scopes": ["cli"]
      },
      {
        "name": "gbq",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.bigquery",
      "class_name": "BigQueryAdapter",
      "registration_order": 12
    },
    "display_name": "Google BigQuery",
    "description": "Columnar data warehouse • Serverless • Petabyte-scale",
    "category": "cloud",
    "libraries": [
      {
        "name": "google.cloud.bigquery",
        "required": true,
        "import_name": "google.cloud.bigquery"
      },
      {
        "name": "google.cloud.storage",
        "required": true,
        "import_name": "google.cloud.storage"
      }
    ],
    "requirements": [
      "google-cloud-bigquery>=3.0.0",
      "google-cloud-storage>=2.0.0"
    ],
    "installation_command": "uv add google-cloud-bigquery google-cloud-storage",
    "adoption": "mainstream",
    "supports": [
      "olap",
      "serverless",
      "petabyte_scale"
    ],
    "driver_package": "google-cloud-bigquery",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_credits"
    },
    "required_credentials": [
      "project_id",
      "credentials_file"
    ],
    "support_status": "beta"
  },
  {
    "key": "databricks",
    "aliases": [
      {
        "name": "dbx",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.databricks",
      "class_name": "DatabricksAdapter",
      "registration_order": 4
    },
    "display_name": "Databricks SQL",
    "description": "Lakehouse platform • Distributed • Spark-based",
    "category": "cloud",
    "libraries": [
      {
        "name": "databricks.sql",
        "required": true,
        "import_name": "databricks.sql"
      }
    ],
    "requirements": [
      "databricks-sql-connector>=2.0.0"
    ],
    "installation_command": "uv add databricks-sql-connector",
    "adoption": "mainstream",
    "supports": [
      "olap",
      "spark",
      "lakehouse"
    ],
    "driver_package": "databricks-sql-connector",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_credits",
      "compute": {
        "resource_kind": "warehouse",
        "native_resource_keys": ["warehouse_id"],
        "native_size_keys": []
      },
      "execution_engines": {
        "photon": {
          "class": "native-vectorized",
          "display_name": "Photon",
          "description": "Databricks SQL warehouse Photon engine",
          "dependencies": [],
          "selectable": false
        },
        "standard": {
          "class": "standard",
          "display_name": "Standard",
          "description": "Databricks SQL warehouse standard engine",
          "dependencies": [],
          "selectable": false
        }
      }
    },
    "required_credentials": [
      "server_hostname",
      "http_path",
      "access_token"
    ],
    "support_status": "beta"
  },
  {
    "key": "databricks-df",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.databricks",
      "class_name": "DatabricksDataFrameAdapter",
      "registration_order": 5
    },
    "display_name": "Databricks DataFrame",
    "description": "Databricks with PySpark DataFrame API • Databricks Connect",
    "category": "cloud",
    "libraries": [
      {
        "name": "databricks.sql",
        "required": true,
        "import_name": "databricks.sql"
      },
      {
        "name": "databricks.connect",
        "required": true,
        "import_name": "databricks.connect"
      }
    ],
    "requirements": [
      "databricks-sql-connector>=2.0.0",
      "databricks-connect>=14.0.0"
    ],
    "installation_command": "uv add databricks-sql-connector databricks-connect",
    "adoption": "niche",
    "supports": [
      "olap",
      "spark",
      "lakehouse",
      "dataframe"
    ],
    "driver_package": "databricks-connect",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "dataframe",
      "cost_class": "paid_credits",
      "compute": {
        "resource_kind": "cluster",
        "native_resource_keys": ["cluster_id"],
        "native_size_keys": []
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "snowflake",
    "aliases": [
      {
        "name": "snow",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.snowflake",
      "class_name": "SnowflakeAdapter",
      "registration_order": 14
    },
    "display_name": "Snowflake",
    "description": "Columnar data warehouse • Serverless • Multi-cloud",
    "category": "cloud",
    "libraries": [
      {
        "name": "snowflake.connector",
        "required": true,
        "import_name": "snowflake.connector"
      }
    ],
    "requirements": [
      "snowflake-connector-python>=3.0.0"
    ],
    "installation_command": "uv add snowflake-connector-python",
    "adoption": "mainstream",
    "supports": [
      "olap",
      "serverless",
      "multi_cloud"
    ],
    "driver_package": "snowflake-connector-python",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_credits",
      "compute": {
        "resource_kind": "warehouse",
        "native_resource_keys": ["warehouse"],
        "native_size_keys": ["warehouse_size"]
      }
    },
    "required_credentials": [
      "account",
      "user",
      "password",
      "warehouse"
    ],
    "support_status": "beta"
  },
  {
    "key": "redshift",
    "aliases": [
      {
        "name": "rs",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.redshift",
      "class_name": "RedshiftAdapter",
      "registration_order": 13
    },
    "display_name": "Amazon Redshift",
    "description": "Columnar data warehouse • Distributed • AWS MPP",
    "category": "cloud",
    "libraries": [
      {
        "name": "redshift_connector",
        "required": true
      },
      {
        "name": "boto3",
        "required": true
      }
    ],
    "requirements": [
      "redshift-connector>=2.0.0",
      "boto3>=1.20.0"
    ],
    "installation_command": "uv add redshift-connector boto3",
    "adoption": "established",
    "supports": [
      "olap",
      "columnar",
      "aws"
    ],
    "driver_package": "redshift-connector",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": {"workgroup_name": "workgroup", "cluster_identifier": "cluster"},
        "native_resource_keys": ["workgroup_name", "cluster_identifier"],
        "native_size_keys": []
      }
    },
    "required_credentials": [
      "host",
      "port",
      "database",
      "user",
      "password"
    ],
    "support_status": "beta"
  },
  {
    "key": "trino",
    "aliases": [
      {
        "name": "trinodb",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.trino",
      "class_name": "TrinoAdapter",
      "registration_order": 15
    },
    "display_name": "Trino",
    "description": "Distributed SQL • Federated • Multi-source",
    "category": "distributed",
    "libraries": [
      {
        "name": "trino",
        "required": true
      }
    ],
    "requirements": [
      "trino>=0.328.0"
    ],
    "installation_command": "uv add trino",
    "adoption": "established",
    "supports": [
      "olap",
      "federated",
      "distributed"
    ],
    "driver_package": "trino",
    "notes": "Supports Trino and Starburst Enterprise. For PrestoDB use presto-python-client. For AWS Athena use the athena adapter.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "trino",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "Trino Self-Hosted",
          "description": "Self-hosted Trino cluster",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "trino"
          ],
          "auth_methods": [
            "password",
            "oauth"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "starburst",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.starburst",
      "class_name": "StarburstAdapter",
      "registration_order": 16
    },
    "display_name": "Starburst",
    "description": "Managed Trino • Starburst Galaxy • Serverless",
    "category": "cloud",
    "libraries": [
      {
        "name": "trino",
        "required": true
      }
    ],
    "requirements": [
      "trino>=0.328.0"
    ],
    "installation_command": "uv add trino",
    "adoption": "emerging",
    "supports": [
      "olap",
      "federated",
      "distributed",
      "serverless",
      "cloud"
    ],
    "driver_package": "trino",
    "notes": "Starburst Galaxy managed Trino service. Uses trino Python driver with HTTPS. For self-hosted Trino use the trino adapter.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "trino",
      "inherits_from": "trino",
      "cost_class": "paid_compute",
      "default_deployment": "managed",
      "deployment_modes": {
        "managed": {
          "mode": "managed",
          "display_name": "Starburst Galaxy",
          "description": "Starburst Galaxy managed Trino service",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "trino"
          ],
          "auth_methods": [
            "password",
            "api_key"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "presto",
    "aliases": [
      {
        "name": "prestodb",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.presto",
      "class_name": "PrestoAdapter",
      "registration_order": 17
    },
    "display_name": "PrestoDB",
    "description": "Distributed SQL • Federated • Meta fork",
    "category": "distributed",
    "libraries": [
      {
        "name": "prestodb",
        "required": true,
        "import_name": "prestodb"
      }
    ],
    "requirements": [
      "presto-python-client>=0.8.4"
    ],
    "installation_command": "uv add presto-python-client",
    "adoption": "niche",
    "supports": [
      "olap",
      "federated",
      "distributed"
    ],
    "driver_package": "presto-python-client",
    "notes": "Supports PrestoDB (Meta's fork) with X-Presto-* headers. For Trino/Starburst use the trino adapter. For AWS Athena use the athena adapter.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "PrestoDB Self-Hosted",
          "description": "Self-hosted PrestoDB server",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "prestodb"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "postgresql",
    "aliases": [
      {
        "name": "pg",
        "scopes": ["cli"]
      },
      {
        "name": "pgsql",
        "scopes": ["cli"]
      },
      {
        "name": "postgres",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.postgresql",
      "class_name": "PostgreSQLAdapter",
      "registration_order": 18,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "PostgreSQL",
    "description": "Relational database • COPY loading",
    "category": "relational",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "established",
    "supports": [
      "olap",
      "oltp",
      "relational"
    ],
    "driver_package": "psycopg",
    "notes": "Supports PostgreSQL 12+. COPY-based bulk loading. For time-series workloads use timescaledb.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "PostgreSQL Self-Hosted",
          "description": "Self-hosted PostgreSQL server",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "timescaledb",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.timescaledb",
      "class_name": "TimescaleDBAdapter",
      "registration_order": 19,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "TimescaleDB",
    "description": "Time-series database • Hypertables • Compression",
    "category": "timeseries",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "niche",
    "supports": [
      "timeseries",
      "olap",
      "compression"
    ],
    "driver_package": "psycopg",
    "notes": "PostgreSQL extension for time-series. Automatic hypertables, compression policies. Requires TimescaleDB 2.x on server.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "timescaledb",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "TimescaleDB Self-Hosted",
          "description": "Self-hosted TimescaleDB server",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        },
        "cloud": {
          "mode": "managed",
          "display_name": "TigerData",
          "description": "TigerData managed PostgreSQL service",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "pg-mooncake",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.pg_mooncake",
      "class_name": "PgMooncakeAdapter",
      "registration_order": 21,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "pg_mooncake",
    "description": "Columnstore PostgreSQL • Parquet/Iceberg • DuckDB Execution",
    "category": "olap",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "emerging",
    "supports": [
      "olap",
      "columnstore",
      "analytics"
    ],
    "driver_package": "psycopg",
    "notes": "PostgreSQL extension adding native columnstore tables (Parquet/Iceberg) with DuckDB execution. Requires pg_mooncake on server. Conflicts with standalone pg_duckdb (shared libduckdb.so).",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "pg_mooncake",
      "conflicts_with": [
        "pg-duckdb"
      ],
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "pg_mooncake Self-Hosted",
          "description": "Self-hosted PostgreSQL with pg_mooncake extension",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "cedardb",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.cedardb",
      "class_name": "CedarDBAdapter",
      "registration_order": 23,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "CedarDB",
    "description": "High-performance OLAP/OLTP • PostgreSQL-compatible • Formerly Umbra",
    "category": "olap",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "emerging",
    "supports": [
      "olap",
      "oltp",
      "relational"
    ],
    "driver_package": "psycopg",
    "notes": "CedarDB (formerly Umbra) is a standalone RDBMS with PostgreSQL wire protocol compatibility. Not a PostgreSQL extension - connects via standard psycopg3 (psycopg) drivers.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "cedardb",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "CedarDB Self-Hosted",
          "description": "Self-hosted CedarDB server",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "pg-duckdb",
    "aliases": [
      {
        "name": "pg_duckdb",
        "scopes": ["registry"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.pg_duckdb",
      "class_name": "PgDuckDBAdapter",
      "registration_order": 20,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "pg_duckdb",
    "description": "DuckDB-accelerated PostgreSQL • Vectorized OLAP • MotherDuck",
    "category": "olap",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "emerging",
    "supports": [
      "olap",
      "analytics"
    ],
    "driver_package": "psycopg",
    "notes": "PostgreSQL extension embedding DuckDB vectorized execution. Requires pg_duckdb 1.0+ on server. Conflicts with pg_mooncake (shared libduckdb.so).",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "pg_duckdb",
      "conflicts_with": [
        "pg-mooncake"
      ],
      "cost_class": "paid_credits",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "pg_duckdb Self-Hosted",
          "description": "Self-hosted PostgreSQL with pg_duckdb extension",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        },
        "motherduck": {
          "mode": "managed",
          "display_name": "pg_duckdb + MotherDuck",
          "description": "pg_duckdb with MotherDuck cloud offload for hybrid queries",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "token"
          ]
        }
      },
      "execution_engines": {
        "duckdb": {
          "class": "delegated",
          "display_name": "DuckDB",
          "description": "Force pg_duckdb to route queries through DuckDB execution",
          "dependencies": [],
          "selectable": true
        },
        "postgres": {
          "class": "standard",
          "display_name": "PostgreSQL",
          "description": "Allow PostgreSQL's normal query routing without forcing DuckDB execution",
          "dependencies": [],
          "selectable": true
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "paradedb",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.paradedb",
      "class_name": "ParadeDBAdapter",
      "registration_order": 47,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "paradedb",
    "description": "Hybrid search + analytics PostgreSQL • BM25 via pg_analytics",
    "category": "olap",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "emerging",
    "supports": [
      "olap",
      "analytics"
    ],
    "driver_package": "psycopg",
    "notes": "PostgreSQL extension adding Elasticsearch-compatible BM25 search and analytics over heap tables. Requires pg_analytics on server. No known extension conflicts.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "paradedb",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "ParadeDB Self-Hosted",
          "description": "Self-hosted PostgreSQL with pg_analytics extension",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "citus",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.citus",
      "class_name": "CitusAdapter",
      "registration_order": 48,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "citus",
    "description": "Distributed PostgreSQL • Sharded tables via citus",
    "category": "olap",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1"
    ],
    "installation_command": "uv add 'psycopg[binary]'",
    "adoption": "emerging",
    "supports": [
      "olap",
      "analytics"
    ],
    "driver_package": "psycopg",
    "notes": "PostgreSQL extension distributing tables across coordinator and workers. Requires citus on server. Tables stay coordinator-local unless distribution_column is set. No known extension conflicts.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "citus",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "Citus Self-Hosted",
          "description": "Self-hosted Citus coordinator with citus extension",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "synapse",
    "aliases": [
      {
        "name": "azure-synapse",
        "scopes": ["cli"]
      },
      {
        "name": "azure_synapse",
        "scopes": ["registry"]
      },
      {
        "name": "azuresynapse",
        "scopes": ["cli"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.azure_synapse",
      "class_name": "AzureSynapseAdapter",
      "registration_order": 24
    },
    "display_name": "Azure Synapse Analytics",
    "description": "Cloud data warehouse • Dedicated SQL Pool • Azure MPP",
    "category": "cloud",
    "libraries": [
      {
        "name": "pyodbc",
        "required": true
      },
      {
        "name": "azure.storage.blob",
        "required": false,
        "import_name": "azure.storage.blob"
      },
      {
        "name": "azure.identity",
        "required": false,
        "import_name": "azure.identity"
      }
    ],
    "requirements": [
      "pyodbc>=4.0.0"
    ],
    "installation_command": "uv add pyodbc azure-storage-blob azure-identity",
    "adoption": "established",
    "supports": [
      "olap",
      "columnar",
      "azure",
      "distributed"
    ],
    "driver_package": "pyodbc",
    "notes": "Supports Azure Synapse Dedicated SQL Pools. COPY INTO for bulk loading. T-SQL dialect.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_compute"
    },
    "support_status": "beta"
  },
  {
    "key": "fabric_dw",
    "aliases": [
      {
        "name": "fabric-dw",
        "scopes": ["cli", "registry"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.fabric_warehouse",
      "class_name": "FabricWarehouseAdapter",
      "registration_order": 31
    },
    "display_name": "Microsoft Fabric Warehouse",
    "description": "Microsoft Fabric Warehouse • OneLake • Delta Lake native",
    "category": "cloud",
    "libraries": [
      {
        "name": "pyodbc",
        "required": true
      },
      {
        "name": "azure.identity",
        "required": true,
        "import_name": "azure.identity"
      },
      {
        "name": "azure.storage.filedatalake",
        "required": false,
        "import_name": "azure.storage.filedatalake"
      }
    ],
    "requirements": [
      "pyodbc>=4.0.0",
      "azure-identity>=1.15.0"
    ],
    "installation_command": "uv add pyodbc azure-identity azure-storage-file-datalake",
    "adoption": "niche",
    "supports": [
      "olap",
      "columnar",
      "azure",
      "delta_lake",
      "onelake"
    ],
    "driver_package": "pyodbc",
    "notes": "Supports Fabric Warehouse only (not Lakehouse). Entra ID auth only. OneLake + COPY INTO for bulk loading. T-SQL dialect (subset).",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_compute"
    },
    "support_status": "beta"
  },
  {
    "key": "firebolt",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.firebolt",
      "class_name": "FireboltAdapter",
      "registration_order": 26
    },
    "display_name": "Firebolt",
    "description": "Vectorized analytics • Local/Cloud • PG-wire",
    "category": "cloud",
    "libraries": [
      {
        "name": "firebolt.db",
        "required": true,
        "import_name": "firebolt.db"
      }
    ],
    "requirements": [
      "firebolt-sdk>=1.18.0"
    ],
    "installation_command": "uv add firebolt-sdk",
    "adoption": "emerging",
    "supports": [
      "olap",
      "vectorized",
      "columnar",
      "local",
      "cloud"
    ],
    "driver_package": "firebolt-sdk",
    "notes": "Supports Firebolt Core (free, local Docker) and Firebolt Cloud. PostgreSQL-compatible SQL dialect. Vectorized query execution optimized for analytics.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "firebolt",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "engine",
        "native_resource_keys": ["engine_name"],
        "native_size_keys": ["engine_size", "compute_size"]
      },
      "default_deployment": "core",
      "deployment_modes": {
        "core": {
          "mode": "local",
          "display_name": "Firebolt Core",
          "description": "Free local Firebolt via Docker container",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "firebolt-sdk"
          ],
          "auth_methods": []
        },
        "cloud": {
          "mode": "managed",
          "display_name": "Firebolt Cloud",
          "description": "Firebolt Cloud managed service",
          "requires_credentials": true,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "firebolt-sdk"
          ],
          "auth_methods": [
            "oauth",
            "service_account"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "starrocks",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.starrocks",
      "class_name": "StarRocksAdapter",
      "registration_order": 10
    },
    "display_name": "StarRocks",
    "description": "Columnar analytics engine • Distributed • Fast OLAP",
    "category": "analytical",
    "libraries": [
      {
        "name": "pymysql",
        "required": true,
        "import_name": "pymysql"
      }
    ],
    "requirements": [
      "pymysql>=1.1.0"
    ],
    "installation_command": "uv add pymysql",
    "adoption": "emerging",
    "supports": [
      "olap",
      "columnar",
      "distributed",
      "mpp"
    ],
    "driver_package": "pymysql",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "starrocks",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "StarRocks Self-Hosted",
          "description": "Self-hosted StarRocks cluster",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "pymysql"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "databend",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.databend",
      "class_name": "DatabendAdapter",
      "registration_order": 27
    },
    "display_name": "Databend",
    "description": "Cloud-native OLAP • Rust • Snowflake-compatible",
    "category": "cloud",
    "libraries": [
      {
        "name": "databend_driver",
        "required": true,
        "import_name": "databend_driver"
      }
    ],
    "requirements": [
      "databend-driver>=0.28.0"
    ],
    "installation_command": "uv add databend-driver",
    "adoption": "emerging",
    "supports": [
      "olap",
      "cloud",
      "columnar",
      "object_storage",
      "snowflake_compatible"
    ],
    "driver_package": "databend-driver",
    "notes": "Cloud-native Rust-based data warehouse with Snowflake-compatible SQL. Compute/storage separation on object storage (S3, GCS, Azure Blob). Uses Snowflake dialect as sqlglot translation proxy.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "databend",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "warehouse",
        "native_resource_keys": ["warehouse"],
        "native_size_keys": []
      },
      "default_deployment": "cloud",
      "deployment_modes": {
        "cloud": {
          "mode": "managed",
          "display_name": "Databend Cloud",
          "description": "Databend Cloud managed service",
          "requires_credentials": true,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "databend-driver"
          ],
          "auth_methods": [
            "password"
          ]
        },
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "Databend Self-Hosted",
          "description": "User-managed Databend cluster with object storage",
          "requires_credentials": true,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "databend-driver"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "doris",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.doris",
      "class_name": "DorisAdapter",
      "registration_order": 28,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "Apache Doris",
    "description": "MPP OLAP • Real-time analytics • MySQL protocol",
    "category": "distributed",
    "libraries": [
      {
        "name": "pymysql",
        "required": true
      }
    ],
    "requirements": [
      "pymysql>=1.0.0"
    ],
    "installation_command": "uv add pymysql",
    "adoption": "emerging",
    "supports": [
      "olap",
      "mpp",
      "columnar",
      "real-time",
      "vectorized"
    ],
    "driver_package": "pymysql",
    "notes": "Apache Doris 2.0+ with vectorized execution. MySQL protocol on port 9030, Stream Load on port 8030. SQLGlot 'doris' dialect.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "doris",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "Apache Doris Self-Hosted",
          "description": "Self-hosted Apache Doris cluster",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "pymysql"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "singlestore",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.singlestore",
      "class_name": "SingleStoreAdapter",
      "registration_order": 29,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "SingleStore",
    "description": "Distributed SQL • Real-time analytics • MySQL protocol",
    "category": "distributed",
    "libraries": [
      {
        "name": "singlestoredb",
        "required": true,
        "import_name": "singlestoredb"
      }
    ],
    "requirements": [
      "singlestoredb>=1.0.0"
    ],
    "installation_command": "uv add singlestoredb",
    "adoption": "emerging",
    "supports": [
      "olap",
      "htap",
      "distributed",
      "columnstore",
      "real-time",
      "mysql-compatible"
    ],
    "driver_package": "singlestoredb",
    "notes": "SingleStore 8.0+ with columnstore analytics. MySQL wire protocol on port 3306. SQLGlot 'mysql' dialect. Supports both Helios (cloud) and self-managed deployments.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "singlestore",
      "cost_class": "paid_compute",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "SingleStore Self-Managed",
          "description": "Self-managed SingleStore cluster",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "singlestoredb"
          ],
          "auth_methods": [
            "password"
          ]
        },
        "cloud": {
          "mode": "managed",
          "display_name": "SingleStore Helios",
          "description": "SingleStore Helios managed cloud service",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "singlestoredb"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "required_credentials": [
      "host",
      "port",
      "database",
      "username",
      "password"
    ],
    "support_status": "beta"
  },
  {
    "key": "influxdb",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.influxdb",
      "class_name": "InfluxDBAdapter",
      "registration_order": 30
    },
    "display_name": "InfluxDB",
    "description": "Time series database • FlightSQL • Arrow-native",
    "category": "timeseries",
    "libraries": [
      {
        "name": "influxdb3",
        "required": true,
        "import_name": "influxdb3"
      },
      {
        "name": "flightsql",
        "required": false,
        "alternative": true,
        "import_name": "flightsql"
      }
    ],
    "requirements": [
      "influxdb3-python>=0.1.0"
    ],
    "installation_command": "uv add influxdb3-python",
    "adoption": "niche",
    "supports": [
      "timeseries",
      "olap",
      "arrow",
      "flightsql"
    ],
    "driver_package": "influxdb3-python",
    "notes": "InfluxDB 3.x time series database with native SQL support via FlightSQL. Built on Apache Arrow, DataFusion, and Parquet. Optimized for TSBS DevOps workloads.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "default_deployment": "cloud",
      "deployment_modes": {
        "core": {
          "mode": "self-hosted",
          "display_name": "InfluxDB Core",
          "description": "Self-hosted InfluxDB Core server",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "influxdb3"
          ],
          "auth_methods": [
            "password"
          ]
        },
        "cloud": {
          "mode": "managed",
          "display_name": "InfluxDB Cloud",
          "description": "InfluxDB Cloud managed service",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "influxdb3"
          ],
          "auth_methods": [
            "token"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "questdb",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.questdb",
      "class_name": "QuestDBAdapter",
      "registration_order": 22,
      "stream_connection_capability": "independent_connection"
    },
    "display_name": "QuestDB",
    "description": "Time-series database • PG wire protocol • High-performance ingestion",
    "category": "timeseries",
    "libraries": [
      {
        "name": "psycopg",
        "required": true
      },
      {
        "name": "requests",
        "required": true
      }
    ],
    "requirements": [
      "psycopg[binary]>=3.1",
      "requests>=2.28.0"
    ],
    "installation_command": "uv add benchbox --extra questdb",
    "adoption": "emerging",
    "supports": [
      "timeseries",
      "olap",
      "columnar",
      "high_throughput"
    ],
    "driver_package": "psycopg",
    "notes": "QuestDB 7.0+ time-series database. PostgreSQL wire protocol for queries, REST API for data import. Optimized for fast ingestion and time-series analytics.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "questdb",
      "default_deployment": "self-hosted",
      "deployment_modes": {
        "self-hosted": {
          "mode": "self-hosted",
          "display_name": "QuestDB Self-Hosted",
          "description": "Self-hosted QuestDB server (Docker recommended)",
          "requires_credentials": true,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "psycopg[binary]"
          ],
          "auth_methods": [
            "password"
          ]
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "athena",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.athena",
      "class_name": "AthenaAdapter",
      "registration_order": 32
    },
    "display_name": "Amazon Athena",
    "description": "Serverless SQL • S3 data lake • Pay-per-query",
    "category": "cloud",
    "libraries": [
      {
        "name": "pyathena",
        "required": true
      },
      {
        "name": "boto3",
        "required": true
      }
    ],
    "requirements": [
      "pyathena>=3.0.0",
      "boto3>=1.20.0"
    ],
    "installation_command": "uv add pyathena boto3",
    "adoption": "established",
    "supports": [
      "olap",
      "serverless",
      "s3",
      "data_lake"
    ],
    "driver_package": "pyathena",
    "notes": "AWS serverless query service using Trino under the hood. Pay-per-query pricing ($5/TB scanned). Native S3 and Glue Data Catalog integration.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_credits",
      "compute": {
        "resource_kind": "workgroup",
        "native_resource_keys": ["workgroup"],
        "native_size_keys": []
      }
    },
    "required_credentials": [
      "s3_staging_dir",
      "region"
    ],
    "support_status": "beta"
  },
  {
    "key": "glue",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.aws",
      "class_name": "AWSGlueAdapter",
      "registration_order": 33
    },
    "display_name": "AWS Glue",
    "description": "Managed Spark • Serverless ETL • Pay-per-DPU",
    "category": "cloud",
    "libraries": [
      {
        "name": "boto3",
        "required": true
      }
    ],
    "requirements": [
      "boto3>=1.34.0"
    ],
    "installation_command": "uv add boto3",
    "adoption": "niche",
    "supports": [
      "olap",
      "serverless",
      "spark",
      "etl",
      "s3"
    ],
    "driver_package": "boto3",
    "notes": "AWS managed Spark ETL service. Pay-per-DPU pricing (~$0.44/DPU-hour). Uses Glue Data Catalog for metadata. Supports both SQL and DataFrame execution modes.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute"
    },
    "support_status": "experimental"
  },
  {
    "key": "emr-serverless",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.aws",
      "class_name": "EMRServerlessAdapter",
      "registration_order": 34
    },
    "display_name": "Amazon EMR Serverless",
    "description": "Serverless Spark • Sub-second startup • Pay-per-use",
    "category": "cloud",
    "libraries": [
      {
        "name": "boto3",
        "required": true
      }
    ],
    "requirements": [
      "boto3>=1.34.0"
    ],
    "installation_command": "uv add boto3",
    "adoption": "niche",
    "supports": [
      "olap",
      "serverless",
      "spark",
      "s3"
    ],
    "driver_package": "boto3",
    "notes": "AWS serverless Spark with automatic scaling and sub-second startup. Pay per vCPU-hour and memory-GB-hour. Uses Glue Data Catalog for metadata.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "application",
        "native_resource_keys": ["application_id"],
        "native_size_keys": []
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "athena-spark",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.aws",
      "class_name": "AthenaSparkAdapter",
      "registration_order": 35
    },
    "display_name": "Amazon Athena for Apache Spark",
    "description": "Interactive Spark • Sub-second startup • Session-based",
    "category": "cloud",
    "libraries": [
      {
        "name": "boto3",
        "required": true
      }
    ],
    "requirements": [
      "boto3>=1.34.0"
    ],
    "installation_command": "uv add boto3",
    "adoption": "niche",
    "supports": [
      "olap",
      "interactive",
      "spark",
      "s3",
      "sessions"
    ],
    "driver_package": "boto3",
    "notes": "AWS interactive Spark with notebook-style sessions. Sub-second startup with pre-provisioned capacity. Uses Glue Data Catalog for metadata. Pay per DPU-hour.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "workgroup",
        "native_resource_keys": ["workgroup"],
        "native_size_keys": []
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "dataproc",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.gcp",
      "class_name": "DataprocAdapter",
      "registration_order": 36
    },
    "display_name": "Google Cloud Dataproc",
    "description": "Managed Spark • Google Cloud clusters • Per-second billing",
    "category": "cloud",
    "libraries": [
      {
        "name": "google-cloud-dataproc",
        "required": true
      },
      {
        "name": "google-cloud-storage",
        "required": true
      }
    ],
    "requirements": [
      "google-cloud-dataproc>=5.0.0",
      "google-cloud-storage>=2.0.0"
    ],
    "installation_command": "uv add google-cloud-dataproc google-cloud-storage",
    "adoption": "niche",
    "supports": [
      "olap",
      "spark",
      "cluster",
      "gcs",
      "hive"
    ],
    "driver_package": "google-cloud-dataproc",
    "notes": "Google Cloud managed Spark service. Per-second billing with preemptible VM support. Supports persistent and ephemeral clusters. Uses Hive Metastore for table metadata.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "cluster",
        "native_resource_keys": ["cluster_name"],
        "native_size_keys": []
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "dataproc-serverless",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.gcp",
      "class_name": "DataprocServerlessAdapter",
      "registration_order": 37
    },
    "display_name": "Google Cloud Dataproc Serverless",
    "description": "Serverless Spark • No cluster management • Auto-scaling",
    "category": "cloud",
    "libraries": [
      {
        "name": "google-cloud-dataproc",
        "required": true
      },
      {
        "name": "google-cloud-storage",
        "required": true
      }
    ],
    "requirements": [
      "google-cloud-dataproc>=5.0.0",
      "google-cloud-storage>=2.0.0"
    ],
    "installation_command": "uv add google-cloud-dataproc google-cloud-storage",
    "adoption": "niche",
    "supports": [
      "olap",
      "spark",
      "serverless",
      "gcs",
      "hive"
    ],
    "driver_package": "google-cloud-dataproc",
    "notes": "Google Cloud Dataproc Serverless for fully managed Spark. No cluster management required. Sub-minute startup, auto-scaling, per-second billing. Uses Batch Controller API.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute"
    },
    "support_status": "experimental"
  },
  {
    "key": "fabric-spark",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.azure",
      "class_name": "FabricSparkAdapter",
      "registration_order": 38
    },
    "display_name": "Microsoft Fabric Spark",
    "description": "SaaS Spark • OneLake storage • Entra ID auth",
    "category": "cloud",
    "libraries": [
      {
        "name": "azure-identity",
        "required": true
      },
      {
        "name": "azure-storage-file-datalake",
        "required": true
      },
      {
        "name": "requests",
        "required": true
      }
    ],
    "requirements": [
      "azure-identity>=1.15.0",
      "azure-storage-file-datalake>=12.14.0",
      "requests>=2.31.0"
    ],
    "installation_command": "uv add azure-identity azure-storage-file-datalake requests",
    "adoption": "niche",
    "supports": [
      "olap",
      "spark",
      "saas",
      "delta",
      "onelake"
    ],
    "driver_package": "azure-identity",
    "notes": "Microsoft Fabric SaaS Spark with OneLake storage. Uses Livy API for session management. Entra ID (Azure AD) authentication. Capacity Units billing model.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "pool",
        "native_resource_keys": ["spark_pool_name"],
        "native_size_keys": []
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "fabric-lakehouse",
    "aliases": [
      {
        "name": "fabric_lakehouse",
        "scopes": ["registry"]
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.fabric_lakehouse",
      "class_name": "FabricLakehouseAdapter",
      "registration_order": 39
    },
    "display_name": "Microsoft Fabric Lakehouse SQL",
    "description": "Read-only T-SQL endpoint • Lakehouse analytics",
    "category": "cloud",
    "libraries": [
      {
        "name": "pyodbc",
        "required": true
      },
      {
        "name": "azure-identity",
        "required": true
      }
    ],
    "requirements": [
      "pyodbc>=4.0.39",
      "azure-identity>=1.15.0"
    ],
    "installation_command": "uv add benchbox --extra fabric",
    "adoption": "niche",
    "supports": [
      "olap",
      "cloud",
      "read_only",
      "delta",
      "onelake"
    ],
    "driver_package": "pyodbc",
    "notes": "Fabric Lakehouse SQL Analytics Endpoint is read-only. Use fabric-spark for generate/load phases and fabric-lakehouse for query phases.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "cost_class": "paid_compute"
    },
    "support_status": "beta"
  },
  {
    "key": "synapse-spark",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.azure",
      "class_name": "SynapseSparkAdapter",
      "registration_order": 40
    },
    "display_name": "Azure Synapse Analytics Spark",
    "description": "Enterprise Spark • ADLS Gen2 • Spark pools",
    "category": "cloud",
    "libraries": [
      {
        "name": "azure-identity",
        "required": true
      },
      {
        "name": "azure-storage-file-datalake",
        "required": true
      },
      {
        "name": "requests",
        "required": true
      }
    ],
    "requirements": [
      "azure-identity>=1.15.0",
      "azure-storage-file-datalake>=12.14.0",
      "requests>=2.31.0"
    ],
    "installation_command": "uv add azure-identity azure-storage-file-datalake requests",
    "adoption": "niche",
    "supports": [
      "olap",
      "spark",
      "enterprise",
      "adls",
      "hive"
    ],
    "driver_package": "azure-identity",
    "notes": "Azure Synapse Analytics Spark with ADLS Gen2 storage. Uses Livy API for session management. vCore-hour billing. Supports external Hive Metastore.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": "pool",
        "native_resource_keys": ["spark_pool_name"],
        "native_size_keys": []
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "spark",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.spark",
      "class_name": "SparkAdapter",
      "registration_order": 41,
      "stream_connection_capability": "shared_cursor"
    },
    "display_name": "Apache Spark",
    "description": "Distributed SQL • Local/cluster • Spark engine",
    "category": "distributed",
    "libraries": [
      {
        "name": "pyspark",
        "required": true
      }
    ],
    "requirements": [
      "pyspark>=3.5.0"
    ],
    "installation_command": "uv add pyspark",
    "adoption": "mainstream",
    "supports": [
      "olap",
      "distributed",
      "spark",
      "batch"
    ],
    "driver_package": "pyspark",
    "notes": "Apache Spark distributed SQL engine. Supports local, standalone, YARN, and Kubernetes modes. Use 'pyspark' for DataFrame API benchmarking.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql"
    },
    "support_status": "beta"
  },
  {
    "key": "velox",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.velox",
      "class_name": "VeloxAdapter",
      "registration_order": 43
    },
    "display_name": "Apache Gluten + Velox",
    "description": "Spark SQL • Native C++ acceleration • Gluten plugin",
    "category": "distributed",
    "libraries": [
      {
        "name": "pyspark",
        "required": true
      }
    ],
    "requirements": [
      "pyspark>=3.5.0"
    ],
    "installation_command": "uv add benchbox --extra velox",
    "adoption": "emerging",
    "supports": [
      "olap",
      "distributed",
      "spark",
      "native",
      "accelerated",
      "batch"
    ],
    "driver_package": "pyspark",
    "notes": "Apache Gluten + Velox accelerates Spark SQL by offloading physical operators to a vectorized C++ engine. Requires the Gluten bundle jar on the execution host. Linux only for local mode; Docker is the primary path on macOS/Windows. See docs/platforms/velox.md and docker/velox/.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": false,
      "default_mode": "sql",
      "platform_family": "spark",
      "default_deployment": "local",
      "deployment_modes": {
        "local": {
          "mode": "local",
          "display_name": "Velox Local",
          "description": "SparkSession with Gluten jar on local Linux host (or Docker container)",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "pyspark"
          ],
          "auth_methods": []
        },
        "remote": {
          "mode": "self-hosted",
          "display_name": "Velox Remote",
          "description": "Connect to a pre-started Spark-Connect server with Gluten wired",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "pyspark"
          ],
          "auth_methods": []
        }
      },
      "execution_engines": {
        "velox": {
          "class": "native-vectorized",
          "display_name": "Velox",
          "description": "Gluten offloads supported Spark operators to Velox",
          "dependencies": [],
          "selectable": false
        },
        "standard": {
          "class": "standard",
          "display_name": "Standard",
          "description": "Spark executes operators without a Velox offload",
          "dependencies": [],
          "selectable": false
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "lakesail",
    "aliases": [
      {
        "name": "lakesail-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.lakesail",
      "class_name": "LakeSailAdapter",
      "registration_order": 42
    },
    "display_name": "LakeSail Sail",
    "description": "Spark-compatible SQL • Rust/DataFusion • Spark Connect",
    "category": "analytical",
    "libraries": [
      {
        "name": "pyspark",
        "required": true
      }
    ],
    "requirements": [
      "pyspark>=3.4.0"
    ],
    "installation_command": "uv add pyspark",
    "adoption": "emerging",
    "supports": [
      "olap",
      "spark_compatible",
      "datafusion",
      "rust",
      "batch"
    ],
    "driver_package": "pyspark",
    "notes": "LakeSail Sail is a Rust-based drop-in Spark replacement built on DataFusion. Connects via Spark Connect protocol using standard PySpark client. 4x faster than Apache Spark on TPC-H SF100.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "platform_family": "spark",
      "default_deployment": "local",
      "deployment_modes": {
        "local": {
          "mode": "local",
          "display_name": "LakeSail Local",
          "description": "Single-node multi-threaded execution",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "pyspark"
          ],
          "auth_methods": []
        },
        "distributed": {
          "mode": "self-hosted",
          "display_name": "LakeSail Distributed",
          "description": "Distributed cluster of Rust workers",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": true,
          "default_for_platform": false,
          "dependencies": [
            "pyspark"
          ],
          "auth_methods": []
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "snowpark-connect",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.snowpark_connect",
      "class_name": "SnowparkConnectAdapter",
      "registration_order": 45
    },
    "display_name": "Snowpark Connect for Spark",
    "description": "PySpark API • Snowflake native • No cluster required",
    "category": "cloud",
    "libraries": [
      {
        "name": "snowflake.snowpark",
        "required": true,
        "import_name": "snowflake.snowpark"
      }
    ],
    "requirements": [
      "snowflake-snowpark-python>=1.20.0"
    ],
    "installation_command": "uv add snowflake-snowpark-python",
    "adoption": "niche",
    "supports": [
      "olap",
      "pyspark_compatible",
      "snowflake",
      "dataframe"
    ],
    "driver_package": "snowflake-snowpark-python",
    "notes": "PySpark DataFrame API compatibility layer on Snowflake. NOT Apache Spark - translates DataFrame operations to Snowflake SQL. No Spark cluster required.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "dataframe",
      "cost_class": "paid_credits",
      "compute": {
        "resource_kind": "warehouse",
        "native_resource_keys": ["warehouse"],
        "native_size_keys": ["warehouse_size"]
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "quanton",
    "aliases": [],
    "adapter": {
      "module": "benchbox.platforms.onehouse",
      "class_name": "QuantonAdapter",
      "registration_order": 46
    },
    "display_name": "Onehouse Quanton",
    "description": "Serverless Spark • Hudi/Iceberg/Delta • 2-3x faster",
    "category": "cloud",
    "libraries": [
      {
        "name": "requests",
        "required": true
      },
      {
        "name": "boto3",
        "required": true
      }
    ],
    "requirements": [
      "requests>=2.31.0",
      "boto3>=1.34.0"
    ],
    "installation_command": "uv add requests boto3",
    "adoption": "emerging",
    "supports": [
      "olap",
      "serverless",
      "spark",
      "hudi",
      "iceberg",
      "delta",
      "s3",
      "lakehouse"
    ],
    "driver_package": "requests",
    "notes": "Onehouse Quanton serverless Spark. Multi-table-format support (Hudi, Iceberg, Delta). XTable cross-format metadata translation. 2-3x better price-performance than EMR/Databricks.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "sql",
      "platform_family": "spark",
      "cost_class": "paid_compute",
      "compute": {
        "resource_kind": null,
        "native_resource_keys": [],
        "native_size_keys": ["cluster_size"],
        "size_choices": ["small", "medium", "large", "xlarge"],
        "size_default": "small"
      },
      "default_deployment": "managed",
      "deployment_modes": {
        "managed": {
          "mode": "managed",
          "display_name": "Onehouse Quanton",
          "description": "Serverless managed Spark on Onehouse",
          "requires_credentials": true,
          "requires_cloud_storage": true,
          "requires_network": true,
          "default_for_platform": true,
          "dependencies": [
            "requests",
            "boto3"
          ],
          "auth_methods": [
            "api_key"
          ]
        }
      }
    },
    "support_status": "experimental"
  },
  {
    "key": "pandas",
    "aliases": [
      {
        "name": "pandas-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      }
    ],
    "adapter": null,
    "display_name": "Pandas",
    "description": "Python DataFrame library • In-memory • Single-node",
    "category": "dataframe",
    "libraries": [
      {
        "name": "pandas",
        "required": true
      }
    ],
    "requirements": [
      "pandas>=3.0.0"
    ],
    "installation_command": "uv add pandas",
    "adoption": "emerging",
    "supports": [
      "dataframe",
      "in_memory"
    ],
    "driver_package": null,
    "capabilities": {
      "supports_sql": false,
      "supports_dataframe": true,
      "default_mode": "dataframe"
    },
    "support_status": "stable"
  },
  {
    "key": "cudf",
    "aliases": [
      {
        "name": "cudf-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      }
    ],
    "adapter": null,
    "display_name": "cuDF",
    "description": "GPU DataFrame • NVIDIA RAPIDS • CUDA required",
    "category": "dataframe",
    "libraries": [
      {
        "name": "cudf",
        "required": true
      }
    ],
    "requirements": [
      "cudf-cu12>=24.0.0"
    ],
    "installation_command": "pip install cudf-cu12 (requires NVIDIA GPU)",
    "adoption": "niche",
    "supports": [
      "dataframe",
      "gpu"
    ],
    "driver_package": null,
    "capabilities": {
      "supports_sql": false,
      "supports_dataframe": true,
      "default_mode": "dataframe"
    },
    "support_status": "experimental"
  },
  {
    "key": "dask",
    "aliases": [
      {
        "name": "dask-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      }
    ],
    "adapter": null,
    "display_name": "Dask",
    "description": "Distributed DataFrame • Lazy eval • Cluster-scale",
    "category": "dataframe",
    "libraries": [
      {
        "name": "dask",
        "required": true
      }
    ],
    "requirements": [
      "dask[distributed]>=2024.0.0"
    ],
    "installation_command": "uv add dask[distributed]",
    "adoption": "niche",
    "supports": [
      "dataframe",
      "distributed",
      "lazy"
    ],
    "driver_package": null,
    "capabilities": {
      "supports_sql": false,
      "supports_dataframe": true,
      "default_mode": "dataframe",
      "execution_engines": {
        "threaded": {
          "class": "standard",
          "display_name": "Threaded",
          "description": "Dask uses its local threaded scheduler",
          "dependencies": [],
          "selectable": true
        },
        "distributed": {
          "class": "distributed",
          "display_name": "Distributed",
          "description": "Dask uses a local cluster or connected distributed scheduler",
          "dependencies": [],
          "selectable": true
        }
      }
    },
    "support_status": "beta"
  },
  {
    "key": "pyspark",
    "aliases": [
      {
        "name": "pyspark-df",
        "scopes": ["cli"],
        "implied_mode": "dataframe"
      }
    ],
    "adapter": {
      "module": "benchbox.platforms.pyspark",
      "class_name": "PySparkSQLAdapter",
      "registration_order": 25,
      "stream_connection_capability": "shared_cursor"
    },
    "display_name": "PySpark",
    "description": "Spark DataFrame API • Distributed • Java 17+",
    "category": "dataframe",
    "libraries": [
      {
        "name": "pyspark",
        "required": true
      }
    ],
    "requirements": [
      "pyspark>=3.5.0"
    ],
    "installation_command": "uv add pyspark",
    "adoption": "established",
    "supports": [
      "dataframe",
      "distributed",
      "spark"
    ],
    "driver_package": null,
    "notes": "Requires Java 17 or 21. Java 23+ not supported by PySpark 4.x.",
    "capabilities": {
      "supports_sql": true,
      "supports_dataframe": true,
      "default_mode": "dataframe",
      "platform_family": "spark",
      "default_deployment": "local",
      "deployment_modes": {
        "local": {
          "mode": "local",
          "display_name": "PySpark Local",
          "description": "Local PySpark with single-node Spark",
          "requires_credentials": false,
          "requires_cloud_storage": false,
          "requires_network": false,
          "default_for_platform": true,
          "dependencies": [
            "pyspark"
          ],
          "auth_methods": []
        }
      }
    },
    "support_status": "beta"
  }
]"""

PLATFORM_MANIFEST: tuple[PlatformManifestEntry, ...] = _load_manifest(_PLATFORM_MANIFEST_JSON)
PLATFORM_MANIFEST_BY_KEY: Mapping[str, PlatformManifestEntry] = MappingProxyType(
    {entry.key: entry for entry in PLATFORM_MANIFEST}
)


def get_platform_manifest_entry(key: str) -> PlatformManifestEntry | None:
    return PLATFORM_MANIFEST_BY_KEY.get(key.lower())


def is_valid_platform_key(key: str) -> bool:
    return bool(_PLATFORM_KEY_PATTERN.fullmatch(key))


def get_platform_aliases(scope: AliasScope) -> dict[str, str]:
    return {alias.name: entry.key for entry in PLATFORM_MANIFEST for alias in entry.aliases if scope in alias.scopes}


def get_all_platform_aliases() -> dict[str, str]:
    return {alias.name: entry.key for entry in PLATFORM_MANIFEST for alias in entry.aliases}


def get_platform_alias_modes(scope: AliasScope) -> dict[str, DefaultMode]:
    return {
        alias.name: alias.implied_mode
        for entry in PLATFORM_MANIFEST
        for alias in entry.aliases
        if scope in alias.scopes and alias.implied_mode is not None
    }


def get_platform_metadata() -> dict[str, dict[str, Any]]:
    return {entry.key: entry.metadata_dict() for entry in PLATFORM_MANIFEST}


def get_adapter_imports() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (entry.key, entry.adapter.module, entry.adapter.class_name)
        for entry in sorted(
            PLATFORM_MANIFEST,
            key=lambda item: item.adapter.registration_order if item.adapter is not None else len(PLATFORM_MANIFEST),
        )
        if entry.adapter is not None
    )


__all__ = [
    "AdapterImportSpec",
    "COMPUTE_RESOURCE_KINDS",
    "DEFAULT_EXECUTION_ENGINE",
    "DefaultMode",
    "EXECUTION_ENGINE_CLASSES",
    "PLATFORM_MANIFEST",
    "PLATFORM_MANIFEST_BY_KEY",
    "PlatformManifestEntry",
    "PlatformAliasSpec",
    "SUPPORT_STATUS_VALUES",
    "SupportStatus",
    "get_all_platform_aliases",
    "get_adapter_imports",
    "get_platform_aliases",
    "get_platform_alias_modes",
    "get_platform_manifest_entry",
    "get_platform_metadata",
    "is_valid_platform_key",
]
