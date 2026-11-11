from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.utils.datagen_version import (
    DATA_GENERATION_VERSION,
    compute_base_constants_hash,
    compute_datagen_identity_hash,
    current_datagen_stamp,
    describe_datagen_staleness,
    generator_binary_digest,
    manifest_datagen_is_current,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_current_stamp_is_current() -> None:
    manifest = {"benchmark": "tpch", **current_datagen_stamp("tpch")}
    assert manifest_datagen_is_current(manifest, benchmark="tpch") is True
    assert describe_datagen_staleness(manifest, benchmark="tpch") is None


def test_missing_stamp_is_stale() -> None:
    manifest = {"benchmark": "tpch", "scale_factor": 1.0}
    assert manifest_datagen_is_current(manifest, benchmark="tpch") is False
    reason = describe_datagen_staleness(manifest, benchmark="tpch")
    assert reason is not None and "predates version stamping" in reason


def test_changed_base_constants_hash_is_stale() -> None:
    manifest = {"benchmark": "tpch", **current_datagen_stamp("tpch")}
    manifest["base_constants_hash"] = "0" * 64
    assert manifest_datagen_is_current(manifest, benchmark="tpch") is False
    reason = describe_datagen_staleness(manifest, benchmark="tpch")
    assert reason is not None and "base constants changed" in reason


def test_base_constants_hash_is_stable() -> None:
    assert compute_base_constants_hash("tpch") == compute_base_constants_hash("tpch")


def test_base_constants_hash_fingerprints_spec_files() -> None:
    assert compute_base_constants_hash("tpch") != compute_base_constants_hash("tsbs_devops")


def test_base_constants_hash_changes_with_spec_contents(tmp_path, monkeypatch) -> None:
    import benchbox.utils.datagen_version as datagen_version

    specs = tmp_path / "generator_specs.yaml"
    specs.write_text("base_row_counts:\n  lineitem: 1\n")
    monkeypatch.setitem(datagen_version._BENCHMARK_SPECS_FILES, "probe", (str(specs),))
    before = compute_base_constants_hash("probe")
    specs.write_text("base_row_counts:\n  lineitem: 2\n")
    assert compute_base_constants_hash("probe") != before


def test_manifest_writer_stamps_version(tmp_path: Path) -> None:
    from benchbox.utils.datagen_manifest import DataGenerationManifest as DatagenManifest

    manifest = DatagenManifest(output_dir=tmp_path, benchmark="tpch", scale_factor=1.0)
    stamped = manifest.to_dict()
    assert stamped["data_generation_version"] == DATA_GENERATION_VERSION
    assert stamped["base_constants_hash"] == compute_base_constants_hash("tpch")
    assert manifest_datagen_is_current(stamped, benchmark="tpch") is True


def test_stale_manifest_fails_reuse_validation(tmp_path: Path) -> None:
    from benchbox.core.runner.runner import _validate_manifest_if_present

    data_file = tmp_path / "customer.tbl"
    data_file.write_text("1|a\n")
    manifest = {
        "benchmark": "tpch",
        "scale_factor": 1.0,
        "tables": {"customer": {"formats": {"tbl": [{"path": "customer.tbl"}]}}},
    }
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

    class _Benchmark:
        output_dir = tmp_path
        tables = None

    class _Config:
        scale_factor = 1.0
        options = {}

    valid, _data, found = _validate_manifest_if_present(_Benchmark(), _Config())
    assert found is True
    assert valid is False


def test_result_carries_verified_data_generation_version(tmp_path) -> None:
    import json

    from benchbox.core.results.loader import reconstruct_benchmark_results
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    (tmp_path / "_datagen_manifest.json").write_text(
        json.dumps({"benchmark": "Test Benchmark", **current_datagen_stamp("Test Benchmark")})
    )

    class _Benchmark:
        output_dir = tmp_path

    result = _attach_datagen_version(make_benchmark_results(), _Benchmark())
    assert result.data_generation_version == DATA_GENERATION_VERSION
    payload = build_result_payload(result)
    assert payload["benchmark"]["data_generation_version"] == DATA_GENERATION_VERSION
    assert reconstruct_benchmark_results(payload).data_generation_version == DATA_GENERATION_VERSION


def test_result_leaves_version_unset_without_verified_manifest(tmp_path) -> None:
    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    class _Benchmark:
        output_dir = tmp_path

    assert _attach_datagen_version(make_benchmark_results(), _Benchmark()).data_generation_version is None
    assert _attach_datagen_version(make_benchmark_results()).data_generation_version is None


def test_result_rejects_manifest_for_another_benchmark(tmp_path) -> None:
    import json

    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    (tmp_path / "_datagen_manifest.json").write_text(
        json.dumps({"benchmark": "tpch", "scale_factor": 1.0, **current_datagen_stamp("tpch")})
    )

    class _Benchmark:
        output_dir = tmp_path

    result = _attach_datagen_version(make_benchmark_results(), _Benchmark())
    assert result.data_generation_version is None


def test_result_accepts_shared_data_alias_manifest(tmp_path) -> None:
    import json

    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    (tmp_path / "_datagen_manifest.json").write_text(json.dumps({"benchmark": "tpch", **current_datagen_stamp("tpch")}))

    class _Benchmark:
        output_dir = tmp_path

        def get_data_source_benchmark(self) -> str:
            return "tpch"

    result = _attach_datagen_version(make_benchmark_results(benchmark_name="Read Primitives"), _Benchmark())
    assert result.data_generation_version == DATA_GENERATION_VERSION
    assert result.data_generation_hash == compute_base_constants_hash("tpch")


def test_result_carries_generation_hash_round_trip(tmp_path) -> None:
    import json

    from benchbox.core.results.loader import reconstruct_benchmark_results
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    (tmp_path / "_datagen_manifest.json").write_text(
        json.dumps({"benchmark": "Test Benchmark", **current_datagen_stamp("Test Benchmark")})
    )

    class _Benchmark:
        output_dir = tmp_path

    result = _attach_datagen_version(make_benchmark_results(), _Benchmark())
    assert result.data_generation_hash == compute_base_constants_hash("Test Benchmark")
    payload = build_result_payload(result)
    assert payload["benchmark"]["data_generation_hash"] == compute_base_constants_hash("Test Benchmark")
    assert reconstruct_benchmark_results(payload).data_generation_hash == compute_base_constants_hash("Test Benchmark")


def test_tpcds_direct_manifest_writer_stamps_version(tmp_path: Path) -> None:
    import json

    from benchbox.core.tpcds.generator.filesystem import FileArtifactMixin

    class _Writer(FileArtifactMixin):
        def __init__(self) -> None:
            self.scale_factor = 0.01
            self.parallel = 1
            self._manifest_entries: dict = {}

        def should_use_compression(self) -> bool:
            return False

    data_file = tmp_path / "store_sales.dat"
    data_file.write_bytes(b"1|2|3\n")
    _Writer()._write_manifest(tmp_path, {"store_sales": [data_file]})
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest_datagen_is_current(manifest, benchmark="tpcds") is True


def test_ssb_direct_manifest_writer_stamps_version(tmp_path: Path) -> None:
    import json

    from benchbox.core.ssb.generator import SSBDataGenerator

    data_file = tmp_path / "customer.tbl"
    data_file.write_bytes(b"1|Alice\n")
    SSBDataGenerator(scale_factor=0.01, output_dir=tmp_path)._write_manifest(tmp_path, {"customer": data_file})
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest_datagen_is_current(manifest, benchmark="ssb") is True


def test_scan_rebuilt_manifest_carries_no_stamp(tmp_path: Path) -> None:
    import json

    from benchbox.utils.data_validation import BenchmarkDataValidator

    (tmp_path / "customer.tbl").write_bytes(b"1|a\n")
    BenchmarkDataValidator(benchmark_name="tpch", scale_factor=1.0)._write_manifest_from_scan(tmp_path)
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert "data_generation_version" not in manifest
    assert "base_constants_hash" not in manifest
    assert manifest_datagen_is_current(manifest, benchmark="tpch") is False


def test_stale_stamp_fails_directory_validation(tmp_path: Path) -> None:
    import json

    from benchbox.utils.data_validation import BenchmarkDataValidator

    (tmp_path / "customer.tbl").write_bytes(b"1|a\n")
    manifest = {
        "benchmark": "ssb",
        "scale_factor": 1.0,
        "data_generation_version": DATA_GENERATION_VERSION - 1,
        "base_constants_hash": "0" * 64,
        "tables": {},
    }
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))
    result = BenchmarkDataValidator(benchmark_name="ssb", scale_factor=1.0).validate_data_directory(tmp_path)
    assert result.valid is False
    assert any("stale" in issue for issue in result.issues)


