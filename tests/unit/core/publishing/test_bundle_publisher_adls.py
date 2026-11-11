from __future__ import annotations

import sys
from types import ModuleType

import pytest

from benchbox.core.publishing.bundle_publisher import _copy_to_cloud

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_copy_to_cloud_uploads_adls_files_directly(tmp_path, monkeypatch):
    uploaded: list[tuple[str, bytes, bool]] = []

    class FileClient:
        def __init__(self, path: str) -> None:
            self.path = path

        def upload_data(self, payload: bytes, *, overwrite: bool) -> None:
            uploaded.append((self.path, payload, overwrite))

    class FileSystem:
        def get_file_client(self, path: str) -> FileClient:
            return FileClient(path)

    class Service:
        def __init__(self, *, account_url: str, credential: object) -> None:
            assert account_url == "https://account.dfs.core.windows.net"
            assert credential == "credential"

        def get_file_system_client(self, container: str) -> FileSystem:
            assert container == "container"
            return FileSystem()

    azure = ModuleType("azure")
    azure.__path__ = []
    identity = ModuleType("azure.identity")
    identity.DefaultAzureCredential = lambda: "credential"
    storage = ModuleType("azure.storage")
    storage.__path__ = []
    filedatalake = ModuleType("azure.storage.filedatalake")
    filedatalake.DataLakeServiceClient = Service
    monkeypatch.setitem(
        sys.modules,
        "azure",
        azure,
    )
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.storage", storage)
    monkeypatch.setitem(sys.modules, "azure.storage.filedatalake", filedatalake)

    source = tmp_path / "bundle.json"
    source.write_bytes(b"bundle")

    _copy_to_cloud([source], "abfss://container@account.dfs.core.windows.net/prefix")

    assert uploaded == [("prefix/bundle.json", b"bundle", True)]


def test_copy_to_cloud_uses_documented_account_key_when_available(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class NamedKeyCredential:
        def __init__(self, account_name: str, account_key: str) -> None:
            captured["credential"] = (account_name, account_key)

    class FileClient:
        def upload_data(self, payload: bytes, *, overwrite: bool) -> None:
            captured["upload"] = (payload, overwrite)

    class FileSystem:
        def get_file_client(self, path: str) -> FileClient:
            captured["path"] = path
            return FileClient()

    class Service:
        def __init__(self, *, account_url: str, credential: object) -> None:
            captured["account_url"] = account_url
            captured["service_credential"] = credential

        def get_file_system_client(self, container: str) -> FileSystem:
            captured["container"] = container
            return FileSystem()

    azure = ModuleType("azure")
    azure.__path__ = []
    identity = ModuleType("azure.identity")
    identity.DefaultAzureCredential = lambda: pytest.fail("account-key auth should not use a default credential")
    core = ModuleType("azure.core")
    core.__path__ = []
    credentials = ModuleType("azure.core.credentials")
    credentials.AzureNamedKeyCredential = NamedKeyCredential
    storage = ModuleType("azure.storage")
    storage.__path__ = []
    filedatalake = ModuleType("azure.storage.filedatalake")
    filedatalake.DataLakeServiceClient = Service
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.core", core)
    monkeypatch.setitem(sys.modules, "azure.core.credentials", credentials)
    monkeypatch.setitem(sys.modules, "azure.storage", storage)
    monkeypatch.setitem(sys.modules, "azure.storage.filedatalake", filedatalake)
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "account")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "account-key")

    source = tmp_path / "bundle.json"
    source.write_bytes(b"bundle")
    _copy_to_cloud([source], "abfss://container@account.dfs.core.windows.net/prefix")

    assert captured["credential"] == ("account", "account-key")
    assert captured["account_url"] == "https://account.dfs.core.windows.net"


