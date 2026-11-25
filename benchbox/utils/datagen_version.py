from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

DATA_GENERATION_VERSION = 1

BENCHMARK_DATAGEN_VERSIONS: dict[str, int] = {}

VERIFIED_BYTE_IDENTICAL_BACKENDS: Mapping[tuple[str, str], frozenset[float]] = MappingProxyType(
    {
        ("tpch", "4.0.0"): frozenset({0.01, 0.1, 1.0}),
        ("tpcds", "4.0.0"): frozenset({1.0}),
    }
)

_BENCHBOX_PARQUET_WRITERS: frozenset[str] = frozenset(
    {
        "benchbox-legacy",
        "benchbox-streaming",
        "benchbox-duckdb-streaming",
        "benchbox-pyarrow-streaming",
    }
)


_BENCHMARK_SPECS_FILES: dict[str, tuple[str, ...]] = {
    "tpch": ("benchbox/core/tpch/generator_specs.yaml",),
    "tpch_skew": (
        "benchbox/core/tpch/generator_specs.yaml",
        "benchbox/core/tpch_skew/generator_specs.yaml",
    ),
    "tpcdi": ("benchbox/core/tpcdi/generator_specs.yaml",),
    "tsbs_devops": ("benchbox/core/tsbs_devops/generator_specs.yaml",),
    "tsbs": ("benchbox/core/tsbs_devops/generator_specs.yaml",),
}

_GENERATOR_BINARIES: dict[str, str] = {
    "tpch": "dbgen",
    "tpchavoc": "dbgen",
    "tpch_skew": "dbgen",
    "read_primitives": "dbgen",
    "write_primitives": "dbgen",
    "transaction_primitives": "dbgen",
    "ai_primitives": "dbgen",
    "datavault": "dbgen",
    "tpcds": "dsdgen",
    "tpcds_obt": "dsdgen",
}

_DEFECTIVE_GENERATOR_BINARIES: dict[str, frozenset[str]] = {
    "dbgen": frozenset(
        {
            "1ef8282179910b38e517a9393ad8ab0df9f2b6575bbcab6455b38a6617475bc1",
            "1077578cf4d2bf6754f458d26a7ac95759e3ef1bb02e05d33d0ce17bc2fd8e06",
            "7f90ccc6fa0313067830b3f04318259a4c2ec182ded55143bbcec4791fdb7fc2",
            "e08b12a356314a3b305e583c6d4c5621423ec9df5d2ebea4267df3203ed5367a",
        }
    ),
}

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

_binary_digest_cache: dict[tuple[str, int, int], str] = {}


def _spec_files_for(benchmark: str | None) -> list[Path]:
    if not benchmark:
        return []
    return [_PACKAGE_ROOT / rel for rel in _BENCHMARK_SPECS_FILES.get(str(benchmark).lower(), ())]


def _generator_binary_name(benchmark: str | None) -> str | None:
    return _GENERATOR_BINARIES.get(str(benchmark).lower()) if benchmark else None


def generator_binary_digest(benchmark: str | None) -> str | None:
    binary_name = _generator_binary_name(benchmark)
    if binary_name is None:
        return None
    try:
        from benchbox.utils.tpc_compilation import get_tpc_compiler

        resolved = get_tpc_compiler(auto_compile=False).get_binary_path(binary_name)
        if resolved is None:
            return None
        path = Path(resolved)
        info = path.stat()
        key = (str(path), info.st_mtime_ns, info.st_size)
        digest = _binary_digest_cache.get(key)
        if digest is None:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            _binary_digest_cache[key] = digest
        return digest
    except Exception:
        return None


def get_benchmark_datagen_version(benchmark: str | None) -> int:
    if not benchmark:
        return 1
    return int(BENCHMARK_DATAGEN_VERSIONS.get(str(benchmark).lower(), 1))


