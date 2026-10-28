# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, mock_open, patch

import pytest

from benchbox.utils.system_info import (
    SystemInfo,
    get_cpu_info,
    get_memory_info,
    get_system_info,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestSystemInfo:
    def test_system_info_creation(self):
        info = SystemInfo(
            os_name="Linux",
            os_version="5.4.0",
            architecture="x86_64",
            cpu_model="Intel Core i7-8700K",
            cpu_cores=8,
            total_memory_gb=16.0,
            available_memory_gb=12.5,
            python_version="3.11.0",
            hostname="test-machine",
        )

        assert info.os_name == "Linux"
        assert info.os_version == "5.4.0"
        assert info.architecture == "x86_64"
        assert info.cpu_model == "Intel Core i7-8700K"
        assert info.cpu_cores == 8
        assert info.total_memory_gb == 16.0
        assert info.available_memory_gb == 12.5
        assert info.python_version == "3.11.0"
        assert info.hostname == "test-machine"

    def test_system_info_to_dict(self):
        info = SystemInfo(
            os_name="Darwin",
            os_version="21.6.0",
            architecture="arm64",
            cpu_model="Apple M1",
            cpu_cores=8,
            total_memory_gb=16.0,
            available_memory_gb=14.2,
            python_version="3.11.2",
            hostname="macbook-pro",
        )

        result = info.to_dict()

        expected = {
            "os_type": "Darwin",
            "os_version": "21.6.0",
            "architecture": "arm64",
            "cpu_model": "Apple M1",
            "cpu_vendor": None,
            "cpu_identity_provenance": None,
            "cpu_cores": 8,
            "total_memory_gb": 16.0,
            "available_memory_gb": 14.2,
            "python_version": "3.11.2",
            "hostname": "macbook-pro",
            "cpu_count": 8,
            "memory_gb": 16.0,
            "os_release": "21.6.0",
            "execution_class": None,
            "container_runtime": None,
            "container_host_os": None,
            "execution_class_source": None,
        }

        assert result == expected
        assert isinstance(result, dict)

    def test_system_info_to_dict_compatibility_mapping(self):
        info = SystemInfo(
            os_name="Windows",
            os_version="10",
            architecture="AMD64",
            cpu_model="AMD Ryzen 7",
            cpu_cores=16,
            total_memory_gb=32.0,
            available_memory_gb=28.0,
            python_version="3.11.1",
            hostname="windows-pc",
        )

        result = info.to_dict()

        assert result["os_type"] == "Windows"
        assert "os_name" not in result

    def test_system_info_edge_cases(self):
        info = SystemInfo(
            os_name="Unknown",
            os_version="Unknown",
            architecture="Unknown",
            cpu_model="Unknown CPU",
            cpu_cores=0,
            total_memory_gb=0.0,
            available_memory_gb=0.0,
            python_version="0.0.0",
            hostname="unknown",
        )

        assert info.os_name == "Unknown"
        assert info.cpu_cores == 0
        assert info.total_memory_gb == 0.0

        result = info.to_dict()
        assert result["os_type"] == "Unknown"
        assert result["cpu_cores"] == 0


class TestGetSystemInfo:
    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.system")
    @patch("benchbox.utils.system_info.platform.release")
    @patch("benchbox.utils.system_info.platform.machine")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.python_version")
    @patch("benchbox.utils.system_info.platform.node")
    def test_get_system_info_basic(
        self,
        mock_node,
        mock_python_version,
        mock_processor,
        mock_machine,
        mock_release,
        mock_system,
        mock_cpu_count,
        mock_virtual_memory,
    ):
        mock_memory = MagicMock()
        mock_memory.total = 16 * (1024**3)
        mock_memory.available = 12 * (1024**3)
        mock_virtual_memory.return_value = mock_memory

        mock_cpu_count.return_value = 8
        mock_system.return_value = "Linux"
        mock_release.return_value = "5.4.0"
        mock_machine.return_value = "x86_64"
        mock_processor.return_value = "Intel Core i7-8700K"
        mock_python_version.return_value = "3.11.0"
        mock_node.return_value = "test-machine"

        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            result = get_system_info()

        assert isinstance(result, SystemInfo)
        assert result.os_name == "Linux"
        assert result.os_version == "5.4.0"
        assert result.architecture == "x86_64"
        assert result.cpu_model == "Intel Core i7-8700K"
        assert result.cpu_cores == 8
        assert result.total_memory_gb == 16.0
        assert result.available_memory_gb == 12.0
        assert result.python_version == "3.11.0"
        assert result.hostname == "test-machine"

    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.system")
    @patch("benchbox.utils.system_info.platform.release")
    @patch("benchbox.utils.system_info.platform.machine")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.python_version")
    @patch("benchbox.utils.system_info.platform.node")
    def test_get_system_info_empty_processor(
        self,
        mock_node,
        mock_python_version,
        mock_processor,
        mock_machine,
        mock_release,
        mock_system,
        mock_cpu_count,
        mock_virtual_memory,
    ):
        mock_memory = MagicMock()
        mock_memory.total = 8 * (1024**3)
        mock_memory.available = 6 * (1024**3)
        mock_virtual_memory.return_value = mock_memory

        mock_cpu_count.return_value = 4
        mock_system.return_value = "Darwin"
        mock_release.return_value = "21.6.0"
        mock_machine.return_value = "arm64"
        mock_processor.return_value = ""
        mock_python_version.return_value = "3.11.2"
        mock_node.return_value = "macbook"

        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch(
                "builtins.open",
                mock_open(read_data="model name\t: Apple M1\nother: value\n"),
            ):
                result = get_system_info()

        assert result.cpu_model == "Apple M1"
        assert result.os_name == "Darwin"
        assert result.architecture == "arm64"

    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.system")
    @patch("benchbox.utils.system_info.platform.release")
    @patch("benchbox.utils.system_info.platform.machine")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.python_version")
    @patch("benchbox.utils.system_info.platform.node")
    def test_get_system_info_processor_fallback_to_machine(
        self,
        mock_node,
        mock_python_version,
        mock_processor,
        mock_machine,
        mock_release,
        mock_system,
        mock_cpu_count,
        mock_virtual_memory,
    ):
        mock_memory = MagicMock()
        mock_memory.total = 4 * (1024**3)
        mock_memory.available = 3 * (1024**3)
        mock_virtual_memory.return_value = mock_memory

        mock_cpu_count.return_value = 2
        mock_system.return_value = "Windows"
        mock_release.return_value = "10"
        mock_machine.return_value = "AMD64"
        mock_processor.return_value = None
        mock_python_version.return_value = "3.11.1"
        mock_node.return_value = "windows-pc"

        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                result = get_system_info()

        assert result.cpu_model is None
        assert result.cpu_identity_provenance is None
        assert result.os_name == "Windows"
        assert result.architecture == "AMD64"

    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.system")
    @patch("benchbox.utils.system_info.platform.release")
    @patch("benchbox.utils.system_info.platform.machine")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.python_version")
    @patch("benchbox.utils.system_info.platform.node")
    def test_get_system_info_exception_handling(
        self,
        mock_node,
        mock_python_version,
        mock_processor,
        mock_machine,
        mock_release,
        mock_system,
        mock_cpu_count,
        mock_virtual_memory,
    ):
        mock_memory = MagicMock()
        mock_memory.total = 2 * (1024**3)
        mock_memory.available = 1.5 * (1024**3)
        mock_virtual_memory.return_value = mock_memory

        mock_cpu_count.return_value = 1
        mock_system.return_value = "Linux"
        mock_release.return_value = "4.15.0"
        mock_machine.return_value = "i686"
        mock_processor.side_effect = Exception("Processor detection failed")
        mock_python_version.return_value = "3.9.0"
        mock_node.return_value = "old-system"

        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                result = get_system_info()

        assert result.cpu_model is None
        assert result.cpu_identity_provenance is None
        assert result.os_name == "Linux"
        assert result.cpu_cores == 1
        assert result.total_memory_gb == 2.0
        assert result.available_memory_gb == 1.5

    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.system")
    @patch("benchbox.utils.system_info.platform.release")
    @patch("benchbox.utils.system_info.platform.machine")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.python_version")
    @patch("benchbox.utils.system_info.platform.node")
    def test_get_system_info_memory_calculation(
        self,
        mock_node,
        mock_python_version,
        mock_processor,
        mock_machine,
        mock_release,
        mock_system,
        mock_cpu_count,
        mock_virtual_memory,
    ):
        mock_memory = MagicMock()
        mock_memory.total = 17179869184
        mock_memory.available = 8589934592
        mock_virtual_memory.return_value = mock_memory

        mock_cpu_count.return_value = 4
        mock_system.return_value = "Linux"
        mock_release.return_value = "5.10.0"
        mock_machine.return_value = "x86_64"
        mock_processor.return_value = "Test CPU"
        mock_python_version.return_value = "3.10.0"
        mock_node.return_value = "test"

        result = get_system_info()

        assert result.total_memory_gb == 16.0
        assert result.available_memory_gb == 8.0

    def test_get_system_info_real_system(self):
        result = get_system_info()

        assert isinstance(result, SystemInfo)
        assert isinstance(result.os_name, str)
        assert isinstance(result.os_version, str)
        assert isinstance(result.architecture, str)
        assert isinstance(result.cpu_model, str)
        assert isinstance(result.cpu_cores, int)
        assert isinstance(result.total_memory_gb, float)
        assert isinstance(result.available_memory_gb, float)
        assert isinstance(result.python_version, str)
        assert isinstance(result.hostname, str)

        assert result.cpu_cores > 0
        assert result.total_memory_gb > 0
        assert result.available_memory_gb > 0
        assert result.available_memory_gb <= result.total_memory_gb
        assert len(result.os_name) > 0
        assert len(result.hostname) > 0


class TestGetMemoryInfo:
    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    def test_get_memory_info(self, mock_virtual_memory):
        mock_memory = MagicMock()
        mock_memory.total = 16 * (1024**3)
        mock_memory.available = 10 * (1024**3)
        mock_memory.used = 6 * (1024**3)
        mock_memory.percent = 37.5
        mock_virtual_memory.return_value = mock_memory

        result = get_memory_info()

        expected = {
            "total_gb": 16.0,
            "available_gb": 10.0,
            "used_gb": 6.0,
            "percent_used": 37.5,
        }

        assert result == expected
        mock_virtual_memory.assert_called_once()

    @patch("benchbox.utils.system_info.psutil.virtual_memory")
    def test_get_memory_info_precision(self, mock_virtual_memory):
        mock_memory = MagicMock()
        mock_memory.total = 17179869184
        mock_memory.available = 5368709120
        mock_memory.used = 11811160064
        mock_memory.percent = 68.75
        mock_virtual_memory.return_value = mock_memory

        result = get_memory_info()

        assert result["total_gb"] == 16.0
        assert result["available_gb"] == 5.0
        assert result["used_gb"] == 11.0
        assert result["percent_used"] == 68.75

    def test_get_memory_info_real_system(self):
        result = get_memory_info()

        assert isinstance(result, dict)
        assert "total_gb" in result
        assert "available_gb" in result
        assert "used_gb" in result
        assert "percent_used" in result

        assert isinstance(result["total_gb"], float)
        assert isinstance(result["available_gb"], float)
        assert isinstance(result["used_gb"], float)
        assert isinstance(result["percent_used"], float)

        assert result["total_gb"] > 0
        assert result["available_gb"] >= 0
        assert result["used_gb"] >= 0
        assert result["available_gb"] <= result["total_gb"]
        assert 0 <= result["percent_used"] <= 100


class TestGetCPUInfo:
    @patch("benchbox.utils.system_info.psutil.cpu_percent")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.machine")
    def test_get_cpu_info(self, mock_machine, mock_processor, mock_cpu_count, mock_cpu_percent):
        mock_cpu_count.side_effect = lambda logical=True: 8 if logical else 4
        mock_cpu_percent.side_effect = [
            25.5,
            [10.0, 15.0, 30.0, 40.0, 20.0, 25.0, 35.0, 45.0],
        ]
        mock_processor.return_value = "Intel Core i7-8700K"
        mock_machine.return_value = "x86_64"

        result = get_cpu_info()

        expected = {
            "logical_cores": 8,
            "physical_cores": 4,
            "current_usage_percent": 25.5,
            "per_core_usage": [10.0, 15.0, 30.0, 40.0, 20.0, 25.0, 35.0, 45.0],
            "model": "Intel Core i7-8700K",
        }

        assert result == expected

        mock_cpu_count.assert_any_call()
        mock_cpu_count.assert_any_call(logical=False)
        mock_cpu_percent.assert_any_call(interval=1)
        mock_cpu_percent.assert_any_call(interval=1, percpu=True)

    @patch("benchbox.utils.system_info.psutil.cpu_percent")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.machine")
    def test_get_cpu_info_processor_fallback(self, mock_machine, mock_processor, mock_cpu_count, mock_cpu_percent):
        mock_cpu_count.side_effect = lambda logical=True: 2 if logical else 2
        mock_cpu_percent.side_effect = [50.0, [45.0, 55.0]]
        mock_processor.return_value = ""
        mock_machine.return_value = "arm64"

        result = get_cpu_info()

        assert result["model"] == "arm64 CPU"
        assert result["logical_cores"] == 2
        assert result["physical_cores"] == 2
        assert result["current_usage_percent"] == 50.0
        assert result["per_core_usage"] == [45.0, 55.0]

    @patch("benchbox.utils.system_info.psutil.cpu_percent")
    @patch("benchbox.utils.system_info.psutil.cpu_count")
    @patch("benchbox.utils.system_info.platform.processor")
    @patch("benchbox.utils.system_info.platform.machine")
    def test_get_cpu_info_single_core(self, mock_machine, mock_processor, mock_cpu_count, mock_cpu_percent):
        mock_cpu_count.side_effect = lambda logical=True: 1
        mock_cpu_percent.side_effect = [75.5, [75.5]]
        mock_processor.return_value = "Single Core CPU"
        mock_machine.return_value = "i386"

        result = get_cpu_info()

        assert result["logical_cores"] == 1
        assert result["physical_cores"] == 1
        assert result["current_usage_percent"] == 75.5
        assert result["per_core_usage"] == [75.5]
        assert result["model"] == "Single Core CPU"

    def test_get_cpu_info_real_system(self):
        result = get_cpu_info()

        assert isinstance(result, dict)
        assert "logical_cores" in result
        assert "physical_cores" in result
        assert "current_usage_percent" in result
        assert "per_core_usage" in result
        assert "model" in result

        assert isinstance(result["logical_cores"], int)
        assert isinstance(result["physical_cores"], int)
        assert isinstance(result["current_usage_percent"], float)
        assert isinstance(result["per_core_usage"], list)
        assert isinstance(result["model"], str)

        assert result["logical_cores"] > 0
        assert result["physical_cores"] > 0
        assert result["logical_cores"] >= result["physical_cores"]
        assert 0 <= result["current_usage_percent"] <= 100
        assert len(result["per_core_usage"]) == result["logical_cores"]
        assert all(0 <= usage <= 100 for usage in result["per_core_usage"])
        assert len(result["model"]) > 0


class TestSystemInfoIntegration:
    def test_system_info_consistency(self):
        system_info = get_system_info()
        memory_info = get_memory_info()
        cpu_info = get_cpu_info()

        assert abs(system_info.total_memory_gb - memory_info["total_gb"]) < 0.1
        assert abs(system_info.available_memory_gb - memory_info["available_gb"]) < 1.0

        assert system_info.cpu_cores == cpu_info["logical_cores"]

    def test_system_info_to_dict_integration(self):
        system_info = get_system_info()
        system_dict = system_info.to_dict()

        expected_keys = {
            "os_type",
            "os_version",
            "architecture",
            "cpu_model",
            "cpu_vendor",
            "cpu_identity_provenance",
            "cpu_cores",
            "total_memory_gb",
            "available_memory_gb",
            "python_version",
            "hostname",
            "cpu_count",
            "memory_gb",
            "os_release",
            "execution_class",
            "container_runtime",
            "container_host_os",
            "execution_class_source",
        }
        assert set(system_dict.keys()) == expected_keys

        assert system_dict["os_type"] == system_info.os_name
        assert system_dict["cpu_cores"] == system_info.cpu_cores

    def test_multiple_calls_consistency(self):
        info1 = get_system_info()
        info2 = get_system_info()

        assert info1.os_name == info2.os_name
        assert info1.os_version == info2.os_version
        assert info1.architecture == info2.architecture
        assert info1.cpu_model == info2.cpu_model
        assert info1.cpu_cores == info2.cpu_cores
        assert info1.python_version == info2.python_version
        assert info1.hostname == info2.hostname

        assert abs(info1.total_memory_gb - info2.total_memory_gb) < 0.1
        assert abs(info1.available_memory_gb - info2.available_memory_gb) < 2.0

    @patch("benchbox.utils.system_info.psutil")
    @patch("benchbox.utils.system_info.platform")
    def test_error_resilience(self, mock_platform, mock_psutil):
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "5.4.0"
        mock_platform.machine.return_value = "x86_64"
        mock_platform.processor.return_value = "Test CPU"
        mock_platform.python_version.return_value = "3.11.0"
        mock_platform.node.return_value = "test-host"

        mock_psutil.virtual_memory.side_effect = Exception("Memory error")
        mock_psutil.cpu_count.side_effect = Exception("CPU count error")

        with pytest.raises(Exception):
            get_system_info()

        mock_psutil.virtual_memory.side_effect = None
        mock_memory = MagicMock()
        mock_memory.total = 8 * (1024**3)
        mock_memory.available = 4 * (1024**3)
        mock_memory.used = 4 * (1024**3)
        mock_memory.percent = 50.0
        mock_psutil.virtual_memory.return_value = mock_memory

        memory_info = get_memory_info()
        assert memory_info["total_gb"] == 8.0


class TestCpuIdentityCapture:
    def test_cpu_model_is_never_the_bare_architecture(self) -> None:
        import platform

        info = get_system_info()
        assert info.cpu_model
        assert info.cpu_model != platform.machine()
        assert info.cpu_model != platform.processor() or info.cpu_model != platform.machine()

    def test_preserves_absence_when_detection_fails(self) -> None:
        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info.platform.processor", return_value=""):
                with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                    info = get_system_info()
        assert info.cpu_model is None
        assert info.cpu_identity_provenance is None

    def test_rejects_architecture_returned_by_primary_detector(self) -> None:
        with patch("benchbox.utils.environment.detect_cpu_info", return_value=("arm", "ARM")):
            with patch("benchbox.utils.system_info.platform.machine", return_value="arm64"):
                with patch("benchbox.utils.system_info.platform.processor", return_value=""):
                    with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                        info = get_system_info()
        assert info.cpu_model is None
        assert info.cpu_identity_provenance is None

    def test_never_publishes_the_arch_even_when_processor_returns_it(self) -> None:
        import platform

        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info.platform.processor", return_value=platform.machine()):
                with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                    info = get_system_info()
        assert info.cpu_model != platform.machine()

    def test_rejects_an_architecture_alias_that_is_not_equal_to_machine(self) -> None:
        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info.platform.processor", return_value="arm"):
                with patch("benchbox.utils.system_info.platform.machine", return_value="arm64"):
                    with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                        info = get_system_info()
        assert info.cpu_model is None
        assert info.cpu_identity_provenance is None

    @pytest.mark.parametrize(
        "processor,machine",
        [
            ("arm", "arm64"),
            ("ARM", "arm64"),
            ("x86_64", "x86_64"),
            ("amd64", "x86_64"),
            ("i686", "i686"),
            ("aarch64", "aarch64"),
            ("  arm  ", "arm64"),
        ],
    )
    def test_no_architecture_token_is_ever_published_as_a_model(self, processor, machine) -> None:
        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info.platform.processor", return_value=processor):
                with patch("benchbox.utils.system_info.platform.machine", return_value=machine):
                    with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                        info = get_system_info()
        assert info.cpu_model is None
        assert info.cpu_identity_provenance is None

    def test_keeps_a_real_model_that_merely_contains_an_arch_word(self) -> None:
        with patch("benchbox.utils.environment.detect_cpu_info", return_value=(None, None)):
            with patch("benchbox.utils.system_info.platform.processor", return_value="ARMv8 Neoverse-N1"):
                with patch("benchbox.utils.system_info.platform.machine", return_value="aarch64"):
                    with patch("benchbox.utils.system_info._proc_cpuinfo_model", return_value=None):
                        info = get_system_info()
        assert info.cpu_model == "ARMv8 Neoverse-N1"

    def test_to_dict_emits_the_keys_the_environment_consumer_reads(self) -> None:
        keys = get_system_info().to_dict().keys()
        for consumed in ("cpu_model", "cpu_vendor", "cpu_count", "memory_gb", "os_release"):
            assert consumed in keys, f"from_system_profile reads {consumed!r}"

    def test_to_dict_keeps_its_original_key_names(self) -> None:
        keys = get_system_info().to_dict().keys()
        for original in ("cpu_cores", "total_memory_gb", "os_version", "os_type"):
            assert original in keys
