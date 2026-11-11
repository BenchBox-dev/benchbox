from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import benchbox.utils.verbosity as verbosity
from benchbox.utils.verbosity import (
    VerbosityMixin,
    VerbositySettings,
    compute_verbosity,
    create_debug_logger,
    log_debug_context,
    log_import_debug,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _VerbosityHarness(VerbosityMixin):
    def __init__(self) -> None:
        logger = logging.getLogger("benchbox.tests.verbosity")
        self.logger = logger
        self.logger.info = MagicMock()
        self.logger.debug = MagicMock()
        self.logger.warning = MagicMock()
        self.logger.error = MagicMock()


def test_verbosity_settings_normalize_flags_and_mappings() -> None:
    verbose = VerbositySettings.from_flags(True, False)
    assert verbose.level == 2
    assert verbose.verbose is True
    assert verbose.to_config()["very_verbose"] is True

    quiet = compute_verbosity(2, True)
    assert quiet.level == 0
    assert quiet.quiet is True
    assert quiet.verbose is False

    mapped = VerbositySettings.from_mapping({"verbose_level": 1, "very_verbose": False, "quiet": False})
    assert mapped.level == 1
    assert mapped.verbose_enabled is True
    assert VerbositySettings.default() == VerbositySettings()


def test_verbosity_mixin_log_methods_respect_levels_and_quiet() -> None:
    harness = _VerbosityHarness()
    harness.apply_verbosity(VerbositySettings(level=1, verbose_enabled=True, very_verbose=False, quiet=False))

    harness.log_verbose("hello")
    harness.log_operation_start("load", "table")
    harness.log_operation_complete("load", duration=1.25)

    harness.logger.info.assert_any_call("hello")
    harness.logger.info.assert_any_call("Starting load")
    harness.logger.info.assert_any_call("\u2713 load completed in 1.25s")

    harness.apply_verbosity(VerbositySettings(level=2, verbose_enabled=True, very_verbose=True, quiet=False))
    harness.log_very_verbose("details")
    harness.log_operation_start("query", "Q1")
    harness.log_operation_complete("query", duration=0.5, details="done")

    harness.logger.debug.assert_any_call("details")
    harness.logger.debug.assert_any_call("Starting query: Q1")
    harness.logger.debug.assert_any_call("Completed query in 0.50s: done")

    harness.apply_verbosity(VerbositySettings(level=0, verbose_enabled=False, very_verbose=False, quiet=True))
    harness.log_verbose("suppressed")
    harness.log_error_with_debug_info(RuntimeError("boom"), "Quiet")
    assert not any(call.args == ("suppressed",) for call in harness.logger.info.call_args_list)
    assert not any(call.args == ("Quiet: boom",) for call in harness.logger.error.call_args_list)


def test_log_debug_info_and_error_paths_include_version_and_traceback() -> None:
    harness = _VerbosityHarness()
    harness.apply_verbosity(VerbositySettings(level=2, verbose_enabled=True, very_verbose=True, quiet=False))

    with patch.object(verbosity, "format_version_report", return_value="Version: 1.2.3\nPython: 3.11"):
        harness.log_debug_info("Context")

    harness.logger.debug.assert_any_call("=== Context Information ===")
    assert any("Version: 1.2.3" in call.args[0] for call in harness.logger.debug.call_args_list)

    with patch("traceback.format_exc", return_value="Traceback line 1\nTraceback line 2"):
        harness.log_error_with_debug_info(RuntimeError("boom"), "Failure")

    harness.logger.error.assert_any_call("Failure: boom")
    harness.logger.debug.assert_any_call("Traceback:")
    assert any("Traceback line 1" in call.args[0] for call in harness.logger.debug.call_args_list)


def test_log_version_warning_emits_inconsistency_details() -> None:
    harness = _VerbosityHarness()
    harness.apply_verbosity(VerbositySettings(level=2, verbose_enabled=True, very_verbose=True, quiet=False))
    result = SimpleNamespace(consistent=False, message="mismatch", sources={"package": "1.0.0"})

    with patch.object(verbosity, "check_version_consistency", return_value=result):
        harness.log_version_warning()

    harness.logger.warning.assert_called_once_with("Version inconsistency detected: mismatch")
    harness.logger.debug.assert_any_call("  package: 1.0.0")


def test_create_debug_logger_and_helper_functions_cover_debug_utilities() -> None:
    result = SimpleNamespace(consistent=False, message="mismatch", sources={})

    with patch.object(verbosity, "check_version_consistency", return_value=result):
        logger = create_debug_logger("benchbox.tests.created-debug", verbose_level=2, quiet=False)

    assert logger.level == logging.DEBUG

    mock_logger = Mock()
    log_debug_context(mock_logger, {"phase": "load", "table": "lineitem"}, title="Run Context")
    mock_logger.debug.assert_any_call("=== Run Context ===")
    mock_logger.debug.assert_any_call("  phase: load")
    mock_logger.debug.assert_any_call("  table: lineitem")

    with patch.object(verbosity, "format_version_report", return_value="Version: 1.2.3\nPython: 3.11\nOS: macOS"):
        log_import_debug(mock_logger, "duckdb", RuntimeError("not installed"))

    mock_logger.debug.assert_any_call("Failed to import duckdb: not installed")
    assert any("Version context for import failure:" in call.args[0] for call in mock_logger.debug.call_args_list)
    assert any("Version: 1.2.3" in call.args[0] for call in mock_logger.debug.call_args_list)
    assert any("Python: 3.11" in call.args[0] for call in mock_logger.debug.call_args_list)

    log_import_debug(mock_logger, "polars")
    mock_logger.debug.assert_any_call("Successfully imported polars")


