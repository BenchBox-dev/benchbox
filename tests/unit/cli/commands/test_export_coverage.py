from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

ex = importlib.import_module("benchbox.cli.commands.export")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Exporter:
    def __init__(self, output_dir: Path, *_a: object, **_k: object):
        self.output_dir = output_dir

    def export_result(self, _result, formats: list[str]):
        out = {}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for fmt in formats:
            p = self.output_dir / f"result.{fmt}"
            p.write_text(f"{fmt}\n", encoding="utf-8")
            out[fmt] = p
        return out


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=0.01,
        total_queries=22,
        duration_seconds=12.34,
    )


def test_export_requires_file_or_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "console", SimpleNamespace(print=lambda *a, **k: None))
    result = CliRunner().invoke(ex.export, [])
    assert result.exit_code == 0


def test_export_last_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "find_latest_result", lambda *_a, **_k: None)
    result = CliRunner().invoke(ex.export, ["--last"])
    assert result.exit_code == 0
    assert "No results found" in result.output


def test_export_load_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (_ for _ in ()).throw(ex.ResultLoadError("bad")))
    result = CliRunner().invoke(ex.export, [str(src)])
    assert result.exit_code != 0
    assert "Error loading result file" in result.output


def test_export_happy_path_with_force(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (_fake_result(), {"x": 1}))
    monkeypatch.setattr(ex, "ResultExporter", _Exporter)

    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        ex.export,
        [str(src), "--format", "json", "--format", "csv", "--output-dir", str(out_dir), "--force"],
    )
    assert result.exit_code == 0
    assert "Export complete" in result.output
    assert (out_dir / "result.json").exists()
    assert (out_dir / "result.csv").exists()


def test_export_conflict_cancelled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "input.json").write_text("old\n", encoding="utf-8")

    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (_fake_result(), {}))
    monkeypatch.setattr(ex, "ResultExporter", _Exporter)
    monkeypatch.setattr("click.confirm", lambda *_a, **_k: False)

    result = CliRunner().invoke(
        ex.export,
        [str(src), "--format", "json", "--output-dir", str(out_dir)],
    )
    assert result.exit_code == 0
    assert "Export cancelled" in result.output
