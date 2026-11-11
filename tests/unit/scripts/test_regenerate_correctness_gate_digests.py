from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def generator():
    path = Path(__file__).resolve().parents[3] / "_project/scripts/regenerate_correctness_gate_digests.py"
    spec = importlib.util.spec_from_file_location("_digest_regenerator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_regeneration_uses_qgen_defaults_and_records_the_actual_parameter_set(generator, monkeypatch, tmp_path):
    output = tmp_path / "reference.json"
    calls = []

    def run_gate(work_dir, query_ids, seed):
        calls.append((query_ids, seed))
        return {"queries": [{"id": "18", "stream_id": "0", "status": "SUCCESS", "digest": "digest18"}]}

    monkeypatch.setenv(generator.CORRECTNESS_GATE_QUERY_IDS_ENV, "18")
    monkeypatch.setattr(generator, "_run_gate", run_gate)
    monkeypatch.setattr(generator, "REFERENCE_PATH", output)
    monkeypatch.setattr(generator, "_REPO_ROOT", tmp_path)
    assert generator.main() == 0
    assert calls == [(["18"], None)]
    reference = json.loads(output.read_text())
    assert reference["reference_seed"] is None
    assert reference["digests"] == {"18": "digest18"}
    assert "qgen -d" in reference["provenance"]["note"]


@pytest.mark.parametrize("seed", [None, 7])
def test_gate_child_receives_only_a_real_explicit_seed(generator, monkeypatch, tmp_path, seed):
    from benchbox.core.results import loader

    result_path = tmp_path / "result.json"
    result_path.write_text('{"queries": []}')
    calls = []
    monkeypatch.setattr(loader, "find_latest_result", lambda *a, **k: result_path)
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )
    assert generator._run_gate(tmp_path, ["18"], seed) == {"queries": []}
    command, options = calls[0]
    if seed is None:
        assert "--seed" not in command
        assert "None" not in command
    else:
        assert command[command.index("--seed") + 1] == "7"
    assert options["env"][generator.EMIT_RESULT_DIGEST_ENV] == "1"
    assert options["env"]["BENCHBOX_OUTPUT_DIR"] == str(tmp_path / "benchmark_runs")


