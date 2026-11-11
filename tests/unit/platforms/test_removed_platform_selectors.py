import pytest

from benchbox.platforms.adapter_factory import get_adapter, reject_removed_platform

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize("selector", ["modin", "modin-df", "modin:local", "modin-df:local"])
def test_removed_modin_selectors_name_supported_replacements(selector: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        get_adapter(selector)

    message = str(exc_info.value)
    assert "removed" in message
    assert "pandas-df" in message
    assert "dask-df" in message


@pytest.mark.parametrize("selector", ["modin", "modin-df", "modin:local", "modin-df:local"])
def test_reject_removed_platform_direct(selector: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        reject_removed_platform(selector)

    message = str(exc_info.value)
    assert "removed" in message
    assert "pandas-df" in message
    assert "dask-df" in message


def test_reject_removed_platform_noop_for_supported_platform() -> None:
    reject_removed_platform("duckdb")
    reject_removed_platform("pandas-df")
    reject_removed_platform("dask-df")


