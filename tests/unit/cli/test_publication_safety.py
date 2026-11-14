from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.auth import auth
from benchbox.cli.commands.export import _confirm_overwrite
from benchbox.cli.commands.setup import setup_credentials
from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.cli.platform_checks import check_and_setup_platform_credentials
from benchbox.cli.publication_guard import (
    HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR,
    MAINTAINER_LABEL_ENV_VAR,
)
from benchbox.core.config import BenchmarkConfig

pub = importlib.import_module("benchbox.cli.commands.publish")
sub = importlib.import_module("benchbox.cli.commands.submit")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REFUSED = int(ExitCode.ACKNOWLEDGEMENT_REQUIRED)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR,
        MAINTAINER_LABEL_ENV_VAR,
        "BENCHBOX_NON_INTERACTIVE",
        "BENCHBOX_SUBMIT_TOKEN",
        "BENCHBOX_SERVICE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()


def _result_file(tmp_path: Path) -> Path:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"schema_version": "2.0"}), encoding="utf-8")
    return path


class TestHostedSubmissionGate:
    def test_service_upload_is_refused_without_acknowledgement(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(sub.submit, [str(_result_file(tmp_path)), "--service"])

        assert result.exit_code == REFUSED
        assert "Hosted submission refused" in result.output
        assert "--acknowledge-hosted-submission" in result.output
        assert HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR in result.output

    def test_refusal_happens_before_the_bundle_is_read_or_any_token_resolved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args, **_kwargs):
            raise AssertionError("reached past the acknowledgement gate")

        monkeypatch.setattr(sub, "load_result_file", fail)
        monkeypatch.setattr(sub, "resolve_submission_token", fail)
        monkeypatch.setattr(sub, "submit_hosted_bundle", fail)

        result = CliRunner().invoke(sub.submit, [str(_result_file(tmp_path)), "--service", "--last"])

        assert result.exit_code == REFUSED

    def test_flag_passes_the_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        reached: list[str] = []
        monkeypatch.setattr(
            sub,
            "load_result_file",
            lambda *_a, **_k: reached.append("load") or (_ for _ in ()).throw(FileNotFoundError()),
        )

        result = CliRunner().invoke(
            sub.submit, [str(_result_file(tmp_path)), "--service", "--acknowledge-hosted-submission"]
        )

        assert reached == ["load"]
        assert result.exit_code != REFUSED
        assert "Hosted submission refused" not in result.output

    def test_environment_variable_passes_the_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR, "1")
        monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()))

        result = CliRunner().invoke(sub.submit, [str(_result_file(tmp_path)), "--service"])

        assert result.exit_code != REFUSED

    def test_dry_run_is_exempt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()))

        result = CliRunner().invoke(sub.submit, [str(_result_file(tmp_path)), "--service", "--dry-run"])

        assert result.exit_code != REFUSED

    def test_package_mode_is_not_gated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()))

        result = CliRunner().invoke(sub.submit, [str(_result_file(tmp_path))])

        assert result.exit_code != REFUSED

    def test_interactive_session_is_not_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from benchbox.cli import publication_guard

        monkeypatch.setattr(publication_guard, "is_non_interactive_session", lambda *_a, **_k: False)
        monkeypatch.setattr(sub, "load_result_file", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()))

        result = CliRunner().invoke(sub.submit, [str(_result_file(tmp_path)), "--service"])

        assert result.exit_code != REFUSED

    def test_help_documents_the_flag_variable_and_exit_code(self) -> None:
        output = " ".join(CliRunner().invoke(sub.submit, ["--help"]).output.split())

        assert "--acknowledge-hosted-submission" in output
        assert HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR in output
        assert "6" in output


