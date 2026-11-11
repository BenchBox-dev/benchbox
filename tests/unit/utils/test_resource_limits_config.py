# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time

import pytest

from benchbox.utils.resource_limits import (
    ResourceLimitExceeded,
    ResourceLimitsConfig,
    ResourceUsageSummary,
    ResourceWarning,
    ResourceWarningLevel,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestResourceWarningLevel:
    def test_warning_levels_exist(self):
        assert ResourceWarningLevel.INFO.value == "info"
        assert ResourceWarningLevel.WARNING.value == "warning"
        assert ResourceWarningLevel.CRITICAL.value == "critical"


class TestResourceWarning:
    def test_create_resource_warning(self):
        warning = ResourceWarning(
            timestamp=time.time(),
            level=ResourceWarningLevel.WARNING,
            resource_type="memory",
            current_value=85.0,
            threshold_value=75.0,
            message="High memory usage",
        )
        assert warning.resource_type == "memory"
        assert warning.level == ResourceWarningLevel.WARNING

    def test_resource_warning_to_dict(self):
        ts = time.time()
        warning = ResourceWarning(
            timestamp=ts,
            level=ResourceWarningLevel.CRITICAL,
            resource_type="cpu",
            current_value=95.0,
            threshold_value=90.0,
            message="High CPU usage",
        )
        result = warning.to_dict()

        assert result["timestamp"] == ts
        assert result["level"] == "critical"
        assert result["resource_type"] == "cpu"
        assert result["current_value"] == 95.0
        assert result["threshold_value"] == 90.0
        assert result["message"] == "High CPU usage"


class TestResourceLimitsConfig:
    def test_default_config(self):
        config = ResourceLimitsConfig()

        assert config.memory_limit_mb is None
        assert config.memory_warning_percent == 75.0
        assert config.memory_critical_percent == 90.0
        assert config.cpu_warning_percent == 90.0
        assert config.default_operation_timeout == 300.0
        assert config.enforce_timeouts is True
        assert config.enable_graceful_degradation is False
        assert config.degradation_memory_threshold_percent == 80.0

    def test_custom_config(self):
        config = ResourceLimitsConfig(
            memory_limit_mb=4096.0,
            memory_warning_percent=60.0,
            memory_critical_percent=80.0,
            cpu_warning_percent=85.0,
            enable_graceful_degradation=True,
        )

        assert config.memory_limit_mb == 4096.0
        assert config.memory_warning_percent == 60.0
        assert config.memory_critical_percent == 80.0
        assert config.cpu_warning_percent == 85.0
        assert config.enable_graceful_degradation is True

    def test_validation_warning_less_than_critical(self):
        with pytest.raises(ValueError, match="memory_warning_percent must be less than memory_critical_percent"):
            ResourceLimitsConfig(
                memory_warning_percent=90.0,
                memory_critical_percent=90.0,
            )

        with pytest.raises(ValueError, match="memory_warning_percent must be less than memory_critical_percent"):
            ResourceLimitsConfig(
                memory_warning_percent=95.0,
                memory_critical_percent=90.0,
            )

    def test_validation_warning_percent_range(self):
        with pytest.raises(ValueError, match="memory_warning_percent must be between 0 and 100"):
            ResourceLimitsConfig(memory_warning_percent=0, memory_critical_percent=50.0)

        with pytest.raises(ValueError):
            ResourceLimitsConfig(memory_warning_percent=101.0)

    def test_validation_critical_percent_range(self):
        with pytest.raises(ValueError):
            ResourceLimitsConfig(
                memory_warning_percent=50.0,
                memory_critical_percent=0,
            )

        with pytest.raises(ValueError, match="memory_critical_percent must be between 0 and 100"):
            ResourceLimitsConfig(
                memory_warning_percent=50.0,
                memory_critical_percent=101.0,
            )

    def test_from_config_dict(self):
        config_dict = {
            "memory_limit_mb": 8192.0,
            "memory_warning_percent": 70.0,
            "memory_critical_percent": 85.0,
            "enable_graceful_degradation": True,
        }

        config = ResourceLimitsConfig.from_config_dict(config_dict)

        assert config.memory_limit_mb == 8192.0
        assert config.memory_warning_percent == 70.0
        assert config.memory_critical_percent == 85.0
        assert config.enable_graceful_degradation is True

    def test_from_config_dict_with_defaults(self):
        config = ResourceLimitsConfig.from_config_dict({})

        assert config.memory_limit_mb is None
        assert config.memory_warning_percent == 75.0
        assert config.memory_critical_percent == 90.0


class TestResourceLimitExceeded:
    def test_exception_attributes(self):
        exc = ResourceLimitExceeded(
            message="Memory limit exceeded",
            resource_type="memory",
            current_value=5000.0,
            limit_value=4096.0,
        )

        assert str(exc) == "Memory limit exceeded"
        assert exc.resource_type == "memory"
        assert exc.current_value == 5000.0
        assert exc.limit_value == 4096.0


class TestResourceUsageSummary:
    def test_default_summary(self):
        summary = ResourceUsageSummary()

        assert summary.peak_memory_mb == 0.0
        assert summary.average_memory_mb == 0.0
        assert summary.peak_cpu_percent == 0.0
        assert summary.average_cpu_percent == 0.0
        assert summary.warnings == []
        assert summary.limit_exceeded is False
        assert summary.degradation_triggered is False

    def test_summary_to_dict(self):
        warning = ResourceWarning(
            timestamp=time.time(),
            level=ResourceWarningLevel.WARNING,
            resource_type="memory",
            current_value=80.0,
            threshold_value=75.0,
            message="High memory",
        )

        summary = ResourceUsageSummary(
            peak_memory_mb=1024.0,
            average_memory_mb=512.0,
            peak_cpu_percent=85.0,
            average_cpu_percent=50.0,
            warnings=[warning],
            limit_exceeded=False,
            degradation_triggered=True,
        )

        result = summary.to_dict()

        assert result["peak_memory_mb"] == 1024.0
        assert result["average_memory_mb"] == 512.0
        assert result["peak_cpu_percent"] == 85.0
        assert result["average_cpu_percent"] == 50.0
        assert result["warning_count"] == 1
        assert len(result["warnings"]) == 1
        assert result["limit_exceeded"] is False
        assert result["degradation_triggered"] is True


