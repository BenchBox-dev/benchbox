# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import re
from typing import Any, Optional

from benchbox.utils.scale_factor import format_scale_factor


def _tuning_config_to_dict(tuning_config: Optional[Any]) -> Optional[dict[str, Any]]:
    if not tuning_config:
        return None

    if isinstance(tuning_config, dict):
        return tuning_config

    if hasattr(tuning_config, "__dict__"):
        try:
            import dataclasses

            if dataclasses.is_dataclass(tuning_config):
                return dataclasses.asdict(tuning_config)
            else:
                return tuning_config.__dict__
        except (ImportError, AttributeError):
            return vars(tuning_config) if hasattr(tuning_config, "__dict__") else None

    return None


def _clean_name_component(name: str, max_length: Optional[int] = None) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", name.lower())

    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length]

    return cleaned


def _get_tuning_mode(tuning_config: Optional[Any]) -> str:
    config_dict = _tuning_config_to_dict(tuning_config)
    if not config_dict:
        return "notuning"

    metadata = config_dict.get("_metadata", {})
    config_type = metadata.get("configuration_type", "")

    if config_type == "notuning":
        return "notuning"
    elif config_type in ("tuned", "optimized"):
        return "tuned"

    primary_keys = config_dict.get("primary_keys", {})
    foreign_keys = config_dict.get("foreign_keys", {})
    platform_opts = config_dict.get("platform_optimizations", {})
    table_tunings = config_dict.get("table_tunings", {})

    if (
        not primary_keys.get("enabled", False)
        and not foreign_keys.get("enabled", False)
        and not table_tunings
        and not any(platform_opts.get(key, False) for key in platform_opts if "enabled" in str(key))
    ):
        return "notuning"

    return "custom"


def _get_constraints_suffix(tuning_config: Optional[Any]) -> str:
    config_dict = _tuning_config_to_dict(tuning_config)
    if not config_dict:
        return "noconstraints"

    components = []

    primary_keys = config_dict.get("primary_keys", {})
    if primary_keys.get("enabled", False):
        components.append("pk")

    foreign_keys = config_dict.get("foreign_keys", {})
    if foreign_keys.get("enabled", False):
        components.append("fk")

    unique_constraints = config_dict.get("unique_constraints", {})
    if unique_constraints.get("enabled", False):
        components.append("uniq")

    check_constraints = config_dict.get("check_constraints", {})
    if check_constraints.get("enabled", False):
        components.append("check")

    if not components:
        return "noconstraints"

    return "_".join(components)


def _get_optimizations_suffix(tuning_config: Optional[Any]) -> str:
    config_dict = _tuning_config_to_dict(tuning_config)
    if not config_dict:
        return ""

    components = []

    platform_opts = config_dict.get("platform_optimizations", {})
    table_tunings = config_dict.get("table_tunings", {})

    _PLATFORM_OPT_LABELS = [
        ("z_ordering_enabled", "zorder"),
        ("auto_optimize_enabled", "autoopt"),
        ("materialized_views_enabled", "matview"),
    ]
    components.extend(label for key, label in _PLATFORM_OPT_LABELS if platform_opts.get(key, False))

    _TABLE_TUNING_LABELS = [
        ("partitioning", "part"),
        ("clustering", "clust"),
        ("sorting", "sort"),
        ("distribution", "dist"),
    ]
    detected = {
        key for tc in table_tunings.values() if isinstance(tc, dict) for key, _ in _TABLE_TUNING_LABELS if tc.get(key)
    }
    components.extend(label for key, label in _TABLE_TUNING_LABELS if key in detected)

    return "_".join(components) if components else ""


def _canonicalize_for_hash(val: Any) -> Any:
    if isinstance(val, dict):
        items = sorted(val.items(), key=lambda item: (str(item[0]), type(item[0]).__name__, repr(item[0])))
        return [(key, _canonicalize_for_hash(value)) for key, value in items]
    if isinstance(val, list):
        return [_canonicalize_for_hash(item) for item in val]
    if isinstance(val, tuple):
        return tuple(_canonicalize_for_hash(item) for item in val)
    return val


def _get_config_hash(tuning_config: Optional[Any]) -> str:
    config_dict = _tuning_config_to_dict(tuning_config)
    if not config_dict:
        return "000000"

    config_copy = config_dict.copy()
    config_copy.pop("_metadata", None)

    config_str = str(_canonicalize_for_hash(config_copy))
    hash_obj = hashlib.md5(config_str.encode("utf-8"))
    return hash_obj.hexdigest()[:6]


