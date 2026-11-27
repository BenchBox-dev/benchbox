from __future__ import annotations

import ast
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
LARGE_FILE_LIMIT_BYTES = 1000 * 1024
MULTI_PART_QUERIES = (14, 23, 24, 39)
TEMPLATES_DIR = BINARIES_DIR / "tpc-ds" / "templates" / "query_templates"
QUALIFICATION_VALUES = REPO_ROOT / "benchbox" / "core" / "tpcds" / "dataframe_queries" / "qualification_values.json"
QUALIFICATION_INPUTS = REPO_ROOT / "benchbox" / "core" / "tpcds" / "qualification" / "inputs.json"
PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.\d+$")


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
    assert hashlib.sha256(replay.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == manifest["replay"]["sha256"]


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
    covered = {case["seed"] for case in tpch_named if case["sf"] == 1.0 and "s" not in case}
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
    assert len(section["stream_all_seeds"]) == 7
    assert sum(len(line["h"]) for line in tpcds_stream_all) == section["stream_all_statements"]


@pytest.fixture(scope="module")
def tpcds_explicit() -> list[dict[str, Any]]:
    return read_records("tpcds_explicit")


@pytest.fixture(scope="module")
def replay(manifest: dict[str, Any]) -> dict[str, Any]:
    return json.loads((CORPUS_DIR / manifest["replay"]["file"]).read_text(encoding="utf-8"))


def qualification_statements() -> list[str]:
    return [f"{n}{part}" for n in range(1, 100) for part in (("a", "b") if n in MULTI_PART_QUERIES else ("",))]


def equivalence_statements() -> list[str]:
    first = [f"{n}{'a' if n in MULTI_PART_QUERIES else ''}" for n in range(1, 100)]
    return first + [f"{n}b" for n in MULTI_PART_QUERIES]


def rewritten_template_hash(record: dict[str, Any]) -> str:
    from benchbox.core.tpcds.c_tools import SUBSCALE_TEMPLATE_OVERRIDES, _substitute_parameters

    number = int(record["q"].rstrip("ab"))
    name = f"query{number}.tpl"
    text = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    if name in SUBSCALE_TEMPLATE_OVERRIDES and record["sf"] < 1:
        official, override = SUBSCALE_TEMPLATE_OVERRIDES[name]
        text = text.replace(official, override, 1)
    return hashlib.sha256(_substitute_parameters(text, record["p"], record["q"]).encode("utf-8")).hexdigest()


def test_every_shard_is_under_the_large_file_limit() -> None:
    for path in CORPUS_DIR.iterdir():
        assert path.stat().st_size < LARGE_FILE_LIMIT_BYTES, path.name


def test_replay_entries_are_classified_replayable_or_not(replay: dict[str, Any]) -> None:
    for entry in replay["entries"]:
        assert isinstance(entry["replayable"], bool)
        assert entry["reason"]
        assert entry["replayable"] == (entry["seed"] is not None or entry["query_parameters"] is not None)
        if not entry["replayable"]:
            assert entry["seed"] is None and entry["query_parameters"] is None
            assert entry["qgen_seeds"] == []


def test_manifest_replay_counts_match_the_replay_file(manifest: dict[str, Any], replay: dict[str, Any]) -> None:
    summary = manifest["replay"]
    entries = replay["entries"]
    replayable = [entry for entry in entries if entry["replayable"]]
    excluded = [entry for entry in entries if not entry["replayable"]]
    assert summary["entries"] == len(entries) == 22
    assert summary["bundles"] == replay["bundle_count"] == 135
    assert summary["replayable"] == {"entries": len(replayable), "bundles": sum(len(e["bundles"]) for e in replayable)}
    assert summary["not_replayable"] == {"entries": len(excluded), "bundles": sum(len(e["bundles"]) for e in excluded)}
    assert summary["replayable"]["bundles"] == 39
    assert summary["not_replayable"]["bundles"] == 96
    by_benchmark: dict[str, int] = {}
    for entry in excluded:
        by_benchmark[entry["benchmark"]] = by_benchmark.get(entry["benchmark"], 0) + len(entry["bundles"])
    assert summary["not_replayable_bundles_by_benchmark"] == dict(sorted(by_benchmark.items()))
    assert "excluded from the retirement gate" in summary["rule"]


def test_explicit_qualification_cases_cover_all_99_templates(
    manifest: dict[str, Any], tpcds_explicit: list[dict[str, Any]]
) -> None:
    section = manifest["tpcds"]["explicit_parameters"]["qualification"]
    document = json.loads(QUALIFICATION_VALUES.read_text(encoding="utf-8"))
    pinned = json.loads(QUALIFICATION_INPUTS.read_text(encoding="utf-8"))["parameters_sha256"]
    assert section["parameters_sha256"] == hashlib.sha256(QUALIFICATION_VALUES.read_bytes()).hexdigest() == pinned
    records = [record for record in tpcds_explicit if record["set"] == "qualification"]
    assert [record["q"] for record in records] == qualification_statements()
    assert section["statements"] == len(records) == 103
    assert section["templates"] == 99
    assert section["dialect"] == "netezza"
    for record in records:
        assert set(record) - {"s"} == {"set", "q", "sf", "seed", "p", "t", "h", "h_alt"}
        assert record["sf"] == section["scale_factor"] == 1.0
        assert record["seed"] == section["seed"] == 1
        assert record["p"] == document["values"][str(int(record["q"].rstrip("ab")))]
        assert all(PARAMETER_NAME.match(name) and isinstance(value, str) for name, value in record["p"].items())
        assert HEX64.match(record["t"])
        if "s" in record:
            assert record["s"] in ABNORMAL_STATUSES and record["h"] is None
        else:
            assert HEX64.match(record["h"]) and HEX64.match(record["h_alt"])
    sensitive = {record["q"] for record in records if "s" not in record and record["h_alt"] != record["h"]}
    assert set(section["seed_sensitive_statements"]) == sensitive


def test_explicit_equivalence_cases_cover_every_draw(
    manifest: dict[str, Any], tpcds_explicit: list[dict[str, Any]]
) -> None:
    from benchbox.core.tpcds.dataframe_queries.production_binding import power_parameter_seed

    section = manifest["tpcds"]["explicit_parameters"]["equivalence"]
    records = [record for record in tpcds_explicit if record["set"] == "equivalence"]
    assert section["statements_per_draw"] == 103
    assert len(section["draws"]) == len({(d["seed"], d["stream"], d["scale_factor"]) for d in section["draws"]}) >= 5
    assert len(records) == len(section["draws"]) * 103
    for index, draw in enumerate(section["draws"]):
        assert draw["rngseed"] == power_parameter_seed(draw["seed"], draw["stream"])
        chunk = records[index * 103 : (index + 1) * 103]
        assert [record["q"] for record in chunk] == equivalence_statements()
        for record in chunk:
            assert set(record) - {"s"} == {"set", "q", "sf", "seed", "draw", "p", "t", "h", "r"}
            assert record["sf"] == draw["scale_factor"]
            assert record["seed"] == draw["rngseed"]
            assert record["draw"] == [draw["seed"], draw["stream"]]
            if "s" in record:
                assert record["s"] in ABNORMAL_STATUSES and record["h"] is None
            else:
                assert HEX64.match(record["h"]) and record["r"] == record["h"]
        by_query: dict[str, dict[str, str]] = {}
        for record in chunk:
            if record["p"] is not None:
                assert by_query.setdefault(record["q"].rstrip("ab"), record["p"]) == record["p"]
    assert any("s" not in record for record in records)


def test_explicit_template_rewrites_reproduce_from_the_bundled_templates(tpcds_explicit: list[dict[str, Any]]) -> None:
    checked = 0
    for record in tpcds_explicit:
        if record["p"] is None:
            assert record["t"] is None
            continue
        assert rewritten_template_hash(record) == record["t"], (record["set"], record["q"], record["sf"])
        checked += 1
    assert checked >= 103


def test_explicit_mechanism_is_recorded(manifest: dict[str, Any]) -> None:
    from benchbox.core.tpcds.c_tools import _PARAMETER_NAME_RE

    mechanism = manifest["tpcds"]["explicit_parameters"]["mechanism"]
    assert mechanism["name_pattern"] == _PARAMETER_NAME_RE.pattern
    assert "-DIALECT netezza" in mechanism["command"]
    assert len(mechanism["steps"]) >= 6
    assert manifest["text_normalisation"]["tpcds_explicit"]


def python_sources() -> Iterator[Path]:
    for path in (REPO_ROOT / "benchbox").rglob("*.py"):
        if "_vendor" not in path.parts:
            yield path


def calls_to(tree: ast.AST, callee: str) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(callee)]


