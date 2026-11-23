from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchbox.core.expected_results.models import ExpectedQueryResult, ValidationMode

REFERENCE_DIR = Path(__file__).with_name("reference_row_counts")
SCHEMA_VERSION = 2
KIND_TPC_ANSWER_SET = "tpc_answer_set"
KIND_ENGINE_AGREEMENT = "engine_agreement"
KIND_LABELS = {
    KIND_TPC_ANSWER_SET: "TPC answer set",
    KIND_ENGINE_AGREEMENT: "agreement with reference engines",
}
REFERENCE_BENCHMARKS = ("tpch", "tpcds", "ssb")

_TPCDS_SPLIT_QUERIES = {14: ("a", "b"), 23: ("a", "b"), 24: ("a", "b"), 39: ("a", "b")}
_REQUIRED_PROVENANCE = ("generator", "data_checksum", "engines", "generated_on", "agreement")


def _tpcds_query_ids() -> tuple[str, ...]:
    ids: list[str] = []
    for number in range(1, 100):
        variants = _TPCDS_SPLIT_QUERIES.get(number)
        if variants:
            ids.extend(f"{number}{variant}" for variant in variants)
        else:
            ids.append(str(number))
    return tuple(ids)


QUERY_IDS: Mapping[str, tuple[str, ...]] = {
    "tpch": tuple(str(number) for number in range(1, 23)),
    "tpcds": _tpcds_query_ids(),
    "ssb": ("1.1", "1.2", "1.3", "2.1", "2.2", "2.3", "3.1", "3.2", "3.3", "3.4", "4.1", "4.2", "4.3"),
}


@dataclass(frozen=True)
class StreamReference:
    stream: int
    query_parameters: str
    seed: int | None
    row_counts: Mapping[str, int]


@dataclass(frozen=True)
class RowCountReference:
    benchmark: str
    scale_factor: float
    kind: str
    streams: Mapping[int, StreamReference]
    parameter_sensitive_query_ids: frozenset[str]
    provenance: Mapping[str, Any]

    @property
    def label(self) -> str:
        if self.kind != KIND_ENGINE_AGREEMENT:
            return KIND_LABELS[self.kind]
        engines = ", ".join(f"{name} {version}" for name, version in self.provenance["engines"].items())
        return f"agreement with {engines}"

    def stream_reference(self, stream: int) -> StreamReference | None:
        return self.streams.get(stream)


def reference_path(benchmark: str, scale_factor: float) -> Path:
    return REFERENCE_DIR / f"{benchmark}_row_counts_sf{float(scale_factor):g}.json"


def reference_scales(benchmark: str) -> tuple[float, ...]:
    scales = []
    for path in REFERENCE_DIR.glob(f"{benchmark}_row_counts_sf*.json"):
        scale = float(path.stem.removeprefix(f"{benchmark}_row_counts_sf"))
        if path == reference_path(benchmark, scale):
            scales.append(scale)
    return tuple(sorted(scales))


def has_reference(benchmark: str, scale_factor: float) -> bool:
    return any(math.isclose(scale, float(scale_factor), rel_tol=1e-9) for scale in reference_scales(benchmark))


def _scale_match(benchmark: str, scale_factor: float) -> float:
    for scale in reference_scales(benchmark):
        if math.isclose(scale, float(scale_factor), rel_tol=1e-9):
            return scale
    raise FileNotFoundError(f"No {benchmark} row-count reference is recorded for scale factor {scale_factor}")


