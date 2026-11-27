from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.tpch import queries as module
from benchbox.core.tpch.queries import QGenBinary
from benchbox.core.tpch.streams import TPCHStreams

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def qgen():
    instance = QGenBinary.__new__(QGenBinary)
    instance.qgen_path = "/nonexistent/qgen"
    instance.templates_dir = None
    instance._work_dir = None
    return instance


@pytest.mark.parametrize("query_id", [7, 12])
def test_seed_zero_rejected_before_qgen_runs(qgen, query_id):
    with patch.object(module, "run_child") as run_child:
        with pytest.raises(ValueError, match="does not terminate for seed 0.*non-zero seed"):
            qgen.generate(query_id, seed=0)
    run_child.assert_not_called()


@pytest.mark.parametrize(("query_id", "seed"), [(1, 0), (7, 1), (12, None)])
def test_other_cases_reach_qgen(qgen, query_id, seed, tmp_path):
    qgen.templates_dir = tmp_path
    qgen._work_dir = str(tmp_path)
    with patch.object(module, "run_child") as run_child:
        run_child.return_value.stdout = "select 1;"
        qgen.generate(query_id, seed=seed)
    cmd = run_child.call_args.args[0]
    assert ("-d" in cmd) == (seed is None)


def test_stream_generation_rejects_seed_zero_before_compiling(tmp_path):
    streams = TPCHStreams(output_dir=tmp_path, rng_seed=0)
    with patch.object(TPCHStreams, "_compile_qgen") as compile_qgen:
        with pytest.raises(ValueError, match="does not terminate for seed 0"):
            streams.generate_streams()
    compile_qgen.assert_not_called()