def test_skew_fingerprint_covers_both_specs() -> None:
    assert compute_base_constants_hash("tpch_skew") != compute_base_constants_hash("tpch")
    stamp = current_datagen_stamp("tpch_skew")
    assert manifest_datagen_is_current(dict(stamp), benchmark="tpch_skew") is True


def test_skew_configuration_changes_data_generation_comparison_identity() -> None:
    from benchbox.core.results.exporter import ResultExporter
    from benchbox.core.tpch_skew.skew_config import AttributeSkewConfig, SkewConfiguration

    original = SkewConfiguration(seed=7, attribute_skew=AttributeSkewConfig(part_brand_skew=0.5))
    changed = SkewConfiguration(seed=7, attribute_skew=AttributeSkewConfig(part_brand_skew=0.9))
    original_hash = compute_datagen_identity_hash("tpch_skew", original.datagen_identity())
    changed_hash = compute_datagen_identity_hash("tpch_skew", changed.datagen_identity())
    assert original_hash != changed_hash

    def _result(identity_hash: str) -> dict:
        return {
            "benchmark": {
                "data_generation_version": DATA_GENERATION_VERSION,
                "data_generation_hash": identity_hash,
            }
        }

    comparison = ResultExporter._check_generation_compatibility(_result(original_hash), _result(changed_hash))
    assert comparison["status"] == "incompatible"
    assert comparison["compatible"] is False


