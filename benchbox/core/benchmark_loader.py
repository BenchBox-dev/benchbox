# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any, Union

from benchbox.core.benchmark_registry import (
    get_benchmark_optional_extras,
    get_core_benchmark_class_name,
    get_family_plugin,
    list_loader_benchmark_ids,
    validate_scale_factor,
)
from benchbox.core.schemas import BenchmarkConfig, SystemProfile
from benchbox.utils.dependencies import BenchmarkDependencyError, find_missing_module

BENCHMARK_LOADER_API_SURFACE = "internal"


COMPLIANCE_GATED_BENCHMARKS = frozenset({"tpcds", "tpch"})


def compliance_mode_kwargs(config: BenchmarkConfig) -> dict[str, Any]:
    if config.name.lower() not in COMPLIANCE_GATED_BENCHMARKS:
        return {}
    return {"official": bool(getattr(config, "official", False))}


def get_benchmark_instance(
    config: BenchmarkConfig,
    system_profile: SystemProfile | None = None,
    *,
    benchmark_class: type[Any] | None = None,
    output_dir: Union[str, Path] | None = None,
    verbose: Union[int, bool] | None = None,
    quiet: bool | None = None,
    benchmark_options: dict[str, Any] | None = None,
    extra_kwargs: dict[str, Any] | None = None,
    instantiate_fn: Any | None = None,
) -> Any:
    if benchmark_class is None:
        validate_scale_factor(config.name, config.scale_factor)
        plugin = get_family_plugin(config.name)
        if plugin is not None:
            return plugin.create(config, system_profile)
        benchmark_class = get_core_benchmark_class(config.name)

    cpu_cores = 1
    if system_profile:
        cpu_cores = getattr(system_profile, "cpu_cores_logical", 1)

    benchmark_kwargs: dict[str, Any] = {
        "scale_factor": getattr(config, "scale_factor", 1.0),
        "compress_data": getattr(config, "compress_data", False),
        "compression_type": getattr(config, "compression_type", None),
        "compression_level": getattr(config, "compression_level", None),
    }

    opts = getattr(config, "options", {}) or {}
    optional_kwargs: dict[str, Any] = {"parallel": cpu_cores}

    if output_dir is not None:
        optional_kwargs["output_dir"] = output_dir

    if verbose is not None:
        optional_kwargs["verbose"] = verbose
    if quiet is not None:
        optional_kwargs["quiet"] = quiet

    optional_kwargs["force_regenerate"] = bool(opts.get("force_regenerate"))

    options_to_forward = (
        dict(benchmark_options) if benchmark_options is not None else dict(opts.get("benchmark_options", {}))
    )
    optional_kwargs.update(options_to_forward)

    if extra_kwargs:
        optional_kwargs.update(extra_kwargs)

    optional_kwargs.update(compliance_mode_kwargs(config))

    instantiate = instantiate_fn or instantiate_benchmark_class
    return instantiate(benchmark_class, benchmark_kwargs, optional_kwargs)


def instantiate_benchmark_class(
    benchmark_class: type[Any],
    required_kwargs: dict[str, Any],
    optional_kwargs: dict[str, Any],
) -> Any:
    constructor_kwargs = dict(required_kwargs)
    for name, value in optional_kwargs.items():
        if constructor_accepts_argument(benchmark_class, name):
            constructor_kwargs[name] = value
    return benchmark_class(**constructor_kwargs)


def constructor_accepts_argument(benchmark_class: type[Any], argument_name: str) -> bool:
    try:
        parameters = inspect.signature(benchmark_class).parameters
    except (TypeError, ValueError):
        return False
    return argument_name in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def get_core_benchmark_class(benchmark_name: str) -> Any:
    benchmark_name = benchmark_name.lower()
    plugin = get_family_plugin(benchmark_name)
    if plugin is not None:
        return plugin.core_class
    module_name = f"benchbox.core.{benchmark_name}.benchmark"
    class_name = get_core_benchmark_class_name(benchmark_name) or f"{benchmark_name.capitalize()}Benchmark"

    try:
        module = importlib.import_module(module_name)
        benchmark_class = getattr(module, class_name)
        return benchmark_class
    except (ImportError, AttributeError) as e:
        missing_module = find_missing_module(e)
        if missing_module is not None:
            raise BenchmarkDependencyError(
                benchmark_name, missing_module, get_benchmark_optional_extras(benchmark_name)
            ) from e
        available = list_loader_benchmark_ids()
        raise ValueError(f"Benchmark '{benchmark_name}' not supported yet. Available: {', '.join(available)}") from e


def get_benchmark_class(benchmark_name: str) -> Any:
    return get_core_benchmark_class(benchmark_name)
