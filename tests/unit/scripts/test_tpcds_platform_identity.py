from __future__ import annotations

import copy
import json
import platform
from pathlib import Path

import pytest
from tpcds_platform_identity import (
    VOLATILE_TABLES,
    _bundle_platform,
    build_manifest,
    compare_manifests,
    main,
    pinned_bundle_hashes,
    table_entry,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def _manifest(system: str = "Linux", **overrides) -> dict:
    manifest = {
        "scale_factor": 0.01,
        "seed": 7,
        "platform": {"system": system, "machine": "x86_64", "python": "3.12.0"},
        "binaries": {"dsqgen": "a" * 64, "dsdgen": "b" * 64},
        "tables": {
            "store_sales": {"rows": 10, "bytes": 100, "sha256": "1" * 64},
            "dbgen_version": {"rows": 1, "bytes": 50, "sha256": "2" * 64},
        },
        "queries": {
            "39": {"values": {"YEAR.01": "2002", "MONTH.01": "4"}, "sql_sha256": "3" * 64},
            "8": {"values": {"YEAR.01": "2001"}, "sql_sha256": "4" * 64},
        },
    }
    manifest.update(overrides)
    return manifest


class TestCompareManifests:
    def test_identical_manifests_have_no_differences(self):
        assert compare_manifests({"linux": _manifest(), "macos": _manifest("Darwin")}) == []

    def test_a_table_checksum_difference_is_reported(self):
        other = _manifest("Darwin")
        other["tables"]["store_sales"]["sha256"] = "9" * 64

        problems = compare_manifests({"linux": _manifest(), "macos": other})

        assert len(problems) == 1
        assert "table store_sales: sha256" in problems[0]

    def test_a_row_count_difference_is_reported_with_the_checksum(self):
        other = _manifest("Darwin")
        other["tables"]["store_sales"].update(rows=11, sha256="9" * 64)

        problems = compare_manifests({"linux": _manifest(), "macos": other})

        assert any("10 rows against 11" in problem for problem in problems)

    def test_a_missing_table_is_reported(self):
        other = _manifest("Darwin")
        del other["tables"]["store_sales"]

        problems = compare_manifests({"linux": _manifest(), "macos": other})

        assert problems == ["linux vs macos: table store_sales only in linux"]

    def test_a_volatile_table_is_compared_by_row_count_only(self):
        assert "dbgen_version" in VOLATILE_TABLES
        other = _manifest("Darwin")
        other["tables"]["dbgen_version"]["sha256"] = "9" * 64
        assert compare_manifests({"linux": _manifest(), "macos": other}) == []

        other["tables"]["dbgen_version"]["rows"] = 2
        assert any(
            "dbgen_version: 1 rows against 2" in problem
            for problem in compare_manifests({"linux": _manifest(), "macos": other})
        )

    def test_a_parameter_difference_names_the_values(self):
        other = _manifest("Darwin")
        other["queries"]["39"]["values"]["MONTH.01"] = "5"

        problems = compare_manifests({"linux": _manifest(), "macos": other})

        assert len(problems) == 1
        assert "query 39 parameters differ (1): MONTH.01: '4' against '5'" in problems[0]

    def test_different_sql_for_identical_parameters_is_reported(self):
        other = _manifest("Darwin")
        other["queries"]["8"]["sql_sha256"] = "9" * 64

        problems = compare_manifests({"linux": _manifest(), "macos": other})

        assert problems == ["linux vs macos: query 8 renders different SQL for identical parameters"]

    def test_manifests_built_with_different_seeds_are_not_comparable(self):
        problems = compare_manifests({"linux": _manifest(), "macos": _manifest("Darwin", seed=8)})

        assert len(problems) == 1
        assert "seed differs" in problems[0]

    def test_a_single_manifest_cannot_be_compared(self):
        assert compare_manifests({"linux": _manifest()}) == ["need at least two manifests to compare"]

    def test_three_manifests_are_each_compared_to_the_first(self):
        third = copy.deepcopy(_manifest("Windows"))
        third["queries"]["8"]["values"]["YEAR.01"] = "1999"

        problems = compare_manifests({"a-linux": _manifest(), "b-macos": _manifest("Darwin"), "c-windows": third})

        assert len(problems) == 1
        assert problems[0].startswith("a-linux vs c-windows: query 8")


def test_compare_command_exit_code_and_report(tmp_path: Path, capsys):
    good, bad = _manifest(), _manifest("Darwin")
    bad["tables"]["store_sales"]["sha256"] = "9" * 64
    paths = []
    for name, manifest in (("linux.json", good), ("same.json", copy.deepcopy(good)), ("macos.json", bad)):
        path = tmp_path / name
        path.write_text(json.dumps(manifest), encoding="utf-8")
        paths.append(path)

    assert main(["compare", str(paths[0]), str(paths[1])]) == 0
    assert main(["compare", str(paths[0]), str(paths[2])]) == 1
    assert "table store_sales: sha256" in capsys.readouterr().out


def test_manifest_is_deterministic_on_one_platform_and_covers_the_pinned_seed():

    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

    try:
        DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")

    first = build_manifest(query_ids=(8, 39, 46))
    second = build_manifest(query_ids=(8, 39, 46))

    assert compare_manifests({"first": first, "second": {**second, "platform": {"system": "x", "machine": "y"}}}) == []
    assert first["seed"] == 7 and first["scale_factor"] == 0.01
    assert len(first["tables"]) == 25
    assert first["tables"]["store_sales"]["rows"] > 0
    assert first["queries"]["39"]["values"]["YEAR.01"]
    assert len(first["queries"]["39"]["sql_sha256"]) == 64
    small = first["queries"]["46"]["values"]
    large = first["parameter_scales"]["100.0"]["46"]["values"]
    assert len({value for key, value in small.items() if key.startswith("CITY_")}) == 1
    assert len({value for key, value in large.items() if key.startswith("CITY_")}) > 1


def test_a_parameter_difference_at_a_parameter_only_scale_is_reported():
    queries = {"46": {"values": {"CITY_A.01": "Oak Grove"}, "sql_sha256": "5" * 64}}
    reference = _manifest(parameter_scales={"100.0": queries})
    other = _manifest("Darwin", parameter_scales={"100.0": copy.deepcopy(queries)})
    other["parameter_scales"]["100.0"]["46"]["values"]["CITY_A.01"] = "Midway"
    problems = compare_manifests({"a": reference, "b": other})
    assert problems == ["a vs b at SF 100.0: query 46 parameters differ (1): CITY_A.01: 'Oak Grove' against 'Midway'"]


def test_manifests_with_different_parameter_only_scales_are_not_comparable_there():
    problems = compare_manifests({"a": _manifest(parameter_scales={"100.0": {}}), "b": _manifest("Darwin")})
    assert len(problems) == 1 and "parameter-only scales differ" in problems[0]


def test_crlf_table_files_compare_equal_to_lf(tmp_path: Path):
    lf, crlf = tmp_path / "lf.dat", tmp_path / "crlf.dat"
    lf.write_bytes(b"1|a|\n2|b|\n")
    crlf.write_bytes(b"1|a|\r\n2|b|\r\n")
    assert table_entry(lf) == table_entry(crlf)


def test_pinned_bundle_hashes_match_the_manifest_entries():
    manifest_path = Path(__file__).resolve().parents[3] / "benchbox" / "_binaries" / "SHA256MANIFEST.json"
    if not manifest_path.is_file():
        pytest.skip("bundled binary tree not available")
    entries = json.loads(manifest_path.read_bytes())["files"]
    suffix = ".exe" if platform.system() == "Windows" else ""
    expected = {name: entries[f"tpc-ds/{_bundle_platform()}/{name}{suffix}"] for name in ("dsqgen", "dsdgen")}
    assert pinned_bundle_hashes() == expected


