# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

try:
    import dask.dataframe as dd
    from dask.distributed import Client, LocalCluster

    DASK_AVAILABLE = True
except ImportError:
    dd = None
    Client = None
    LocalCluster = None
    DASK_AVAILABLE = False

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.core.execution_engine import (
    ExecutionEngineReceipt,
    legacy_option_engine,
    resolve_requested,
    supported_execution_engines,
)
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.platforms.dataframe.pandas_df import _pandas_string_columns
from benchbox.platforms.dataframe.pandas_family import (
    PandasFamilyAdapter,
)
from benchbox.platforms.dataframe.shared_loading import coerce_empty_string_columns
from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN, has_trailing_delimiter

logger = logging.getLogger(__name__)

DaskDF = dd.DataFrame if DASK_AVAILABLE else Any

_RESOURCE_ENVELOPE_PATTERNS = (
    "exit 137",
    "exit code 137",
    "returncode 137",
    "returned non-zero exit status 137",
    "sigkill",
    "signal 9",
    "killedworker",
    "worker was killed",
    "worker died",
    "workers died",
    "exceeded memory",
    "memory budget",
    "memoryerror",
)
_DEFAULT_LOCAL_MEMORY_LIMIT = "2GB"
_DEFAULT_LOCAL_WORKER_CAP = 2
_DEFAULT_LOCAL_THREADS_PER_WORKER_CAP = 2


class DaskResourceEnvelopeError(RuntimeError):
    pass


def _cap_default_memory_limit(memory_limit: str) -> str:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(gib|gb)\s*$", memory_limit, re.IGNORECASE)
    if match is None:
        return memory_limit

    value = float(match.group(1))
    if value < 1:
        return "1GB"
    if value > 2:
        return _DEFAULT_LOCAL_MEMORY_LIMIT
    return memory_limit


