import logging
import os
import subprocess
import sys
from typing import Any

import pytest

from tests.integration.mcp._security import write_security_config

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _process_state() -> tuple[Any, ...]:
    from benchbox.utils.printing import is_quiet

    loggers = [
        logging.getLogger(),
        logging.getLogger("benchbox"),
        logging.getLogger("mcp.server.transport_security"),
    ]
    return (
        tuple((logger.level, list(logger.handlers), list(logger.filters)) for logger in loggers),
        is_quiet(),
    )


def test_building_local_and_remote_servers_leaves_process_state_unchanged(tmp_path, monkeypatch):
    from benchbox.mcp import create_server
    from benchbox.mcp.security import RemoteSecurityRuntime
    from benchbox.utils.printing import set_quiet

    benchbox_logger = logging.getLogger("benchbox")
    benchbox_logger.setLevel(logging.WARNING)
    benchbox_logger.handlers[:] = []
    benchbox_logger.filters[:] = []
    monkeypatch.setattr(logging.getLogger("mcp.server.transport_security"), "filters", [])
    set_quiet(False)
    config = write_security_config(tmp_path, tokens={"token": ("tenant-a", ("benchbox:read",))})
    before = _process_state()

    create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")
    create_server(remote_security=RemoteSecurityRuntime.from_file(config))

    assert _process_state() == before


def test_building_a_server_leaves_the_global_tracer_provider_unconfigured(tmp_path):
    probe = (
        "import sys\n"
        "from opentelemetry import trace\n"
        "from benchbox.mcp import create_server\n"
        "create_server(results_dir=sys.argv[1], charts_dir=sys.argv[1])\n"
        "assert type(trace.get_tracer_provider()).__name__ == 'ProxyTracerProvider', trace.get_tracer_provider()\n"
    )
    env = {**os.environ, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:9/v1/traces"}

    subprocess.run([sys.executable, "-c", probe, str(tmp_path)], check=True, capture_output=True, text=True, env=env)
