from __future__ import annotations

import pytest

pytest.importorskip("polars")

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.platforms import get_dataframe_adapter
from tests.utilities.deprecation_capture import captured_deprecations

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _adapter(explicit: str | None, tuning: str | None, legacy: bool):
    config: dict[str, object] = {}
    if explicit is not None:
        config["execution_engine"] = explicit
    if tuning is not None:
        tuning_config = DataFrameTuningConfiguration()
        tuning_config.execution.engine_affinity = tuning
        config["tuning_config"] = tuning_config
    if legacy:
        config["streaming"] = True
    return get_dataframe_adapter("polars-df", **config)


@pytest.mark.parametrize(
    ("explicit", "tuning", "legacy", "requested", "resolution"),
    [
        (None, None, False, "default", "version_default"),
        ("in-memory", None, False, "in-memory", "explicit"),
        (None, "in-memory", False, "in-memory", "tuning_profile"),
        (None, None, True, "streaming", "legacy_option"),
        ("streaming", "streaming", False, "streaming", "explicit"),
        ("streaming", None, True, "streaming", "explicit"),
        (None, "streaming", True, "streaming", "tuning_profile"),
        ("streaming", "streaming", True, "streaming", "explicit"),
    ],
    ids=[
        "default",
        "explicit",
        "tuning",
        "legacy",
        "explicit-over-tuning",
        "explicit-over-legacy",
        "tuning-over-legacy",
        "explicit-over-both",
    ],
)
def test_highest_precedence_source_sets_the_resolution(
    explicit: str | None, tuning: str | None, legacy: bool, requested: str, resolution: str, caplog
) -> None:
    with captured_deprecations(caplog):
        receipt = _adapter(explicit, tuning, legacy).get_platform_info()["execution_engine"]

    assert receipt["requested"] == requested
    assert receipt["resolution"] == resolution


@pytest.mark.parametrize(
    ("explicit", "tuning", "legacy", "named"),
    [
        ("in-memory", "streaming", False, ("explicit setting", "'in-memory'", "tuning file", "'streaming'")),
        ("in-memory", None, True, ("explicit setting", "'in-memory'", "legacy option", "'streaming'")),
        (None, "in-memory", True, ("tuning file", "'in-memory'", "legacy option", "'streaming'")),
        ("in-memory", "streaming", True, ("explicit setting", "tuning file", "legacy option")),
    ],
    ids=["explicit-vs-tuning", "explicit-vs-legacy", "tuning-vs-legacy", "all-three"],
)
def test_sources_with_different_engines_fail_and_name_each_source(
    explicit: str | None, tuning: str | None, legacy: bool, named: tuple[str, ...], caplog
) -> None:
    with (
        captured_deprecations(caplog),
        pytest.raises(ValueError, match="Conflicting execution engine sources") as raised,
    ):
        _adapter(explicit, tuning, legacy)

    assert all(part in str(raised.value) for part in named)
