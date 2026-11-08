from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
UAT_DIR = REPO_ROOT / "tests" / "uat"
LOC_TABLE = REPO_ROOT / "_project" / "scripts" / "uat_loc_table.py"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not LOC_TABLE.exists(), reason="_project scripts are not in this checkout (release tree)"),
]

UNBUCKETED_LEGACY_MODULES = frozenset(
    {
        "clickhouse_certification.py",
        "clickhouse_memory.py",
        "docker_path_helpers.py",
        "managed_runtime.py",
        "mocker_launch.py",
    }
)


def _load_loc_table():
    spec = importlib.util.spec_from_file_location("uat_loc_bucket_coverage_table", LOC_TABLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _production_modules() -> set[str]:
    return {
        path.relative_to(UAT_DIR).as_posix()
        for path in UAT_DIR.rglob("*.py")
        if not path.name.startswith("test_")
        and path.name != "conftest.py"
        and not {"configs", "fixtures", "data", "__pycache__"} & set(path.relative_to(UAT_DIR).parts)
    }


def test_every_production_uat_module_belongs_to_a_loc_bucket_or_the_legacy_allowlist():
    bucketed = set(_load_loc_table().ALL_MODULES)
    unbucketed = _production_modules() - bucketed - UNBUCKETED_LEGACY_MODULES
    assert not unbucketed, f"add to BUCKETS in _project/scripts/uat_loc_table.py: {sorted(unbucketed)}"


def test_legacy_allowlist_names_only_existing_unbucketed_modules():
    bucketed = set(_load_loc_table().ALL_MODULES)
    assert _production_modules() >= UNBUCKETED_LEGACY_MODULES
    assert not UNBUCKETED_LEGACY_MODULES & bucketed


def test_throughput_baseline_counts_toward_the_throughput_bucket():
    buckets = dict(_load_loc_table().BUCKETS)
    assert "throughput_baseline.py" in buckets["throughput"]
