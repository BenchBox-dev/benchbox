from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from benchbox.utils.datagen_backend import (
    NATIVE_BACKEND_NAME,
    DatagenBackendError,
    manifest_backend_name,
    validate_native_scale,
)

DELIMITED_EXTENSIONS: dict[str, str] = {"tpch": ".tbl", "tpcds": ".dat"}

PARQUET_WRITER_METADATA_KEY = b"benchbox.parquet_writer"
PARQUET_ROW_GROUP_ROWS = 122_880


@dataclass(frozen=True)
class NativeTableFile:
    path: Path
    rows: int
    size_bytes: int


@dataclass(frozen=True)
class NativeGeneration:
    version: str
    files: dict[str, NativeTableFile]


def load_benchbox_datagen() -> ModuleType:
    try:
        import benchbox_datagen
    except ImportError as exc:
        raise DatagenBackendError(
            "--datagen-backend native requires the 'native-datagen' extra and a benchbox-datagen wheel "
            f"for this platform ({exc}). Install it with 'uv sync --extra native-datagen' "
            "or pass --datagen-backend legacy."
        ) from exc
    return benchbox_datagen


def write_native_tables(
    benchmark: str,
    scale_factor: float,
    output_dir: Path,
    *,
    table_format: str = "tbl",
    compression: str | None = None,
    threads: int | None = None,
) -> NativeGeneration:
    benchmark_key = benchmark.strip().lower()
    validate_native_scale(benchmark_key, scale_factor)
    module = load_benchbox_datagen()
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = module.write_tables(
        benchmark_key,
        float(scale_factor),
        output_dir,
        format=table_format,
        compression=compression,
        threads=threads,
    )
    files: dict[str, NativeTableFile] = {}
    for stat in stats:
        path = Path(stat.path)
        if table_format == "tbl":
            target = path.with_suffix(DELIMITED_EXTENSIONS[benchmark_key])
            if target != path:
                path = path.replace(target)
        files[stat.table] = NativeTableFile(path=path, rows=stat.rows, size_bytes=stat.bytes)
    return NativeGeneration(version=str(module.__version__), files=files)


def read_parquet_writer(path: Path) -> str:
    import pyarrow.parquet as pq

    value = (pq.read_metadata(path).metadata or {}).get(PARQUET_WRITER_METADATA_KEY)
    if value is None:
        raise DatagenBackendError(f"{path} does not record its Parquet writer ({PARQUET_WRITER_METADATA_KEY.decode()})")
    return value.decode("utf-8")


class NativeDatagenMixin:
    datagen_backend: str | None = None
    datagen_backend_version: str | None = None

    @property
    def uses_native_datagen(self) -> bool:
        return manifest_backend_name(self.datagen_backend) == NATIVE_BACKEND_NAME

    def require_native_compatible_options(self) -> None:
        if self.should_use_compression():
            raise DatagenBackendError(
                "--datagen-backend native writes uncompressed delimited files; "
                "remove the compression setting or pass --datagen-backend legacy."
            )
        if getattr(self, "_data_organization_config", None) is not None:
            raise DatagenBackendError(
                "--datagen-backend native does not support data organization (sorting, partitioning, clustering); "
                "remove it or pass --datagen-backend legacy."
            )


def bind_datagen_backend(benchmark: Any, backend: str, version: str | None) -> None:
    implementation = benchmark.__dict__.get("_impl")
    for target in (benchmark, implementation):
        if target is None:
            continue
        target.datagen_backend = backend
        target.datagen_backend_version = version
        generator = getattr(target, "data_generator", None)
        if generator is not None:
            generator.datagen_backend = backend
            generator.datagen_backend_version = version


__all__ = [
    "bind_datagen_backend",
    "DELIMITED_EXTENSIONS",
    "PARQUET_ROW_GROUP_ROWS",
    "PARQUET_WRITER_METADATA_KEY",
    "NativeDatagenMixin",
    "NativeGeneration",
    "NativeTableFile",
    "load_benchbox_datagen",
    "read_parquet_writer",
    "write_native_tables",
]
