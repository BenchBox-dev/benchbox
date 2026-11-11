import logging
import os

import pytest

from benchbox.experimental.gpu import capabilities as capabilities_module
from benchbox.experimental.gpu.capabilities import _detect_cuda_toolkit

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestCUDACapabilitiesErrorCapture:
    def test_malformed_version_file_logs_debug(self, caplog, tmp_path, monkeypatch):
        (tmp_path / "version.txt").write_text("CUDA Version")

        monkeypatch.setenv("CUDA_HOME", str(tmp_path))
        monkeypatch.delenv("CUDA_PATH", raising=False)

        def raise_on_run(*args, **kwargs):
            raise FileNotFoundError("nvcc not available")

        monkeypatch.setattr("subprocess.run", raise_on_run)

        with caplog.at_level(logging.DEBUG, logger=capabilities_module.__name__):
            available, _version = _detect_cuda_toolkit()

        assert available is True
        assert any("Failed to parse CUDA version file" in r.message for r in caplog.records)


