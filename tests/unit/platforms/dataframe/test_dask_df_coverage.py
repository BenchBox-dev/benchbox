from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN
from tests.utilities.deprecation_capture import captured_deprecations

mod = importlib.import_module("benchbox.platforms.dataframe.dask_df")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeDaskDF:
    def __init__(self, columns=None, rows=None, npartitions=2):
        self.columns = list(columns or [])
        self._rows = list(rows or [])
        self.npartitions = npartitions

    def drop(self, columns):
        cols = set(columns)
        return _FakeDaskDF([c for c in self.columns if c not in cols], self._rows, self.npartitions)

    def head(self, n):
        return _FakePandasFrame(self._rows[:n])

    def compute(self):
        return ("computed", len(self._rows))

    def persist(self):
        return self

    def groupby(self, by):
        return _FakeDaskGroupBy(by)

    def merge(self, other, on=None):
        return ("merged", on, other)

    def reset_index(self):
        return self

    def rename(self, columns):
        renamed = list(self.columns)
        for old, new in columns.items():
            if old in renamed:
                renamed[renamed.index(old)] = new
        return _FakeDaskDF(renamed, self._rows, self.npartitions)

    def repartition(self, npartitions):
        return _FakeDaskDF(self.columns, self._rows, npartitions=npartitions)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, key):
        if isinstance(key, list):
            return _FakeDaskDF(columns=key, rows=self._rows, npartitions=self.npartitions)
        return _FakeDaskSeries(key, self)


class _FakeDaskSeries:
    def __init__(self, name, df):
        self.name = name
        self.df = df

    def nunique(self):
        return _FakeDaskDF(columns=[self.name], rows=[(1,)])


class _FakeDaskGroupBy:
    def __init__(self, by):
        self.by = by

    def agg(self, *args, **kwargs):
        if kwargs:
            cols = list(kwargs.keys())
        else:
            cols = list(args[0].keys()) if args else []
        return _FakeDaskDF(columns=list(self.by) + cols, rows=[(1,)])

    def size(self):
        return _FakeDaskDF(columns=list(self.by) + ["size"], rows=[(1,)])

    def __getitem__(self, key):
        return _FakeDaskSeries(key, _FakeDaskDF(columns=[key], rows=[(1,)]))


class _FakePandasFrame:
    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    @property
    def iloc(self):
        class _ILoc:
            def __init__(self, rows):
                self.rows = rows

            def __getitem__(self, idx):
                return self.rows[idx]

        return _ILoc(self._rows)


def _make_adapter():
    adapter = object.__new__(mod.DaskDataFrameAdapter)
    adapter._log_verbose = lambda *_a, **_k: None
    adapter._tuning_config = SimpleNamespace(
        parallelism=SimpleNamespace(worker_count=3, threads_per_worker=2),
        memory=SimpleNamespace(memory_limit="2GB", spill_to_disk=True),
    )
    adapter.n_workers = None
    adapter.threads_per_worker = 1
    adapter.use_distributed = False
    adapter.scheduler_address = None
    adapter._legacy_use_distributed = None
    adapter._client = None
    adapter._cluster = None
    adapter.verbose = False
    adapter.working_dir = "."
    adapter._memory_limit = None
    adapter._spill_to_disk = False
    adapter._configured_spill_directory = None
    adapter._spill_directory = None
    adapter._owns_spill_directory = False
    adapter._n_workers_configured = False
    adapter._threads_per_worker_configured = False
    adapter._memory_limit_configured = False
    adapter._engine_receipt = adapter.resolve_execution_engine("threaded")
    return adapter


def test_init_raises_without_dask(monkeypatch):
    monkeypatch.setattr(mod, "DASK_AVAILABLE", False)
    with pytest.raises(ImportError, match="Dask not installed"):
        mod.DaskDataFrameAdapter()


def _engine_resolver(*, legacy=None, scheduler_address=None):
    adapter = object.__new__(mod.DaskDataFrameAdapter)
    adapter._legacy_use_distributed = legacy
    adapter.scheduler_address = scheduler_address
    return adapter


def test_default_execution_engine_resolves_to_distributed():
    receipt = _engine_resolver().resolve_execution_engine("default")

    assert receipt.requested == "default"
    assert receipt.applied == "distributed"
    assert receipt.applied_class == "distributed"
    assert receipt.resolution == "platform_default"


