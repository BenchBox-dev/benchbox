from __future__ import annotations

import datetime
import http.server
import ipaddress
import ssl
import threading
import urllib.error
from pathlib import Path

import certifi
import pytest

from benchbox.utils import tls

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def clean_ca_environment(monkeypatch):
    for name in tls.CA_BUNDLE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_certifi_bundle():
    assert tls.ca_bundle_path() == certifi.where()


def test_ssl_cert_file_overrides_certifi(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/ssl-cert-file.pem")
    assert tls.ca_bundle_path() == "/tmp/ssl-cert-file.pem"


def test_requests_ca_bundle_takes_precedence_over_ssl_cert_file(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/ssl-cert-file.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/tmp/requests-bundle.pem")
    assert tls.ca_bundle_path() == "/tmp/requests-bundle.pem"


def test_blank_override_falls_back_to_certifi(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "  ")
    monkeypatch.setenv("SSL_CERT_FILE", "")
    assert tls.ca_bundle_path() == certifi.where()


def _write_ca_signed_server_certificate(directory: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)

    def name(common_name: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(name("benchbox test ca"))
        .issuer_name(name("benchbox test ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(name("127.0.0.1"))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    chain_path = directory / "server.pem"
    key_path = directory / "server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    chain_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, chain_path, key_path


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return


@pytest.fixture
def private_ca_server(tmp_path):
    pytest.importorskip("cryptography")
    ca_path, chain_path, key_path = _write_ca_signed_server_certificate(tmp_path)
    server = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(chain_path, key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}/", ca_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_urlopen_rejects_certificate_from_ca_outside_bundle(private_ca_server):
    url, _ca_path = private_ca_server
    with pytest.raises(urllib.error.URLError) as excinfo:
        tls.urlopen(url, timeout=5)
    assert isinstance(excinfo.value.reason, ssl.SSLCertVerificationError)


def test_urlopen_accepts_certificate_when_ca_supplied_through_override(private_ca_server, monkeypatch):
    url, ca_path = private_ca_server
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
    with tls.urlopen(url, timeout=5) as response:
        assert response.read() == b"ok"


def test_ssl_context_verifies_certificates_and_hostnames():
    context = tls.ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
