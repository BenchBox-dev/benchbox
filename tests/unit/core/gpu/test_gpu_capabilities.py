# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.experimental.gpu.capabilities import (
    GPUCapabilities,
    GPUDevice,
    GPUInfo,
    GPUVendor,
    _detect_cuda_toolkit,
    _detect_nvidia_smi,
    _detect_rapids,
    detect_gpu,
    get_gpu_capabilities,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGPUVendor:
    def test_nvidia_value(self):
        assert GPUVendor.NVIDIA.value == "nvidia"

    def test_amd_value(self):
        assert GPUVendor.AMD.value == "amd"

    def test_unknown_value(self):
        assert GPUVendor.UNKNOWN.value == "unknown"


class TestGPUDevice:
    def test_basic_creation(self):
        device = GPUDevice(
            index=0,
            name="NVIDIA A100",
            vendor=GPUVendor.NVIDIA,
            memory_total_mb=40960,
        )
        assert device.index == 0
        assert device.name == "NVIDIA A100"
        assert device.vendor == GPUVendor.NVIDIA
        assert device.memory_total_mb == 40960

    def test_with_all_fields(self):
        device = GPUDevice(
            index=0,
            name="NVIDIA RTX 4090",
            vendor=GPUVendor.NVIDIA,
            memory_total_mb=24576,
            memory_free_mb=20000,
            compute_capability="8.9",
            driver_version="535.54.03",
            cuda_version="12.1",
            pcie_generation=4,
            pcie_width=16,
            temperature_celsius=45.0,
            power_watts=150.0,
            utilization_percent=25.0,
        )
        assert device.compute_capability == "8.9"
        assert device.driver_version == "535.54.03"
        assert device.pcie_generation == 4
        assert device.temperature_celsius == 45.0

    def test_to_dict(self):
        device = GPUDevice(
            index=0,
            name="Test GPU",
            vendor=GPUVendor.NVIDIA,
            memory_total_mb=8000,
            memory_free_mb=6000,
        )
        d = device.to_dict()
        assert d["index"] == 0
        assert d["name"] == "Test GPU"
        assert d["vendor"] == "nvidia"
        assert d["memory_total_mb"] == 8000
        assert d["memory_free_mb"] == 6000


class TestGPUInfo:
    def test_default_creation(self):
        info = GPUInfo()
        assert info.available is False
        assert info.device_count == 0
        assert info.devices == []

    def test_with_devices(self):
        device = GPUDevice(
            index=0,
            name="Test GPU",
            vendor=GPUVendor.NVIDIA,
            memory_total_mb=16000,
            memory_free_mb=12000,
        )
        info = GPUInfo(
            available=True,
            device_count=1,
            devices=[device],
            cuda_available=True,
            cuda_version="12.1",
        )
        assert info.available is True
        assert info.device_count == 1
        assert len(info.devices) == 1

    def test_total_memory_mb(self):
        devices = [
            GPUDevice(index=0, name="GPU0", vendor=GPUVendor.NVIDIA, memory_total_mb=16000, memory_free_mb=12000),
            GPUDevice(index=1, name="GPU1", vendor=GPUVendor.NVIDIA, memory_total_mb=16000, memory_free_mb=14000),
        ]
        info = GPUInfo(available=True, device_count=2, devices=devices)
        assert info.total_memory_mb == 32000
        assert info.total_free_memory_mb == 26000

    def test_to_dict(self):
        info = GPUInfo(
            available=True,
            device_count=1,
            cuda_available=True,
            cuda_version="12.1",
            cudf_available=True,
            cudf_version="23.10",
        )
        d = info.to_dict()
        assert d["available"] is True
        assert d["device_count"] == 1
        assert d["cuda_available"] is True
        assert d["cuda_version"] == "12.1"
        assert d["cudf_available"] is True


class TestGPUCapabilities:
    def test_basic_creation(self):
        info = GPUInfo()
        caps = GPUCapabilities(info=info)
        assert caps.info == info
        assert caps.supports_fp16 is False
        assert caps.supports_tensor_cores is False

    def test_to_dict(self):
        info = GPUInfo(available=True)
        caps = GPUCapabilities(
            info=info,
            supports_fp16=True,
            supports_tensor_cores=True,
            warp_size=32,
        )
        d = caps.to_dict()
        assert d["supports_fp16"] is True
        assert d["supports_tensor_cores"] is True
        assert d["warp_size"] == 32
        assert "info" in d