@pytest.mark.parametrize(("legacy", "engine"), [(True, "distributed"), (False, "threaded")])
def test_legacy_use_distributed_alias_warns_and_maps(legacy, engine, caplog):
    with captured_deprecations(caplog) as seen:
        receipt = _engine_resolver(legacy=legacy).resolve_execution_engine("default")

    assert len(seen) == 1
    assert "'use_distributed'" in seen[0]
    assert f"--execution-engine {engine}" in seen[0]

    assert receipt.requested == engine
    assert receipt.applied == engine
    assert receipt.resolution == "legacy_option"


def test_explicit_and_legacy_dask_engines_conflict():
    with pytest.raises(ValueError, match="Conflicting execution engine sources"):
        _engine_resolver(legacy=False).resolve_execution_engine("distributed")


def test_threaded_engine_rejects_scheduler_address():
    with pytest.raises(ValueError, match="scheduler_address requires execution_engine='distributed'"):
        _engine_resolver(scheduler_address="tcp://scheduler:8786").resolve_execution_engine("threaded")


@pytest.mark.skipif(not mod.DASK_AVAILABLE, reason="Dask not installed")
def test_factory_points_legacy_use_distributed_false_with_scheduler_address_at_distributed(tmp_path, caplog):
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
    from benchbox.platforms.adapter_factory import get_adapter

    database_config = PlatformHookRegistry.build_database_config(
        "dask", {"use_distributed": False, "scheduler_address": "tcp://scheduler:8786"}
    )
    option_names = PlatformHookRegistry.list_option_specs("dask")
    options = {key: value for key, value in database_config.options.items() if key in option_names}
    assert options["scheduler_address"] == "tcp://scheduler:8786"

    with captured_deprecations(caplog) as seen:
        with pytest.raises(
            ValueError, match="use_distributed=false cannot be combined with scheduler_address"
        ) as raised:
            get_adapter(
                "dask",
                mode="dataframe",
                working_dir=tmp_path,
                execution_engine=database_config.execution_engine,
                **options,
            )

    assert "execution_engine='distributed'" in str(raised.value)
    assert "threaded" not in str(raised.value)
    assert seen == []


@pytest.mark.skipif(not mod.DASK_AVAILABLE, reason="Dask not installed")
def test_saved_platform_option_resolves_legacy_dask_engine(tmp_path, caplog):
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
    from benchbox.platforms.adapter_factory import get_adapter

    database_config = PlatformHookRegistry.build_database_config("dask", {"use_distributed": False})
    option_names = PlatformHookRegistry.list_option_specs("dask")
    options = {key: value for key, value in database_config.options.items() if key in option_names}
    with captured_deprecations(caplog) as seen:
        adapter = get_adapter(
            "dask",
            mode="dataframe",
            working_dir=tmp_path,
            execution_engine=database_config.execution_engine,
            **options,
        )

    assert len(seen) == 1
    receipt = adapter.get_platform_info()["execution_engine"]
    assert receipt["requested"] == "threaded"
    assert receipt["applied"] == "threaded"
    assert receipt["resolution"] == "legacy_option"
    adapter.close()


@pytest.mark.skipif(not mod.DASK_AVAILABLE, reason="Dask not installed")
def test_factory_applies_explicit_threaded_engine(tmp_path):
    from benchbox.platforms.adapter_factory import get_adapter

    adapter = get_adapter(
        "dask",
        mode="dataframe",
        working_dir=tmp_path,
        execution_engine="threaded",
    )

    receipt = adapter.get_platform_info()["execution_engine"]
    assert receipt["requested"] == "threaded"
    assert receipt["applied"] == "threaded"
    assert receipt["resolution"] == "explicit"
    adapter.close()


