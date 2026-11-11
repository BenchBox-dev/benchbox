from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.azure import _credentials as credentials_module
from benchbox.platforms.azure._credentials import AzureTokenProvider

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _token(value: str, expires_at: float) -> MagicMock:
    tok = MagicMock()
    tok.token = value
    tok.expires_on = expires_at
    return tok


def _provider_with_mock_cred(
    scope: str = "https://x/.default", tenant_id: str | None = None
) -> tuple[AzureTokenProvider, MagicMock]:
    cred_class = MagicMock(name="DefaultAzureCredential")
    provider = AzureTokenProvider(
        scope=scope,
        credential_class=cred_class,
        tenant_id=tenant_id,
    )
    return provider, cred_class


class TestCredentialConstruction:
    def test_without_tenant_id_passes_no_kwargs(self) -> None:
        provider, cred_class = _provider_with_mock_cred()
        provider.credential()
        cred_class.assert_called_once_with()

    def test_with_tenant_id_passes_additionally_allowed_tenants(self) -> None:
        provider, cred_class = _provider_with_mock_cred(tenant_id="abc-123")
        provider.credential()
        cred_class.assert_called_once_with(additionally_allowed_tenants=["*"])

    def test_credential_is_cached(self) -> None:
        provider, cred_class = _provider_with_mock_cred()
        first = provider.credential()
        second = provider.credential()
        assert first is second
        cred_class.assert_called_once()


class TestAccessTokenCaching:
    def test_fetches_token_first_call(self) -> None:
        provider, cred_class = _provider_with_mock_cred()
        cred_class.return_value.get_token.return_value = _token("tok-A", expires_at=9999999999)
        assert provider.access_token() == "tok-A"
        cred_class.return_value.get_token.assert_called_once_with("https://x/.default")

    def test_reuses_cached_token_when_fresh(self) -> None:
        provider, cred_class = _provider_with_mock_cred()
        cred_class.return_value.get_token.return_value = _token("tok-A", expires_at=9999999999)
        provider.access_token()
        provider.access_token()
        assert cred_class.return_value.get_token.call_count == 1

    def test_refreshes_when_within_buffer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider, cred_class = _provider_with_mock_cred()
        cred_class.return_value.get_token.side_effect = [
            _token("tok-A", expires_at=1000),
            _token("tok-B", expires_at=9999999999),
        ]
        now = {"value": 500}
        monkeypatch.setattr(credentials_module, "time", SimpleNamespace(time=lambda: now["value"]))
        assert provider.access_token() == "tok-A"

        now["value"] = 800
        assert provider.access_token() == "tok-B"
        assert cred_class.return_value.get_token.call_count == 2


class TestAuthHeaders:
    def test_returns_bearer_and_json_content_type(self) -> None:
        provider, cred_class = _provider_with_mock_cred()
        cred_class.return_value.get_token.return_value = _token("tok-X", expires_at=9999999999)
        headers = provider.auth_headers()
        assert headers == {
            "Authorization": "Bearer tok-X",
            "Content-Type": "application/json",
        }


