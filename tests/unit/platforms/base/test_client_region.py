from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.client_region import (
    discover_client_region,
    reset_client_region_cache,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_client_region_cache()
    yield
    reset_client_region_cache()


def test_aws_imds_discovery() -> None:
    token_response = MagicMock()
    token_response.read.return_value = b"test-aws-token"
    token_response.__enter__.return_value = token_response

    doc_response = MagicMock()
    doc_response.read.return_value = json.dumps({"region": "us-west-2"}).encode("utf-8")
    doc_response.__enter__.return_value = doc_response

    def fake_urlopen(req, timeout=0.2):
        if "latest/api/token" in req.full_url:
            assert req.get_method() == "PUT"
            assert req.headers.get("X-aws-ec2-metadata-token-ttl-seconds") == "60"
            return token_response
        if "instance-identity/document" in req.full_url:
            assert req.get_method() == "GET"
            assert req.headers.get("X-aws-ec2-metadata-token") == "test-aws-token"
            return doc_response
        raise urllib.error.URLError("Not found")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_urlopen):
        result = discover_client_region()

    assert result == {
        "client_region": "us-west-2",
        "client_cloud": "aws",
        "source": "observed",
    }


def test_gcp_metadata_discovery() -> None:
    gcp_response = MagicMock()
    gcp_response.read.return_value = b"projects/123456789/zones/us-central1-a"
    gcp_response.__enter__.return_value = gcp_response

    def fake_urlopen(req, timeout=0.2):
        if "computeMetadata/v1/instance/zone" in req.full_url:
            assert req.headers.get("Metadata-flavor") == "Google"
            return gcp_response
        raise urllib.error.URLError("AWS not available")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_urlopen):
        result = discover_client_region()

    assert result == {
        "client_region": "us-central1",
        "client_cloud": "gcp",
        "source": "observed",
    }


def test_azure_imds_discovery() -> None:
    azure_response = MagicMock()
    azure_response.read.return_value = b"eastus2"
    azure_response.__enter__.return_value = azure_response

    def fake_urlopen(req, timeout=0.2):
        if "metadata/instance/compute/location" in req.full_url:
            assert req.headers.get("Metadata") == "true"
            return azure_response
        raise urllib.error.URLError("Not available")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_urlopen):
        result = discover_client_region()

    assert result == {
        "client_region": "eastus2",
        "client_cloud": "azure",
        "source": "observed",
    }


def test_unreachable_imds_fallback() -> None:
    with patch(
        "benchbox.platforms.base.client_region._imds_open", side_effect=urllib.error.URLError("Connection refused")
    ):
        result = discover_client_region()

    assert result == {
        "client_region": None,
        "client_cloud": None,
        "source": "unavailable",
    }


def test_cli_option_override_region_and_cloud() -> None:
    config = {
        "client_region": "eu-central-1",
        "client_cloud": "aws",
    }
    with patch("benchbox.platforms.base.client_region._imds_open") as mock_urlopen:
        result = discover_client_region(config)
        mock_urlopen.assert_not_called()

    assert result == {
        "client_region": "eu-central-1",
        "client_cloud": "aws",
        "source": "cli_option",
    }


def test_cli_option_override_region_only_defaults_unknown_cloud() -> None:
    config = {
        "client_region": "my-custom-region",
    }
    with patch("benchbox.platforms.base.client_region._imds_open") as mock_urlopen:
        result = discover_client_region(config)
        mock_urlopen.assert_not_called()

    assert result == {
        "client_region": "my-custom-region",
        "client_cloud": "unknown",
        "source": "cli_option",
    }


def test_process_caching() -> None:
    token_response = MagicMock()
    token_response.read.return_value = b"token"
    token_response.__enter__.return_value = token_response

    doc_response = MagicMock()
    doc_response.read.return_value = json.dumps({"region": "ap-southeast-1"}).encode("utf-8")
    doc_response.__enter__.return_value = doc_response

    call_count = 0

    def fake_urlopen(req, timeout=0.2):
        nonlocal call_count
        call_count += 1
        if "latest/api/token" in req.full_url:
            return token_response
        if "instance-identity/document" in req.full_url:
            return doc_response
        raise urllib.error.URLError("Not found")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_urlopen):
        res1 = discover_client_region()
        assert call_count == 2

        res2 = discover_client_region()
        assert call_count == 2

    assert res1 == res2
    assert res1["client_region"] == "ap-southeast-1"


@pytest.fixture
def _no_cloud_metadata_probes():
    yield


