from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ThroughputTestConfig:
    num_streams: int = 2
    scale_factor: float = 1.0
    base_seed: int = 42
    query_timeout: int = 300
    stream_timeout: int = 3600
    max_retries: int = 3
    enable_validation: bool = True
    output_dir: Optional[Path] = None


@dataclass
class MaintenanceTestConfig:
    scale_factor: float = 1.0
    concurrent_streams: int = 2
    refresh_runs: int = 1
    first_refresh_set: int = 1
    verbose: bool = False
    output_dir: Optional[Path] = None
    seed: Optional[int] = None


__all__ = ["ThroughputTestConfig", "MaintenanceTestConfig"]
