from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "tpc_query_corpus"
BINARIES_DIR = REPO_ROOT / "benchbox" / "_binaries"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ABNORMAL_STATUSES = {"timeout", "nondeterministic"}
MAX_SEED = 2**31 - 2
TPCH_QUERIES = [str(n) for n in range(1, 23)] + ["15a"]
TPCDS_ROWCOUNT_IDS = {"1", "9", "16", "27", "33", "34", "36", "43", "44", "46", "68", "73", "27a", "36a"}
TPCDS_SCALES = {0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 10.0, 100.0, 1000.0, 3000.0}
TPCH_Q11_SCALES = {0.01, 0.1, 1.0, 3.0, 10.0, 30.0, 100.0, 1000.0}
CORPUS_MAX_BYTES = 10 * 1024 * 1024


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))


def read_shard(name: str) -> list[Any]:
    with gzip.open(CORPUS_DIR / name, "rb") as handle:
        return [json.loads(line) for line in handle.read().decode("utf-8").splitlines()]


def read_records(stem: str) -> list[Any]:
    return [record for path in sorted(CORPUS_DIR.glob(f"{stem}-*.jsonl.gz")) for record in read_shard(path.name)]


def uncompressed_sha256(name: str) -> str:
    with gzip.open(CORPUS_DIR / name, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@pytest.fixture(scope="module")
def tpch_named() -> list[dict[str, Any]]:
    return read_records("tpch_named")


@pytest.fixture(scope="module")
def tpch_random() -> list[dict[str, Any]]:
    return read_records("tpch_random")


@pytest.fixture(scope="module")
def tpcds_single() -> list[dict[str, Any]]:
    return read_records("tpcds_single")


@pytest.fixture(scope="module")
def tpcds_stream_template() -> list[dict[str, Any]]:
    return read_records("tpcds_stream_template")


@pytest.fixture(scope="module")
def tpcds_stream_all() -> list[dict[str, Any]]:
    return read_records("tpcds_stream_all")


def corpus_files(manifest: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for section in ("tpch", "tpcds"):
        yield from manifest[section]["files"].items()


def test_corpus_stays_within_size_budget() -> None:
    total = sum(path.stat().st_size for path in CORPUS_DIR.iterdir() if path.is_file())
    assert total < CORPUS_MAX_BYTES


def test_manifest_covers_every_corpus_file(manifest: dict[str, Any]) -> None:
    assert manifest["schema_version"] == 1
    assert manifest["profile"] == "full"
    on_disk = {path.name for path in CORPUS_DIR.iterdir() if path.is_file()}
    declared = {name for name, _ in corpus_files(manifest)} | {"manifest.json", manifest["replay"]["file"]}
    assert on_disk == declared


def abnormal_in(record: dict[str, Any]) -> int:
    if isinstance(record["h"], list):
        return sum(digest is None for digest in record["h"])
    return int("s" in record)


def test_corpus_file_hashes_and_record_counts_match_manifest(manifest: dict[str, Any]) -> None:
    for name, info in corpus_files(manifest):
        records = read_shard(name)
        assert len(records) == info["records"], name
        assert sum(abnormal_in(record) for record in records) == info["abnormal"], name
        assert uncompressed_sha256(name) == info["sha256"], name
        assert HEX64.match(info["sha256"])


def test_replay_file_hash_matches_manifest(manifest: dict[str, Any]) -> None:
    replay = CORPUS_DIR / manifest["replay"]["file"]
    assert hashlib.sha256(replay.read_bytes()).hexdigest() == manifest["replay"]["sha256"]


@pytest.mark.parametrize("suite", ["tpch", "tpcds"])
def test_oracle_inputs_match_bundled_manifest(manifest: dict[str, Any], suite: str) -> None:
    bundled = json.loads((BINARIES_DIR / "SHA256MANIFEST.json").read_text(encoding="utf-8"))["files"]
    oracle = manifest["oracle"]
    section = oracle[suite]
    assert oracle["platform"] == "linux-x86_64"
    assert section["binary"]["sha256"] == bundled[section["binary"]["path"]]
    for path, digest in section["distributions"].items():
        assert bundled[path] == digest
    assert section["templates"]
    prefix = "tpc-h" if suite == "tpch" else "tpc-ds"
    for relative, digest in section["templates"].items():
        assert HEX64.match(digest)
        assert bundled[f"{prefix}/templates/{relative}"] == digest
        assert hashlib.sha256((BINARIES_DIR / prefix / "templates" / relative).read_bytes()).hexdigest() == digest


def assert_case_outcome(case: dict[str, Any]) -> None:
    if "s" in case:
        assert case["s"] in ABNORMAL_STATUSES
        assert case["p"] is None and case["h"] is None
    else:
        assert HEX64.match(case["h"])
        assert case["p"]
        values = case["p"].values() if isinstance(case["p"], dict) else case["p"]
        assert all(isinstance(value, str) for value in values)


def test_tpch_named_cases_cover_the_gate(manifest: dict[str, Any], tpch_named: list[dict[str, Any]]) -> None:
    section = manifest["tpch"]
    assert section["queries"] == TPCH_QUERIES
    by_seed: dict[Any, set[str]] = {}
    for case in tpch_named:
        assert set(case) - {"s"} == {"q", "seed", "sf", "p", "h"}
        assert_case_outcome(case)
        if case["sf"] == 1.0 and case["p"] is not None:
            by_seed.setdefault(case["seed"], set()).add(case["q"])
    for seed in (None, 1, 42, MAX_SEED):
        assert by_seed[seed] == set(TPCH_QUERIES), seed
    seed_zero = {case["q"]: case.get("s") for case in tpch_named if case["seed"] == 0 and case["sf"] == 1.0}
    assert set(seed_zero) == set(TPCH_QUERIES)
    assert {query for query, status in seed_zero.items() if status == "timeout"} >= {"7", "12"}
    q11_scales = {case["sf"] for case in tpch_named if case["q"] == "11"}
    assert q11_scales == TPCH_Q11_SCALES
    keys = [(case["q"], case["seed"], case["sf"]) for case in tpch_named]
    assert len(keys) == len(set(keys))


def test_tpch_random_seeds(manifest: dict[str, Any], tpch_random: list[dict[str, Any]]) -> None:
    assert manifest["tpch"]["random_seed_count"] == 10_000
    assert len(tpch_random) == 10_000
    seeds = [record["seed"] for record in tpch_random]
    assert len(set(seeds)) == 10_000
    assert all(0 <= seed <= MAX_SEED for seed in seeds)
    for record in tpch_random:
        assert set(record) - {"s"} == {"seed", "p", "h"}
        assert list(record["p"]) == TPCH_QUERIES
        assert HEX64.match(record["h"])
        abnormal = record.get("s", {})
        assert set(abnormal.values()) <= ABNORMAL_STATUSES
        for query, values in record["p"].items():
            if query in abnormal:
                assert values is None
            else:
                assert all(isinstance(value, str) for value in values)


def test_tpch_replay_seeds_are_covered(manifest: dict[str, Any], tpch_named: list[dict[str, Any]]) -> None:
    replay = json.loads((CORPUS_DIR / manifest["replay"]["file"]).read_text(encoding="utf-8"))
    covered = {case["seed"] for case in tpch_named if case["sf"] == 1.0}
    wanted = {seed for entry in replay["entries"] for seed in entry["qgen_seeds"]}
    assert wanted
    assert wanted <= covered
    assert replay["bundle_count"] == sum(len(entry["bundles"]) for entry in replay["entries"])


def test_replay_entries_are_well_formed(manifest: dict[str, Any]) -> None:
    replay = json.loads((CORPUS_DIR / manifest["replay"]["file"]).read_text(encoding="utf-8"))
    assert replay["schema_version"] == 1
    assert replay["entries"]
    benchmarks = {entry["benchmark"] for entry in replay["entries"]}
    assert benchmarks <= {"tpch", "tpcds", "tpcds_obt"}
    assert {"tpch", "tpcds"} <= benchmarks
    for entry in replay["entries"]:
        assert entry["bundles"] == sorted(set(entry["bundles"]))
        assert entry["streams"] >= 1


def test_tpcds_single_covers_templates_seeds_and_scales(
    manifest: dict[str, Any], tpcds_single: list[dict[str, Any]]
) -> None:
    section = manifest["tpcds"]
    expected_ids = {str(n) for n in range(1, 100)} | {f"{n}{part}" for n in (14, 23, 24, 39) for part in "ab"}
    expected_ids |= {"5a", "10a", "18a", "22a", "27a", "35a", "36a", "51a", "67a", "70a", "77a", "80a", "86a"}
    assert set(section["single_queries"]) == expected_ids
    seeds_by_query: dict[str, set[Any]] = {}
    scales_by_query: dict[str, set[float]] = {}
    for case in tpcds_single:
        assert set(case) - {"s"} == {"q", "seed", "sf", "p", "h"}
        assert_case_outcome(case)
        if case["sf"] == 1.0:
            seeds_by_query.setdefault(case["q"], set()).add(case["seed"])
        scales_by_query.setdefault(case["q"], set()).add(case["sf"])
    assert set(seeds_by_query) == expected_ids
    required_seeds = {None, 0, 1, 42, MAX_SEED}
    for query, seeds in seeds_by_query.items():
        assert required_seeds <= seeds, query
        assert len(seeds) >= 100, query
    for query in TPCDS_ROWCOUNT_IDS:
        assert scales_by_query[query] == TPCDS_SCALES, query
    assert scales_by_query["93"] == {0.01, 0.05, 0.1, 0.25, 0.5, 1.0}
    keys = [(case["q"], case["seed"], case["sf"]) for case in tpcds_single]
    assert len(keys) == len(set(keys))


def test_tpcds_stream_template_covers_streams_0_to_20(
    manifest: dict[str, Any], tpcds_stream_template: list[dict[str, Any]]
) -> None:
    section = manifest["tpcds"]
    assert section["stream_count"] == 21
    seen: dict[tuple[str, Any], set[int]] = {}
    for case in tpcds_stream_template:
        assert set(case) - {"s"} == {"q", "seed", "stream", "p", "h"}
        assert_case_outcome(case)
        seen.setdefault((case["q"], case["seed"]), set()).add(case["stream"])
    assert {query for query, _ in seen} == set(section["stream_template_queries"])
    assert all(streams == set(range(21)) for streams in seen.values())
    assert {seed for _, seed in seen} == set(section["stream_template_seeds"])


def test_tpcds_stream_all_covers_streams_order_and_scales(
    manifest: dict[str, Any], tpcds_stream_all: list[dict[str, Any]]
) -> None:
    section = manifest["tpcds"]
    runs: dict[tuple[Any, float], list[dict[str, Any]]] = {}
    for line in tpcds_stream_all:
        assert set(line) == {"seed", "sf", "stream", "order", "templates", "h"}
        assert len(line["order"]) == len(line["h"])
        assert all(digest is None or HEX64.match(digest) for digest in line["h"])
        assert len(line["templates"]) == len({name.rstrip("ab") for name in line["order"]})
        runs.setdefault((line["seed"], line["sf"]), []).append(line)
    for lines in runs.values():
        assert [line["stream"] for line in lines] == list(range(21))
        for line in lines:
            assert len(set(line["order"])) == len(line["order"])
            assert {name.rstrip("ab") for name in line["order"]} == {str(n) for n in range(1, 100)}
    assert {sf for _, sf in runs} == TPCDS_SCALES
    assert {seed for seed, sf in runs if sf == 1.0} == set(section["stream_all_seeds"])
    assert sum(len(line["h"]) for line in tpcds_stream_all) == section["stream_all_statements"]