def test_skew_manifest_identity_reaches_result_provenance(tmp_path: Path) -> None:
    from benchbox.core.results.loader import reconstruct_benchmark_results
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.runner.runner import _attach_datagen_version
    from benchbox.core.tpch_skew.generator import TPCHSkewDataGenerator
    from benchbox.core.tpch_skew.skew_config import AttributeSkewConfig, SkewConfiguration
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    config = SkewConfiguration(seed=7, attribute_skew=AttributeSkewConfig(part_brand_skew=0.9))
    generator = TPCHSkewDataGenerator(scale_factor=0.01, output_dir=tmp_path, skew_config=config)
    table_path = tmp_path / "customer.tbl"
    table_path.write_text("1|x|\n", encoding="utf-8")
    generator._write_manifest({"customer": table_path})
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))

    class _Benchmark:
        output_dir = tmp_path

    result = _attach_datagen_version(
        make_benchmark_results(benchmark_name="TPC-H Skew", scale_factor=0.01),
        _Benchmark(),
    )
    assert result.data_generation_hash == manifest["data_generation_identity_hash"]
    payload = build_result_payload(result)
    assert payload["benchmark"]["data_generation_hash"] == manifest["data_generation_identity_hash"]
    assert reconstruct_benchmark_results(payload).data_generation_hash == manifest["data_generation_identity_hash"]


def test_adapter_reused_database_flag() -> None:
    from types import SimpleNamespace

    from benchbox.core.runner.runner import _adapter_reused_database

    assert _adapter_reused_database(None) is False
    assert _adapter_reused_database(SimpleNamespace()) is False
    assert _adapter_reused_database(SimpleNamespace(database_was_reused=False)) is False
    assert _adapter_reused_database(SimpleNamespace(database_was_reused=True)) is True


def test_attach_leaves_provenance_unset_without_dataset_identity(tmp_path: Path) -> None:
    import json

    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    (tmp_path / "_datagen_manifest.json").write_text(
        json.dumps({"benchmark": "Test Benchmark", **current_datagen_stamp("Test Benchmark")})
    )

    class _Benchmark:
        output_dir = tmp_path

    result = _attach_datagen_version(make_benchmark_results(), _Benchmark(), dataset_identity_established=False)
    assert result.data_generation_version is None
    assert result.data_generation_hash is None


