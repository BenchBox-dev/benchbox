from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from benchbox.core.tsbs_devops.dataframe_queries import queries as qmod

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _Expr:
    value: object

    def __eq__(self, other):
        return _Expr(("eq", self.value, getattr(other, "value", other)))

    def __gt__(self, other):
        return _Expr(("gt", self.value, getattr(other, "value", other)))

    def __lt__(self, other):
        return _Expr(("lt", self.value, getattr(other, "value", other)))

    def __ge__(self, other):
        return _Expr(("ge", self.value, getattr(other, "value", other)))

    def __le__(self, other):
        return _Expr(("le", self.value, getattr(other, "value", other)))

    def __and__(self, other):
        return _Expr(("and", self.value, getattr(other, "value", other)))

    def __add__(self, other):
        return _Expr(("add", self.value, getattr(other, "value", other)))

    def __truediv__(self, other):
        return _Expr(("div", self.value, getattr(other, "value", other)))

    def alias(self, name):
        return _Expr(("alias", self.value, name))

    def max(self):
        return _Expr(("max", self.value))

    def mean(self):
        return _Expr(("mean", self.value))

    def sum(self):
        return _Expr(("sum", self.value))

    def min(self):
        return _Expr(("min", self.value))

    def count(self):
        return _Expr(("count", self.value))

    def n_unique(self):
        return _Expr(("n_unique", self.value))

    def cast_float(self):
        return _Expr(("cast_float", self.value))

    @property
    def dt(self):
        return _DtOps(self)


class _DtOps:
    def __init__(self, expr: _Expr):
        self.expr = expr

    def truncate(self, every: str):
        return _Expr(("truncate", self.expr.value, every))


class _WhenThen:
    def __init__(self, cond: _Expr, then_value: _Expr):
        self.cond = cond
        self.then_value = then_value

    def otherwise(self, value):
        return _Expr(("case", self.cond.value, self.then_value.value, getattr(value, "value", value)))


class _When:
    def __init__(self, cond: _Expr):
        self.cond = cond

    def then(self, value):
        return _WhenThen(self.cond, value if isinstance(value, _Expr) else _Expr(value))


class _ExprTable:
    def filter(self, _cond):
        return self

    def select(self, *_cols):
        return self

    def sort(self, *_args, **_kwargs):
        return self

    def group_by(self, *_cols):
        return self

    def agg(self, *_exprs):
        return self

    def with_columns(self, *_exprs):
        return self

    def join(self, _other, **_kwargs):
        return self

    def unique(self):
        return self


class _ExprContext:
    def __init__(self):
        self._table = _ExprTable()

    def get_table(self, _name: str):
        return self._table

    def col(self, name: str):
        return _Expr(("col", name))

    def lit(self, value):
        return _Expr(("lit", value))

    def when(self, condition):
        return _When(condition if isinstance(condition, _Expr) else _Expr(condition))


class _PandasContext:
    def __init__(self, cpu: pd.DataFrame, mem: pd.DataFrame, disk: pd.DataFrame, net: pd.DataFrame, tags: pd.DataFrame):
        self.tables = {"cpu": cpu, "mem": mem, "disk": disk, "net": net, "tags": tags}

    def get_table(self, name: str):
        return self.tables[name]


def _build_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    t0 = pd.Timestamp("2021-01-01T00:00:00")
    t1 = pd.Timestamp("2021-01-01T00:01:00")
    t2 = pd.Timestamp("2021-01-01T00:02:00")

    cpu = pd.DataFrame(
        {
            "hostname": ["host_0", "host_0", "host_1"],
            "time": [t0, t1, t2],
            "usage_user": [95.0, 60.0, 30.0],
            "usage_system": [2.0, 5.0, 10.0],
            "usage_idle": [3.0, 35.0, 60.0],
            "usage_iowait": [1.0, 2.0, 0.5],
        }
    )
    mem = pd.DataFrame(
        {
            "hostname": ["host_0", "host_1"],
            "time": [t0, t1],
            "used_percent": [70.0, 55.0],
            "available": [300, 450],
            "available_percent": [30.0, 45.0],
        }
    )
    disk = pd.DataFrame(
        {
            "hostname": ["host_0", "host_1"],
            "time": [t0, t1],
            "device": ["sda", "sdb"],
            "reads_completed": [10, 0],
            "writes_completed": [5, 2],
            "read_time_ms": [100.0, 0.0],
            "write_time_ms": [50.0, 40.0],
        }
    )
    net = pd.DataFrame(
        {
            "hostname": ["host_0", "host_1"],
            "time": [t0, t1],
            "interface": ["eth0", "eth0"],
            "bytes_sent": [1000, 2000],
            "bytes_recv": [1100, 2100],
            "err_in": [0, 1],
            "err_out": [0, 0],
            "drop_in": [0, 0],
            "drop_out": [0, 1],
        }
    )
    tags = pd.DataFrame(
        {
            "hostname": ["host_0", "host_1"],
            "region": ["us-east-1", "us-west-2"],
            "service": ["api", "db"],
        }
    )
    return cpu, mem, disk, net, tags


def _params(_qid: str) -> dict[str, object]:
    return {
        "start_time": pd.Timestamp("2021-01-01T00:00:00"),
        "end_time": pd.Timestamp("2021-01-01T01:00:00"),
        "hostname": "host_0",
        "region": "us-east-1",
    }


@pytest.mark.parametrize("i", range(1, 19))
def test_all_expression_query_impls_execute(monkeypatch: pytest.MonkeyPatch, i: int) -> None:
    monkeypatch.setattr(qmod, "get_parameters", _params)
    ctx = _ExprContext()
    fn = getattr(qmod, f"q{i}_expression_impl")
    out = fn(ctx)
    assert isinstance(out, _ExprTable)


@pytest.mark.parametrize("i", range(1, 19))
def test_all_pandas_query_impls_execute(monkeypatch: pytest.MonkeyPatch, i: int) -> None:
    monkeypatch.setattr(qmod, "get_parameters", _params)
    cpu, mem, disk, net, tags = _build_frames()
    ctx = _PandasContext(cpu=cpu, mem=mem, disk=disk, net=net, tags=tags)
    fn = getattr(qmod, f"q{i}_pandas_impl")
    out = fn(ctx)
    assert isinstance(out, pd.DataFrame)