@pytest.fixture
def gcp_metadata_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if (
                self.path.startswith("/computeMetadata/v1/instance/zone")
                and self.headers.get("Metadata-Flavor") == "Google"
            ):
                body = b"projects/1/zones/europe-west1-b"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_PUT(self):
            self.do_GET()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_imds_bypasses_proxy_environment(gcp_metadata_server) -> None:
    proxy_hits: list[str] = []

    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_hits.append(self.path)
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_PUT(self):
            self.do_GET()

        def log_message(self, *args):
            pass

    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
    code = (
        "import json, sys\n"
        "from benchbox.platforms.base import client_region\n"
        "client_region._IMDS_BASE_URL = sys.argv[1]\n"
        "print(json.dumps(client_region.discover_client_region()))\n"
    )
    env = {
        **os.environ,
        "HTTP_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": "",
        "no_proxy": "",
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code, gcp_metadata_server],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=Path(__file__).resolve().parents[4],
        )
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["client_region"] == "europe-west1"
    assert result["client_cloud"] == "gcp"
    assert proxy_hits == []


def test_imds_html_body_rejected() -> None:
    html_response = MagicMock()
    html_response.read.return_value = b"<html><body>Proxy auth required</body></html>"
    html_response.__enter__.return_value = html_response

    def fake_imds_open(req, timeout=0.2):
        return html_response

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_imds_open):
        result = discover_client_region()

    assert result == {
        "client_region": None,
        "client_cloud": None,
        "source": "unavailable",
    }


def test_aws_imdsv1_fallback() -> None:
    doc_response = MagicMock()
    doc_response.read.return_value = json.dumps({"region": "sa-east-1"}).encode("utf-8")
    doc_response.__enter__.return_value = doc_response

    def fake_imds_open(req, timeout=0.2):
        if req.get_method() == "PUT":
            raise urllib.error.URLError("IMDSv1-only host")
        if "instance-identity/document" in req.full_url:
            assert "X-aws-ec2-metadata-token" not in req.headers
            return doc_response
        raise urllib.error.URLError("Not found")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_imds_open):
        result = discover_client_region()

    assert result == {
        "client_region": "sa-east-1",
        "client_cloud": "aws",
        "source": "observed",
    }


def test_cli_option_invalid_values_ignored() -> None:
    config = {
        "client_region": "not a region!!",
        "client_cloud": "my-laptop",
    }
    with patch("benchbox.platforms.base.client_region._imds_open") as mock_open:
        mock_open.side_effect = urllib.error.URLError("no IMDS")
        result = discover_client_region(config)

    assert result == {
        "client_region": None,
        "client_cloud": None,
        "source": "unavailable",
    }


def test_cli_cloud_only_drops_mismatched_observed_region() -> None:
    token_response = MagicMock()
    token_response.read.return_value = b"token"
    token_response.__enter__.return_value = token_response
    doc_response = MagicMock()
    doc_response.read.return_value = json.dumps({"region": "us-east-1"}).encode("utf-8")
    doc_response.__enter__.return_value = doc_response

    def fake_imds_open(req, timeout=0.2):
        if "latest/api/token" in req.full_url:
            return token_response
        if "instance-identity/document" in req.full_url:
            return doc_response
        raise urllib.error.URLError("Not found")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_imds_open):
        result = discover_client_region({"client_cloud": "gcp"})

    assert result == {
        "client_region": None,
        "client_cloud": "gcp",
        "source": "cli_option",
    }


def test_imds_redirects_refused() -> None:
    from benchbox.platforms.base import client_region as client_region_module

    handler = client_region_module._NoRedirectHandler()
    assert (
        handler.redirect_request(
            MagicMock(),
            MagicMock(),
            302,
            "Found",
            {},
            "http://attacker.invalid/steal",
        )
        is None
    )
    assert any(
        isinstance(handler, client_region_module._NoRedirectHandler)
        for handler in client_region_module._IMDS_OPENER.handlers
    )


def test_cli_cloud_unknown_keeps_observed_region() -> None:
    token_response = MagicMock()
    token_response.read.return_value = b"token"
    token_response.__enter__.return_value = token_response
    doc_response = MagicMock()
    doc_response.read.return_value = json.dumps({"region": "us-east-1"}).encode("utf-8")
    doc_response.__enter__.return_value = doc_response

    def fake_imds_open(req, timeout=0.2):
        if "latest/api/token" in req.full_url:
            return token_response
        if "instance-identity/document" in req.full_url:
            return doc_response
        raise urllib.error.URLError("Not found")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_imds_open):
        result = discover_client_region({"client_cloud": "unknown"})

    assert result == {
        "client_region": "us-east-1",
        "client_cloud": "unknown",
        "source": "cli_option",
    }


def test_cli_cloud_only_keeps_matching_observed_region() -> None:
    token_response = MagicMock()
    token_response.read.return_value = b"token"
    token_response.__enter__.return_value = token_response
    doc_response = MagicMock()
    doc_response.read.return_value = json.dumps({"region": "us-east-1"}).encode("utf-8")
    doc_response.__enter__.return_value = doc_response

    def fake_imds_open(req, timeout=0.2):
        if "latest/api/token" in req.full_url:
            return token_response
        if "instance-identity/document" in req.full_url:
            return doc_response
        raise urllib.error.URLError("Not found")

    with patch("benchbox.platforms.base.client_region._imds_open", side_effect=fake_imds_open):
        result = discover_client_region({"client_cloud": "aws"})

    assert result == {
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "source": "cli_option",
    }