def test_compare_flags_generation_mismatch() -> None:
    from benchbox.core.results.exporter import ResultExporter

    def _data(version: int | None, hash_value: str | None) -> dict:
        benchmark: dict = {"name": "tpch", "scale_factor": 1.0}
        if version is not None:
            benchmark["data_generation_version"] = version
        if hash_value is not None:
            benchmark["data_generation_hash"] = hash_value
        return {"benchmark": benchmark}

    same = ResultExporter._check_generation_compatibility(
        _data(DATA_GENERATION_VERSION, "a" * 64), _data(DATA_GENERATION_VERSION, "a" * 64)
    )
    assert same["status"] == "compatible"
    assert same["compatible"] is True
    assert same["warning"] is None

    mismatch = ResultExporter._check_generation_compatibility(
        _data(DATA_GENERATION_VERSION, "a" * 64), _data(DATA_GENERATION_VERSION + 1, "b" * 64)
    )
    assert mismatch["status"] == "incompatible"
    assert mismatch["compatible"] is False
    assert mismatch["warning"] is not None and "different data generations" in mismatch["warning"]

    evolved_specs = ResultExporter._check_generation_compatibility(
        _data(DATA_GENERATION_VERSION, "a" * 64), _data(DATA_GENERATION_VERSION, "b" * 64)
    )
    assert evolved_specs["status"] == "incompatible"
    assert evolved_specs["compatible"] is False
    assert evolved_specs["warning"] is not None and "fingerprints differ" in evolved_specs["warning"]

    legacy = ResultExporter._check_generation_compatibility(_data(None, None), _data(DATA_GENERATION_VERSION, "a" * 64))
    assert legacy["status"] == "unknown"
    assert legacy["compatible"] is None
    assert legacy["warning"] is not None and "predate" in legacy["warning"]

    missing_hash = ResultExporter._check_generation_compatibility(
        _data(DATA_GENERATION_VERSION, None), _data(DATA_GENERATION_VERSION, "a" * 64)
    )
    assert missing_hash["status"] == "unknown"
    assert missing_hash["compatible"] is None


def test_generation_warning_travels_with_saved_artifacts() -> None:
    from benchbox.cli.commands.compare import (
        _format_html_comparison,
        _format_markdown_comparison,
        _format_text_comparison,
    )

    comparison = {
        "baseline_file": "b.json",
        "current_file": "c.json",
        "generation_compatibility": {"status": "incompatible", "compatible": False, "warning": "dataset differences"},
    }

    class _Result:
        benchmark_name = "tpch"
        platform = "duckdb"
        scale_factor = 1.0

    assert "dataset differences" in _format_text_comparison(comparison, _Result(), _Result(), False)
    assert "dataset differences" in _format_markdown_comparison(comparison, _Result(), _Result(), False)
    assert "dataset differences" in _format_html_comparison(comparison, _Result(), _Result())

    clean = dict(comparison, generation_compatibility={"status": "compatible", "compatible": True, "warning": None})
    assert "DATA GENERATION" not in _format_text_comparison(clean, _Result(), _Result(), False)


DEFECTIVE_DBGEN_SHA256 = (
    "1ef8282179910b38e517a9393ad8ab0df9f2b6575bbcab6455b38a6617475bc1",
    "1077578cf4d2bf6754f458d26a7ac95759e3ef1bb02e05d33d0ce17bc2fd8e06",
    "7f90ccc6fa0313067830b3f04318259a4c2ec182ded55143bbcec4791fdb7fc2",
    "e08b12a356314a3b305e583c6d4c5621423ec9df5d2ebea4267df3203ed5367a",
)


class _Compiler:
    def __init__(self, paths: dict[str, Path | None]) -> None:
        self.paths = paths

    def get_binary_path(self, binary_name: str) -> Path | None:
        return self.paths.get(binary_name)


@pytest.fixture
def generator_binaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dbgen = tmp_path / "dbgen"
    dsdgen = tmp_path / "dsdgen"
    dbgen.write_bytes(b"dbgen-one")
    dsdgen.write_bytes(b"dsdgen-one")
    paths: dict[str, Path | None] = {"dbgen": dbgen, "dsdgen": dsdgen}
    monkeypatch.setattr("benchbox.utils.tpc_compilation.get_tpc_compiler", lambda **_kwargs: _Compiler(paths))
    return paths


@pytest.mark.parametrize(
    ("name", "binary_name"),
    [
        ("tpch", "dbgen"),
        ("tpchavoc", "dbgen"),
        ("tpch_skew", "dbgen"),
        ("read_primitives", "dbgen"),
        ("write_primitives", "dbgen"),
        ("transaction_primitives", "dbgen"),
        ("datavault", "dbgen"),
        ("tpcds", "dsdgen"),
        ("tpcds_obt", "dsdgen"),
        ("TPCH", "dbgen"),
    ],
)
def test_stamp_records_the_generator_binary(generator_binaries, name: str, binary_name: str) -> None:
    import hashlib

    expected = hashlib.sha256(generator_binaries[binary_name].read_bytes()).hexdigest()
    assert generator_binary_digest(name) == expected
    assert current_datagen_stamp(name)["generator_binary_sha256"] == expected


@pytest.mark.parametrize("name", ["ssb", "clickbench", "tpcdi", "tsbs_devops", "Test Benchmark", None])
def test_other_benchmarks_record_no_generator_binary(generator_binaries, name: str | None) -> None:
    assert generator_binary_digest(name) is None
    assert "generator_binary_sha256" not in current_datagen_stamp(name)