class DaskDataFrameAdapter(PandasFamilyAdapter[DaskDF]):
    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        n_workers: int | None = None,
        threads_per_worker: int | None = None,
        use_distributed: bool | None = None,
        scheduler_address: str | None = None,
        memory_limit: str | None = None,
        spill_directory: str | Path | None = None,
        tuning_config: DataFrameTuningConfiguration | None = None,
        execution_engine: str = "default",
    ) -> None:
        if not DASK_AVAILABLE:
            raise ImportError("Dask not installed. Install with: pip install dask[distributed]")

        if not PANDAS_AVAILABLE:
            raise ImportError("Pandas not installed. Dask requires Pandas.")

        super().__init__(
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )

        self.n_workers = n_workers
        self.threads_per_worker = threads_per_worker if threads_per_worker is not None else 1
        self.scheduler_address = scheduler_address
        self._legacy_use_distributed = use_distributed
        self._engine_receipt = self.resolve_execution_engine(execution_engine)
        self.use_distributed = self._engine_receipt.applied == "distributed"
        self._memory_limit: str | None = memory_limit
        self._spill_to_disk = False
        self._configured_spill_directory = Path(spill_directory).expanduser() if spill_directory else None
        self._spill_directory: Path | None = None
        self._owns_spill_directory = False
        self._n_workers_configured = n_workers is not None
        self._threads_per_worker_configured = threads_per_worker is not None
        self._memory_limit_configured = memory_limit is not None

        self._client: Client | None = None
        self._cluster: LocalCluster | None = None

        self._validate_and_apply_tuning()
        self._apply_local_resource_envelope_defaults()

        if self.use_distributed:
            self._setup_distributed()
            self._record_distributed_tuning()
        else:
            self._warn_cluster_only_options()

    def _warn_cluster_only_options(self) -> None:
        from benchbox.core.option_notices import warn_option_not_applied

        supplied = {
            "n_workers": self._n_workers_configured,
            "threads_per_worker": self._threads_per_worker_configured,
            "memory_limit": self._memory_limit_configured,
            "spill_directory": self._configured_spill_directory is not None,
            "spill_to_disk": self._spill_to_disk,
        }
        for option, is_supplied in supplied.items():
            if is_supplied:
                warn_option_not_applied(
                    "dask",
                    option,
                    "it configures the local cluster that only the 'distributed' execution engine starts",
                )

    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:
        resolved = resolve_requested("dask", requested)
        explicit = None if resolved == DEFAULT_EXECUTION_ENGINE else resolved
        legacy = None
        if self._legacy_use_distributed is not None:
            if not isinstance(self._legacy_use_distributed, bool):
                raise ValueError("Dask use_distributed must be a boolean")
            if self.scheduler_address and not self._legacy_use_distributed:
                raise ValueError(
                    "Dask use_distributed=false cannot be combined with scheduler_address; "
                    "use execution_engine='distributed' (--execution-engine distributed) to connect to a scheduler"
                )
            legacy = legacy_option_engine("dask", "use_distributed", self._legacy_use_distributed)
        if explicit is not None and legacy is not None and explicit != legacy:
            raise ValueError(
                "Conflicting execution engine sources: "
                f"explicit setting requests {explicit!r} vs legacy option use_distributed requests {legacy!r}"
            )
        selected = explicit or legacy or "distributed"
        if self.scheduler_address and selected != "distributed":
            raise ValueError("Dask scheduler_address requires execution_engine='distributed'")
        engines = supported_execution_engines("dask")
        if explicit is not None:
            resolution = "explicit"
        elif legacy is not None:
            resolution = "legacy_option"
        else:
            resolution = "platform_default"
        return ExecutionEngineReceipt(
            requested=selected if explicit is not None or legacy is not None else DEFAULT_EXECUTION_ENGINE,
            applied=selected,
            applied_class=engines[selected].engine_class,
            resolution=resolution,
        )

    def _apply_tuning(self) -> None:
        config = self._tuning_config

        if config.parallelism.worker_count is not None:
            self.n_workers = config.parallelism.worker_count
            self._n_workers_configured = True
            self._log_verbose(f"Set n_workers={self.n_workers} from tuning configuration")

        if config.parallelism.threads_per_worker is not None:
            self.threads_per_worker = config.parallelism.threads_per_worker
            self._threads_per_worker_configured = True
            self._log_verbose(f"Set threads_per_worker={self.threads_per_worker} from tuning configuration")

        if config.memory.memory_limit is not None:
            self._memory_limit = config.memory.memory_limit
            self._memory_limit_configured = True
            self._log_verbose(f"Set memory_limit={self._memory_limit} from tuning configuration")

        if config.memory.spill_to_disk:
            self._spill_to_disk = True
            self._log_verbose("Enabled spill to disk from tuning configuration")

        spill_directory = getattr(config.memory, "spill_directory", None)
        if spill_directory is not None:
            self._configured_spill_directory = Path(spill_directory).expanduser()

    def _record_distributed_tuning(self) -> None:
        if not self.use_distributed or self.scheduler_address:
            return

        config = self._tuning_config
        if config.parallelism.worker_count is not None:
            self._record_runtime_tuning(f"n_workers={self.n_workers}")
        if config.parallelism.threads_per_worker is not None:
            self._record_runtime_tuning(f"threads_per_worker={self.threads_per_worker}")
        if config.memory.memory_limit is not None:
            self._record_runtime_tuning(f"memory_limit={self._memory_limit}")
        if config.memory.spill_to_disk:
            self._record_runtime_tuning("spill_to_disk=on")
        if (
            getattr(config.memory, "spill_directory", None) is not None
            and self._configured_spill_directory is not None
            and self._spill_directory is not None
        ):
            self._record_runtime_tuning(f"spill_directory={self._configured_spill_directory}")

    def _apply_local_resource_envelope_defaults(self) -> None:
        if not self.use_distributed or self.scheduler_address:
            return

        from benchbox.core.dataframe.tuning import get_smart_defaults

        defaults = get_smart_defaults("dask")

        if not self._n_workers_configured and defaults.parallelism.worker_count is not None:
            self.n_workers = min(defaults.parallelism.worker_count, _DEFAULT_LOCAL_WORKER_CAP)

        if not self._threads_per_worker_configured and defaults.parallelism.threads_per_worker is not None:
            self.threads_per_worker = min(
                defaults.parallelism.threads_per_worker,
                _DEFAULT_LOCAL_THREADS_PER_WORKER_CAP,
            )

        if not self._memory_limit_configured and defaults.memory.memory_limit is not None:
            self._memory_limit = _cap_default_memory_limit(defaults.memory.memory_limit)

        if defaults.memory.spill_to_disk:
            self._spill_to_disk = True

    def _configure_spill_to_disk(self) -> None:
        if not self._spill_to_disk:
            return

        import dask

        self._resolve_spill_directory()
        dask.config.set(
            {
                "distributed.worker.memory.spill": True,
                "distributed.worker.memory.target": 0.6,
                "distributed.worker.memory.pause": 0.8,
            }
        )

    def _resolve_spill_directory(self) -> Path:
        spill_directory = getattr(self, "_spill_directory", None)
        if spill_directory is not None:
            return spill_directory

        configured_spill_directory = getattr(self, "_configured_spill_directory", None)
        if configured_spill_directory is not None:
            configured_spill_directory.mkdir(parents=True, exist_ok=True)
            self._spill_directory = configured_spill_directory
            return self._spill_directory

        parent = Path(self.working_dir) / ".benchbox-dask-spill"
        parent.mkdir(parents=True, exist_ok=True)
        self._spill_directory = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
        self._owns_spill_directory = True
        return self._spill_directory

    def _setup_distributed(self) -> None:
        try:
            if self.scheduler_address:
                self._client = Client(self.scheduler_address)
                if self.verbose:
                    logger.info(f"Connected to Dask scheduler at {self.scheduler_address}")
            else:
                self._configure_spill_to_disk()

                cluster_kwargs: dict[str, Any] = {
                    "n_workers": self.n_workers,
                    "threads_per_worker": self.threads_per_worker,
                    "silence_logs": logging.INFO if self.verbose else logging.ERROR,
                }

                if self._memory_limit is not None:
                    cluster_kwargs["memory_limit"] = self._memory_limit

                if self._spill_directory is not None:
                    cluster_kwargs["local_directory"] = str(self._spill_directory)

                self._cluster = LocalCluster(**cluster_kwargs)
                self._client = Client(self._cluster)
                if self.verbose:
                    logger.info(
                        f"Dask local cluster started: "
                        f"{self._cluster.scheduler.address}, "
                        f"{len(self._cluster.workers)} workers"
                    )
                    if self._memory_limit:
                        logger.info(f"Memory limit per worker: {self._memory_limit}")
                    if self._spill_directory:
                        logger.info(f"Dask spill directory: {self._spill_directory}")
                    if hasattr(self._cluster, "dashboard_link"):
                        logger.info(f"Dashboard: {self._cluster.dashboard_link}")
        except Exception as e:
            self._cleanup_owned_spill_directory()
            raise DaskResourceEnvelopeError(
                f"Could not set up Dask distributed resource envelope. Original error: {type(e).__name__}: {e}"
            ) from e

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        if not hasattr(self, "_client"):
            return
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Failed to close Dask client: {e}")
            self._client = None

        if self._cluster is not None:
            try:
                self._cluster.close()
            except Exception as e:
                logger.debug(f"Failed to close Dask cluster: {e}")
            self._cluster = None

        self._cleanup_owned_spill_directory()

    def _cleanup_owned_spill_directory(self) -> None:
        if not getattr(self, "_owns_spill_directory", False):
            return

        spill_directory = getattr(self, "_spill_directory", None)
        if spill_directory is None:
            return

        try:
            shutil.rmtree(spill_directory, ignore_errors=True)
            parent = spill_directory.parent
            if parent.name == ".benchbox-dask-spill":
                try:
                    parent.rmdir()
                except OSError:
                    pass
        finally:
            self._spill_directory = None
            self._owns_spill_directory = False

    @property
    def platform_name(self) -> str:
        return "Dask"

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        header: int | None = 0,
        names: list[str] | None = None,
        null_marker: str | None = None,
        column_types: list[str] | None = None,
    ) -> DaskDF:
        read_kwargs: dict[str, Any] = {
            "sep": delimiter,
            "header": header if header is not None else "infer",
            "on_bad_lines": "skip",
        }

        if header is None:
            read_kwargs["header"] = None

        string_columns: list[str] = []
        if names:
            read_kwargs["names"] = names
            string_columns = _pandas_string_columns(names, column_types, set())
            if string_columns:
                read_kwargs["dtype"] = dict.fromkeys(string_columns, "object")

        if null_marker is not None and names and has_trailing_delimiter(path, delimiter, names):
            extended_names = names + [TRAILING_DUMMY_COLUMN]
            read_kwargs["names"] = extended_names

        df = dd.read_csv(path, **read_kwargs)

        df = coerce_empty_string_columns(df, string_columns, null_marker)

        if TRAILING_DUMMY_COLUMN in df.columns:
            df = df.drop(columns=[TRAILING_DUMMY_COLUMN])

        return df

    def read_parquet(self, path: Path) -> DaskDF:
        return dd.read_parquet(path)

    def to_datetime(self, series: Any) -> Any:
        return pd.to_datetime(series)

    def timedelta_days(self, days: int) -> timedelta:
        return pd.Timedelta(days=days)

    def concat(self, dfs: list[DaskDF]) -> DaskDF:
        if len(dfs) == 1:
            return dfs[0]
        if all(isinstance(df, pd.DataFrame) for df in dfs):
            return pd.concat(dfs, ignore_index=True)
        return dd.concat(dfs, ignore_index=True)

    def get_row_count(self, df: DaskDF) -> int:
        return self._run_with_resource_diagnostics("row count", lambda: len(df))

    def _get_first_row(self, df: DaskDF) -> tuple | None:
        head_df = self._run_with_resource_diagnostics("first row", lambda: df.head(1))
        if len(head_df) == 0:
            return None

        return tuple(head_df.iloc[0])

    def compute(self, df: DaskDF) -> Any:
        return self._run_with_resource_diagnostics("compute", df.compute)

    def persist(self, df: DaskDF) -> DaskDF:
        return self._run_with_resource_diagnostics("persist", df.persist)

    def _run_with_resource_diagnostics(self, operation: str, func: Any) -> Any:
        try:
            return func()
        except Exception as exc:
            diagnostic = self._resource_envelope_diagnostic(operation, exc)
            if diagnostic is not None:
                raise DaskResourceEnvelopeError(diagnostic) from exc
            raise

    def _resource_envelope_diagnostic(self, operation: str, exc: Exception) -> str | None:
        error_text = f"{type(exc).__name__}: {exc}"
        normalized = error_text.lower()

        if not any(pattern in normalized for pattern in _RESOURCE_ENVELOPE_PATTERNS):
            return None

        envelope = self._resource_envelope_settings()
        return (
            f"Dask resource-envelope failure during {operation}: worker or subprocess was killed "
            f"by the operating system/resource manager. Envelope: {envelope}. "
            f"Original error: {error_text}"
        )

    def _resource_envelope_settings(self) -> str:
        return (
            f"use_distributed={self.use_distributed}, "
            f"n_workers={self.n_workers}, "
            f"threads_per_worker={self.threads_per_worker}, "
            f"memory_limit={getattr(self, '_memory_limit', None) or 'unbounded'}, "
            f"spill_to_disk={getattr(self, '_spill_to_disk', False)}, "
            "spill_directory="
            f"{getattr(self, '_spill_directory', None) or getattr(self, '_configured_spill_directory', None) or 'default'}"
        )

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "use_distributed": self.use_distributed,
            "working_dir": str(self.working_dir),
        }
        if self.use_distributed:
            info.update(
                {
                    "n_workers": self.n_workers,
                    "threads_per_worker": self.threads_per_worker,
                    "memory_limit": getattr(self, "_memory_limit", None),
                    "spill_to_disk": getattr(self, "_spill_to_disk", False),
                    "spill_directory": str(self._spill_directory) if getattr(self, "_spill_directory", None) else None,
                }
            )
        info["execution_engine"] = asdict(self._engine_receipt)

        if DASK_AVAILABLE:
            import dask

            info["version"] = dask.__version__

        if self._client is not None:
            try:
                info["scheduler_address"] = self._client.scheduler.address
                info["n_active_workers"] = len(self._client.scheduler_info()["workers"])
            except Exception as e:
                logger.debug(f"Failed to get scheduler info: {e}")

        return info

    def _merge_frames(
        self,
        left: DaskDF,
        right: DaskDF,
        *,
        on: str | list[str] | None,
        left_on: str | list[str] | None,
        right_on: str | list[str] | None,
        how: str,
    ) -> DaskDF:
        return dd.merge(
            left,
            right,
            on=on,
            left_on=left_on,
            right_on=right_on,
            how=how,
        )

    def groupby_agg(
        self,
        df: DaskDF,
        by: str | list[str],
        agg_spec: dict[str, Any],
        as_index: bool = False,
        **kwargs: Any,
    ) -> DaskDF:
        by_list = [by] if isinstance(by, str) else list(by)

        nunique_aggs = {}
        regular_aggs = {}

        for name, spec in agg_spec.items():
            if isinstance(spec, tuple) and len(spec) >= 2 and spec[1] == "nunique":
                nunique_aggs[name] = spec
            elif spec == "nunique":
                nunique_aggs[name] = (name, "nunique")
            else:
                regular_aggs[name] = spec

        if nunique_aggs:
            return self._groupby_agg_with_nunique(
                df, by_list, regular_aggs, nunique_aggs, list(agg_spec), as_index, **kwargs
            )

        is_named_agg = any(isinstance(v, tuple) for v in regular_aggs.values())

        if is_named_agg:
            first_alias: dict[Any, str] = {}
            alias_of = {
                name: first_alias.setdefault(tuple(spec) if isinstance(spec, tuple) else spec, name)
                for name, spec in regular_aggs.items()
            }
            distinct_aggs = {name: regular_aggs[name] for name, alias in alias_of.items() if name == alias}
            result = df.groupby(by_list, **kwargs).agg(**distinct_aggs)
            if len(distinct_aggs) != len(regular_aggs):
                result = result.assign(**{name: result[alias] for name, alias in alias_of.items() if name != alias})[
                    list(regular_aggs)
                ]
        else:
            result = df.groupby(by_list, **kwargs).agg(regular_aggs)

        if not as_index:
            result = result.reset_index()

        return result

    def _groupby_agg_with_nunique(
        self,
        df: DaskDF,
        by: list[str],
        regular_aggs: dict[str, Any],
        nunique_aggs: dict[str, tuple[str, str]],
        column_order: list[str],
        as_index: bool = False,
        **kwargs: Any,
    ) -> DaskDF:
        if regular_aggs:
            result = df.groupby(by, **kwargs).agg(**regular_aggs).reset_index()
        else:
            size_result = df.groupby(by, **kwargs).size().reset_index()
            size_cols = [c for c in size_result.columns if c not in by]
            result = size_result.drop(columns=size_cols) if size_cols else size_result

        for col_name, (source_col, _) in nunique_aggs.items():
            nunique_result = df.groupby(by, **kwargs)[source_col].nunique().reset_index()
            nunique_result = nunique_result.rename(columns={source_col: col_name})
            result = result.merge(nunique_result, on=by)

        ordered = by + [name for name in column_order if name in result.columns and name not in by]
        result = result[ordered]
        if as_index and len(by) == 1:
            result = result.set_index(by[0])
        return result

    def groupby_size(
        self,
        df: DaskDF,
        by: str | list[str],
        name: str = "size",
    ) -> DaskDF:
        by_list = [by] if isinstance(by, str) else list(by)
        result = df.groupby(by_list).size().reset_index()
        count_col = [c for c in result.columns if c not in by_list][0]
        if count_col != name:
            result = result.rename(columns={count_col: name})
        return result

    def repartition(self, df: DaskDF, npartitions: int) -> DaskDF:
        return df.repartition(npartitions=npartitions)

    def get_npartitions(self, df: DaskDF) -> int:
        return df.npartitions
