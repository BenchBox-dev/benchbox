# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Type, Union

__version__ = "0.5.0rc2"

from benchbox.base import BaseBenchmark
from benchbox.flightdata import FlightData
from benchbox.tpcds import TPCDS
from benchbox.tpch import TPCH
from benchbox.tpch_skew import TPCHSkew
from benchbox.tpchavoc import TPCHavoc
from benchbox.tsbs_devops import TSBSDevOps

from . import platforms

try:
    from benchbox.utils.version import validate_version_consistency

    try:
        validate_version_consistency()
    except RuntimeError as exc:
        import warnings

        warnings.warn(
            f"{exc}",
            UserWarning,
            stacklevel=2,
        )
except ImportError:
    pass

if TYPE_CHECKING:
    from benchbox.amplab import AMPLab
    from benchbox.clickbench import ClickBench
    from benchbox.coffeeshop import CoffeeShop
    from benchbox.h2odb import H2ODB
    from benchbox.joinorder import JoinOrder
    from benchbox.nyctaxi import NYCTaxi
    from benchbox.read_primitives import ReadPrimitives
    from benchbox.ssb import SSB
    from benchbox.tpcdi import TPCDI
    from benchbox.tpcds_obt import TPCDSOBT


@dataclass(frozen=True)
class _BenchmarkSpec:
    module: str
    class_name: str
    optional_dependencies: tuple[str, ...] = ()
    store_errors: bool = True


_BENCHMARK_REGISTRY: dict[str, _BenchmarkSpec] = {
    "TPCDI": _BenchmarkSpec("tpcdi", "TPCDI", ("tpcdi",)),
    "SSB": _BenchmarkSpec("ssb", "SSB", ("ssb",)),
    "AMPLab": _BenchmarkSpec("amplab", "AMPLab", ("amplab",)),
    "H2ODB": _BenchmarkSpec("h2odb", "H2ODB", ("h2odb",)),
    "ClickBench": _BenchmarkSpec("clickbench", "ClickBench", ("clickbench",)),
    "MetadataPrimitives": _BenchmarkSpec("metadata_primitives", "MetadataPrimitives", ()),
    "ReadPrimitives": _BenchmarkSpec("read_primitives", "ReadPrimitives", ()),
    "WritePrimitives": _BenchmarkSpec("write_primitives", "WritePrimitives", ()),
    "TransactionPrimitives": _BenchmarkSpec("transaction_primitives", "TransactionPrimitives", ()),
    "JoinOrder": _BenchmarkSpec("joinorder", "JoinOrder", ("joinorder",)),
    "NYCTaxi": _BenchmarkSpec("nyctaxi", "NYCTaxi", ()),
    "CoffeeShop": _BenchmarkSpec("coffeeshop", "CoffeeShop", ("coffeeshop",)),
    "DataVault": _BenchmarkSpec("datavault", "DataVault", ()),
    "TPCDSOBT": _BenchmarkSpec("tpcds_obt", "TPCDSOBT", ()),
    "VectorSearch": _BenchmarkSpec("vector_search", "VectorSearch", ()),
}


_PUBLIC_REEXPORTS: dict[str, tuple[str, str]] = {
    "TPCH_DATAFRAME_QUERIES": ("benchbox.core.tpch.dataframe_queries", "TPCH_DATAFRAME_QUERIES"),
    "TPCDS_DATAFRAME_QUERIES": ("benchbox.core.tpcds.dataframe_queries", "TPCDS_DATAFRAME_QUERIES"),
    "DATAFRAME_PLATFORMS": ("benchbox.platforms.dataframe.platform_checker", "DATAFRAME_PLATFORMS"),
}

_lazy_cache: dict[str, Union[type[BaseBenchmark], ImportError, None]] = {}


def _import_module(module_path: str):

    return import_module(module_path)


def _load_benchmark_class(name: str) -> tuple[Optional[type[BaseBenchmark]], Optional[ImportError]]:

    cached = _lazy_cache.get(name)
    if isinstance(cached, ImportError):
        return None, cached
    if cached is not None:
        return cached, None

    spec = _BENCHMARK_REGISTRY[name]
    module_path = f"benchbox.{spec.module}"
    logger = logging.getLogger(module_path)

    try:
        module = _import_module(module_path)
        benchmark_class = getattr(module, spec.class_name)
        _lazy_cache[name] = benchmark_class
        logger.debug("Successfully lazy-loaded %s from %s", spec.class_name, module_path)
        return benchmark_class, None
    except ImportError as exc:
        if spec.store_errors:
            _lazy_cache[name] = exc
        else:
            _lazy_cache[name] = None
        logger.debug("Failed to lazy-load %s from %s: %s", spec.class_name, module_path, exc)
        return None, exc


def _clear_lazy_cache() -> None:

    _lazy_cache.clear()


def __getattr__(name: str) -> Any:
    try:
        from benchbox.utils.version import create_import_error
    except ImportError:

        def create_import_error(benchmark_name, missing_dependencies=None, original_error=None):
            return ImportError(f"Could not import {benchmark_name}")

    if name == "platforms":
        return platforms

    if name in _PUBLIC_REEXPORTS:
        module_path, attr = _PUBLIC_REEXPORTS[name]
        return getattr(import_module(module_path), attr)

    if name in _BENCHMARK_REGISTRY:
        spec = _BENCHMARK_REGISTRY[name]
        cls, original_error = _load_benchmark_class(name)
        if cls is None:
            raise create_import_error(
                benchmark_name=name,
                missing_dependencies=list(spec.optional_dependencies) or None,
                original_error=original_error,
            )
        return cls
    else:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "platforms",
    "BaseBenchmark",
    "TPCH",
    "TPCDS",
    "TPCHavoc",
    "TPCHSkew",
    "TSBSDevOps",
    "NYCTaxi",
    "FlightData",
    "TPCDI",
    "SSB",
    "AMPLab",
    "H2ODB",
    "ClickBench",
    "MetadataPrimitives",
    "ReadPrimitives",
    "WritePrimitives",
    "TransactionPrimitives",
    "JoinOrder",
    "CoffeeShop",
    "DataVault",
    "TPCDSOBT",
    "VectorSearch",
    "TPCH_DATAFRAME_QUERIES",
    "TPCDS_DATAFRAME_QUERIES",
    "DATAFRAME_PLATFORMS",
]