@pytest.mark.parametrize("name", ["tpch", "tpch_skew", "datavault", "write_primitives", "tpcds", "tpcds_obt"])
def test_freshly_stamped_data_is_current(generator_binaries, name: str) -> None:
    manifest = {"benchmark": name, **current_datagen_stamp(name)}
    assert manifest_datagen_is_current(manifest, benchmark=name) is True
    assert describe_datagen_staleness(manifest, benchmark=name) is None


@pytest.mark.parametrize("name", ["tpch", "tpchavoc", "tpch_skew", "datavault", "read_primitives"])
@pytest.mark.parametrize("digest", DEFECTIVE_DBGEN_SHA256)
def test_data_written_by_a_defective_dbgen_is_stale(generator_binaries, name: str, digest: str) -> None:
    manifest = {"benchmark": name, **current_datagen_stamp(name), "generator_binary_sha256": digest}
    assert manifest_datagen_is_current(manifest, benchmark=name) is False
    assert "known-defective dbgen" in (describe_datagen_staleness(manifest, benchmark=name) or "")


@pytest.mark.parametrize("name", ["tpch", "tpch_skew", "datavault", "tpcds", "tpcds_obt"])
def test_stamp_without_generator_record_is_stale(generator_binaries, name: str) -> None:
    manifest = {"benchmark": name, **current_datagen_stamp(name)}
    del manifest["generator_binary_sha256"]
    assert manifest_datagen_is_current(manifest, benchmark=name) is False
    reason = describe_datagen_staleness(manifest, benchmark=name)
    assert reason is not None and "does not record which" in reason


def test_stamp_without_generator_record_stays_current_for_other_benchmarks(generator_binaries) -> None:
    manifest = {"benchmark": "ssb", **current_datagen_stamp("ssb")}
    assert manifest_datagen_is_current(manifest, benchmark="ssb") is True


def test_data_from_another_good_binary_stays_current(generator_binaries) -> None:
    manifest = {"benchmark": "tpch", **current_datagen_stamp("tpch")}
    generator_binaries["dbgen"].write_bytes(b"a different but correct dbgen build")
    assert manifest_datagen_is_current(manifest, benchmark="tpch") is True


def test_generator_binary_does_not_change_the_comparison_hash(generator_binaries) -> None:
    before = compute_base_constants_hash("tpch")
    generator_binaries["dbgen"].write_bytes(b"a different dbgen build")
    assert compute_base_constants_hash("tpch") == before
    assert compute_datagen_identity_hash("tpch_skew", {"seed": 1}) == compute_datagen_identity_hash(
        "tpch_skew", {"seed": 1}
    )


def test_unresolvable_generator_leaves_mapped_stamp_unproven(generator_binaries) -> None:
    generator_binaries["dbgen"] = None
    stamp = current_datagen_stamp("tpch")
    assert "generator_binary_sha256" not in stamp
    assert manifest_datagen_is_current({"benchmark": "tpch", **stamp}, benchmark="tpch") is False


def test_shipped_generator_binaries_are_not_marked_defective() -> None:
    from benchbox.utils.datagen_version import _DEFECTIVE_GENERATOR_BINARIES

    manifest = json.loads((Path(__file__).resolve().parents[3] / "benchbox/_binaries/SHA256MANIFEST.json").read_text())
    shipped = set(manifest["files"].values())
    assert shipped.isdisjoint(_DEFECTIVE_GENERATOR_BINARIES["dbgen"])


def test_binary_defect_forces_regeneration_on_reuse_check(generator_binaries, tmp_path: Path) -> None:
    from benchbox.core.runner.runner import _validate_manifest_if_present
    from benchbox.core.schemas import BenchmarkConfig

    (tmp_path / "customer.tbl").write_bytes(b"1|a\n")

    def _write(**extra: object) -> None:
        manifest = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "tables": {"customer": {"formats": {"tbl": [{"path": "customer.tbl", "size_bytes": 4}]}}},
            **current_datagen_stamp("tpch"),
            **extra,
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest))

    class _Benchmark:
        output_dir = tmp_path
        tables = None

    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0, options={})

    _write()
    assert _validate_manifest_if_present(_Benchmark(), config)[::2] == (True, True)

    _write(generator_binary_sha256=DEFECTIVE_DBGEN_SHA256[1])
    assert _validate_manifest_if_present(_Benchmark(), config)[::2] == (False, True)