def validate_payload(payload: Any, name: str) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{name} must be a schema version {SCHEMA_VERSION} row-count reference")
    benchmark = payload.get("benchmark")
    if benchmark not in QUERY_IDS:
        raise ValueError(f"{name} must describe one of {sorted(QUERY_IDS)}")
    if type(payload.get("scale_factor")) is not float:
        raise ValueError(f"{name} scale_factor must be a float")
    if payload.get("reference_kind") not in KIND_LABELS:
        raise ValueError(f"{name} reference_kind must be one of {sorted(KIND_LABELS)}")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or any(key not in provenance for key in _REQUIRED_PROVENANCE):
        raise ValueError(f"{name} provenance must record {', '.join(_REQUIRED_PROVENANCE)}")
    engines = provenance["engines"]
    if not isinstance(engines, dict) or not all(isinstance(v, str) and v for v in engines.values()):
        raise ValueError(f"{name} provenance engines must map each engine to its version")
    if payload["reference_kind"] == KIND_ENGINE_AGREEMENT and len(engines) < 2:
        raise ValueError(f"{name} records agreement but names fewer than two engines")
    checksum = provenance["data_checksum"]
    if not isinstance(checksum, dict) or not isinstance(checksum.get("sha256"), str) or len(checksum["sha256"]) != 64:
        raise ValueError(f"{name} provenance data_checksum must carry a sha256 digest")
    agreement = provenance["agreement"]
    if not isinstance(agreement, dict) or agreement.get("result") != "identical":
        raise ValueError(f"{name} provenance agreement must record result 'identical'")
    streams = payload.get("streams")
    if not isinstance(streams, dict) or not streams:
        raise ValueError(f"{name} must list at least one stream")
    expected_ids = QUERY_IDS[benchmark]
    for stream_key, stream in streams.items():
        if not stream_key.isdigit() or not isinstance(stream, dict):
            raise ValueError(f"{name} stream keys must be integers")
        counts = stream.get("row_counts")
        if not isinstance(counts, dict) or set(counts) != set(expected_ids):
            raise ValueError(f"{name} stream {stream_key} must list row counts for every {benchmark} query")
        if any(type(count) is not int or count < 0 for count in counts.values()):
            raise ValueError(f"{name} stream {stream_key} row counts must be non-negative integers")
        if not isinstance(stream.get("query_parameters"), str):
            raise ValueError(f"{name} stream {stream_key} must describe its query parameters")
        if stream.get("seed") is not None and type(stream["seed"]) is not int:
            raise ValueError(f"{name} stream {stream_key} seed must be an integer or null")
    sensitive = payload.get("parameter_sensitive_query_ids")
    if not isinstance(sensitive, list) or not set(sensitive) <= set(expected_ids):
        raise ValueError(f"{name} parameter_sensitive_query_ids must list {benchmark} query ids")


def parse_reference(payload: dict[str, Any], name: str) -> RowCountReference:
    validate_payload(payload, name)
    streams = {
        int(key): StreamReference(
            stream=int(key),
            query_parameters=stream["query_parameters"],
            seed=stream.get("seed"),
            row_counts=dict(stream["row_counts"]),
        )
        for key, stream in payload["streams"].items()
    }
    return RowCountReference(
        benchmark=payload["benchmark"],
        scale_factor=payload["scale_factor"],
        kind=payload["reference_kind"],
        streams=streams,
        parameter_sensitive_query_ids=frozenset(payload["parameter_sensitive_query_ids"]),
        provenance=payload["provenance"],
    )


def load_row_count_reference(benchmark: str, scale_factor: float) -> RowCountReference:
    scale = _scale_match(benchmark, scale_factor)
    path = reference_path(benchmark, scale)
    reference = parse_reference(json.loads(path.read_text(encoding="utf-8")), path.name)
    if reference.benchmark != benchmark or reference.scale_factor != scale:
        raise ValueError(f"{path.name} must describe {benchmark} at scale factor {scale}")
    return reference


def write_reference(payload: dict[str, Any]) -> Path:
    path = reference_path(payload["benchmark"], payload["scale_factor"])
    validate_payload(payload, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def expected_query_results(reference: RowCountReference, stream: int) -> dict[str, ExpectedQueryResult] | None:
    entry = reference.stream_reference(stream)
    if entry is None:
        return None
    return {
        query_id: ExpectedQueryResult(
            query_id=query_id,
            scale_factor=reference.scale_factor,
            expected_row_count=row_count,
            validation_mode=ValidationMode.EXACT,
            reference_parameters_only=query_id in reference.parameter_sensitive_query_ids,
            reference_kind=reference.kind,
            reference_label=reference.label,
            notes=(
                f"BenchBox {reference.benchmark} row-count reference, {reference.label}. "
                f"Stream {stream}: {entry.query_parameters}. SF={reference.scale_factor:g}"
            ),
        )
        for query_id, row_count in entry.row_counts.items()
    }


def stream_query_results(reference: RowCountReference) -> dict[int, dict[str, ExpectedQueryResult]]:
    return {stream: results for stream in reference.streams if (results := expected_query_results(reference, stream))}
