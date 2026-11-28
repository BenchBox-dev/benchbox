from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from benchbox.utils.binary_manifest import DEFAULT_ROOT, MANIFEST_NAME, read_binary_manifest

QUERY_GENERATION_VERSION = 1

_GENERATOR_INPUTS: dict[str, tuple[str, frozenset[str]]] = {
    "tpch": ("tpc-h", frozenset({"qgen", "qgen.exe", "dists.dss"})),
    "tpcds": ("tpc-ds", frozenset({"dsqgen", "dsqgen.exe", "tpcds.dst", "tpcds.idx"})),
}
_ALIASES = {"tpc-h": "tpch", "tpc-ds": "tpcds"}


def _canonical(benchmark: str | None) -> str | None:
    name = str(benchmark or "").strip().lower()
    name = _ALIASES.get(name, name)
    return name if name in _GENERATOR_INPUTS else None


@lru_cache(maxsize=1)
def _shipped_manifest() -> dict[str, str]:
    return read_binary_manifest((DEFAULT_ROOT / MANIFEST_NAME).read_bytes())


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _subscale_overrides(benchmark: str) -> dict[str, list[str]]:
    if benchmark != "tpcds":
        return {}
    from benchbox.core.tpcds.c_tools import SUBSCALE_TEMPLATE_OVERRIDES

    return {name: list(pair) for name, pair in sorted(SUBSCALE_TEMPLATE_OVERRIDES.items())}


def current_query_generation_stamp(benchmark: str | None) -> dict[str, Any] | None:
    name = _canonical(benchmark)
    if name is None:
        return None
    folder, generator_files = _GENERATOR_INPUTS[name]
    manifest = _shipped_manifest()
    generator = [
        f"{path} {digest}"
        for path, digest in sorted(manifest.items())
        if path.startswith(f"{folder}/") and path.rsplit("/", 1)[-1] in generator_files
    ]
    templates = [
        f"{path} {digest}" for path, digest in sorted(manifest.items()) if path.startswith(f"{folder}/templates/")
    ]
    if not generator or not templates:
        return None
    overrides = json.dumps(_subscale_overrides(name), sort_keys=True, separators=(",", ":"))
    parts = {
        "generator_sha256": _digest(generator),
        "templates_sha256": _digest(templates),
        "overrides_sha256": hashlib.sha256(overrides.encode("utf-8")).hexdigest(),
    }
    return {
        "query_generation_version": QUERY_GENERATION_VERSION,
        **parts,
        "query_generation_hash": _digest([str(QUERY_GENERATION_VERSION), *(parts[key] for key in sorted(parts))]),
    }


def current_query_generation_hash(benchmark: str | None) -> str | None:
    stamp = current_query_generation_stamp(benchmark)
    return None if stamp is None else stamp["query_generation_hash"]