def is_byte_identical_source_data(
    benchmark: str | None,
    *,
    backend_version: str | None = None,
    scale_factor: float | None = None,
) -> bool:
    if not benchmark or not backend_version or scale_factor is None:
        return False
    verified_scales = VERIFIED_BYTE_IDENTICAL_BACKENDS.get((str(benchmark).lower(), str(backend_version).strip()))
    if not verified_scales:
        return False
    return float(scale_factor) in verified_scales


def is_benchbox_parquet_writer(parquet_writer: str | None) -> bool:
    if parquet_writer is None:
        return True
    parts = [part.strip().lower() for part in str(parquet_writer).split("+") if part.strip()]
    return bool(parts) and all(part in _BENCHBOX_PARQUET_WRITERS for part in parts)


def is_byte_identical_backend_output(
    benchmark: str | None,
    *,
    backend_version: str | None = None,
    parquet_writer: str | None = None,
    scale_factor: float | None = None,
) -> bool:
    if not is_benchbox_parquet_writer(parquet_writer):
        return False
    return is_byte_identical_source_data(
        benchmark,
        backend_version=backend_version,
        scale_factor=scale_factor,
    )


def compute_base_constants_hash(benchmark: str | None = None) -> str:

    digest = hashlib.sha256(f"datagen-v{DATA_GENERATION_VERSION}".encode())
    benchmark_version = get_benchmark_datagen_version(benchmark)
    if benchmark_version > 1:
        digest.update(f"benchmark-datagen-v{benchmark_version}".encode())
    for path in _spec_files_for(benchmark):
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def current_datagen_stamp(
    benchmark: str | None = None,
    *,
    backend: str | None = None,
    backend_version: str | None = None,
) -> dict[str, Any]:
    stamp: dict[str, Any] = {
        "data_generation_version": DATA_GENERATION_VERSION,
        "base_constants_hash": compute_base_constants_hash(benchmark),
    }
    benchmark_version = get_benchmark_datagen_version(benchmark)
    if benchmark_version > 1:
        stamp["benchmark_datagen_version"] = benchmark_version
    if backend is not None:
        stamp["generator_backend"] = str(backend).lower()
    if backend_version is not None:
        stamp["generator_backend_version"] = str(backend_version)
    if backend is None or str(backend).lower() == "legacy":
        binary_digest = generator_binary_digest(benchmark)
        if binary_digest is not None:
            stamp["generator_binary_sha256"] = binary_digest
    return stamp


def compute_datagen_identity_hash(
    benchmark: str | None,
    configuration: Mapping[str, Any] | None = None,
    *,
    backend: str | None = None,
    backend_version: str | None = None,
    parquet_writer: str | None = None,
    parquet_row_group_size: int | None = None,
    scale_factor: float | None = None,
) -> str:

    payload: dict[str, Any] = {
        "base_constants_hash": compute_base_constants_hash(benchmark),
        "configuration": dict(configuration) if configuration is not None else None,
        "data_generation_version": DATA_GENERATION_VERSION,
    }
    benchmark_version = get_benchmark_datagen_version(benchmark)
    if benchmark_version > 1:
        payload["benchmark_datagen_version"] = benchmark_version
    if parquet_writer is not None:
        payload["parquet_writer"] = str(parquet_writer)
    if parquet_row_group_size is not None:
        payload["parquet_row_group_size"] = int(parquet_row_group_size)
    byte_identical = is_byte_identical_backend_output(
        benchmark,
        backend_version=backend_version,
        parquet_writer=parquet_writer,
        scale_factor=scale_factor,
    )
    if not byte_identical:
        if backend is not None and str(backend).lower() != "legacy":
            payload["generator_backend"] = str(backend).lower()
            if backend_version is not None:
                payload["generator_backend_version"] = str(backend_version)
        elif backend_version is not None:
            payload["generator_backend_version"] = str(backend_version)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _backend_staleness(
    manifest: Mapping[str, Any],
    benchmark: str | None,
    *,
    expected_backend: str | None,
    expected_backend_version: str | None,
) -> str | None:
    from benchbox.utils.datagen_backend import get_default_datagen_backend

    recorded_backend = str(manifest.get("generator_backend") or "legacy").lower()
    wanted_backend = (
        str(expected_backend).lower() if expected_backend is not None else get_default_datagen_backend(benchmark)
    )
    scale_factor_raw = manifest.get("scale_factor")
    scale_factor = (
        float(scale_factor_raw)
        if isinstance(scale_factor_raw, (int, float)) and not isinstance(scale_factor_raw, bool)
        else None
    )
    parquet_writer = manifest.get("parquet_writer")
    native_version = (
        manifest.get("generator_backend_version") if recorded_backend != "legacy" else expected_backend_version
    )
    if recorded_backend != wanted_backend and is_byte_identical_backend_output(
        benchmark,
        backend_version=str(native_version) if native_version is not None else None,
        parquet_writer=str(parquet_writer) if parquet_writer is not None else None,
        scale_factor=scale_factor,
    ):
        return None
    if recorded_backend != wanted_backend:
        return (
            f"cached dataset was generated with backend '{recorded_backend}', not requested backend '{wanted_backend}'"
        )
    if expected_backend_version is not None:
        recorded_version = manifest.get("generator_backend_version")
        if recorded_version != expected_backend_version:
            return (
                f"cached dataset backend version {recorded_version!r} "
                f"does not match expected {expected_backend_version!r}"
            )
    return None


