import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import _print_results_location, run

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_output_help_states_where_results_go():
    result = CliRunner().invoke(run, ["--help"], terminal_width=400)
    flat = " ".join(result.output.split())
    assert "generated data and databases" in flat
    assert "Result JSON is not written here" in flat
    assert "BENCHBOX_OUTPUT_DIR" in flat
    assert "absolute result path is printed" in flat


def test_results_location_prints_absolute_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("benchbox.cli.commands.run.console.width", 400, raising=False)
    _print_results_location({"json": "benchmark_runs/results/x.json"})
    out = "".join(capsys.readouterr().out.split())
    assert str((tmp_path / "benchmark_runs" / "results").resolve()).replace(" ", "") in out
