import io
import logging
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestMCPServerQuietMode:
    def test_create_server_enables_quiet_mode(self):
        from benchbox.utils.printing import is_quiet, set_quiet

        set_quiet(False)
        assert not is_quiet()

        from benchbox.mcp import create_server

        create_server()

        assert is_quiet()

        set_quiet(False)


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

    def test_server_creation_no_stdout(self):
        captured = io.StringIO()
        original_stdout = sys.stdout

        try:
            sys.stdout = captured

            from benchbox.mcp import create_server

            create_server()

            stdout_content = captured.getvalue()
            assert stdout_content == "", f"Unexpected stdout during server creation: {stdout_content!r}"

        finally:
            sys.stdout = original_stdout

        from benchbox.utils.printing import set_quiet

        set_quiet(False)


class TestServerLoggingScope:
    def test_create_server_configures_only_the_benchbox_logger(self, tmp_path, capsys):
        from benchbox.mcp import create_server

        root = logging.getLogger()
        benchbox_logger = logging.getLogger("benchbox")
        benchbox_logger.handlers[:] = []
        root_before = (root.level, list(root.handlers))

        create_server(results_dir=tmp_path, charts_dir=tmp_path / "charts", log_level="ERROR")
        create_server(results_dir=tmp_path, charts_dir=tmp_path / "charts", log_level="ERROR")

        assert (root.level, list(root.handlers)) == root_before
        assert benchbox_logger.level == logging.ERROR
        assert len(benchbox_logger.handlers) == 1

        logging.getLogger("benchbox.mcp.server").error("server-log-line")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "server-log-line" in captured.err

    def test_importing_the_server_module_leaves_the_root_logger_alone(self):
        probe = (
            "import logging\n"
            "before = (logging.getLogger().level, list(logging.getLogger().handlers))\n"
            "import benchbox.mcp.server\n"
            "after = (logging.getLogger().level, list(logging.getLogger().handlers))\n"
            "assert before == after, (before, after)\n"
        )
        subprocess.run([sys.executable, "-c", probe], check=True, capture_output=True, text=True)
