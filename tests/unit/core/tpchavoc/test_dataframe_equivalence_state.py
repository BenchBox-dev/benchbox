from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.tpch import dataframe_queries as dq
from benchbox.core.tpchavoc import dataframe_equivalence

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("fails", [False, True], ids=["return", "raise"])
def test_dataframe_gate_restores_ambient_parameters(monkeypatch: pytest.MonkeyPatch, fails: bool) -> None:
    benchmark = Mock(scale_factor=0.1)
    benchmark.get_implemented_queries.return_value = []

    def inspect_gate(*args, **kwargs):
        assert dq.get_tpch_parameters(3)["segment"] == "BUILDING"
        assert dq.get_tpch_parameters(11)["fraction"] == pytest.approx(0.001)
        if fails:
            raise RuntimeError("gate failure")
        return []

    monkeypatch.setattr(dataframe_equivalence, "find_surface_divergences", inspect_gate)
    with dq.seeded_parameter_overrides(None, 0.5, 0):
        dq.set_parameter_overrides({3: {"segment": "OUTER"}})
        previous = {query_id: dq.get_tpch_parameters(query_id) for query_id in (3, 11)}
        if fails:
            with pytest.raises(RuntimeError, match="gate failure"):
                dataframe_equivalence.find_dataframe_divergences(Mock(), benchmark, Mock(), {})
        else:
            assert dataframe_equivalence.find_dataframe_divergences(Mock(), benchmark, Mock(), {}) == []
        assert {query_id: dq.get_tpch_parameters(query_id) for query_id in (3, 11)} == previous