def test_apply_tuning_and_setup_distributed_paths(monkeypatch, tmp_path):
    adapter = _make_adapter()
    adapter._configured_spill_directory = tmp_path / "spill"
    fake_config_calls = []
    monkeypatch.setitem(
        sys.modules, "dask", SimpleNamespace(config=SimpleNamespace(set=lambda v: fake_config_calls.append(v)))
    )
    adapter._apply_tuning()
    assert adapter.n_workers == 3
    assert adapter.threads_per_worker == 2
    assert adapter._memory_limit == "2GB"
    assert adapter._spill_to_disk is True

    adapter.scheduler_address = "tcp://x"
    mod.Client = lambda addr: SimpleNamespace(
        scheduler=SimpleNamespace(address=addr), scheduler_info=lambda: {"workers": {"w1": {}}}, close=lambda: None
    )
    adapter._setup_distributed()
    assert adapter._client is not None

    adapter.scheduler_address = None
    mod.LocalCluster = lambda **kwargs: SimpleNamespace(
        scheduler=SimpleNamespace(address="local"), workers={"w": {}}, close=lambda: None, dashboard_link="x"
    )
    mod.Client = lambda cluster: SimpleNamespace(
        scheduler=SimpleNamespace(address="local"),
        scheduler_info=lambda: {"workers": {"w1": {}, "w2": {}}},
        close=lambda: None,
    )
    adapter._setup_distributed()
    assert adapter._cluster is not None
    assert fake_config_calls


def test_local_resource_envelope_defaults_are_capped(monkeypatch):
    adapter = _make_adapter()
    adapter.use_distributed = True
    defaults = SimpleNamespace(
        parallelism=SimpleNamespace(worker_count=8, threads_per_worker=6),
        memory=SimpleNamespace(memory_limit="12GB", spill_to_disk=True),
    )

    import benchbox.core.dataframe.tuning as tuning

    monkeypatch.setattr(tuning, "get_smart_defaults", lambda platform: defaults)
    adapter._apply_local_resource_envelope_defaults()

    assert adapter.n_workers == 2
    assert adapter.threads_per_worker == 2
    assert adapter._memory_limit == "2GB"
    assert adapter._spill_to_disk is True


def test_local_resource_envelope_preserves_explicit_values(monkeypatch):
    adapter = _make_adapter()
    adapter.use_distributed = True
    adapter.n_workers = 4
    adapter.threads_per_worker = 3
    adapter._memory_limit = "6GB"
    adapter._n_workers_configured = True
    adapter._threads_per_worker_configured = True
    adapter._memory_limit_configured = True
    defaults = SimpleNamespace(
        parallelism=SimpleNamespace(worker_count=8, threads_per_worker=6),
        memory=SimpleNamespace(memory_limit="12GB", spill_to_disk=True),
    )

    import benchbox.core.dataframe.tuning as tuning

    monkeypatch.setattr(tuning, "get_smart_defaults", lambda platform: defaults)
    adapter._apply_local_resource_envelope_defaults()

    assert adapter.n_workers == 4
    assert adapter.threads_per_worker == 3
    assert adapter._memory_limit == "6GB"
    assert adapter._spill_to_disk is True


def test_spill_directory_cleanup_only_removes_owned_run_dir(tmp_path):
    adapter = _make_adapter()
    adapter.working_dir = tmp_path
    owned_spill = adapter._resolve_spill_directory()
    (owned_spill / "spill.bin").write_text("temporary", encoding="utf-8")

    adapter._cleanup_owned_spill_directory()

    assert not owned_spill.exists()
    assert not (tmp_path / ".benchbox-dask-spill").exists()

    explicit_spill = tmp_path / "explicit-spill"
    explicit_adapter = _make_adapter()
    explicit_adapter._configured_spill_directory = explicit_spill
    assert explicit_adapter._resolve_spill_directory() == explicit_spill

    explicit_adapter._cleanup_owned_spill_directory()

    assert explicit_spill.exists()


def test_resource_envelope_diagnostic_wraps_exit_137():
    adapter = _make_adapter()

    diagnostic = adapter._resource_envelope_diagnostic("compute", RuntimeError("exit code 137"))

    assert diagnostic is not None
    assert "Dask resource-envelope failure during compute" in diagnostic
    assert "exit code 137" in diagnostic
    assert "use_distributed=False" in diagnostic


def test_resource_envelope_diagnostic_ignores_unrelated_errors():
    adapter = _make_adapter()

    assert adapter._resource_envelope_diagnostic("compute", RuntimeError("bad column")) is None


