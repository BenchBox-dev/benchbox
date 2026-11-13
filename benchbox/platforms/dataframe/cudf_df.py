# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

try:
    import cudf

    CUDF_AVAILABLE = True
except ImportError:
    cudf = None
    CUDF_AVAILABLE = False

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.platforms.dataframe.pandas_df import _pandas_string_columns
from benchbox.platforms.dataframe.pandas_family import (
    PandasFamilyAdapter,
)
from benchbox.platforms.dataframe.shared_loading import coerce_empty_string_columns
from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN, has_trailing_delimiter

logger = logging.getLogger(__name__)

CuDFDF = cudf.DataFrame if CUDF_AVAILABLE else Any


class CuDFDataFrameAdapter(PandasFamilyAdapter[CuDFDF]):
    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        device_id: int = 0,
        spill_to_host: bool = True,
        tuning_config: DataFrameTuningConfiguration | None = None,
        allow_third_party_extensions: bool = False,
    ) -> None:
        if not CUDF_AVAILABLE:
            raise ImportError("cuDF not installed. Install with: pip install cudf-cu12 (for CUDA 12.x)")

        super().__init__(
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )

        self.device_id = device_id
        self.spill_to_host = spill_to_host
        self.allow_third_party_extensions = bool(allow_third_party_extensions)
        self._pool_type = "default"

        self._validate_and_apply_tuning()

        self._configure_gpu()

    def _apply_tuning(self) -> None:
        config = self._tuning_config

        if config.gpu.enabled:
            if config.gpu.device_id is not None:
                self.device_id = config.gpu.device_id
                self._log_verbose(f"Set device_id={self.device_id} from tuning configuration")

            self.spill_to_host = config.gpu.spill_to_host
            self._log_verbose(f"Set spill_to_host={self.spill_to_host} from tuning configuration")

            self._pool_type = config.gpu.pool_type
            self._log_verbose(f"Set pool_type={self._pool_type} from tuning configuration")

    def _configure_gpu(self) -> None:
        try:
            import rmm

            rmm_kwargs: dict[str, Any] = {
                "devices": self.device_id,
            }

            if self._pool_type == "pool":
                rmm_kwargs["pool_allocator"] = True
            elif self._pool_type == "managed":
                rmm_kwargs["managed_memory"] = True
            elif self._pool_type == "cuda":
                rmm_kwargs["pool_allocator"] = False
            else:
                rmm_kwargs["pool_allocator"] = True

            rmm.reinitialize(**rmm_kwargs)

            if self.verbose:
                import cupy

                device = cupy.cuda.Device(self.device_id)
                mem_info = device.mem_info
                logger.info(
                    f"cuDF using GPU {self.device_id}: "
                    f"{mem_info[1] / 1e9:.1f}GB total, "
                    f"{mem_info[0] / 1e9:.1f}GB free, "
                    f"pool_type={self._pool_type}"
                )
        except Exception as e:
            if self.verbose:
                logger.warning(f"Could not configure GPU: {e}")

    @property
    def platform_name(self) -> str:
        return "cuDF"

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        header: int | None = 0,
        names: list[str] | None = None,
        null_marker: str | None = None,
        column_types: list[str] | None = None,
    ) -> CuDFDF:
        read_kwargs: dict[str, Any] = {
            "sep": delimiter,
        }

        if header is None:
            read_kwargs["header"] = None
        else:
            read_kwargs["header"] = header

        string_columns: list[str] = []
        if names:
            read_kwargs["names"] = names
            string_columns = _pandas_string_columns(names, column_types, set())
            if string_columns:
                read_kwargs["dtype"] = dict.fromkeys(string_columns, "object")

        if null_marker is not None and names and has_trailing_delimiter(path, delimiter, names):
            extended_names = names + [TRAILING_DUMMY_COLUMN]
            read_kwargs["names"] = extended_names

        df = cudf.read_csv(path, **read_kwargs)

        df = coerce_empty_string_columns(df, string_columns, null_marker)

        if TRAILING_DUMMY_COLUMN in df.columns:
            df = df.drop(columns=[TRAILING_DUMMY_COLUMN])

        return df

    def read_parquet(self, path: Path) -> CuDFDF:
        return cudf.read_parquet(path)

    def to_datetime(self, series: Any) -> Any:
        return cudf.to_datetime(series)

    def timedelta_days(self, days: int) -> timedelta:
        if PANDAS_AVAILABLE:
            return pd.Timedelta(days=days)
        return timedelta(days=days)

    def concat(self, dfs: list[CuDFDF]) -> CuDFDF:
        if len(dfs) == 1:
            return dfs[0]
        return cudf.concat(dfs, ignore_index=True)

    def get_row_count(self, df: CuDFDF) -> int:
        return len(df)

    def _get_first_row(self, df: CuDFDF) -> tuple | None:
        if len(df) == 0:
            return None

        return tuple(df.head(1).to_pandas().iloc[0])

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "device_id": self.device_id,
            "spill_to_host": self.spill_to_host,
            "working_dir": str(self.working_dir),
        }

        if CUDF_AVAILABLE:
            info["version"] = cudf.__version__

            try:
                import cupy

                device = cupy.cuda.Device(self.device_id)
                mem_info = device.mem_info
                info["gpu_name"] = cupy.cuda.runtime.getDeviceProperties(self.device_id)["name"]
                info["gpu_memory_total_gb"] = round(mem_info[1] / 1e9, 2)
                info["gpu_memory_free_gb"] = round(mem_info[0] / 1e9, 2)
            except Exception:
                pass

        self._enrich_with_extension_options(info)
        return info

    def _merge_frames(
        self,
        left: CuDFDF,
        right: CuDFDF,
        *,
        on: str | list[str] | None,
        left_on: str | list[str] | None,
        right_on: str | list[str] | None,
        how: str,
    ) -> CuDFDF:
        return cudf.merge(
            left,
            right,
            on=on,
            left_on=left_on,
            right_on=right_on,
            how=how,
        )

    def to_pandas(self, df: CuDFDF) -> Any:
        return df.to_pandas()

    def from_pandas(self, df: Any) -> CuDFDF:
        return cudf.from_pandas(df)

    def get_gpu_memory_usage(self) -> dict[str, float]:
        try:
            import cupy

            device = cupy.cuda.Device(self.device_id)
            mem_info = device.mem_info
            return {
                "free_gb": round(mem_info[0] / 1e9, 2),
                "total_gb": round(mem_info[1] / 1e9, 2),
                "used_gb": round((mem_info[1] - mem_info[0]) / 1e9, 2),
            }
        except Exception:
            return {"free_gb": 0.0, "total_gb": 0.0, "used_gb": 0.0}
