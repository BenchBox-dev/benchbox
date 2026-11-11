from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.spark_logging import suppress_window_exec_warning

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestSuppressWindowExecWarning:
    def test_suppresses_via_logger_object_call_shape(self):

        mock_spark = MagicMock()
        mock_jvm = mock_spark.sparkContext._jvm
        mock_logger = MagicMock()
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.return_value = mock_logger

        suppress_window_exec_warning(mock_spark)

        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.assert_called_once_with(
            "org.apache.spark.sql.execution.window.WindowExec"
        )
        mock_jvm.org.apache.logging.log4j.core.config.Configurator.setLevel.assert_called_once_with(
            mock_logger,
            mock_jvm.org.apache.logging.log4j.Level.ERROR,
        )

    def test_does_not_raise_on_jvm_error(self):
        mock_spark = MagicMock()
        mock_spark.sparkContext._jvm.org.apache.logging.log4j.LogManager.getLogger.side_effect = RuntimeError(
            "JVM gateway unavailable"
        )

        suppress_window_exec_warning(mock_spark)

    def test_logs_debug_on_failure(self, caplog):
        mock_spark = MagicMock()
        mock_spark.sparkContext._jvm.org.apache.logging.log4j.LogManager.getLogger.side_effect = RuntimeError(
            "JVM gateway unavailable"
        )

        with caplog.at_level(logging.DEBUG, logger="benchbox.platforms.base.spark_logging"):
            suppress_window_exec_warning(mock_spark)

        assert any("WindowExec warning suppression failed" in msg for msg in caplog.messages)


