from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from benchbox.core.expected_results import row_count_references as references
from benchbox.utils.datagen_version import current_datagen_stamp

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "_project/scripts"
if not (SCRIPTS / "generate_tpch_subscale_row_counts.py").is_file():
    pytest.skip("the release tree has no row-count generator", allow_module_level=True)

sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_test", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load("row_count_reference_generation")
tpch_generator = _load("generate_tpch_subscale_row_counts")
tpcds_generator = _load("generate_tpcds_subscale_row_counts")
ssb_generator = _load("generate_ssb_row_count_references")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_QUERY_IDS = references.QUERY_IDS["tpch"]
_COUNTS = {query_id: int(query_id) for query_id in _QUERY_IDS}


def _both(counts: dict[str, int]) -> dict[str, dict[str, int]]:
    return {"duckdb": counts, "datafusion": counts}


def test_agreeing_engines_produce_the_agreed_counts() -> None:
    agreed, disagreements = gen.agree("stream 0", _QUERY_IDS, _both(_COUNTS))

    assert agreed == _COUNTS
    assert disagreements == []


def test_a_disagreement_names_the_stream_the_query_and_both_counts() -> None:
    other = {**_COUNTS, "15": 0}

    agreed, disagreements = gen.agree("stream 1", _QUERY_IDS, {"duckdb": _COUNTS, "datafusion": other})

    assert "15" not in agreed
    assert disagreements == ["stream 1 Q15: {'duckdb': 15, 'datafusion': 0}"]


def test_a_disagreement_stops_the_generator_and_writes_the_report(tmp_path) -> None:
    report = tmp_path / "disagreements.txt"

    with pytest.raises(SystemExit, match="Q15"):
        gen.report_disagreements("tpch", 1.0, ["stream 1 Q15: {'duckdb': 15, 'datafusion': 0}"], report)

    assert "Q15" in report.read_text(encoding="utf-8")


def _manifest(directory: Path, shards: dict[str, list[tuple[str, bytes]]], extra: dict | None = None) -> None:
    tables = {}
    for table, files in shards.items():
        for name, content in files:
            (directory / name).write_bytes(content)
        tables[table] = {"formats": {"tbl": [{"path": name} for name, _ in files]}}
    (directory / gen.MANIFEST_NAME).write_text(json.dumps({"tables": tables, **(extra or {})}), encoding="utf-8")


def test_the_data_checksum_is_stable_across_shard_listing_order_and_ignores_the_generation_time(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _manifest(
        first,
        {
            "orders": [("orders.tbl.1", b"a|\n"), ("orders.tbl.10", b"c|\n"), ("orders.tbl.2", b"b|\n")],
            "dbgen_version": [("dbgen_version.dat", b"2026-11-23 10:00:00")],
        },
    )
    _manifest(
        second,
        {
            "orders": [("orders.tbl.10", b"c|\n"), ("orders.tbl.2", b"b|\n"), ("orders.tbl.1", b"a|\n")],
            "dbgen_version": [("dbgen_version.dat", b"2026-11-24 11:11:11")],
        },
    )

    assert gen.data_checksum(first)["sha256"] == gen.data_checksum(second)["sha256"]
    assert "dbgen_version" not in gen.data_checksum(first)["tables"]


def test_the_data_checksum_changes_when_a_table_changes(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _manifest(first, {"orders": [("orders.tbl", b"a|\n")]})
    _manifest(second, {"orders": [("orders.tbl", b"b|\n")]})

    assert gen.data_checksum(first)["sha256"] != gen.data_checksum(second)["sha256"]


def test_a_built_payload_is_a_valid_reference_with_full_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(references, "REFERENCE_DIR", tmp_path)
    payload = gen.build_payload(
        benchmark="tpch",
        scale_factor=0.01,
        streams={
            0: {"query_parameters": "qgen -d", "seed": None, "row_counts": _COUNTS},
            1: {"query_parameters": "qgen -d", "seed": None, "row_counts": _COUNTS},
        },
        sensitive=["11"],
        versions={"duckdb": "1", "datafusion": "2"},
        generator={"data_generator": "dbgen", "data_stamp": current_datagen_stamp("tpch")},
        checksum={"algorithm": "x", "sha256": "0" * 64},
        agreement=gen.agreement_record("tpch", [0, 1]),
        regenerate="make row-count-references-regen",
    )

    path = gen.write(payload)

    reference = references.load_row_count_reference("tpch", 0.01)
    assert path.parent == tmp_path
    assert sorted(reference.streams) == [0, 1]
    assert reference.label == "agreement with duckdb 1, datafusion 2"
    assert reference.provenance["generated_on"]


def test_a_payload_without_agreement_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(references, "REFERENCE_DIR", tmp_path)
    payload = gen.build_payload(
        benchmark="ssb",
        scale_factor=0.01,
        streams={
            0: {"query_parameters": "fixed", "seed": None, "row_counts": dict.fromkeys(references.QUERY_IDS["ssb"], 1)}
        },
        sensitive=[],
        versions={"duckdb": "1", "datafusion": "2"},
        generator={"data_generator": "ssb"},
        checksum={"algorithm": "x", "sha256": "0" * 64},
        agreement={"result": "disagree"},
        regenerate="make row-count-references-regen",
    )

    with pytest.raises(ValueError, match="identical"):
        gen.write(payload)


def test_the_tpch_generator_defaults_cover_the_recorded_scales_and_streams() -> None:
    assert set(tpch_generator.DEFAULT_SCALE_FACTORS) >= {0.01, 0.1, 1.0}
    assert set(tpch_generator.DEFAULT_STREAMS) >= {0, 1, 2}
    assert set(tpcds_generator.DEFAULT_STREAMS) >= {0, 1, 2, 3}
    assert set(ssb_generator.DEFAULT_SCALE_FACTORS) >= {0.01, 0.1, 1.0, 10.0}


def test_tpcds_variant_ids_split_into_query_number_and_variant() -> None:
    assert tpcds_generator._split("14a") == (14, "a")
    assert tpcds_generator._split("39b") == (39, "b")
    assert tpcds_generator._split("7") == (7, None)


def test_every_committed_tpch_reference_is_bound_to_the_current_query_templates() -> None:
    expected = tpch_generator.template_digest()["sha256"]

    for scale in references.reference_scales("tpch"):
        reference = references.load_row_count_reference("tpch", scale)
        assert reference.provenance["generator"]["query_template_digest"]["sha256"] == expected, scale
