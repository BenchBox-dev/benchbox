from __future__ import annotations

import pytest

pytest.importorskip("polars")

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.platforms import get_dataframe_adapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _adapter(explicit: str | None, tuning: str | None):
    config: dict[str, object] = {}
    if explicit is not None:
        config["execution_engine"] = explicit
    if tuning is not None:
        tuning_config = DataFrameTuningConfiguration()
        tuning_config.execution.engine_affinity = tuning
        config["tuning_config"] = tuning_config
    return get_dataframe_adapter("polars-df", **config)


@pytest.mark.parametrize(
    ("explicit", "tuning", "requested", "resolution"),
    [
        (None, None, "default", "version_default"),
        ("in-memory", None, "in-memory", "explicit"),
        (None, "in-memory", "in-memory", "tuning_profile"),
        ("streaming", "streaming", "streaming", "explicit"),
    ],
    ids=["default", "explicit", "tuning", "explicit-over-tuning"],
)
def test_highest_precedence_source_sets_the_resolution(
    explicit: str | None, tuning: str | None, requested: str, resolution: str
) -> None:
    receipt = _adapter(explicit, tuning).get_platform_info()["execution_engine"]

    assert receipt["requested"] == requested
    assert receipt["resolution"] == resolution


def test_sources_with_different_engines_fail_and_name_each_source() -> None:
    with pytest.raises(ValueError, match="Conflicting execution engine sources") as raised:
        _adapter("in-memory", "streaming")

    assert all(part in str(raised.value) for part in ("explicit setting", "'in-memory'", "tuning file", "'streaming'"))


def test_removed_streaming_keyword_is_rejected() -> None:
    with pytest.raises(TypeError, match="--execution-engine streaming"):
        get_dataframe_adapter("polars-df", streaming=True)
