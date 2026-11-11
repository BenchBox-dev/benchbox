from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.cli.config import ConfigManager
from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE_DIRS = (
    REPO_ROOT / "examples" / "tunings" / "duckdb",
    REPO_ROOT / "benchbox" / "core" / "tuning" / "templates" / "duckdb",
)
TUNED_TEMPLATES = sorted(path for directory in TEMPLATE_DIRS for path in directory.glob("*_tuned.yaml"))


def _template_id(path: Path) -> str:
    return f"{'examples' if 'examples' in path.parts else 'packaged'}/{path.name}"


def _load(path: Path):
    return ConfigManager().load_unified_tuning_config(path, platform="duckdb")


def test_every_duckdb_tuned_template_is_discovered() -> None:
    for directory in TEMPLATE_DIRS:
        assert sorted(directory.glob("*_tuned.yaml")), directory


@pytest.mark.parametrize("template", TUNED_TEMPLATES, ids=_template_id)
def test_duckdb_tuned_template_requests_no_partitioning(template: Path) -> None:
    config = _load(template)

    partitioned = {
        name: [column.name for column in tuning.get_columns_by_type(TuningType.PARTITIONING)]
        for name, tuning in config.table_tunings.items()
        if tuning.get_columns_by_type(TuningType.PARTITIONING)
    }

    assert partitioned == {}


@pytest.mark.parametrize("template", TUNED_TEMPLATES, ids=_template_id)
def test_duckdb_tuned_template_disables_check_constraints(template: Path) -> None:
    assert _load(template).check_constraints.enabled is False


LEGACY_DEFAULT_PARTITIONING = {
    "amplab": {"uservisits": ["visitdate"]},
    "clickbench": {"hits": ["EventDate"]},
    "h2odb": {},
    "joinorder": {},
    "read_primitives": {"LINEITEM": ["L_SHIPDATE"], "ORDERS": ["O_ORDERDATE"]},
    "ssb": {"lineorder": ["lo_orderdate"]},
    "tpcds": {
        "CATALOG_RETURNS": ["CR_RETURNED_DATE_SK"],
        "CATALOG_SALES": ["CS_SOLD_DATE_SK"],
        "STORE_RETURNS": ["SR_RETURNED_DATE_SK"],
        "STORE_SALES": ["SS_SOLD_DATE_SK"],
        "WEB_RETURNS": ["WR_RETURNED_DATE_SK"],
        "WEB_SALES": ["WS_SOLD_DATE_SK"],
    },
    "tpch": {"LINEITEM": ["L_SHIPDATE"], "ORDERS": ["O_ORDERDATE"]},
    "tpchavoc": {"LINEITEM": ["L_SHIPDATE"], "ORDERS": ["O_ORDERDATE"]},
}


@pytest.mark.parametrize("benchmark_name", sorted(LEGACY_DEFAULT_PARTITIONING))
def test_default_partitioning_layout_is_unchanged_without_a_duckdb_partitioning_template(benchmark_name: str) -> None:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.PARTITIONING, benchmark=benchmark_name)

    partitioned = {
        name: [column.name for column in tuning.get_columns_by_type(TuningType.PARTITIONING)]
        for name, tuning in config.table_tunings.items()
    }
    assert partitioned == LEGACY_DEFAULT_PARTITIONING[benchmark_name]


def test_default_layouts_other_than_partitioning_still_come_only_from_the_duckdb_template() -> None:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.DISTRIBUTION, benchmark="tpch")
    config.enable_platform_optimization(TuningType.CLUSTERING, benchmark="tpch")

    assert config.table_tunings == {}