def generate_database_name(
    benchmark_name: str,
    scale_factor: float,
    platform: str,
    tuning_config: Optional[Any] = None,
    custom_name: Optional[str] = None,
    template: str = "{benchmark}_{scale}_{tuning}_{constraints}_{optimizations}",
) -> str:
    if custom_name:
        return _clean_name_component(custom_name, max_length=64)

    tuning_dict = _tuning_config_to_dict(tuning_config)
    if tuning_dict and "_metadata" in tuning_dict:
        metadata_db_name = tuning_dict["_metadata"].get("database_name")
        if metadata_db_name:
            return _clean_name_component(metadata_db_name, max_length=64)

    benchmark = _clean_name_component(benchmark_name)
    scale = format_scale_factor(scale_factor)
    tuning_mode = _get_tuning_mode(tuning_config)
    constraints = _get_constraints_suffix(tuning_config)
    optimizations = _get_optimizations_suffix(tuning_config)

    name_parts = {
        "benchmark": benchmark,
        "scale": scale,
        "tuning": tuning_mode,
        "constraints": constraints,
        "optimizations": optimizations,
        "platform": _clean_name_component(platform),
        "hash": _get_config_hash(tuning_config),
    }

    try:
        name = template.format(**name_parts)
    except KeyError:
        name = f"{benchmark}_{scale}_{tuning_mode}_{constraints}"
        if optimizations:
            name += f"_{optimizations}"

    name = re.sub(r"_+", "_", name)
    name = re.sub(r"_$", "", name)
    name = re.sub(r"^_", "", name)

    max_length = 63
    if len(name) > max_length:
        hash_part = _get_config_hash(tuning_config)
        base_length = max_length - len(hash_part) - 1
        name = f"{name[:base_length]}_{hash_part}"

    return name


def generate_database_filename(
    benchmark_name: str,
    scale_factor: float,
    platform: str,
    tuning_config: Optional[Any] = None,
    custom_name: Optional[str] = None,
    template: str = "{benchmark}_{scale}_{tuning}_{constraints}_{optimizations}",
) -> str:
    name = generate_database_name(
        benchmark_name=benchmark_name,
        scale_factor=scale_factor,
        platform=platform,
        tuning_config=tuning_config,
        custom_name=custom_name,
        template=template,
    )

    extensions = {
        "duckdb": ".duckdb",
        "sqlite": ".sqlite",
        "sqlite3": ".sqlite",
        "clickhouse": ".chdb",
        "clickhouse-local": ".chdb",
        "datafusion": ".datafusion",
        "polars": ".polars",
        "pandas": ".pandas",
        "polars-df": ".polars-df",
        "pandas-df": ".pandas-df",
        "cudf-df": ".cudf-df",
        "dask-df": ".dask-df",
        "cudf": ".cudf",
        "spark": ".spark",
    }
    platform_lower = platform.lower()
    if platform_lower not in extensions:
        ext = f".{platform_lower}"
    else:
        ext = extensions[platform_lower]

    return f"{name}{ext}"


_KNOWN_EXTENSIONS = [
    ".duckdb",
    ".sqlite",
    ".chdb",
    ".datafusion",
    ".polars-df",
    ".polars",
    ".pandas-df",
    ".pandas",
    ".cudf-df",
    ".cudf",
    ".dask-df",
    ".spark",
]

_TUNING_KEYWORDS = {"notuning": "notuning", "tuned": "tuned", "custom": "custom"}
_CONSTRAINT_KEYWORDS = {"pk", "fk", "uniq", "check", "noconstraints"}
_OPTIMIZATION_KEYWORDS = {"part", "clust", "sort", "dist", "zorder", "autoopt", "matview"}


def _parse_scale_factor(part: str) -> float | None:
    if not part.startswith("sf"):
        return None
    scale_str = part[2:]
    try:
        if scale_str.startswith("0") and len(scale_str) > 1:
            return float("0." + scale_str[1:])
        return float(scale_str)
    except ValueError:
        return None


def parse_database_name(database_name: str) -> dict[str, Any]:
    name = database_name
    for ext in _KNOWN_EXTENSIONS:
        if name.endswith(ext):
            name = name[: -len(ext)]
            break

    parts = name.split("_")
    parts_set = set(parts)

    result: dict[str, Any] = {
        "original_name": database_name,
        "parsed_parts": parts,
        "benchmark": parts[0] if parts else "",
        "scale_factor": None,
        "tuning_mode": "unknown",
        "has_constraints": False,
        "has_optimizations": False,
        "characteristics": [],
    }

    for part in parts:
        sf = _parse_scale_factor(part)
        if sf is not None:
            result["scale_factor"] = sf
            break

    for keyword, mode in _TUNING_KEYWORDS.items():
        if keyword in parts_set:
            result["tuning_mode"] = mode
            break

    for part in parts:
        if part in _CONSTRAINT_KEYWORDS:
            result["has_constraints"] = part != "noconstraints"
            result["characteristics"].append(part)

    matched_opts = parts_set & _OPTIMIZATION_KEYWORDS
    if matched_opts:
        result["has_optimizations"] = True
        result["characteristics"].extend(p for p in parts if p in matched_opts)

    return result


def validate_database_name(name: str, platform: str) -> bool:
    if not name:
        return False

    if len(name) > 63:
        return False

    if not re.match(r"^[a-z][a-z0-9_]*$", name.lower()):
        return False

    return True


def list_database_configurations(database_names: list[str]) -> list[dict[str, Any]]:
    return [parse_database_name(name) for name in database_names]
