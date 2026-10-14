from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

pl = pytest.importorskip("polars")
pytest.importorskip("duckdb")

from benchbox.core.equivalence import cross_surface

pytestmark = [pytest.mark.integration, pytest.mark.medium, pytest.mark.duckdb]


@contextmanager
def _collect_engines() -> Iterator[list[str | None]]:
    engines: list[str | None] = []
    original = pl.LazyFrame.collect

    @functools.wraps(original)
    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        engines.append(kwargs.get("engine"))
        return original(self, *args, **kwargs)

    pl.LazyFrame.collect = spy
    try:
        yield engines
    finally:
        pl.LazyFrame.collect = original


@pytest.mark.timeout(600)
def test_gate_row_comparison_collects_every_frame_with_the_requested_engine() -> None:
    with _collect_engines() as engines:
        exit_code = cross_surface.main(
            ["--benchmark", "ssb", "--backend", "expression", "--execution-engine", "streaming"]
        )

    assert exit_code == 0
    assert len(engines) >= 13
    assert set(engines) == {"streaming"}


@pytest.mark.timeout(600)
def test_gate_dtype_comparison_collects_every_frame_with_the_requested_engine(tmp_path) -> None:
    gate = cross_surface.get_gate("ssb")
    data = gate.build(gate.scale_factor, tmp_path)
    try:
        contexts = cross_surface.build_production_contexts(
            data.benchmark,
            data.data_dir,
            backends=("expression",),
            scale_factor=gate.scale_factor,
            execution_engine="streaming",
        )
        with _collect_engines() as engines:
            _, compared = cross_surface.find_cross_surface_dtype_divergences(
                data.connection,
                query_ids=data.query_ids,
                reference_sql=data.reference_sql,
                dataframe_query=data.dataframe_query,
                contexts=contexts,
            )
    finally:
        data.connection.close()

    assert compared["expression"] == 13
    assert len(engines) == 13
    assert set(engines) == {"streaming"}
