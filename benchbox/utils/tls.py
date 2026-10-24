from __future__ import annotations

import os
import shutil
import ssl
import urllib.request
from pathlib import Path
from typing import Any

import certifi

CA_BUNDLE_ENV_VARS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def ca_bundle_path() -> str:
    for name in CA_BUNDLE_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return certifi.where()


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_bundle_path())


def urlopen(url: str | urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(url, timeout=timeout, context=ssl_context())


def download_to_file(url: str, destination: str | Path, timeout: float = 60) -> None:
    with urlopen(url, timeout=timeout) as response, open(destination, "wb") as handle:
        shutil.copyfileobj(response, handle)
