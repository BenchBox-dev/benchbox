from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.cloud_import]

connector = pytest.importorskip("snowflake.connector")
pytest.importorskip("cryptography.x509")

CONNECTOR_MAJOR = int(connector.__version__.split(".", 1)[0])
requires_connector_5 = pytest.mark.skipif(
    CONNECTOR_MAJOR < 5, reason="needs snowflake-connector-python 5.x: gateway_ca_bundle goes to extra_root_store_path"
)
requires_connector_below_5 = pytest.mark.skipif(
    CONNECTOR_MAJOR >= 5,
    reason="needs snowflake-connector-python below 5: gateway_ca_bundle is read from REQUESTS_CA_BUNDLE",
)

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

BASE = {"account": "acct", "username": "user", "password": "mock-password", "warehouse": "WH", "database": "DB"}


@dataclass
class TlsServer:
    port: int
    ca_bundle: Path
    handshakes: list[bytes] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _write_private_ca_and_leaf(directory: Path) -> tuple[Path, Path, Path]:
    now = datetime.datetime.now(datetime.timezone.utc)
    window = {"not_valid_before": now - datetime.timedelta(days=1), "not_valid_after": now + datetime.timedelta(days=1)}
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("benchbox-test-ca"))
        .issuer_name(_name("benchbox-test-ca"))
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(window["not_valid_before"])
        .not_valid_after(window["not_valid_after"])
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(window["not_valid_before"])
        .not_valid_after(window["not_valid_after"])
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    cert_path = directory / "leaf.pem"
    key_path = directory / "leaf.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return ca_path, cert_path, key_path


@pytest.fixture
def private_ca_server(tmp_path: Path, monkeypatch) -> Iterator[TlsServer]:
    monkeypatch.setenv("SNOWFLAKE_DISABLE_PLATFORM_DETECTION", "true")
    ca_path, cert_path, key_path = _write_private_ca_and_leaf(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    server = TlsServer(port=listener.getsockname()[1], ca_bundle=ca_path)

    def serve() -> None:
        while True:
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True) as tls:
                    server.handshakes.append(tls.recv(65536)[:32])
                    tls.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except (ssl.SSLError, OSError) as exc:
                server.failures.append(type(exc).__name__)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        listener.close()
        thread.join(timeout=5)


def _login(server: TlsServer, **gateway_options: object) -> None:
    adapter = SnowflakeAdapter(
        **BASE, gateway="custom", gateway_host="localhost", gateway_port=server.port, **gateway_options
    )
    params = adapter._connect_params({}, include_database=False)
    if CONNECTOR_MAJOR >= 5:
        params["enable_server_session_keep_alive_auto_detection"] = False
    with pytest.raises(connector.errors.DatabaseError):
        connector.connect(**params, login_timeout=4, network_timeout=4)


def _assert_login_reached_server(server: TlsServer) -> None:
    assert server.handshakes
    assert all(request.startswith(b"POST /session/v1/login-request") for request in server.handshakes)


def test_private_ca_endpoint_is_rejected_without_ca_bundle(private_ca_server: TlsServer, monkeypatch) -> None:
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    _login(private_ca_server)
    assert private_ca_server.handshakes == []
    assert private_ca_server.failures


@requires_connector_5
def test_private_ca_endpoint_verifies_with_gateway_ca_bundle(private_ca_server: TlsServer, monkeypatch) -> None:
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    _login(private_ca_server, gateway_ca_bundle=str(private_ca_server.ca_bundle))
    _assert_login_reached_server(private_ca_server)


@requires_connector_below_5
def test_private_ca_endpoint_verifies_with_requests_ca_bundle(private_ca_server: TlsServer, monkeypatch) -> None:
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(private_ca_server.ca_bundle))
    _login(private_ca_server, gateway_ca_bundle=str(private_ca_server.ca_bundle))
    _assert_login_reached_server(private_ca_server)