def _manifest_has_parquet(manifest: Mapping[str, Any]) -> bool:
    for key in ("formats", "format_preference"):
        values = manifest.get(key)
        if isinstance(values, (list, tuple)) and any(str(v).lower() == "parquet" for v in values):
            return True
    tables = manifest.get("tables")
    if isinstance(tables, Mapping):
        for table_entry in tables.values():
            if not isinstance(table_entry, Mapping):
                continue
            table_formats = table_entry.get("formats")
            if isinstance(table_formats, Mapping) and "parquet" in table_formats:
                return True
    return False


def _parquet_layout_staleness(manifest: Mapping[str, Any], *, expected_table_format: str | None) -> str | None:
    if expected_table_format is None or str(expected_table_format).strip().lower() != "parquet":
        return None
    if not _manifest_has_parquet(manifest):
        return None
    parquet_writer = manifest.get("parquet_writer")
    parquet_rg = manifest.get("parquet_row_group_size")
    if (
        not isinstance(parquet_writer, str)
        or not parquet_writer.strip()
        or not isinstance(parquet_rg, int)
        or isinstance(parquet_rg, bool)
    ):
        return (
            "cached Parquet files predate streaming row-group layout stamping; "
            "regenerating with the current Parquet layout"
        )
    return None


def manifest_datagen_is_current(
    manifest: Mapping[str, Any] | None,
    benchmark: str | None = None,
    *,
    expected_backend: str | None = None,
    expected_backend_version: str | None = None,
    expected_table_format: str | None = None,
) -> bool:
    return (
        describe_datagen_staleness(
            manifest,
            benchmark=benchmark,
            expected_backend=expected_backend,
            expected_backend_version=expected_backend_version,
            expected_table_format=expected_table_format,
        )
        is None
    )


def _generator_binary_staleness(manifest: Mapping[str, Any], benchmark: str | None) -> str | None:
    binary_name = _generator_binary_name(benchmark)
    if binary_name is None:
        return None
    recorded_backend = str(manifest.get("generator_backend") or "legacy").lower()
    if recorded_backend != "legacy":
        recorded_version = manifest.get("generator_backend_version")
        if not isinstance(recorded_version, str) or not recorded_version.strip():
            return (
                f"this dataset uses backend '{recorded_backend}' without recording generator_backend_version; "
                "it regenerates once to establish provenance"
            )
        return None
    recorded = manifest.get("generator_binary_sha256")
    if not isinstance(recorded, str) or not recorded:
        return (
            f"this dataset does not record which {binary_name} binary wrote it; "
            "it regenerates once to establish provenance"
        )
    if recorded in _DEFECTIVE_GENERATOR_BINARIES.get(binary_name, frozenset()):
        return f"this dataset was written by a known-defective {binary_name} build"
    return None