def test_dialect_coverage_is_netezza_only(manifest: dict[str, Any]) -> None:
    dialects = manifest["tpcds"]["dialects"]
    assert dialects["captured"] == ["netezza"]
    assert dialects["passed_by_benchbox"] == ["netezza"]
    assert dialects["accepted_by_dsqgen_templates"] == sorted(
        path.stem for path in TEMPLATES_DIR.glob("*.tpl") if not path.stem.startswith("query")
    )
    assert "netezza" in dialects["accepted_by_dsqgen_templates"]


def test_every_dsqgen_dialect_site_is_listed_and_defaults_to_netezza(manifest: dict[str, Any]) -> None:
    sites = manifest["tpcds"]["dialects"]["call_sites"]
    pattern = re.compile(r"\{_?opt\}DIALECT|tpcds_option\([\"']dialect")
    command_sites = {
        str(path.relative_to(REPO_ROOT)) for path in python_sources() if pattern.search(path.read_text("utf-8"))
    }
    assert command_sites == {
        "benchbox/core/tpcds/c_tools.py",
        "benchbox/core/tpcds/streams.py",
    }
    for site in sites:
        source = (REPO_ROOT / site["file"]).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for anchor in site["anchors"]:
            assert anchor in source, (site["file"], anchor)
        for call in site["calls"]:
            found = calls_to(tree, call["callee"])
            assert found, (site["file"], call["callee"])
            for node in found:
                keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in node.keywords}
                assert ("dialect" in keywords) == call["dialect_keyword"], (site["file"], call["callee"])
                if call["dialect_keyword"]:
                    assert keywords["dialect"] == call["dialect_value"]
    assert {site["file"] for site in sites} >= command_sites


