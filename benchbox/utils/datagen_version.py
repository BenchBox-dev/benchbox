from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

DATA_GENERATION_VERSION = 1


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


def compute_base_constants_hash(benchmark: str | None = None) -> str:

    digest = hashlib.sha256(f"datagen-v{DATA_GENERATION_VERSION}".encode())
    for path in _spec_files_for(benchmark):
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def current_datagen_stamp(benchmark: str | None = None) -> dict[str, Any]:
    stamp: dict[str, Any] = {
        "data_generation_version": DATA_GENERATION_VERSION,
        "base_constants_hash": compute_base_constants_hash(benchmark),
    }
    binary_digest = generator_binary_digest(benchmark)
    if binary_digest is not None:
        stamp["generator_binary_sha256"] = binary_digest
    return stamp


def compute_datagen_identity_hash(benchmark: str | None, configuration: Mapping[str, Any] | None = None) -> str:

    payload = {
        "base_constants_hash": compute_base_constants_hash(benchmark),
        "configuration": dict(configuration) if configuration is not None else None,
        "data_generation_version": DATA_GENERATION_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def manifest_datagen_is_current(manifest: Mapping[str, Any] | None, benchmark: str | None = None) -> bool:

    if not isinstance(manifest, Mapping):
        return False
    if manifest.get("data_generation_version") != DATA_GENERATION_VERSION:
        return False
    name = benchmark if benchmark is not None else manifest.get("benchmark")
    if manifest.get("base_constants_hash") != compute_base_constants_hash(name):
        return False
    return _generator_binary_staleness(manifest, name) is None


def _generator_binary_staleness(manifest: Mapping[str, Any], benchmark: str | None) -> str | None:
    binary_name = _generator_binary_name(benchmark)
    if binary_name is None:
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


def describe_datagen_staleness(manifest: Mapping[str, Any] | None, benchmark: str | None = None) -> str | None:

    if manifest_datagen_is_current(manifest, benchmark):
        return None
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
    if manifest.get("base_constants_hash") != compute_base_constants_hash(name):
        return "datagen base constants changed since this data was generated"
    return _generator_binary_staleness(manifest, name)
