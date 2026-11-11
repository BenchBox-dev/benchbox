from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchbox.mcp.readiness import CURRENT_PROTOCOL_VERSION
from scripts import verify_mcp_conformance

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _stub_protocol_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, responses: dict[str, tuple[int, str]]):
    commands: list[list[str]] = []
    monkeypatch.setattr(verify_mcp_conformance, "_prepare_conformance", lambda _checkout: tmp_path / "runner.js")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        scenario = command[command.index("--scenario") + 1]
        returncode, output = responses.get(scenario, (0, ""))
        return subprocess.CompletedProcess(command, returncode, stdout=output, stderr="")

    monkeypatch.setattr(verify_mcp_conformance.subprocess, "run", fake_run)
    return commands


def test_certifying_protocol_gate_requires_exact_expected_failure_baseline(monkeypatch, tmp_path: Path) -> None:
    expected_output = "".join(
        f"FAILURE [{failure.split(':', 1)[1]}]\n" for failure in verify_mcp_conformance.EXPECTED_FAILURE_IDS
    )
    commands = _stub_protocol_runner(monkeypatch, tmp_path, {"server-stateless": (1, expected_output)})

    verify_mcp_conformance._run_protocol_gate("http://127.0.0.1:8000/mcp", CURRENT_PROTOCOL_VERSION, tmp_path)

    assert len(commands) == len(verify_mcp_conformance.SCENARIOS)
    assert all("--expected-failures" not in command for command in commands)


def test_certifying_protocol_gate_rejects_unparseable_nonzero_result(monkeypatch, tmp_path: Path) -> None:
    _stub_protocol_runner(monkeypatch, tmp_path, {"caching": (1, "transport failed\n")})

    with pytest.raises(subprocess.CalledProcessError):
        verify_mcp_conformance._run_protocol_gate("http://127.0.0.1:8000/mcp", CURRENT_PROTOCOL_VERSION, tmp_path)


def test_certifying_protocol_gate_rejects_incomplete_expected_baseline(monkeypatch, tmp_path: Path) -> None:
    output = "FAILURE [sep-2575-server-rejects-undeclared-capability]\n"
    _stub_protocol_runner(monkeypatch, tmp_path, {"server-stateless": (1, output)})

    with pytest.raises(subprocess.CalledProcessError):
        verify_mcp_conformance._run_protocol_gate("http://127.0.0.1:8000/mcp", CURRENT_PROTOCOL_VERSION, tmp_path)


def test_certifying_protocol_gate_rejects_unexpected_warning(monkeypatch, tmp_path: Path) -> None:
    expected_failures = "".join(
        f"FAILURE [{failure.split(':', 1)[1]}]\n" for failure in verify_mcp_conformance.EXPECTED_FAILURE_IDS
    )
    output = expected_failures + "WARNING [sep-2575-server-sends-tools-list-changed-on-subscription]\n"
    _stub_protocol_runner(monkeypatch, tmp_path, {"server-stateless": (1, output)})

    with pytest.raises(subprocess.CalledProcessError):
        verify_mcp_conformance._run_protocol_gate("http://127.0.0.1:8000/mcp", CURRENT_PROTOCOL_VERSION, tmp_path)


def test_generated_evidence_does_not_certify_local_multiworker_tests(monkeypatch, tmp_path: Path) -> None:
    def fake_check_output(command: list[str], **_kwargs: object) -> str:
        if command[1] == "status":
            return ""
        if command[1] == "rev-parse":
            return "a" * 40 + "\n"
        raise AssertionError(command)

    monkeypatch.setattr(verify_mcp_conformance.subprocess, "check_output", fake_check_output)
    evidence = tmp_path / "evidence.json"

    verify_mcp_conformance._write_evidence(evidence, CURRENT_PROTOCOL_VERSION)

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["automated"]["conformance"] is True
    assert payload["automated"]["multiworker"] is False


