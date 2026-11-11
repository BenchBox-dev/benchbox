# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import abc
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Literal, Optional, Union

from benchbox.core.benchmark_result_validation import BenchmarkResultValidationMixin
from benchbox.core.tuning import BenchmarkTunings

BENCHMARK_API_SURFACE = "deprecated"
BENCHMARK_API_DECISION = "retained-internal-compatibility-base"


class BaseBenchmark(BenchmarkResultValidationMixin, abc.ABC):
    api_surface = BENCHMARK_API_SURFACE
    compatibility_marker = BENCHMARK_API_DECISION

    def __init__(self, scale_factor: float = 1.0, **config: Union[str, int, float, bool]) -> None:
        if scale_factor <= 0:
            raise ValueError("Scale factor must be positive")

        if scale_factor >= 1 and scale_factor != int(scale_factor):
            raise ValueError(
                f"Scale factors >= 1 must be whole integers. Got: {scale_factor}. "
                f"Use values like 1, 2, 10, etc. for large scale factors. "
                f"Use values like 0.1, 0.01, etc. for small scale factors."
            )

        self.scale_factor = scale_factor
        self.config = config
        self._name: Optional[str] = None
        self._version: Optional[str] = None
        self._description: Optional[str] = None

    @property
    def name(self) -> str:
        if self._name is None:
            class_name = self.__class__.__name__
            if class_name.endswith("Benchmark"):
                self._name = class_name[:-9].lower()
            else:
                self._name = class_name.lower()
        return self._name

    @property
    def version(self) -> str:
        if self._version is None:
            self._version = "1.0"
        return self._version

    @property
    def description(self) -> str:
        if self._description is None:
            self._description = f"{self.name.upper()} benchmark implementation"
        return self._description

    def cleanup(self) -> None:
        pass

    def __enter__(self) -> "BaseBenchmark":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Literal[False]:
        self.cleanup()
        return False

    @property
    def benchmark_name(self) -> str:
        return getattr(self, "_name", type(self).__name__)

    def _minimal_result_phases(
        self,
        phases: Optional[dict[str, dict[str, Any]]],
    ) -> None:
        return None

    @abc.abstractmethod
    def generate_data(self, tables: Optional[list[str]] = None, output_format: str = "memory") -> dict[str, Any]:
        pass

    @abc.abstractmethod
    def get_query(self, query_id: Union[int, str]) -> str:
        pass

    @abc.abstractmethod
    def get_all_queries(self) -> dict[Union[int, str], str]:
        pass

    def get_all_query_ids(self) -> list[str]:
        queries = self.get_all_queries()
        return [str(qid) for qid in sorted(queries.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x))]

    @abc.abstractmethod
    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[Mapping[str, Any]] = None,
    ) -> list[tuple[Any, ...]]:
        pass

    def get_tunings(self) -> Optional[BenchmarkTunings]:
        return None

    def validate_tunings(self, tunings: Optional[BenchmarkTunings] = None) -> dict[str, list[str]]:
        if tunings is None:
            tunings = self.get_tunings()

        if tunings is None:
            return {}

        return tunings.validate_all()
