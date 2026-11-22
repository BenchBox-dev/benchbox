# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union


@dataclass
class PowerRunSettings:
    iterations: int
    warm_up_iterations: int
    fail_fast: bool
    collect_metrics: bool

    @classmethod
    def from_config_manager(cls, config_manager: Any) -> "PowerRunSettings":
        return cls(
            iterations=config_manager.get("execution.power_run.iterations", 4),
            warm_up_iterations=config_manager.get("execution.power_run.warm_up_iterations", 0),
            fail_fast=config_manager.get("execution.power_run.fail_fast", False),
            collect_metrics=config_manager.get("execution.power_run.collect_metrics", True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "warm_up_iterations": self.warm_up_iterations,
            "fail_fast": self.fail_fast,
            "collect_metrics": self.collect_metrics,
        }

    def apply_to_config_manager(self, config_manager: Any) -> None:
        config_manager.set("execution.power_run.iterations", self.iterations)
        config_manager.set("execution.power_run.warm_up_iterations", self.warm_up_iterations)
        config_manager.set("execution.power_run.fail_fast", self.fail_fast)
        config_manager.set("execution.power_run.collect_metrics", self.collect_metrics)


@dataclass
class ConcurrentQueriesSettings:
    enabled: bool
    max_concurrent: int
    stream_timeout_seconds: Optional[int]
    retry_failed_queries: bool
    max_retries: int
    cancel_on_timeout: bool = False

    @classmethod
    def from_config_manager(cls, config_manager: Any) -> "ConcurrentQueriesSettings":
        return cls(
            enabled=config_manager.get("execution.concurrent_queries.enabled", False),
            max_concurrent=config_manager.get("execution.concurrent_queries.max_concurrent", 2),
            stream_timeout_seconds=config_manager.get("execution.concurrent_queries.stream_timeout_seconds"),
            retry_failed_queries=config_manager.get("execution.concurrent_queries.retry_failed_queries", True),
            max_retries=config_manager.get("execution.concurrent_queries.max_retries", 3),
            cancel_on_timeout=bool(config_manager.get("execution.concurrent_queries.cancel_on_timeout", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_concurrent": self.max_concurrent,
            "stream_timeout_seconds": self.stream_timeout_seconds,
            "retry_failed_queries": self.retry_failed_queries,
            "max_retries": self.max_retries,
            "cancel_on_timeout": self.cancel_on_timeout,
        }

    def apply_to_config_manager(self, config_manager: Any) -> None:
        config_manager.set("execution.concurrent_queries.enabled", self.enabled)
        config_manager.set("execution.concurrent_queries.max_concurrent", self.max_concurrent)
        if self.stream_timeout_seconds is not None:
            config_manager.set(
                "execution.concurrent_queries.stream_timeout_seconds",
                self.stream_timeout_seconds,
            )
        config_manager.set(
            "execution.concurrent_queries.retry_failed_queries",
            self.retry_failed_queries,
        )
        config_manager.set("execution.concurrent_queries.max_retries", self.max_retries)
        config_manager.set("execution.concurrent_queries.cancel_on_timeout", self.cancel_on_timeout)


class ExecutionConfigHelper:
    def __init__(self, config_manager: Optional[Any] = None):
        if config_manager is None:
            from benchbox.utils.config_interface import get_config_provider

            self.config_manager = get_config_provider()
        else:
            self.config_manager = config_manager

    def get_power_run_settings(self) -> PowerRunSettings:
        return PowerRunSettings.from_config_manager(self.config_manager)

    def get_concurrent_queries_settings(self) -> ConcurrentQueriesSettings:
        return ConcurrentQueriesSettings.from_config_manager(self.config_manager)

    def update_power_run_settings(self, settings: PowerRunSettings) -> None:
        settings.apply_to_config_manager(self.config_manager)

    def update_concurrent_queries_settings(self, settings: ConcurrentQueriesSettings) -> None:
        settings.apply_to_config_manager(self.config_manager)

    def enable_power_run_iterations(self, iterations: int = 3, warm_up_iterations: int = 1) -> None:
        settings = self.get_power_run_settings()
        settings.iterations = iterations
        settings.warm_up_iterations = warm_up_iterations
        self.update_power_run_settings(settings)

    def enable_concurrent_queries(self, max_concurrent: int = 2) -> None:
        settings = self.get_concurrent_queries_settings()
        settings.enabled = True
        settings.max_concurrent = max_concurrent
        self.update_concurrent_queries_settings(settings)

    def disable_concurrent_queries(self) -> None:
        settings = self.get_concurrent_queries_settings()
        settings.enabled = False
        self.update_concurrent_queries_settings(settings)

    def optimize_for_system(self, cpu_cores: int) -> None:
        concurrent_settings = self.get_concurrent_queries_settings()
        concurrent_settings.max_concurrent = min(8, max(2, cpu_cores // 4))

        self.update_concurrent_queries_settings(concurrent_settings)

    def create_performance_profile(self, profile_name: str) -> dict[str, Any]:
        profiles = {
            "quick": {
                "power_run": PowerRunSettings(
                    iterations=1,
                    warm_up_iterations=0,
                    fail_fast=True,
                    collect_metrics=True,
                ),
                "concurrent_queries": ConcurrentQueriesSettings(
                    enabled=False,
                    max_concurrent=2,
                    stream_timeout_seconds=None,
                    retry_failed_queries=False,
                    max_retries=1,
                ),
            },
            "standard": {
                "power_run": PowerRunSettings(
                    iterations=3,
                    warm_up_iterations=1,
                    fail_fast=False,
                    collect_metrics=True,
                ),
                "concurrent_queries": ConcurrentQueriesSettings(
                    enabled=True,
                    max_concurrent=2,
                    stream_timeout_seconds=None,
                    retry_failed_queries=True,
                    max_retries=3,
                ),
            },
            "thorough": {
                "power_run": PowerRunSettings(
                    iterations=5,
                    warm_up_iterations=2,
                    fail_fast=False,
                    collect_metrics=True,
                ),
                "concurrent_queries": ConcurrentQueriesSettings(
                    enabled=True,
                    max_concurrent=4,
                    stream_timeout_seconds=None,
                    retry_failed_queries=True,
                    max_retries=5,
                ),
            },
            "stress": {
                "power_run": PowerRunSettings(
                    iterations=10,
                    warm_up_iterations=3,
                    fail_fast=False,
                    collect_metrics=True,
                ),
                "concurrent_queries": ConcurrentQueriesSettings(
                    enabled=True,
                    max_concurrent=8,
                    stream_timeout_seconds=None,
                    retry_failed_queries=True,
                    max_retries=10,
                ),
            },
        }

        if profile_name not in profiles:
            raise ValueError(f"Unknown performance profile: {profile_name}. Available: {list(profiles.keys())}")

        profile = profiles[profile_name]
        return {
            "name": profile_name,
            "power_run": profile["power_run"].to_dict(),
            "concurrent_queries": profile["concurrent_queries"].to_dict(),
        }

    def apply_performance_profile(self, profile_name: str) -> None:
        profile = self.create_performance_profile(profile_name)

        power_settings = PowerRunSettings(**profile["power_run"])
        concurrent_settings = ConcurrentQueriesSettings(**profile["concurrent_queries"])

        self.update_power_run_settings(power_settings)
        self.update_concurrent_queries_settings(concurrent_settings)

    def save_config(self) -> None:
        self.config_manager.save_config()

    def validate_execution_config(self) -> bool:
        return self.config_manager.validate_config()

    def get_execution_summary(self) -> dict[str, Any]:
        power_settings = self.get_power_run_settings()
        concurrent_settings = self.get_concurrent_queries_settings()

        return {
            "power_run": {
                "enabled": power_settings.iterations > 1 or power_settings.warm_up_iterations > 0,
                "total_iterations": power_settings.iterations + power_settings.warm_up_iterations,
                "settings": power_settings.to_dict(),
            },
            "concurrent_queries": {
                "enabled": concurrent_settings.enabled,
                "max_streams": concurrent_settings.max_concurrent,
                "estimated_stream_duration_minutes": (
                    concurrent_settings.stream_timeout_seconds / 60
                    if concurrent_settings.stream_timeout_seconds
                    else None
                ),
                "settings": concurrent_settings.to_dict(),
            },
            "general": {
                "max_workers": self.config_manager.get("execution.max_workers", 4),
                "memory_limit_gb": self.config_manager.get("execution.memory_limit_gb", 0),
                "parallel_queries": self.config_manager.get("execution.parallel_queries", False),
            },
        }


def create_sample_execution_config(output_path: Union[str, Path]) -> None:
    output_path = Path(output_path)

    sample_config = {
        "_description": "Sample BenchBox execution configuration with power run and concurrent query settings",
        "execution": {
            "parallel_queries": False,
            "max_workers": 4,
            "memory_limit_gb": 8,
            "verbose": True,
            "power_run": {
                "iterations": 3,
                "warm_up_iterations": 1,
                "fail_fast": False,
                "collect_metrics": True,
                "_description": "Power run configuration for iterative performance testing",
            },
            "concurrent_queries": {
                "enabled": True,
                "max_concurrent": 2,
                "retry_failed_queries": True,
                "max_retries": 3,
                "_description": "Concurrent query configuration for throughput testing",
            },
        },
        "_profiles": {
            "quick": "Fast testing with minimal iterations",
            "standard": "Balanced testing with moderate iterations",
            "thorough": "Comprehensive testing with multiple iterations",
            "stress": "Stress testing with maximum iterations and concurrency",
        },
    }

    import yaml

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(sample_config, f, default_flow_style=False, sort_keys=False)
