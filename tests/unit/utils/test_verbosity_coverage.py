from __future__ import annotations

import logging

import pytest

from benchbox.utils.verbosity import VerbosityMixin, VerbositySettings, compute_verbosity, create_debug_logger

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DummyVerbosity(VerbosityMixin):
    pass


def test_compute_verbosity_normalizes_flags() -> None:
    assert compute_verbosity(verbose=True, quiet=False).level == 2
    assert compute_verbosity(verbose=-2, quiet=False).level == 0
    assert compute_verbosity(verbose=2, quiet=True).quiet is True


def test_settings_from_mapping_and_to_config_round_trip() -> None:
    settings = VerbositySettings.from_mapping({"verbose_level": 2, "quiet": False})
    config = settings.to_config()

    assert config["verbose_level"] == 2
    assert config["very_verbose"] is True
    assert settings.verbose is True


def test_mixin_logger_contract_and_apply_behavior(caplog: pytest.LogCaptureFixture) -> None:
    inst = _DummyVerbosity()

    with pytest.raises(AttributeError, match="requires 'logger'"):
        _ = inst.logger

    with pytest.raises(TypeError, match="logging.Logger"):
        inst.logger = object()

    inst.logger = logging.getLogger("benchbox.test.verbosity")
    inst.apply_verbosity(VerbositySettings(level=1, verbose_enabled=True, quiet=False))

    with caplog.at_level(logging.INFO, logger=inst.logger.name):
        inst.log_operation_start("load")

    assert any("Starting load" in record.message for record in caplog.records)


def test_create_debug_logger_sets_expected_levels() -> None:
    logger_quiet = create_debug_logger("benchbox.test.quiet", verbose_level=2, quiet=True)
    logger_debug = create_debug_logger("benchbox.test.debug", verbose_level=2, quiet=False)

    assert logger_quiet.level == logging.ERROR
    assert logger_debug.level == logging.DEBUG


