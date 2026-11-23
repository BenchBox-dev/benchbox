from __future__ import annotations

import collections
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "results-data" / "validate_corpus.py"
BUNDLES = REPO_ROOT / "results-data" / "bundles"
NOT_RECORDED = "version not recorded"
DEV_WHEEL_RESULTS = 4
MINIMUM_PLATFORMS = 3


@pytest.fixture(scope="module")
def validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_corpus", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def identities(validator) -> dict[str, object]:
    return {
        path.relative_to(BUNDLES).as_posix(): validator._comparison_identity(path, validator._load_bundle(path))
        for path in validator.discover_bundles(BUNDLES)
    }


def test_every_stored_result_has_an_identity_and_a_comparison_version(identities) -> None:
    assert len(identities) > 400
    for found in identities.values():
        assert found.variant_id
        assert found.comparison_version.text
        assert found.comparison_version.recorded or found.comparison_version.text == NOT_RECORDED
        assert found.comparison_version.recorded or found.comparison_version.is_release is False


def test_the_duckdb_dev_wheel_results_resolve_to_a_pre_release(identities) -> None:
    dev = {
        name: found
        for name, found in identities.items()
        if found.comparison_version.version == "1.6.0.dev365" and found.variant_id == "duckdb"
    }

    assert len(dev) == DEV_WHEEL_RESULTS
    assert all(found.comparison_version.is_release is False for found in dev.values())


def test_a_pre_release_is_never_the_newest_release_of_a_platform(identities) -> None:
    releases: dict[str, set[str]] = collections.defaultdict(set)
    for found in identities.values():
        if found.comparison_version.is_release:
            releases[found.variant_id].add(found.comparison_version.text)

    assert "1.6.0.dev365" not in releases["duckdb"]
    assert releases["duckdb"]


def test_unrecorded_versions_are_one_identity_per_variant(identities) -> None:
    unrecorded = collections.Counter(
        found.label for found in identities.values() if not found.comparison_version.recorded
    )

    assert unrecorded
    assert all(label.endswith(f"({NOT_RECORDED})") for label in unrecorded)


def test_versions_of_one_platform_do_not_count_toward_the_cohort_minimum(validator) -> None:
    cohorts = validator.cohort_platforms(validator.discover_bundles(BUNDLES))

    assert cohorts[("ssb", "10.0")] == {"bigquery", "databricks", "duckdb", "snowflake"}
    assert all(not re.search(r" v\d", member) for members in cohorts.values() for member in members)
    assert len(cohorts[("ssb", "10.0")]) >= MINIMUM_PLATFORMS


def test_no_code_path_reads_a_folder_name_to_decide_identity() -> None:
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in (VALIDATOR, REPO_ROOT / "scripts" / "generate_corpus_inventory.py")
    }

    for path, text in sources.items():
        assert "version-matrix" not in text, path
        assert ".parent.name" not in text, path