def test_base_dialect_is_always_netezza() -> None:
    defined = []
    for path in python_sources():
        source = path.read_text(encoding="utf-8")
        if "def get_tpc_base_dialect" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == "get_tpc_base_dialect":
                defined.append(str(path.relative_to(REPO_ROOT)))
                returns = [child.value for child in ast.walk(node) if isinstance(child, ast.Return)]
                assert returns
                assert all(isinstance(value, ast.Constant) and value.value == "netezza" for value in returns)
    assert defined == ["benchbox/platforms/base/dialect_translation.py"]


DSQGEN_CALLEES = (
    "dsqgen.generate",
    "generate_with_parameters",
    "generate_parameter_log",
    "query_manager.get_query",
    "query_manager.get_all_queries",
    "generate_dsqgen_streams",
)


def test_no_call_in_the_repository_passes_a_literal_dialect_to_dsqgen(manifest: dict[str, Any]) -> None:
    expected = manifest["tpcds"]["dialects"]["argument_expressions"]
    base_dialect: set[str] = set()
    dsqgen_dialect: set[str] = set()
    for path in python_sources():
        source = path.read_text(encoding="utf-8")
        if "dialect" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "base_dialect":
                        base_dialect.add(ast.unparse(keyword.value))
                    elif keyword.arg == "dialect" and ast.unparse(node.func).endswith(DSQGEN_CALLEES):
                        dsqgen_dialect.add(ast.unparse(keyword.value))
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "base_dialect":
                        base_dialect.add(ast.unparse(value))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "base_dialect"
                    ):
                        base_dialect.add(ast.unparse(node.value))
    assert sorted(base_dialect) == expected["base_dialect"]
    assert sorted(dsqgen_dialect) == expected["dsqgen_dialect"]


def test_nondeterministic_cases_have_repeated_differing_oracle_runs(
    manifest: dict[str, Any], tpcds_single: list[dict[str, Any]]
) -> None:
    evidence = manifest["tpcds"]["nondeterministic_cases"]
    expected = {(case["q"], case["seed"], case["sf"]) for case in tpcds_single if case.get("s") == "nondeterministic"}
    assert len(expected) == 13
    assert {(case["q"], case["seed"], case["sf"]) for case in evidence["cases"]} == expected
    assert evidence["runs_per_mode"] >= 3
    for case in evidence["cases"]:
        for mode in ("log", "plain"):
            digests = case[mode]["h"]
            assert len(digests) == evidence["runs_per_mode"]
            assert all(HEX64.match(digest) for digest in digests)
            assert case[mode]["distinct_h"] == len(set(digests)) == 1
        assert case["modes_differ"] is True
        assert not set(case["log"]["h"]) & set(case["plain"]["h"])
        assert len(case["log"]["p"]) == evidence["runs_per_mode"]


def test_nonterminating_qgen_cases_record_their_timeout(
    manifest: dict[str, Any], tpch_named: list[dict[str, Any]]
) -> None:
    evidence = manifest["tpch"]["nonterminating_cases"]
    expected = {(case["q"], case["seed"], case["sf"]) for case in tpch_named if case.get("s") == "timeout"}
    assert expected == {("7", 0, 1.0), ("12", 0, 1.0)}
    assert {(case["q"], case["seed"], case["sf"]) for case in evidence["cases"]} == expected
    assert evidence["timeout_seconds"] == 60
    for case in evidence["cases"]:
        assert len(case["attempts"]) == evidence["attempts"]
        assert all(attempt["outcome"] == "timeout" and attempt["seconds"] >= 60 for attempt in case["attempts"])
