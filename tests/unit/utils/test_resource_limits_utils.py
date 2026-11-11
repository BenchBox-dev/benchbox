# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.utils.resource_limits import (
    calculate_safe_memory_limit,
    get_available_memory_mb,
    get_system_memory_mb,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestMemoryUtilities:
    def test_get_system_memory_mb(self):
        memory_mb = get_system_memory_mb()

        assert memory_mb > 1024 or memory_mb == 0
        assert memory_mb < 1024 * 1024 or memory_mb == 0

    def test_get_available_memory_mb(self):
        available_mb = get_available_memory_mb()

        assert available_mb >= 0

    @patch("benchbox.utils.resource_limits.get_system_memory_mb")
    def test_calculate_safe_memory_limit(self, mock_get_system):
        mock_get_system.return_value = 16384.0

        safe_limit = calculate_safe_memory_limit(safety_margin_percent=20.0)

        assert safe_limit == pytest.approx(13107.2, rel=0.01)

    @patch("benchbox.utils.resource_limits.get_system_memory_mb")
    def test_calculate_safe_memory_limit_with_max(self, mock_get_system):
        mock_get_system.return_value = 16384.0

        safe_limit = calculate_safe_memory_limit(
            safety_margin_percent=20.0,
            max_limit_mb=8192.0,
        )

        assert safe_limit == 8192.0

    @patch("benchbox.utils.resource_limits.get_system_memory_mb")
    def test_calculate_safe_memory_limit_unknown_system(self, mock_get_system):
        mock_get_system.return_value = 0

        safe_limit = calculate_safe_memory_limit()

        assert safe_limit == 8192.0

    @patch("benchbox.utils.resource_limits.get_system_memory_mb")
    def test_calculate_safe_memory_limit_unknown_with_max(self, mock_get_system):
        mock_get_system.return_value = 0

        safe_limit = calculate_safe_memory_limit(max_limit_mb=4096.0)

        assert safe_limit == 4096.0

    def test_get_system_memory_mb_without_psutil(self):
        with patch("benchbox.utils.resource_limits.psutil", None):
            assert get_system_memory_mb() == 0.0

    def test_get_system_memory_mb_handles_psutil_errors(self):
        mock_psutil = Mock()
        mock_psutil.virtual_memory.side_effect = RuntimeError("boom")

        with patch("benchbox.utils.resource_limits.psutil", mock_psutil):
            assert get_system_memory_mb() == 0.0

    def test_get_available_memory_mb_without_psutil(self):
        with patch("benchbox.utils.resource_limits.psutil", None):
            assert get_available_memory_mb() == 0.0

    def test_get_available_memory_mb_handles_psutil_errors(self):
        mock_psutil = Mock()
        mock_psutil.virtual_memory.side_effect = RuntimeError("boom")

        with patch("benchbox.utils.resource_limits.psutil", mock_psutil):
            assert get_available_memory_mb() == 0.0


