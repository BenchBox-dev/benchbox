import io
import logging
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from benchbox.mcp import run_server

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@pytest.mark.usefixtures("preserved_quiet_mode")
class TestRunServerStartup:
    def test_startup_enables_quiet_mode(self, tmp_path):
        from benchbox.utils.printing import is_quiet, set_quiet

        set_quiet(False)
        with patch("benchbox.mcp.transport.run_transport"):
            run_server(results_dir=tmp_path, charts_dir=tmp_path / "charts")

        assert is_quiet()

    def test_startup_configures_only_the_benchbox_logger(self, tmp_path, capsys):
        root = logging.getLogger()
        benchbox_logger = logging.getLogger("benchbox")
        benchbox_logger.handlers[:] = []
        root_before = (root.level, list(root.handlers))

        with patch("benchbox.mcp.transport.run_transport"):
            run_server(results_dir=tmp_path, charts_dir=tmp_path / "charts", log_level="ERROR")
            run_server(results_dir=tmp_path, charts_dir=tmp_path / "charts", log_level="ERROR")

        assert (root.level, list(root.handlers)) == root_before
        assert benchbox_logger.level == logging.ERROR
        assert len(benchbox_logger.handlers) == 1
        assert benchbox_logger.handlers[0].stream is sys.stderr

        logging.getLogger("benchbox.mcp.server").error("server-log-line")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "server-log-line" in captured.err

    def test_startup_installs_the_redacted_tracer_provider_when_an_endpoint_is_set(self, tmp_path):
        probe = (
            "import sys\n"
            "from unittest.mock import patch\n"
            "from opentelemetry import trace\n"
            "from benchbox.mcp import run_server\n"
            "assert type(trace.get_tracer_provider()).__name__ == 'ProxyTracerProvider'\n"
            "with patch('benchbox.mcp.transport.run_transport'):\n"
            "    run_server(results_dir=sys.argv[1], charts_dir=sys.argv[1])\n"
            "from opentelemetry.sdk.trace import TracerProvider\n"
            "assert isinstance(trace.get_tracer_provider(), TracerProvider)\n"
        )
        env = {**os.environ, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:9/v1/traces"}
        subprocess.run(
            [sys.executable, "-c", probe, str(tmp_path)], check=True, capture_output=True, text=True, env=env
        )

    def test_startup_installs_the_transport_security_filter_for_a_remote_config(self, tmp_path, monkeypatch):
        from tests.integration.mcp._security import write_security_config

        transport_logger = logging.getLogger("mcp.server.transport_security")
        monkeypatch.setattr(transport_logger, "filters", [])
        config = write_security_config(tmp_path, tokens={"token": ("tenant-a", ("benchbox:read",))})

        with patch("benchbox.mcp.transport.run_transport"):
            run_server(transport="streamable-http", security_config=config)
        assert len(transport_logger.filters) == 1

        transport_logger.filters[:] = []
        with patch("benchbox.mcp.transport.run_transport"):
            run_server(results_dir=tmp_path, charts_dir=tmp_path / "charts")
        assert transport_logger.filters == []


class TestResultExporterQuietConsole:
    def test_benchmark_tool_uses_quiet_console(self):
        from benchbox.mcp.tools.benchmark import get_quiet_console

        assert callable(get_quiet_console)

        quiet_console = get_quiet_console()

        from benchbox.utils.printing import set_quiet

        set_quiet(True)
        quiet_console = get_quiet_console()
        assert hasattr(quiet_console, "file")
        assert isinstance(quiet_console.file, io.StringIO)
        set_quiet(False)

    def test_results_tool_uses_quiet_console(self):
        from benchbox.utils.printing import get_quiet_console

        assert callable(get_quiet_console)


class TestNoStdoutLeakage:
    def test_result_exporter_no_stdout_with_quiet_console(self):
        from benchbox.core.results.exporter import ResultExporter
        from benchbox.utils.printing import get_quiet_console

        captured = io.StringIO()
        original_stdout = sys.stdout

        try:
            sys.stdout = captured

            quiet_console = get_quiet_console()
            exporter = ResultExporter(
                anonymize=False,
                console=quiet_console,
            )

            exporter.console.print("Test message that should not appear on stdout")
            exporter.console.print("[green]Exported JSON:[/green] /path/to/file.json")

            stdout_content = captured.getvalue()
            assert stdout_content == "", f"Unexpected stdout: {stdout_content!r}"

        finally:
            sys.stdout = original_stdout

    @pytest.mark.usefixtures("preserved_quiet_mode")
    def test_server_startup_no_stdout(self, tmp_path):
        captured = io.StringIO()
        original_stdout = sys.stdout

        try:
            sys.stdout = captured

            with patch("benchbox.mcp.transport.run_transport"):
                run_server(results_dir=tmp_path, charts_dir=tmp_path / "charts")

            stdout_content = captured.getvalue()
            assert stdout_content == "", f"Unexpected stdout during server startup: {stdout_content!r}"

        finally:
            sys.stdout = original_stdout


class TestServerModuleImport:
    def test_importing_the_server_module_leaves_the_root_logger_alone(self):
        probe = (
            "import logging\n"
            "before = (logging.getLogger().level, list(logging.getLogger().handlers))\n"
            "import benchbox.mcp.server\n"
            "after = (logging.getLogger().level, list(logging.getLogger().handlers))\n"
            "assert before == after, (before, after)\n"
        )
        subprocess.run([sys.executable, "-c", probe], check=True, capture_output=True, text=True)
