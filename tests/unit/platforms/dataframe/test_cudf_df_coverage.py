from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN

mod = importlib.import_module("benchbox.platforms.dataframe.cudf_df")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeCuDFFrame:
    def __init__(self, columns=None, rows=None):
        self.columns = list(columns or [])
        self._rows = list(rows or [])

    def drop(self, columns):
        cols = set(columns)
        return _FakeCuDFFrame([c for c in self.columns if c not in cols], self._rows)

    def head(self, n):
        return _FakeCuDFFrame(self.columns, self._rows[:n])

    def to_pandas(self):
        class _ILoc:
            def __init__(self, rows):
                self.rows = rows

            def __getitem__(self, idx):
                return self.rows[idx]

        return SimpleNamespace(iloc=_ILoc(self._rows))

    def __len__(self):
        return len(self._rows)

    def groupby(self, by, as_index=False):
        return _FakeGroupBy(by, as_index)


class _FakeGroupBy:
    def __init__(self, by, as_index):
        self.by = by
        self.as_index = as_index

    def agg(self, *args, **kwargs):
        return {"args": args, "kwargs": kwargs, "by": self.by, "as_index": self.as_index}


def _make_adapter():
    adapter = object.__new__(mod.CuDFDataFrameAdapter)
    adapter.device_id = 0
    adapter.spill_to_host = True
    adapter._pool_type = "pool"
    adapter._log_verbose = lambda *_args, **_kwargs: None
    adapter.verbose = False
    adapter.working_dir = "."
    adapter._tuning_config = SimpleNamespace(
        gpu=SimpleNamespace(enabled=True, device_id=1, spill_to_host=False, pool_type="managed")
    )
    return adapter


def test_init_raises_without_cudf(monkeypatch):
    monkeypatch.setattr(mod, "CUDF_AVAILABLE", False)
    with pytest.raises(ImportError, match="cuDF not installed"):
        mod.CuDFDataFrameAdapter()


def test_apply_tuning_updates_gpu_settings():
    adapter = _make_adapter()
    adapter._apply_tuning()
    assert adapter.device_id == 1
    assert adapter.spill_to_host is False
    assert adapter._pool_type == "managed"


def test_configure_gpu_success_and_failure(monkeypatch):
    adapter = _make_adapter()
    adapter.verbose = True

    fake_rmm = SimpleNamespace(reinitialize=lambda **kwargs: kwargs)
    fake_runtime = SimpleNamespace(getDeviceProperties=lambda _idx: {"name": "FakeGPU"})
    fake_cupy = SimpleNamespace(
        cuda=SimpleNamespace(
            Device=lambda _i: SimpleNamespace(mem_info=(4_000_000_000, 8_000_000_000)), runtime=fake_runtime
        )
    )
    monkeypatch.setitem(sys.modules, "rmm", fake_rmm)
    monkeypatch.setitem(sys.modules, "cupy", fake_cupy)
    adapter._configure_gpu()

    def _boom():
        raise RuntimeError("no gpu")

    monkeypatch.setitem(sys.modules, "rmm", SimpleNamespace(reinitialize=lambda **_k: _boom()))
    adapter._configure_gpu()


def test_read_csv_handles_tpc_trailing_column(monkeypatch, tmp_path):
    adapter = _make_adapter()

    calls = {}

    def _read_csv(_path, **kwargs):
        calls["kwargs"] = kwargs
        return _FakeCuDFFrame(columns=["a", "b", TRAILING_DUMMY_COLUMN], rows=[(1, 2, None)])

    monkeypatch.setattr(mod, "cudf", SimpleNamespace(read_csv=_read_csv))
    monkeypatch.setattr(mod, "has_trailing_delimiter", lambda _p, _d, _n: True)

    out = adapter.read_csv(tmp_path / "x.tbl", delimiter="|", header=None, names=["a", "b"], null_marker="")
    assert TRAILING_DUMMY_COLUMN not in out.columns
    assert calls["kwargs"]["names"] == ["a", "b", TRAILING_DUMMY_COLUMN]


def test_read_parquet_to_datetime_timedelta_concat_row_helpers(monkeypatch, tmp_path):
    adapter = _make_adapter()
    monkeypatch.setattr(mod, "cudf", SimpleNamespace(read_parquet=lambda p: ("pq", p), to_datetime=lambda s: ("dt", s)))

    out = adapter.read_parquet(tmp_path / "x.parquet")
    assert out[0] == "pq"
    assert adapter.to_datetime("s") == ("dt", "s")
    assert adapter.timedelta_days(2).days == 2

    one = _FakeCuDFFrame(columns=["a"], rows=[(1,)])
    two = _FakeCuDFFrame(columns=["a"], rows=[(2,)])
    monkeypatch.setattr(
        mod, "cudf", SimpleNamespace(concat=lambda dfs, ignore_index: ("concat", len(dfs), ignore_index))
    )
    assert adapter.concat([one]) is one
    assert adapter.concat([one, two]) == ("concat", 2, True)
    assert adapter.get_row_count(one) == 1
    assert adapter._get_first_row(_FakeCuDFFrame(columns=["a"], rows=[])) is None
    assert adapter._get_first_row(one) == (1,)


def test_platform_info_merge_groupby_pandas_conversions_and_gpu_usage(monkeypatch):
    adapter = _make_adapter()
    monkeypatch.setattr(mod, "CUDF_AVAILABLE", True)
    monkeypatch.setattr(
        mod,
        "cudf",
        SimpleNamespace(__version__="1.2.3", merge=lambda *a, **k: ("merge", a, k), from_pandas=lambda d: ("from", d)),
    )

    fake_runtime = SimpleNamespace(getDeviceProperties=lambda _idx: {"name": "GPU-X"})
    fake_cupy = SimpleNamespace(
        cuda=SimpleNamespace(
            Device=lambda _i: SimpleNamespace(mem_info=(3_000_000_000, 9_000_000_000)), runtime=fake_runtime
        )
    )
    monkeypatch.setitem(sys.modules, "cupy", fake_cupy)

    info = adapter.get_platform_info()
    assert info["platform"] == "cuDF"
    assert info["version"] == "1.2.3"
    assert info["gpu_name"] == "GPU-X"

    merged = adapter.merge("l", "r", on="id")
    assert merged[0] == "merge"

    named = adapter.groupby_agg(_FakeCuDFFrame(rows=[(1,)]), by="k", agg_spec={"sum_x": ("x", "sum")}, as_index=False)
    direct = adapter.groupby_agg(_FakeCuDFFrame(rows=[(1,)]), by="k", agg_spec={"x": "sum"}, as_index=False)
    assert "sum_x" in named["kwargs"]
    assert direct["args"][0] == {"x": "sum"}

    frame = _FakeCuDFFrame(columns=["a"], rows=[(1,)])
    assert hasattr(adapter.to_pandas(frame), "iloc")
    assert adapter.from_pandas({"a": [1]}) == ("from", {"a": [1]})

    usage = adapter.get_gpu_memory_usage()
    assert usage["total_gb"] > 0

    monkeypatch.setitem(
        sys.modules,
        "cupy",
        SimpleNamespace(cuda=SimpleNamespace(Device=lambda _i: (_ for _ in ()).throw(RuntimeError()))),
    )
    assert adapter.get_gpu_memory_usage() == {"free_gb": 0.0, "total_gb": 0.0, "used_gb": 0.0}


