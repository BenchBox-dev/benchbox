# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

mcp = pytest.importorskip("mcp", reason="requires benchbox[mcp] extra")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestMcpCreateServer:
    def test_create_server_forwards_all_kwargs(self, tmp_path):
        with patch("benchbox.mcp.server.create_benchbox_server") as mock_create:
            mock_create.return_value = MagicMock()
            import benchbox.mcp as mcp_mod

            mcp_mod.create_server(
                results_dir=tmp_path,
                charts_dir=tmp_path / "charts",
                env={"FOO": "bar"},
            )
            mock_create.assert_called_once_with(
                results_dir=tmp_path,
                charts_dir=tmp_path / "charts",
                env={"FOO": "bar"},
            )


class TestMcpRunServer:
    def test_run_server_calls_server_run(self, preserved_quiet_mode):
        with patch("benchbox.mcp.server.create_benchbox_server") as mock_create:
            mock_server = MagicMock()
            mock_create.return_value = mock_server

            import benchbox.mcp as mcp_mod

            mcp_mod.run_server()
            mock_server.run.assert_called_once()


class TestMcpLazyExports:
    def test_getattr_error_code(self):
        import benchbox.mcp as mcp_mod

        ErrorCode = mcp_mod.ErrorCode
        assert ErrorCode is not None

    def test_getattr_mcp_error(self):
        import benchbox.mcp as mcp_mod

        MCPError = mcp_mod.MCPError
        assert MCPError is not None

    def test_getattr_make_error(self):
        import benchbox.mcp as mcp_mod

        make_error = mcp_mod.make_error
        assert make_error is not None

    @pytest.mark.parametrize(
        "removed",
        ["ToolCallContext", "get_metrics_collector", "ExecutionStatus", "ExecutionState", "get_execution_tracker"],
    )
    def test_getattr_removed_dead_exports_raise(self, removed: str):

        import benchbox.mcp as mcp_mod

        with pytest.raises(AttributeError):
            getattr(mcp_mod, removed)

    def test_getattr_unknown_raises_attribute_error(self):
        import benchbox.mcp as mcp_mod

        with pytest.raises(AttributeError, match="benchbox.mcp"):
            _ = mcp_mod.totally_unknown_name_xyz