def describe_datagen_staleness(
    manifest: Mapping[str, Any] | None,
    benchmark: str | None = None,
    *,
    expected_backend: str | None = None,
    expected_backend_version: str | None = None,
    expected_table_format: str | None = None,
) -> str | None:

    if not isinstance(manifest, Mapping):
        return "datagen manifest is missing or unreadable"
    if manifest.get("data_generation_version") != DATA_GENERATION_VERSION:
        if "data_generation_version" not in manifest:
            return (
                "this dataset predates version stamping and is treated as stale; "
                "it regenerates once to establish provenance"
            )
        return (
            f"datagen version {manifest.get('data_generation_version')!r} "
            f"does not match current version {DATA_GENERATION_VERSION}"
        )
    name = benchmark if benchmark is not None else manifest.get("benchmark")
    expected_benchmark_version = get_benchmark_datagen_version(name)
    recorded_benchmark_version = manifest.get("benchmark_datagen_version", 1)
    if recorded_benchmark_version != expected_benchmark_version:
        return (
            f"benchmark '{name}' datagen version {recorded_benchmark_version!r} "
            f"does not match current version {expected_benchmark_version}"
        )
    if manifest.get("base_constants_hash") != compute_base_constants_hash(name):
        return "datagen base constants changed since this data was generated"
    binary_reason = _generator_binary_staleness(manifest, name)
    if binary_reason is not None:
        return binary_reason
    backend_reason = _backend_staleness(
        manifest,
        name,
        expected_backend=expected_backend,
        expected_backend_version=expected_backend_version,
    )
    if backend_reason is not None:
        return backend_reason
    return _parquet_layout_staleness(manifest, expected_table_format=expected_table_format)


def resolve_manifest_datagen_hash(manifest: Mapping[str, Any]) -> str | None:
    identity_hash = manifest.get("data_generation_identity_hash")
    if isinstance(identity_hash, str) and identity_hash:
        return identity_hash
    benchmark = manifest.get("benchmark")
    backend = manifest.get("generator_backend")
    backend_version = manifest.get("generator_backend_version")
    parquet_writer = manifest.get("parquet_writer")
    parquet_row_group_raw = manifest.get("parquet_row_group_size")
    parquet_row_group_size = (
        int(parquet_row_group_raw)
        if isinstance(parquet_row_group_raw, int) and not isinstance(parquet_row_group_raw, bool)
        else None
    )
    benchmark_version = manifest.get("benchmark_datagen_version", 1)
    scale_raw = manifest.get("scale_factor")
    scale_factor = float(scale_raw) if isinstance(scale_raw, (int, float)) and not isinstance(scale_raw, bool) else None
    byte_identical = is_byte_identical_backend_output(
        benchmark,
        backend_version=str(backend_version) if backend_version is not None else None,
        parquet_writer=str(parquet_writer) if parquet_writer is not None else None,
        scale_factor=scale_factor,
    )
    if (
        (backend is not None and str(backend).lower() != "legacy" and not byte_identical)
        or (isinstance(benchmark_version, int) and not isinstance(benchmark_version, bool) and benchmark_version > 1)
        or parquet_writer is not None
        or parquet_row_group_size is not None
    ):
        return compute_datagen_identity_hash(
            benchmark,
            backend=str(backend) if backend is not None else None,
            backend_version=str(backend_version) if backend_version is not None else None,
            parquet_writer=str(parquet_writer) if parquet_writer is not None else None,
            parquet_row_group_size=parquet_row_group_size,
            scale_factor=scale_factor,
        )
    base_hash = manifest.get("base_constants_hash")
    return base_hash if isinstance(base_hash, str) else None
