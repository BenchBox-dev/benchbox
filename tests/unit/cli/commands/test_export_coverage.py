from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.core.results.anonymization import public_pseudonym_salt_path

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


class _RecordingExporter(_Exporter):
    configs: list[object] = []

    def __init__(self, output_dir: Path, *_a: object, anonymization_config: object = None, **_k: object):
        super().__init__(output_dir)
        _RecordingExporter.configs.append(anonymization_config)


def test_export_anonymizes_with_a_generated_salt_and_prints_the_notice_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BENCHBOX_MACHINE_ID_SALT", raising=False)
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (_fake_result(), {"x": 1}))
    monkeypatch.setattr(ex, "ResultExporter", _RecordingExporter)
    _RecordingExporter.configs.clear()
    salt_path = public_pseudonym_salt_path()

    args = [str(src), "--format", "json", "--output-dir", str(tmp_path / "out"), "--force"]
    first = CliRunner().invoke(ex.export, args)
    second = CliRunner().invoke(ex.export, args)

    assert first.exit_code == second.exit_code == 0
    salt = salt_path.read_text(encoding="utf-8").strip()
    assert [config.machine_id_salt for config in _RecordingExporter.configs] == [salt, salt]
    assert first.output.count("Created a private pseudonym salt") == 1
    assert "Created a private pseudonym salt" not in second.output
    assert salt not in first.output + second.output


def test_export_env_override_is_used_and_creates_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "override-salt")
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (_fake_result(), {"x": 1}))
    monkeypatch.setattr(ex, "ResultExporter", _RecordingExporter)
    _RecordingExporter.configs.clear()

    result = CliRunner().invoke(ex.export, [str(src), "--output-dir", str(tmp_path / "out"), "--force"])

    assert result.exit_code == 0
    assert [config.machine_id_salt for config in _RecordingExporter.configs] == ["override-salt"]
    assert not public_pseudonym_salt_path().exists()


def test_export_fails_naming_the_path_when_the_salt_store_is_unusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BENCHBOX_MACHINE_ID_SALT", raising=False)
    salt_path = public_pseudonym_salt_path()
    salt_path.parent.mkdir(parents=True)
    salt_path.write_text("", encoding="utf-8")
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (_fake_result(), {"x": 1}))
    monkeypatch.setattr(ex, "ResultExporter", _RecordingExporter)

    result = CliRunner().invoke(ex.export, [str(src), "--output-dir", str(tmp_path / "out"), "--force"])

    assert result.exit_code != 0
    assert "BENCHBOX_MACHINE_ID_SALT" in result.output
    assert salt_path.read_text(encoding="utf-8") == ""


def test_export_output_is_salted_with_the_file_salt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from datetime import datetime

    from benchbox.core.results.anonymization import AnonymizationConfig, AnonymizationManager
    from benchbox.core.results.models import BenchmarkResults

    monkeypatch.delenv("BENCHBOX_MACHINE_ID_SALT", raising=False)
    raw_endpoint = "https://warehouse.example.invalid/query"
    result_obj = BenchmarkResults(
        benchmark_name="TPC-H",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="export-salt-test",
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        ],
        platform_info={"name": "duckdb", "version": "1.0", "endpoint": raw_endpoint},
    )
    src = tmp_path / "input.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ex, "load_result_file", lambda *_a, **_k: (result_obj, {}))
    out_dir = tmp_path / "out"

    result = CliRunner().invoke(ex.export, [str(src), "--format", "json", "--output-dir", str(out_dir), "--force"])

    assert result.exit_code == 0, result.output
    salt = public_pseudonym_salt_path().read_text(encoding="utf-8").strip()
    salted = AnonymizationManager(AnonymizationConfig(machine_id_salt=salt))._hash_public_identifier(
        raw_endpoint, "endpoint"
    )
    unsalted = AnonymizationManager(AnonymizationConfig())._hash_public_identifier(raw_endpoint, "endpoint")
    (exported,) = out_dir.glob("*.json")
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert payload["platform"]["config"]["endpoint"] == salted
    assert salted != unsalted
    assert salt not in exported.read_text(encoding="utf-8")
