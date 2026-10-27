from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.utilities import network_guard

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PUBLIC_IP = "203.0.113.7"


def test_public_ip_connect_is_blocked(expect_network_block):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(network_guard.NetworkAccessBlocked, match="network guard"):
            sock.connect((PUBLIC_IP, 80))


def test_create_connection_to_public_host_is_blocked(expect_network_block):
    with pytest.raises(network_guard.NetworkAccessBlocked, match="non-loopback"):
        socket.create_connection(("example.com", 443), timeout=1)


def test_dns_resolution_of_public_name_is_blocked(expect_network_block):
    with pytest.raises(network_guard.NetworkAccessBlocked):
        socket.getaddrinfo("example.com", 443)


@pytest.mark.parametrize("mechanism", ["urlopen", "build_opener", "http_client", "ssl_wrapped"])
def test_every_fetch_mechanism_is_blocked(mechanism, expect_network_block):
    import http.client
    import ssl
    import urllib.request

    url = f"https://{PUBLIC_IP}/file"
    if mechanism == "urlopen":
        attempt = lambda: urllib.request.urlopen(url, timeout=1)
    elif mechanism == "build_opener":
        attempt = lambda: urllib.request.build_opener().open(url, timeout=1)
    elif mechanism == "http_client":
        attempt = lambda: http.client.HTTPSConnection(PUBLIC_IP, timeout=1).request("GET", "/")
    else:
        attempt = lambda: ssl.create_default_context().wrap_socket(
            socket.create_connection((PUBLIC_IP, 443), timeout=1), server_hostname="example.com"
        )
    with pytest.raises(Exception) as caught:
        attempt()
    assert "BenchBox network guard" in str(caught.value) or "BenchBox network guard" in str(caught.value.__cause__)


def test_udp_sendto_public_host_is_blocked(expect_network_block):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(network_guard.NetworkAccessBlocked):
            sock.sendto(b"x", (PUBLIC_IP, 53))


def test_loopback_server_is_reachable():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        accepted: list[bytes] = []

        def serve() -> None:
            conn, _ = server.accept()
            with conn:
                accepted.append(conn.recv(4))

        thread = threading.Thread(target=serve)
        thread.start()
        with socket.create_connection(("localhost", port), timeout=5) as client:
            client.sendall(b"ping")
        thread.join(timeout=5)
    assert accepted == [b"ping"]


PROBE = Path(__file__).resolve().parents[1] / "utilities" / "network_guard_probe.py"


def _run(host: str, port: int, log: Path | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if log is not None:
        switch_dir = log.parent / "switch"
        switch_dir.mkdir(exist_ok=True)
        (switch_dir / network_guard.SWITCH_NAME).write_text(str(log), encoding="utf-8")
        env[network_guard.ENV_DIR] = str(switch_dir)
    return subprocess.run(
        [sys.executable, str(PROBE), host, str(port)], capture_output=True, text=True, timeout=60, env=env
    )


def test_subprocess_inherits_guard(tmp_path):
    log = tmp_path / "sub.log"
    result = _run(PUBLIC_IP, 80, log)
    assert result.returncode != 0
    assert "BenchBox network guard" in result.stderr
    assert "non-loopback" in log.read_text(encoding="utf-8")


def test_subprocess_violation_is_recorded_for_the_spawning_test(network_guard_log, expect_network_block):
    result = _run(PUBLIC_IP, 80, None)
    assert result.returncode != 0
    assert "BenchBox network guard" in network_guard_log.read_text(encoding="utf-8")


def test_subprocess_loopback_connect_is_allowed(tmp_path):
    log = tmp_path / "sub.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        result = _run("127.0.0.1", server.getsockname()[1], log)
    assert result.returncode == 0, result.stderr
    assert not log.exists()


def test_swallowed_violation_still_fails_the_test(tmp_path):
    inner = tmp_path / "test_inner.py"
    inner.write_text(
        "import socket\n"
        "import pytest\n"
        "pytestmark = pytest.mark.unit\n"
        "def test_swallows():\n"
        "    try:\n"
        f"        socket.create_connection(('{PUBLIC_IP}', 80), timeout=1)\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "BENCHBOX_SKIP_TEST_LOCK": "1", "PYTHONPATH": str(repo)}
    env.pop(network_guard.ENV_DIR, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-c",
            str(repo / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "tests.utilities.network_guard_plugin",
            "-p",
            "no:cacheprovider",
            "-n0",
            "-m",
            "unit",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode != 0, result.stdout
    assert "BenchBox network guard" in result.stdout


def _run_inner_pytest(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    inner = tmp_path / "test_inner.py"
    inner.write_text(body, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "BENCHBOX_SKIP_TEST_LOCK": "1", "PYTHONPATH": str(repo)}
    env.pop(network_guard.ENV_DIR, None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-c",
            str(repo / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "tests.utilities.network_guard_plugin",
            "-p",
            "no:cacheprovider",
            "-n0",
            "-m",
            "unit",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=tmp_path,
    )


def test_allow_network_without_a_reason_fails_collection(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import pytest\n@pytest.mark.unit\n@pytest.mark.allow_network\ndef test_x():\n    pass\n",
    )
    assert result.returncode != 0
    assert "allow_network requires a non-empty reason" in result.stdout + result.stderr


def test_allow_network_with_a_reason_collects(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import pytest\n@pytest.mark.unit\n@pytest.mark.allow_network(reason='subject is the call')\n"
        "def test_x():\n    pass\n",
    )
    assert result.returncode == 0, result.stdout


def test_expect_network_block_fails_when_nothing_was_blocked(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import pytest\npytestmark = pytest.mark.unit\ndef test_x(expect_network_block):\n    pass\n",
    )
    assert result.returncode != 0
    assert "blocked nothing" in result.stdout
