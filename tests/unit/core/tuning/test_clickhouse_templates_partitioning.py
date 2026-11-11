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
    REPO_ROOT / "examples" / "tunings" / "clickhouse",
    REPO_ROOT / "benchbox" / "core" / "tuning" / "templates" / "clickhouse",
)
TUNED_TEMPLATES = sorted(path for directory in TEMPLATE_DIRS for path in directory.glob("*_tuned.yaml"))


def _template_id(path: Path) -> str:
    return f"{'examples' if 'examples' in path.parts else 'packaged'}/{path.name}"


def _load(path: Path):
    return ConfigManager().load_unified_tuning_config(path, platform="clickhouse")


def _leading_sort_column(tuning) -> str | None:
    columns = sorted(tuning.get_columns_by_type(TuningType.SORTING), key=lambda column: column.order)
    return columns[0].name.lower() if columns else None


def test_every_clickhouse_tuned_template_is_discovered() -> None:
    for directory in TEMPLATE_DIRS:
        assert sorted(directory.glob("*_tuned.yaml")), directory


@pytest.mark.parametrize("template", TUNED_TEMPLATES, ids=_template_id)
def test_clickhouse_template_does_not_partition_on_the_leading_sort_column(template: Path) -> None:
    config = _load(template)

    redundant = {
        name: [column.name for column in tuning.get_columns_by_type(TuningType.PARTITIONING)]
        for name, tuning in config.table_tunings.items()
        if _leading_sort_column(tuning)
        and _leading_sort_column(tuning)
        in {column.name.lower() for column in tuning.get_columns_by_type(TuningType.PARTITIONING)}
    }

    assert redundant == {}


def test_clickhouse_tpch_template_requests_no_partitioning() -> None:
    for directory in TEMPLATE_DIRS:
        config = _load(directory / "tpch_tuned.yaml")

        assert all(not tuning.get_columns_by_type(TuningType.PARTITIONING) for tuning in config.table_tunings.values())
        assert {name.upper() for name in config.table_tunings} == {"LINEITEM", "ORDERS"}


