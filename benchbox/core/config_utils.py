# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import argparse
import json
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from benchbox.core.deployment import DEPLOYMENT_SELECTOR_OPTION
from benchbox.platforms.clickhouse.deployment_mode import resolve_clickhouse_deployment_mode
from benchbox.utils.database_naming import generate_database_filename
from benchbox.utils.output_path import normalize_output_root
from benchbox.utils.printing import emit


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


def load_config_file(config_path: Union[str, Path]) -> dict[str, Any]:
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, encoding="utf-8") as f:
            file_content = f.read()

        if config_path.suffix.lower() in [".yaml", ".yml"]:
            config_data = yaml.safe_load(file_content)
        elif config_path.suffix.lower() == ".json":
            config_data = json.loads(file_content)
        else:
            try:
                config_data = yaml.safe_load(file_content)
            except yaml.YAMLError:
                config_data = json.loads(file_content)

        return config_data or {}

    except (yaml.YAMLError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to parse configuration file: {e}") from e
    except Exception as e:
        raise ValueError(f"Error loading configuration: {e}") from e


def save_config_file(config_data: dict[str, Any], config_path: Union[str, Path], format: str = "yaml") -> None:
    config_path = Path(config_path)

    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            if format.lower() == "json":
                json.dump(config_data, f, indent=2)
            elif format.lower() in ["yaml", "yml"]:
                yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
            else:
                raise ValueError(f"Unsupported format: {format}")

    except Exception as e:
        raise ValueError(f"Failed to save configuration: {e}") from e


def build_benchmark_config(
    args_or_config: Union[Any, dict[str, Any]], platform: Optional[str] = None
) -> dict[str, Any]:
    if isinstance(args_or_config, dict):
        config = args_or_config
        benchmark_config = {
            "scale_factor": config.get("scale_factor", 0.01),
            "verbose": config.get("very_verbose", False),
            "force_regenerate": config.get("force", False),
        }

        plat = config.get("platform", platform)
        bname = config.get("benchmark")
        out = config.get("output")

        if out:
            benchmark_config["output_dir"] = normalize_output_root(out, bname, config.get("scale_factor", 0.01))
        else:
            benchmark_config["output_dir"] = _get_default_output_dir(
                plat, bname, config.get("scale_factor", 0.01), config.get("data_path")
            )

        if config.get("compress"):
            benchmark_config.update({"compress_data": True, "compression_type": "zstd"})

    else:
        args = args_or_config
        benchmark_config = {
            "scale_factor": getattr(args, "scale", 0.01),
            "verbose": bool(getattr(args, "verbose", 0)),
            "force_regenerate": bool(getattr(args, "force", False)),
        }

        plat = platform or getattr(args, "platform", None) or "duckdb"
        bname = getattr(args, "benchmark", "bench")
        out = getattr(args, "output", None)

        if out:
            benchmark_config["output_dir"] = normalize_output_root(out, bname, getattr(args, "scale", 0.01))
        else:
            benchmark_config["output_dir"] = _get_default_output_dir(
                plat, bname, getattr(args, "scale", 0.01), getattr(args, "data_path", None)
            )

        if getattr(args, "compress", False):
            benchmark_config.update({"compress_data": True, "compression_type": "zstd"})

    return benchmark_config


def build_platform_adapter_config(
    platform: str,
    args_or_config: Union[Any, dict[str, Any]],
    system_profile: Optional[Any] = None,
    benchmark_name: Optional[str] = None,
    scale_factor: Optional[float] = None,
) -> dict[str, Any]:
    platform = platform.lower()
    cfg = {}

    if isinstance(args_or_config, dict):
        config = args_or_config

        def get_value(key, default=None):
            return config.get(key, default)
    else:
        args = args_or_config

        def get_value(key, default=None):
            return getattr(args, key, default)

    if platform == "duckdb":
        db_path = get_value("duckdb_database_path")
        if not db_path:
            from benchbox.utils.path_utils import get_benchmark_runs_databases_path

            bname = benchmark_name or get_value("benchmark", "bench")
            sf = scale_factor if scale_factor is not None else get_value("scale", 0.01)
            data_dir = get_benchmark_runs_databases_path(bname, sf)
            db_filename = generate_database_filename(
                benchmark_name=bname, scale_factor=sf, platform="duckdb", tuning_config=None
            )
            db_path = str(data_dir / db_filename)

        cfg["database_path"] = db_path
        cfg["memory_limit"] = get_value("memory_limit", "4GB")
        cfg["force_recreate"] = bool(get_value("force", False))

    elif platform == "datafusion":
        working_dir = get_value("datafusion_working_dir")
        if working_dir:
            cfg["working_dir"] = working_dir

        cfg["memory_limit"] = get_value("datafusion_memory_limit", "16G")
        cfg["partitions"] = get_value("datafusion_partitions")
        cfg["format"] = get_value("datafusion_format", "parquet")
        cfg["temp_dir"] = get_value("datafusion_temp_dir")
        cfg["batch_size"] = get_value("datafusion_batch_size", 8192)
        cfg["force_recreate"] = bool(get_value("force", False))

    elif platform == "databricks":
        cfg.update(
            {
                "server_hostname": get_value("server_hostname"),
                "http_path": get_value("http_path"),
                "access_token": get_value("access_token"),
                "catalog": get_value("catalog", "workspace"),
                "schema": f"{(benchmark_name or get_value('benchmark', 'bench'))}_schema",
            }
        )

    elif platform in {"clickhouse", "clickhouse-local", "clickhouse-server"}:
        from benchbox.core.removed_options import first_removed_option_message

        removed = first_removed_option_message(
            "clickhouse", [key for key in ("deployment_mode", "mode", "embedded") if get_value(key) is not None]
        )
        if removed is not None:
            raise ValueError(removed)
        if platform == "clickhouse-local":
            deployment = "local"
        elif platform == "clickhouse-server":
            deployment = "server"
        else:
            deployment = resolve_clickhouse_deployment_mode({})
        cfg[DEPLOYMENT_SELECTOR_OPTION] = deployment
        cfg["data_path"] = get_value("data_path", "/tmp/benchbox_ch_local")

        if deployment == "server":
            cfg.update(
                {
                    "host": get_value("host", "localhost"),
                    "port": get_value("port", 9000),
                    "user": get_value("user", "default"),
                    "password": get_value("password"),
                    "secure": bool(get_value("secure", False)),
                }
            )

    return cfg


def validate_config_sections(config: dict[str, Any], required_sections: list) -> bool:
    return all(section in config for section in required_sections)


def validate_numeric_config(config: dict[str, Any], validations: dict[str, Any]) -> list:
    errors = []

    for key, (min_val, max_val, required) in validations.items():
        if key not in config:
            if required:
                errors.append(f"Required configuration key '{key}' is missing")
            continue

        value = config[key]

        if not isinstance(value, (int, float)):
            errors.append(f"Configuration key '{key}' must be numeric, got {type(value).__name__}")
            continue

        if min_val is not None and value < min_val:
            errors.append(f"Configuration key '{key}' must be >= {min_val}, got {value}")

        if max_val is not None and value > max_val:
            errors.append(f"Configuration key '{key}' must be <= {max_val}, got {value}")

    return errors


def _get_default_output_dir(
    platform: Optional[str], benchmark_name: Optional[str], scale_factor: float, data_path: Optional[str] = None
) -> Optional[str]:
    if not platform or not benchmark_name:
        return None

    if platform.lower() in ["duckdb", "sqlite"]:
        from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

        data_dir = get_benchmark_runs_datagen_path(benchmark_name, scale_factor)
        return str(data_dir)
    elif platform.lower() in {"clickhouse", "clickhouse-local"}:
        return data_path or "/tmp/benchbox_ch_local"
    else:
        return None


def load_platform_config(platform: str, config_path: Optional[str] = None, verbose: bool = False) -> dict[str, Any]:
    config = {}

    if config_path:
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Platform config file not found: {config_path}")
        source = f"file: {config_path}"
    else:
        config_file = Path(f"examples/config/{platform}.yaml")
        if config_file.exists():
            source = f"default file: {config_file}"
        else:
            if verbose:
                emit("Platform config using built-in defaults")
            return config

    try:
        with open(config_file, encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
            if yaml_config and isinstance(yaml_config, dict):
                connection_config = yaml_config.get("connection", {})
                settings_config = yaml_config.get("settings", {})

                config.update(connection_config)
                if "extra_params" in connection_config:
                    config.update(connection_config["extra_params"])

                config.update(settings_config)

                if verbose:
                    emit(f"Platform config loaded from {source}")
    except Exception as e:
        if verbose:
            emit(f"Warning: Failed to load platform config from {config_file}: {e}")
            emit(f"Using built-in defaults for {platform}")

    return config


def load_tuning_config(
    platform: str, benchmark: str, tuning_mode: str, verbose: bool = False
) -> Optional[dict[str, Any]]:
    if tuning_mode in ["tuned", "notuning"]:
        config_file = Path(f"examples/tunings/{platform}/{benchmark}_{tuning_mode}.yaml")
        if not config_file.exists():
            if verbose:
                emit(f"Warning: Tuning config not found: {config_file}")
                emit("Proceeding without tuning configuration")
            return None
        source = f"auto-selected: {config_file}"
    else:
        config_file = Path(tuning_mode)
        if not config_file.exists():
            raise FileNotFoundError(f"Tuning config file not found: {tuning_mode}")
        source = f"custom file: {tuning_mode}"

    try:
        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f)
            if verbose and config:
                tuning_type = config.get("_metadata", {}).get("configuration_type", "unknown")
                emit(f"Tuning config loaded from {source} (type: {tuning_type})")
            return config
    except Exception as e:
        if verbose:
            emit(f"Warning: Failed to load tuning config from {config_file}: {e}")
        return None


def merge_all_configs(
    platform: str,
    benchmark: str,
    args: argparse.Namespace,
    tuning_mode: str = "tuned",
    platform_config_path: Optional[str] = None,
    verbose: bool = False,
) -> dict[str, Any]:
    config = {}

    defaults = get_builtin_defaults(platform, benchmark)
    config.update(defaults)
    if verbose:
        emit("✅ Applied built-in defaults")

    platform_config = load_platform_config(platform, platform_config_path, verbose)
    config.update(platform_config)

    tuning_config = load_tuning_config(platform, benchmark, tuning_mode, verbose)
    if tuning_config:
        config.update(tuning_config)
        config["tuning_config"] = tuning_config

    cli_config = extract_cli_config(args)
    config.update(cli_config)
    if "scale" in config:
        config["scale_factor"] = config["scale"]
    if verbose:
        emit("✅ Applied CLI arguments")

    config["platform"] = platform
    config["benchmark"] = benchmark
    config["verbose_enabled"] = config.get("verbose", 0) > 0
    config["very_verbose"] = config.get("verbose", 0) > 1
    if config.get("quiet", False):
        config["verbose_enabled"] = False
        config["very_verbose"] = False

    return config


def get_builtin_defaults(platform: str, benchmark: str) -> dict[str, Any]:
    defaults = {
        "scale_factor": 0.01,
        "phases": "power",
        "verbose": 0,
        "force": False,
        "compress": False,
        "streams": 2,
        "compression_type": "zstd",
        "force_recreate": False,
        "force_regenerate": False,
    }

    if platform == "duckdb":
        defaults.update(
            {
                "memory_limit": "4GB",
                "database_path": None,
            }
        )
    elif platform == "databricks":
        defaults.update(
            {
                "catalog": "workspace",
                "schema": "benchbox",
                "server_hostname": None,
                "http_path": None,
                "access_token": None,
            }
        )
    elif platform in {"clickhouse", "clickhouse-local", "clickhouse-server"}:
        defaults.update(
            {
                DEPLOYMENT_SELECTOR_OPTION: "local" if platform in {"clickhouse", "clickhouse-local"} else "server",
                "data_path": "/tmp/benchbox_ch_local",
            }
        )
    elif platform == "sqlite":
        defaults.update(
            {
                "timeout": 30.0,
                "database_path": None,
            }
        )
    elif platform == "bigquery":
        defaults.update(
            {
                "location": "US",
                "dataset_id": "benchbox",
                "storage_prefix": "benchbox-data",
                "project_id": None,
                "credentials_path": None,
                "storage_bucket": None,
            }
        )
    elif platform == "redshift":
        defaults.update(
            {
                "port": 5439,
                "database": "dev",
                "host": None,
                "username": None,
                "password": None,
                "compute_resource": None,
            }
        )
    elif platform == "snowflake":
        defaults.update(
            {
                "compute_resource": "COMPUTE_WH",
                "database": "BENCHBOX",
                "schema": "PUBLIC",
                "account": None,
                "username": None,
                "password": None,
                "role": None,
            }
        )

    return defaults


def extract_cli_config(args: argparse.Namespace) -> dict[str, Any]:
    config = {}

    for key, value in vars(args).items():
        if value is not None:
            config[key] = value

    if "duckdb_database_path" in config:
        config["database_path"] = config["duckdb_database_path"]
    if "sqlite_database_path" in config:
        config["database_path"] = config["sqlite_database_path"]
    if "data_path" in config:
        config["data_path"] = config["data_path"]

    platform_mappings = {
        "server_hostname": "server_hostname",
        "http_path": "http_path",
        "access_token": "access_token",
        "redshift_host": "host",
        "redshift_port": "port",
        "redshift_database": "database",
        "redshift_username": "username",
        "redshift_password": "password",
        "snowflake_database": "database",
        "snowflake_schema": "schema",
        "snowflake_username": "username",
        "snowflake_password": "password",
        "project_id": "project_id",
        "dataset_id": "dataset_id",
        "credentials_path": "credentials_path",
        "storage_bucket": "storage_bucket",
        "storage_prefix": "storage_prefix",
    }

    for old_key, new_key in platform_mappings.items():
        if old_key in config:
            config[new_key] = config[old_key]

    return config
