from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchbox.core.expected_results.models import ExpectedQueryResult, ValidationMode

REFERENCE_DIR = Path(__file__).with_name("reference_row_counts")
SCHEMA_VERSION = 3
INTEGRITY_BENCHMARKS = ("tpch", "tpcds")
MIN_INTEGRITY_DRAWS = 512
MIN_INTEGRITY_DRAWS_ABOVE_SF1 = 256
MIN_AGREEMENT_DRAWS = 64
INTEGRITY_GROUPS = frozenset({"reference", "seedless", "spread", "load_end_time"})
INTEGRITY_PATHS = frozenset({"per_template", "streams", "qgen"})
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
    streams_index: int | None
    row_counts: Mapping[str, int]


@dataclass(frozen=True)
class IntegrityChecks:
    draws: int
    invariant_counts: Mapping[str, int]
    never_empty: frozenset[str]
    sample: Mapping[str, Any]


@dataclass(frozen=True)
class RowCountReference:
    benchmark: str
    scale_factor: float
    kind: str
    streams: Mapping[int, StreamReference]
    provenance: Mapping[str, Any]
    integrity: IntegrityChecks | None = None
    file_sha256: str | None = None

    @property
    def data_stamp(self) -> str | None:
        stamp = self.provenance["generator"].get("data_stamp")
        return stamp.get("base_constants_hash") if isinstance(stamp, dict) else None

    @property
    def query_generation_hash(self) -> str | None:
        identity = self.provenance["generator"].get("query_generation")
        return identity.get("query_generation_hash") if isinstance(identity, dict) else None

    def exact_stream(self, seed: int | None, streams_index: int | None) -> StreamReference | None:
        for entry in self.streams.values():
            if entry.seed == seed and entry.streams_index == streams_index:
                return entry
        return None

    def inapplicable_reason(self, run_data_stamp: str | None, run_query_generation_hash: str | None) -> str | None:
        if run_data_stamp is not None and run_data_stamp != self.data_stamp:
            return "reference data differs: the run's data stamp is not the reference's data stamp"
        if self.benchmark in INTEGRITY_BENCHMARKS and run_query_generation_hash != self.query_generation_hash:
            return "reference data differs: the run's query generation identity is not the reference's"
        return None

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


def _validate_streams(payload: dict[str, Any], name: str) -> None:
    benchmark = payload["benchmark"]
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
        for field in ("seed", "streams_index"):
            if stream.get(field) is not None and type(stream[field]) is not int:
                raise ValueError(f"{name} stream {stream_key} {field} must be an integer or null")


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
    _validate_streams(payload, name)
    if benchmark in INTEGRITY_BENCHMARKS:
        identity = provenance["generator"].get("query_generation")
        if not isinstance(identity, dict) or not isinstance(identity.get("query_generation_hash"), str):
            raise ValueError(f"{name} provenance must record the query generation identity")
    _validate_integrity(payload, name)


def _draw_count(groups: Any, name: str) -> int:
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{name} integrity sample must list its seed groups")
    total = 0
    for group in groups:
        if (
            not isinstance(group, dict)
            or group.get("group") not in INTEGRITY_GROUPS
            or group.get("path") not in INTEGRITY_PATHS
            or not isinstance(group.get("seeds"), list)
            or not group["seeds"]
            or any(seed is not None and type(seed) is not int for seed in group["seeds"])
        ):
            raise ValueError(f"{name} integrity sample groups must name a group, a generation path and seeds")
        streams = group.get("streams", 1)
        if type(streams) is not int or streams < 1 or (group["path"] != "streams" and streams != 1):
            raise ValueError(f"{name} integrity sample streams must be a positive integer on the streams path only")
        total += len(group["seeds"]) * streams
    if {group["group"] for group in groups} != INTEGRITY_GROUPS:
        raise ValueError(f"{name} integrity sample must cover the groups {sorted(INTEGRITY_GROUPS)}")
    return total


