import pytest


@pytest.fixture
def preserved_quiet_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import benchbox.utils.printing as printing

    monkeypatch.setattr(printing, "_QUIET", printing._QUIET)
