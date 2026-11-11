import logging
import subprocess

import pytest

from benchbox.experimental.gpu import metrics as metrics_module
from benchbox.experimental.gpu.metrics import GPUMemoryTracker

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestGPUMetricsErrorCapture:
    def test_memory_probe_logs_cupy_and_smi_failures(self, caplog, monkeypatch):
        tracker = GPUMemoryTracker(device_index=0)

        def raise_on_run(*args, **kwargs):
            raise FileNotFoundError("nvidia-smi not found")

        monkeypatch.setattr(subprocess, "run", raise_on_run)

        with caplog.at_level(logging.DEBUG, logger=metrics_module.__name__):
            result = tracker._get_current_memory_mb()

        assert result == 0
        messages = [r.message for r in caplog.records]
        assert any("cupy memory probe failed" in m for m in messages)
        assert any("nvidia-smi memory fallback failed" in m for m in messages)


