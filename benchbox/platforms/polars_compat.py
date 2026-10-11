from __future__ import annotations

import inspect
import sys
from functools import cache
from importlib import metadata

RUNTIME_MODULE_PREFIX = "_polars_runtime_"
RUNTIME_PACKAGE_ABSENT = "absent"
ENGINE_AFFINITY_KEY = "POLARS_ENGINE_AFFINITY"
IN_MEMORY_ENGINE = "in-memory"


@cache
def reader_accepts(reader: str, keyword: str) -> bool:
    import polars as pl

    return keyword in inspect.signature(getattr(pl, reader)).parameters


def csv_empty_string_option(preserve_empty_strings: bool) -> dict[str, bool]:
    if reader_accepts("scan_csv", "empty_string_is_null"):
        return {"empty_string_is_null": not preserve_empty_strings}
    return {"missing_utf8_is_empty_string": preserve_empty_strings}


def reader_rechunk_option(reader: str, rechunk: bool) -> dict[str, bool]:
    if reader_accepts(reader, "rechunk"):
        return {"rechunk": rechunk}
    return {}


def reader_rechunk_effective(rechunk: bool) -> bool:
    return rechunk and reader_accepts("scan_parquet", "rechunk")


@cache
def collect_accepts_engine() -> bool:
    import polars as pl

    return "engine" in inspect.signature(pl.LazyFrame.collect).parameters


@cache
def collect_engine_supported(engine: str) -> bool:
    if engine == "default":
        return True
    if not collect_accepts_engine():
        return False
    import polars as pl

    try:
        pl.LazyFrame({"probe": [0]}).collect(engine=engine)
    except (ValueError, TypeError):
        return False
    return True


@cache
def default_collect_engine() -> str:
    import polars as pl

    parameters = inspect.signature(pl.LazyFrame.collect).parameters
    if "engine" not in parameters:
        return IN_MEMORY_ENGINE
    default = parameters["engine"].default
    if isinstance(default, str) and default:
        return default
    return IN_MEMORY_ENGINE


def collect_affinity() -> str | None:
    import polars as pl

    value = pl.Config.state().get(ENGINE_AFFINITY_KEY)
    return str(value) if value else None


def active_runtime() -> tuple[str, str]:
    plr = sys.modules.get("polars._plr")
    module_name = getattr(plr, "__name__", "")
    if not module_name.startswith(RUNTIME_MODULE_PREFIX):
        return RUNTIME_PACKAGE_ABSENT, RUNTIME_PACKAGE_ABSENT
    package = module_name.split(".", 1)[0].removeprefix("_").replace("_", "-")
    version = getattr(plr, "__version__", None)
    if version is None:
        try:
            version = metadata.version(package)
        except metadata.PackageNotFoundError:
            version = RUNTIME_PACKAGE_ABSENT
    return package, str(version)
