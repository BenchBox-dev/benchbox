from __future__ import annotations

import pytest

from benchbox.mcp.telemetry import TelemetrySettings

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_otlp_endpoint_may_not_embed_credentials() -> None:
    with pytest.raises(ValueError, match="without embedded credentials"):
        TelemetrySettings.from_env({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://user:secret@otel.example/v1/traces"})


def test_standard_otlp_endpoint_is_accepted() -> None:
    settings = TelemetrySettings.from_env(
        {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://otel.example/v1/traces", "OTEL_SERVICE_NAME": "ignored-secret"}
    )
    assert settings.endpoint == "https://otel.example/v1/traces"
    assert settings.service_name == "benchbox-mcp"


def test_generic_otlp_endpoint_appends_trace_signal_path() -> None:
    settings = TelemetrySettings.from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example/collector/"})

    assert settings.endpoint == "https://otel.example/collector/v1/traces"


def test_empty_trace_specific_endpoint_falls_back_to_generic() -> None:
    settings = TelemetrySettings.from_env(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example/collector/",
        }
    )

    assert settings.endpoint == "https://otel.example/collector/v1/traces"


def test_trace_specific_endpoint_takes_precedence_without_rewriting() -> None:
    settings = TelemetrySettings.from_env(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example/custom",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://generic.example",
        }
    )

    assert settings.endpoint == "https://traces.example/custom"