def _validate_integrity(payload: dict[str, Any], name: str) -> None:
    benchmark, scale = payload["benchmark"], payload["scale_factor"]
    integrity = payload.get("integrity")
    if benchmark not in INTEGRITY_BENCHMARKS:
        if integrity is not None:
            raise ValueError(f"{name} must not carry an integrity section")
        return
    if not isinstance(integrity, dict) or set(integrity) != {"sample", "invariant_counts", "never_empty"}:
        raise ValueError(f"{name} must carry an integrity section with sample, invariant_counts and never_empty")
    sample = integrity["sample"]
    if not isinstance(sample, dict):
        raise ValueError(f"{name} integrity sample must be an object")
    draws = _draw_count(sample.get("groups"), name)
    minimum = MIN_INTEGRITY_DRAWS if scale <= 1.0 else MIN_INTEGRITY_DRAWS_ABOVE_SF1
    if sample.get("draws") != draws or draws < minimum:
        raise ValueError(f"{name} integrity sample must list at least {minimum} draws and its draws must add up")
    engines = sample.get("engines")
    agreement = sample.get("agreement")
    if not isinstance(engines, dict) or len(engines) < 2 or not all(isinstance(v, str) and v for v in engines.values()):
        raise ValueError(f"{name} integrity sample must name at least two engines with versions")
    if (
        not isinstance(agreement, dict)
        or agreement.get("result") != "identical"
        or type(agreement.get("draws")) is not int
        or not MIN_AGREEMENT_DRAWS <= agreement["draws"] <= draws
        or sorted(agreement.get("engines", [])) != sorted(engines)
    ):
        raise ValueError(
            f"{name} integrity sample must record an identical agreement of every engine on at least "
            f"{MIN_AGREEMENT_DRAWS} draws"
        )
    generator = payload["provenance"]["generator"]
    if sample.get("data_stamp") != generator.get("data_stamp") or sample.get("query_generation") != generator.get(
        "query_generation"
    ):
        raise ValueError(f"{name} integrity sample must be bound to the reference's data stamp and query generation")
    if sample.get("data_checksum") != payload["provenance"]["data_checksum"]["sha256"]:
        raise ValueError(f"{name} integrity sample must be bound to the reference's data checksum")
    for key in ("benchbox_version", "generated_on", "regenerate"):
        if not isinstance(sample.get(key), str) or not sample[key]:
            raise ValueError(f"{name} integrity sample must record {key}")
    expected_ids = set(QUERY_IDS[benchmark])
    counts = integrity["invariant_counts"]
    never_empty = integrity["never_empty"]
    if not isinstance(counts, dict) or not set(counts) <= expected_ids:
        raise ValueError(f"{name} invariant_counts must map {benchmark} query ids to counts")
    if any(type(count) is not int or count < 0 for count in counts.values()):
        raise ValueError(f"{name} invariant_counts must be non-negative integers")
    if not isinstance(never_empty, list) or not set(never_empty) <= expected_ids:
        raise ValueError(f"{name} never_empty must list {benchmark} query ids")
    if any(counts.get(query_id) == 0 for query_id in never_empty):
        raise ValueError(f"{name} never_empty must not list a statement whose invariant count is zero")


def parse_reference(payload: dict[str, Any], name: str, file_sha256: str | None = None) -> RowCountReference:
    validate_payload(payload, name)
    streams = {
        int(key): StreamReference(
            stream=int(key),
            query_parameters=stream["query_parameters"],
            seed=stream.get("seed"),
            streams_index=stream.get("streams_index"),
            row_counts=dict(stream["row_counts"]),
        )
        for key, stream in payload["streams"].items()
    }
    integrity = payload.get("integrity")
    return RowCountReference(
        benchmark=payload["benchmark"],
        scale_factor=payload["scale_factor"],
        kind=payload["reference_kind"],
        streams=streams,
        provenance=payload["provenance"],
        integrity=None
        if integrity is None
        else IntegrityChecks(
            draws=integrity["sample"]["draws"],
            invariant_counts=dict(integrity["invariant_counts"]),
            never_empty=frozenset(integrity["never_empty"]),
            sample=integrity["sample"],
        ),
        file_sha256=file_sha256,
    )


def load_row_count_reference(benchmark: str, scale_factor: float, *, require_current: bool = True) -> RowCountReference:
    scale = _scale_match(benchmark, scale_factor)
    path = reference_path(benchmark, scale)
    raw = path.read_bytes()
    reference = parse_reference(json.loads(raw), path.name, hashlib.sha256(raw).hexdigest())
    if reference.benchmark != benchmark or reference.scale_factor != scale:
        raise ValueError(f"{path.name} must describe {benchmark} at scale factor {scale}")
    if require_current:
        require_current_generation(reference, path.name)
    return reference


def require_current_generation(reference: RowCountReference, name: str) -> None:
    _require_current_data_stamp(reference, name)
    _require_current_query_generation(reference, name)


def _require_current_query_generation(reference: RowCountReference, name: str) -> None:
    from benchbox.utils.query_generation import current_query_generation_hash

    if reference.benchmark in INTEGRITY_BENCHMARKS and reference.query_generation_hash != current_query_generation_hash(
        reference.benchmark
    ):
        raise ValueError(
            f"{name} was generated with a query generator, template set or sub-SF1 override that no longer matches "
            "the current query generation; regenerate the reference"
        )


def _require_current_data_stamp(reference: RowCountReference, name: str) -> None:
    from benchbox.utils.datagen_version import DATA_GENERATION_VERSION, compute_base_constants_hash

    generator = reference.provenance["generator"]
    stamp = generator.get("data_stamp") if isinstance(generator, dict) else None
    if (
        not isinstance(stamp, dict)
        or stamp.get("data_generation_version") != DATA_GENERATION_VERSION
        or stamp.get("base_constants_hash") != compute_base_constants_hash(reference.benchmark)
    ):
        raise ValueError(
            f"{name} was generated from data whose data stamp no longer matches the current data generation; "
            "regenerate the reference"
        )


def write_reference(payload: dict[str, Any]) -> Path:
    path = reference_path(payload["benchmark"], payload["scale_factor"])
    require_current_generation(parse_reference(payload, path.name), path.name)
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
            reference_kind=reference.kind,
            reference_label=reference.label,
            notes=(
                f"BenchBox {reference.benchmark} row-count reference, {reference.label}. "
                f"Stream {stream}: {entry.query_parameters}. SF={reference.scale_factor:g}"
            ),
        )
        for query_id, row_count in entry.row_counts.items()
    }