def test_q10_not_preemptively_skipped_on_dask_local_envelope():

    adapter = _make_adapter()
    adapter.use_distributed = True
    adapter.scheduler_address = None
    benchmark = SimpleNamespace(name="TPC-H Benchmark")

    skip_query_ids = adapter._collect_skip_query_ids(benchmark)
    assert "Q10" not in skip_query_ids

    assert not hasattr(adapter, "_guarded_resource_query_diagnostic")
    assert not hasattr(adapter, "_tpch_q10_resource_envelope_diagnostic")

    assert adapter._resource_envelope_diagnostic("compute", RuntimeError("exit code 137")) is not None


def test_close_and_del_cleanup():
    adapter = _make_adapter()
    adapter._client = SimpleNamespace(close=lambda: None)
    adapter._cluster = SimpleNamespace(close=lambda: None)
    adapter.close()
    assert adapter._client is None and adapter._cluster is None
    adapter.__del__()


def test_read_csv_parquet_datetime_timedelta_concat_and_counts(monkeypatch, tmp_path):
    adapter = _make_adapter()

    def _read_csv(_path, **_kwargs):
        return _FakeDaskDF(columns=["a", "b", TRAILING_DUMMY_COLUMN], rows=[(1, 2, None)])

    monkeypatch.setattr(
        mod,
        "dd",
        SimpleNamespace(
            read_csv=_read_csv,
            read_parquet=lambda p: ("pq", p),
            concat=lambda dfs, ignore_index: ("concat", len(dfs), ignore_index),
        ),
    )
    monkeypatch.setattr(mod, "has_trailing_delimiter", lambda _p, _d, _n: True)
    monkeypatch.setattr(mod, "pd", pd)

    out = adapter.read_csv(tmp_path / "x.tbl", delimiter="|", header=None, names=["a", "b"], null_marker="")
    assert TRAILING_DUMMY_COLUMN not in out.columns
    assert adapter.read_parquet(tmp_path / "x.parquet")[0] == "pq"
    assert str(adapter.to_datetime(pd.Series(["2020-01-01"])).iloc[0]).startswith("2020-01-01")
    assert adapter.timedelta_days(3).days == 3

    one = _FakeDaskDF(columns=["a"], rows=[(1,)])
    two = _FakeDaskDF(columns=["a"], rows=[(2,)])
    assert adapter.concat([one]) is one
    assert adapter.concat([one, two]) == ("concat", 2, True)
    assert adapter.get_row_count(one) == 1
    assert adapter._get_first_row(_FakeDaskDF(rows=[])) is None
    assert adapter._get_first_row(one) == (1,)


def test_dask_specific_methods(monkeypatch):
    adapter = _make_adapter()
    df = _FakeDaskDF(columns=["k", "x"], rows=[(1, 2)])

    monkeypatch.setattr(mod, "dd", SimpleNamespace(merge=lambda *a, **k: ("merge", a, k)))
    assert adapter.compute(df)[0] == "computed"
    assert adapter.persist(df) is df
    assert adapter.merge("l", "r", on="id")[0] == "merge"

    named = adapter.groupby_agg(df, by="k", agg_spec={"sum_x": ("x", "sum")}, as_index=False)
    direct = adapter.groupby_agg(df, by=["k"], agg_spec={"x": "sum"}, as_index=False)
    with_nunique = adapter.groupby_agg(df, by=["k"], agg_spec={"n_x": ("x", "nunique")}, as_index=False)
    assert "sum_x" in named.columns
    assert "x" in direct.columns
    assert with_nunique[0] == "merged"

    size_df = adapter.groupby_size(df, by=["k"], name="cnt")
    assert "cnt" in size_df.columns
    reparted = adapter.repartition(df, 5)
    assert reparted.npartitions == 5
    assert adapter.get_npartitions(reparted) == 5


def test_get_platform_info_and_error_tolerance():
    adapter = _make_adapter()
    adapter.n_workers = 2
    adapter.threads_per_worker = 1
    adapter.use_distributed = True
    adapter._client = SimpleNamespace(
        scheduler=SimpleNamespace(address="tcp://localhost"),
        scheduler_info=lambda: {"workers": {"w1": {}, "w2": {}}},
    )
    info = adapter.get_platform_info()
    assert info["platform"] == "Dask"
    assert info["n_active_workers"] == 2

    adapter._client = SimpleNamespace(
        scheduler=SimpleNamespace(address="bad"),
        scheduler_info=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    info2 = adapter.get_platform_info()
    assert info2["platform"] == "Dask"