class TestMaintainerLabelRestriction:
    @pytest.fixture
    def source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(
            pub,
            "load_result_file",
            lambda *_a, **_k: (
                SimpleNamespace(
                    benchmark_name="tpch", platform="duckdb", scale_factor=1, total_queries=22, duration_seconds=1.0
                ),
                {},
            ),
        )
        monkeypatch.setattr(pub, "publish_admission", lambda *_a, **_k: SimpleNamespace(allowed=True))
        return _result_file(tmp_path)

    def test_default_label_is_refused_without_the_variable(self, source: Path) -> None:
        result = CliRunner().invoke(pub.publish_run, [str(source), "--dry-run"])

        assert result.exit_code == REFUSED
        assert MAINTAINER_LABEL_ENV_VAR in result.output
        assert "community-submission" in result.output

    def test_explicit_maintainer_label_is_refused_in_any_case(self, source: Path) -> None:
        result = CliRunner().invoke(pub.publish_run, [str(source), "--label", "MAINTAINER-RUN", "--dry-run"])

        assert result.exit_code == REFUSED

    def test_variable_allows_the_label(self, source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(MAINTAINER_LABEL_ENV_VAR, "1")

        result = CliRunner().invoke(pub.publish_run, [str(source), "--dry-run"])

        assert result.exit_code == 0
        assert "Label:   maintainer-run" in result.output

    @pytest.mark.parametrize("label", ["community-submission", "vendor-supplied", "unofficial-research", "local", "ci"])
    def test_other_labels_need_no_variable(self, source: Path, label: str) -> None:
        result = CliRunner().invoke(pub.publish_run, [str(source), "--label", label, "--dry-run"])

        assert result.exit_code == 0

    def test_publish_bundle_used_by_run_publish_refuses_the_label(
        self, source: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pub, "BundlePublisher", lambda **_kw: (_ for _ in ()).throw(AssertionError("published")))

        assert pub.publish_bundle(source, label="maintainer-run") is None

    def test_run_publish_refuses_before_running_anything(self) -> None:
        from benchbox.cli.app import cli

        result = CliRunner().invoke(
            cli,
            ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--publish", "--non-interactive"],
        )

        assert result.exit_code == REFUSED
        assert MAINTAINER_LABEL_ENV_VAR in result.output

    def test_help_documents_the_restriction(self) -> None:
        output = " ".join(CliRunner().invoke(pub.publish, ["--help"]).output.split())

        assert MAINTAINER_LABEL_ENV_VAR in output
        assert "benchbox publish run results/tpch_sf1_duckdb.json" in output
        assert "benchbox publish results/" not in output


class TestPromptsWithoutATerminal:
    def test_setup_remove_refuses_with_closed_stdin(self, tmp_path: Path) -> None:
        creds = tmp_path / "home" / ".benchbox" / "credentials.yaml"
        creds.parent.mkdir()
        creds.write_text("snowflake:\n  account: a\n", encoding="utf-8")

        result = CliRunner().invoke(setup_credentials, ["--platform", "snowflake", "--remove"], input="")

        assert result.exit_code == REFUSED
        assert "--yes" in result.output
        assert "Traceback" not in result.output
        assert not isinstance(result.exception, (EOFError, OSError))
        assert "snowflake" in creds.read_text(encoding="utf-8")

    def test_setup_remove_with_yes_proceeds(self, tmp_path: Path) -> None:
        creds = tmp_path / "home" / ".benchbox" / "credentials.yaml"
        creds.parent.mkdir()
        creds.write_text("snowflake:\n  account: a\n", encoding="utf-8")

        result = CliRunner().invoke(setup_credentials, ["--platform", "snowflake", "--remove", "--yes"], input="")

        assert result.exit_code == 0
        assert "snowflake" not in creds.read_text(encoding="utf-8")

    def test_export_overwrite_refuses_with_closed_stdin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "result.json"
        source.write_text("{}", encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        (out / "result.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        with pytest.raises(SystemExit) as exit_info:
            _confirm_overwrite(source, out, ["json"])

        assert exit_info.value.code == REFUSED

    def test_publish_remove_refuses_with_closed_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = SimpleNamespace(pub_id="abc", benchmark="tpch", platform="duckdb", reference="file:///x")
        removed: list[str] = []
        store = SimpleNamespace(get=lambda _id: record, remove=removed.append)
        monkeypatch.setattr(pub, "PublicationStore", lambda: store)

        result = CliRunner().invoke(pub.publish_remove, ["abc"], input="")

        assert result.exit_code == REFUSED
        assert "--yes" in result.output
        assert removed == []

    def test_publish_remove_with_yes_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = SimpleNamespace(pub_id="abc", benchmark="tpch", platform="duckdb", reference="file:///x")
        removed: list[str] = []
        store = SimpleNamespace(get=lambda _id: record, remove=removed.append)
        monkeypatch.setattr(pub, "PublicationStore", lambda: store)

        result = CliRunner().invoke(pub.publish_remove, ["abc", "--yes"], input="")

        assert result.exit_code == 0
        assert removed == ["abc"]

    @pytest.mark.parametrize("command", ["login", "refresh"])
    def test_auth_without_token_fails_cleanly_with_closed_stdin(self, command: str) -> None:
        result = CliRunner().invoke(auth, [command], input="")

        assert result.exit_code == 1
        assert "No hosted-submit token found" in result.output
        assert "BENCHBOX_SUBMIT_TOKEN" in result.output
        assert isinstance(result.exception, SystemExit)

    def test_credential_check_without_terminal_declines_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        console = SimpleNamespace(print=lambda *_a, **_k: None)

        assert check_and_setup_platform_credentials("snowflake", console, interactive=True) is False


class TestOrchestratorCredentialPrompt:
    @pytest.fixture
    def failing_orchestrator(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> BenchmarkOrchestrator:
        orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))

        def credentials_missing(*_args, **_kwargs):
            raise RuntimeError("Snowflake configuration requires account")

        monkeypatch.setattr(orchestrator, "_get_benchmark_instance", credentials_missing)
        return orchestrator

    @staticmethod
    def _run(orchestrator: BenchmarkOrchestrator):
        return orchestrator.execute_benchmark(
            BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01),
            SimpleNamespace(),
            SimpleNamespace(type="snowflake"),
            ["load"],
        )

    def test_non_interactive_never_reaches_the_prompt(
        self, failing_orchestrator: BenchmarkOrchestrator, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import rich.prompt

        def prompted(*_args, **_kwargs):
            raise AssertionError("credential prompt reached")

        monkeypatch.setattr(rich.prompt.Confirm, "ask", prompted)
        failing_orchestrator.set_non_interactive(True)

        result = self._run(failing_orchestrator)

        assert result is not None

    def test_closed_stdin_declines_instead_of_crashing(
        self, failing_orchestrator: BenchmarkOrchestrator, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        assert self._run(failing_orchestrator) is not None

    @pytest.mark.parametrize(("flags", "expected"), [(["--non-interactive"], True), ([], False)])
    def test_run_command_passes_its_mode_to_the_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch, flags: list[str], expected: bool
    ) -> None:
        from benchbox.cli.app import cli

        run_module = importlib.import_module("benchbox.cli.commands.run")
        seen: list[bool] = []

        class Stop(Exception):
            pass

        def capture(orchestrator, *_args, **_kwargs):
            seen.append(orchestrator._non_interactive)
            raise Stop

        monkeypatch.setattr(run_module, "_execute_orchestrated_run", capture)

        result = CliRunner().invoke(
            cli, ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", *flags, "--force"]
        )

        assert isinstance(result.exception, Stop), result.output
        assert seen == [expected]
