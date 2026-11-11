from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.benchmark_registry import (
    get_all_benchmarks,
    get_benchmark_default_scale,
    get_public_benchmark_class,
)
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path, resolve_benchmark_runs_dir

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

MCP_BENCHMARKS = ["h2odb", "ssb", "vector_search", "tpch"]


@pytest.mark.parametrize("benchmark_id", MCP_BENCHMARKS)
def test_mcp_run_construction_honors_env_output_root(benchmark_id, tmp_path, monkeypatch):
    root = tmp_path / "mcp_runs"
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(root))

    benchmark_class = get_public_benchmark_class(benchmark_id)
    if benchmark_class is None:
        pytest.skip(f"{benchmark_id} not available in this environment")

    benchmark = benchmark_class(scale_factor=0.01)

    output_dir = Path(str(benchmark.output_dir))
    assert str(output_dir).startswith(str(root)), output_dir
    assert Path.cwd() not in output_dir.parents, output_dir

    core = getattr(benchmark, "_impl", benchmark)
    generator = core.__dict__.get("data_generator")
    assert generator is not None and hasattr(generator, "output_dir")
    gen_dir = Path(str(generator.output_dir))
    assert str(gen_dir).startswith(str(root)), gen_dir
    assert Path.cwd() not in gen_dir.parents, gen_dir


def test_mcp_data_only_datagen_path_honors_env_output_root(tmp_path, monkeypatch):
    root = tmp_path / "mcp_runs"
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(root))

    data_dir = get_benchmark_runs_datagen_path("tpch", 0.01)

    assert str(data_dir).startswith(str(root)), data_dir
    assert data_dir.name == "tpch_sf001", data_dir
    assert Path.cwd() not in data_dir.parents, data_dir


def test_mcp_default_without_env_uses_resolved_default_root(monkeypatch):
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

    data_dir = get_benchmark_runs_datagen_path("tpch", 0.01)

    assert data_dir == resolve_benchmark_runs_dir() / "datagen" / "tpch_sf001"


@pytest.mark.parametrize("benchmark_id", sorted(get_all_benchmarks()))
def test_mcp_registry_construction_honors_env_output_root(benchmark_id, tmp_path, monkeypatch):
    root = tmp_path / "mcp_runs"
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(root))

    benchmark_class = get_public_benchmark_class(benchmark_id)
    assert benchmark_class is not None, f"{benchmark_id}: no public benchmark class"

    benchmark = benchmark_class(scale_factor=get_benchmark_default_scale(benchmark_id))

    output_dir = Path(str(benchmark.output_dir))
    assert output_dir.is_relative_to(root), (benchmark_id, output_dir)
    assert Path.cwd() not in output_dir.parents, (benchmark_id, output_dir)

    core = getattr(benchmark, "_impl", benchmark)
    for attr in getattr(core, "OUTPUT_DIR_GENERATOR_ATTRS", ("data_generator",)):
        generator = core.__dict__.get(attr)
        if generator is None or not hasattr(generator, "output_dir"):
            continue
        gen_dir = Path(str(generator.output_dir))
        assert gen_dir.is_relative_to(root), (benchmark_id, attr, gen_dir)
        assert Path.cwd() not in gen_dir.parents, (benchmark_id, attr, gen_dir)