class TestDetectNvidiaSmi:
    @patch("subprocess.run")
    def test_nvidia_smi_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0, NVIDIA A100, 40960, 38000, 8.0, 535.54.03, 45, 150, 25, 4, 16\n",
        )
        devices = _detect_nvidia_smi()
        assert len(devices) == 1
        assert devices[0]["name"] == "NVIDIA A100"
        assert devices[0]["memory_total"] == 40960

    @patch("subprocess.run")
    def test_nvidia_smi_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        devices = _detect_nvidia_smi()
        assert devices == []

    @patch("subprocess.run")
    def test_nvidia_smi_timeout(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)
        devices = _detect_nvidia_smi()
        assert devices == []


class TestDetectCudaToolkit:
    @patch("subprocess.run")
    def test_nvcc_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="nvcc: NVIDIA (R) Cuda compiler driver\nCuda compilation tools, release 12.1, V12.1.66",
        )
        available, version = _detect_cuda_toolkit()
        assert available is True
        assert "12.1" in version

    @patch("subprocess.run")
    def test_nvcc_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        available, version = _detect_cuda_toolkit()


class TestDetectRapids:
    def test_rapids_not_available(self):
        available, version, libraries = _detect_rapids()
        assert isinstance(available, bool)
        assert isinstance(version, str)
        assert isinstance(libraries, dict)


class TestDetectGPU:
    @patch("benchbox.experimental.gpu.capabilities._detect_nvidia_smi")
    @patch("benchbox.experimental.gpu.capabilities._detect_cuda_toolkit")
    @patch("benchbox.experimental.gpu.capabilities._detect_rapids")
    def test_detect_with_nvidia(self, mock_rapids, mock_cuda, mock_nvidia):
        mock_nvidia.return_value = [
            {
                "index": 0,
                "name": "NVIDIA A100",
                "memory_total": 40960,
                "memory_free": 38000,
                "compute_cap": "8.0",
                "driver_version": "535.54.03",
                "temperature": 45.0,
                "power": 150.0,
                "utilization": 25.0,
                "pcie_gen": 4,
                "pcie_width": 16,
            }
        ]
        mock_cuda.return_value = (True, "12.1")
        mock_rapids.return_value = (False, "", {})

        info = detect_gpu()
        assert info.available is True
        assert info.device_count == 1
        assert info.devices[0].name == "NVIDIA A100"
        assert info.cuda_available is True
        assert info.cuda_version == "12.1"

    @patch("benchbox.experimental.gpu.capabilities._detect_nvidia_smi")
    @patch("benchbox.experimental.gpu.capabilities._detect_cuda_toolkit")
    @patch("benchbox.experimental.gpu.capabilities._detect_rapids")
    def test_detect_no_gpu(self, mock_rapids, mock_cuda, mock_nvidia):
        mock_nvidia.return_value = []
        mock_cuda.return_value = (False, "")
        mock_rapids.return_value = (False, "", {})

        info = detect_gpu()
        assert info.available is False
        assert info.device_count == 0


class TestGetGPUCapabilities:
    @patch("benchbox.experimental.gpu.capabilities.detect_gpu")
    def test_capabilities_with_ampere(self, mock_detect):
        mock_detect.return_value = GPUInfo(
            available=True,
            device_count=1,
            devices=[
                GPUDevice(
                    index=0,
                    name="NVIDIA A100",
                    vendor=GPUVendor.NVIDIA,
                    memory_total_mb=40960,
                    compute_capability="8.0",
                )
            ],
        )

        caps = get_gpu_capabilities()
        assert caps.supports_fp16 is True
        assert caps.supports_tensor_cores is True
        assert caps.supports_fp64 is True
        assert caps.memory_bandwidth_gbps > 0

    @patch("benchbox.experimental.gpu.capabilities.detect_gpu")
    def test_capabilities_no_gpu(self, mock_detect):
        mock_detect.return_value = GPUInfo(available=False)

        caps = get_gpu_capabilities()
        assert caps.supports_fp16 is False
        assert caps.supports_tensor_cores is False


