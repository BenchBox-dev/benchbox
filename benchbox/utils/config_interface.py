# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from abc import ABC, abstractmethod
from typing import Any, Optional


class ConfigInterface(ABC):
    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        pass

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        pass


class SimpleConfigProvider(ConfigInterface):
    def __init__(self, defaults: Optional[dict] = None):
        self._config = defaults.copy() if defaults else {}
        self._setup_defaults()

    def _setup_defaults(self):
        default_config = {
            "execution.power_run.iterations": 4,
            "execution.power_run.warm_up_iterations": 0,
            "execution.power_run.concurrent_streams": 1,
            "execution.throughput_test.duration_minutes": 60,
            "execution.throughput_test.concurrent_streams": 4,
            "execution.throughput_test.warm_up_minutes": 5,
            "execution.timeout_minutes": 120,
            "execution.memory_limit_gb": 8,
            "execution.enable_profiling": False,
        }

        for key, value in default_config.items():
            if key not in self._config:
                self._config[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config[key] = value

    def update(self, config_dict: dict) -> None:
        self._config.update(config_dict)


def get_default_config_provider() -> ConfigInterface:
    return SimpleConfigProvider()


_config_provider: ConfigInterface | None = None


def set_config_provider(provider: ConfigInterface | None) -> None:
    global _config_provider
    _config_provider = provider


def get_config_provider() -> ConfigInterface:
    return _config_provider if _config_provider is not None else get_default_config_provider()
