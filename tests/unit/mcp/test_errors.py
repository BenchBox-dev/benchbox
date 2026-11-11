# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.mcp.errors import (
    ERROR_CATEGORIES,
    ErrorCategory,
    ErrorCode,
    MCPError,
    make_error,
    make_execution_error,
    make_not_found_error,
    make_platform_error,
    make_validation_error,
    scrub_secret_material,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestErrorCode:
    def test_validation_error_codes_exist(self):
        assert ErrorCode.VALIDATION_ERROR.value == "VALIDATION_ERROR"
        assert ErrorCode.VALIDATION_UNKNOWN_PLATFORM.value == "VALIDATION_UNKNOWN_PLATFORM"
        assert ErrorCode.VALIDATION_UNKNOWN_BENCHMARK.value == "VALIDATION_UNKNOWN_BENCHMARK"
        assert ErrorCode.VALIDATION_INVALID_SCALE_FACTOR.value == "VALIDATION_INVALID_SCALE_FACTOR"

    def test_platform_error_codes_exist(self):
        assert ErrorCode.PLATFORM_UNAVAILABLE.value == "PLATFORM_UNAVAILABLE"
        assert ErrorCode.PLATFORM_DEPENDENCIES_MISSING.value == "PLATFORM_DEPENDENCIES_MISSING"
        assert ErrorCode.PLATFORM_CREDENTIALS_MISSING.value == "PLATFORM_CREDENTIALS_MISSING"

    def test_benchmark_error_codes_exist(self):
        assert ErrorCode.BENCHMARK_EXECUTION_FAILED.value == "BENCHMARK_EXECUTION_FAILED"
        assert ErrorCode.BENCHMARK_DATA_GENERATION_FAILED.value == "BENCHMARK_DATA_GENERATION_FAILED"
        assert ErrorCode.BENCHMARK_QUERY_FAILED.value == "BENCHMARK_QUERY_FAILED"

    def test_resource_error_codes_exist(self):
        assert ErrorCode.RESOURCE_NOT_FOUND.value == "RESOURCE_NOT_FOUND"
        assert ErrorCode.RESOURCE_INVALID_FORMAT.value == "RESOURCE_INVALID_FORMAT"

    def test_internal_error_codes_exist(self):
        assert ErrorCode.INTERNAL_ERROR.value == "INTERNAL_ERROR"
        assert ErrorCode.INTERNAL_TIMEOUT.value == "INTERNAL_TIMEOUT"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("password: 'hunter2'", "password: ****"),
        ('token: "secret-value"', "token: ****"),
        ("password is hunter2", "password is ****"),
        ("token was abc123", "token was ****"),
    ],
)
def test_scrub_secret_material_covers_quoted_colon_and_prose_values(text: str, expected: str) -> None:
    assert scrub_secret_material(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("secret sauce is the default", "secret sauce is the default"),
        ("token refresh required", "token refresh required"),
        ("password reset required", "password reset required"),
        ("password field is missing", "password field is missing"),
        ("password reset required for user", "password reset required for user"),
        ("password is hunter2", "password is ****"),
        ("token was abc123", "token was ****"),
        ("secret equals REALVAL", "secret equals ****"),
        ("password set to xyz", "password set to ****"),
        ("token configured as ABCDEF", "token configured as ****"),
        ("password: 'hunter2'", "password: ****"),
        ('token: "secret-value"', "token: ****"),
        ("password=REAL_SECRET", "password=****"),
        ("driver returned password PROSE_SECRET", "driver returned password ****"),
        ("driver returned password hunter2", "driver returned password ****"),
        ("secret sauce and password=REAL_SECRET", "secret sauce and password=****"),
        ("token refresh required; password=REAL_SECRET", "token refresh required; password=****"),
    ],
)
def test_scrub_secret_material_prose_precision(text: str, expected: str) -> None:
    assert scrub_secret_material(text) == expected


@pytest.mark.parametrize(
    ("text", "expected_message"),
    [
        ("secret sauce is the default", "secret sauce is the default"),
        ("token refresh required", "token refresh required"),
        ("password reset required", "password reset required"),
        ("password field is missing", "password field is missing"),
        ("password=REAL_SECRET", "password=****"),
        (
            "token refresh required; password=REAL_SECRET",
            "token refresh required; password=****",
        ),
    ],
)
def test_make_execution_error_prose_precision(text: str, expected_message: str) -> None:
    result = make_execution_error(text)
    assert result["message"] == expected_message
    assert "REAL_SECRET" not in result["message"]


class TestErrorCategory:
    def test_categories_defined(self):
        assert ErrorCategory.CLIENT.value == "client"
        assert ErrorCategory.PLATFORM.value == "platform"
        assert ErrorCategory.EXECUTION.value == "execution"
        assert ErrorCategory.SERVER.value == "server"


class TestErrorCategories:
    def test_validation_errors_are_client_category(self):
        assert ERROR_CATEGORIES[ErrorCode.VALIDATION_ERROR] == ErrorCategory.CLIENT
        assert ERROR_CATEGORIES[ErrorCode.VALIDATION_UNKNOWN_PLATFORM] == ErrorCategory.CLIENT
        assert ERROR_CATEGORIES[ErrorCode.VALIDATION_UNKNOWN_BENCHMARK] == ErrorCategory.CLIENT

    def test_platform_errors_are_platform_category(self):
        assert ERROR_CATEGORIES[ErrorCode.PLATFORM_UNAVAILABLE] == ErrorCategory.PLATFORM
        assert ERROR_CATEGORIES[ErrorCode.PLATFORM_DEPENDENCIES_MISSING] == ErrorCategory.PLATFORM

    def test_benchmark_errors_are_execution_category(self):
        assert ERROR_CATEGORIES[ErrorCode.BENCHMARK_EXECUTION_FAILED] == ErrorCategory.EXECUTION
        assert ERROR_CATEGORIES[ErrorCode.BENCHMARK_QUERY_FAILED] == ErrorCategory.EXECUTION

    def test_internal_errors_are_server_category(self):
        assert ERROR_CATEGORIES[ErrorCode.INTERNAL_ERROR] == ErrorCategory.SERVER
        assert ERROR_CATEGORIES[ErrorCode.INTERNAL_TIMEOUT] == ErrorCategory.SERVER


class TestMCPError:
    def test_basic_error_creation(self):
        error = MCPError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Test error message",
        )
        assert error.code == ErrorCode.VALIDATION_ERROR
        assert error.message == "Test error message"
        assert error.details == {}
        assert error.suggestion is None
        assert error.retry_hint is False

    def test_error_with_all_fields(self):
        error = MCPError(
            code=ErrorCode.PLATFORM_CONNECTION_FAILED,
            message="Connection failed",
            details={"host": "localhost", "port": 5432},
            suggestion="Check database is running",
            retry_hint=True,
        )
        assert error.code == ErrorCode.PLATFORM_CONNECTION_FAILED
        assert error.details == {"host": "localhost", "port": 5432}
        assert error.suggestion == "Check database is running"
        assert error.retry_hint is True

    def test_category_property(self):
        error = MCPError(code=ErrorCode.VALIDATION_ERROR, message="test")
        assert error.category == ErrorCategory.CLIENT

        error = MCPError(code=ErrorCode.INTERNAL_ERROR, message="test")
        assert error.category == ErrorCategory.SERVER

    def test_to_dict_basic(self):
        error = MCPError(
            code=ErrorCode.VALIDATION_UNKNOWN_PLATFORM,
            message="Unknown platform: xyz",
        )
        result = error.to_dict()

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_UNKNOWN_PLATFORM"
        assert result["error_category"] == "client"
        assert result["message"] == "Unknown platform: xyz"
        assert "details" not in result
        assert "suggestion" not in result
        assert "retry_hint" not in result

    def test_to_dict_with_all_fields(self):
        error = MCPError(
            code=ErrorCode.INTERNAL_TIMEOUT,
            message="Operation timed out",
            details={"timeout_seconds": 30},
            suggestion="Try a smaller scale factor",
            retry_hint=True,
        )
        result = error.to_dict()

        assert result["error"] is True
        assert result["error_code"] == "INTERNAL_TIMEOUT"
        assert result["error_category"] == "server"
        assert result["message"] == "Operation timed out"
        assert result["details"] == {"timeout_seconds": 30}
        assert result["suggestion"] == "Try a smaller scale factor"
        assert result["retry_hint"] is True


class TestMakeError:
    def test_basic_error(self):
        result = make_error(
            ErrorCode.VALIDATION_ERROR,
            "Invalid input",
        )
        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_ERROR"
        assert result["message"] == "Invalid input"

    def test_error_with_details(self):
        result = make_error(
            ErrorCode.RESOURCE_NOT_FOUND,
            "File not found",
            details={"file": "test.json"},
        )
        assert result["details"] == {"file": "test.json"}

    def test_error_with_suggestion(self):
        result = make_error(
            ErrorCode.PLATFORM_UNAVAILABLE,
            "DuckDB not available",
            suggestion="Install with: pip install duckdb",
        )
        assert result["suggestion"] == "Install with: pip install duckdb"

    def test_error_with_retry_hint(self):
        result = make_error(
            ErrorCode.INTERNAL_TIMEOUT,
            "Timeout",
            retry_hint=True,
        )
        assert result["retry_hint"] is True


class TestMakeValidationError:
    def test_basic_validation_error(self):
        result = make_validation_error("Invalid scale factor")
        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_ERROR"
        assert result["error_category"] == "client"
        assert result["message"] == "Invalid scale factor"

    def test_validation_error_with_details(self):
        result = make_validation_error(
            "Scale factor out of range",
            details={"value": -1, "valid_range": "0.001 to 10000"},
            suggestion="Use a positive scale factor",
        )
        assert result["details"]["value"] == -1
        assert result["suggestion"] == "Use a positive scale factor"


class TestMakeNotFoundError:
    def test_basic_not_found_error(self):
        result = make_not_found_error("benchmark", "xyz")
        assert result["error"] is True
        assert result["error_code"] == "RESOURCE_NOT_FOUND"
        assert result["message"] == "Benchmark 'xyz' not found"
        assert result["details"]["resource_type"] == "benchmark"
        assert result["details"]["requested"] == "xyz"

    def test_not_found_error_with_available(self):
        result = make_not_found_error(
            "platform",
            "invalid",
            available=["duckdb", "polars", "sqlite"],
        )
        assert result["details"]["available"] == ["duckdb", "polars", "sqlite"]
        assert result["suggestion"] == "Use list_platforms() to see available options"


class TestMakePlatformError:
    def test_basic_platform_error(self):
        result = make_platform_error(
            ErrorCode.PLATFORM_UNAVAILABLE,
            "snowflake",
            "Snowflake dependencies not installed",
        )
        assert result["error"] is True
        assert result["error_code"] == "PLATFORM_UNAVAILABLE"
        assert result["details"]["platform"] == "snowflake"

    def test_platform_error_with_installation(self):
        result = make_platform_error(
            ErrorCode.PLATFORM_DEPENDENCIES_MISSING,
            "clickhouse",
            "ClickHouse driver not installed",
            installation_command="pip install clickhouse-driver",
        )
        assert result["suggestion"] == "pip install clickhouse-driver"


class TestExceptionSecretScrubbing:
    def test_secret_assignment_is_scrubbed(self):
        result = make_execution_error("failed", exception=Exception("motherduck_token=SENT-123 during ATTACH"))
        msg = result["details"]["exception_message"]
        assert "SENT-123" not in msg
        assert "motherduck_token=****" in msg

    def test_url_userinfo_is_scrubbed(self):
        result = make_execution_error(
            "failed", exception=Exception("connect databend://joe:PW-SENT@db.example.com:8000 refused")
        )
        msg = result["details"]["exception_message"]
        assert "PW-SENT" not in msg
        assert "joe" not in msg
        assert "://****@db.example.com" in msg

    def test_password_colon_assignment_is_scrubbed(self):
        result = make_execution_error("failed", exception=Exception("bad config: password: hunter2"))
        assert "hunter2" not in result["details"]["exception_message"]

    def test_quoted_secret_assignments_are_scrubbed(self):
        result = make_execution_error("failed", exception=Exception("PASSWORD = 'hunter2'; SECRET = \"token\""))
        msg = result["details"]["exception_message"]
        assert "hunter2" not in msg
        assert "token" not in msg

    def test_json_secret_assignments_are_scrubbed(self):
        result = make_execution_error(
            "failed",
            exception=Exception('{"password": "JSON_SECRET", "token": "JSON_TOKEN"}'),
        )
        msg = result["details"]["exception_message"]
        assert "JSON_SECRET" not in msg
        assert "JSON_TOKEN" not in msg
        assert '"password": "****"' in msg
        assert '"token": "****"' in msg

    def test_prose_secret_values_are_scrubbed(self):
        result = make_execution_error("failed", exception=Exception("driver returned password PROSE_SECRET"))
        assert "PROSE_SECRET" not in result["details"]["exception_message"]

    def test_non_secret_secret_key_prose_is_untouched(self):
        result = make_execution_error(
            "failed",
            exception=Exception("password: field is missing; token expired while connecting"),
        )
        assert result["details"]["exception_message"] == "password: field is missing; token expired while connecting"

    def test_top_level_message_is_scrubbed(self):
        result = make_execution_error("Benchmark execution failed: password=hunter2")
        assert result["message"] == "Benchmark execution failed: password=****"

    def test_plain_text_is_untouched(self):
        result = make_execution_error("failed", exception=Exception("table lineitem not found"))
        assert result["details"]["exception_message"] == "table lineitem not found"

    @pytest.mark.parametrize(
        ("text", "sentinel"),
        [
            ("dsn=DSN_GATE", "DSN_GATE"),
            ("DSN=DSN_GATE", "DSN_GATE"),
            ("dsn='DSN_GATE'", "DSN_GATE"),
            ('dsn="DSN_GATE"', "DSN_GATE"),
            ("connection_string=CONNECTION_GATE", "CONNECTION_GATE"),
            ("CONNECTION_STRING=CONNECTION_GATE", "CONNECTION_GATE"),
            ("connection-string=CONNECTION_GATE", "CONNECTION_GATE"),
            ("private_key=PRIVATE_GATE", "PRIVATE_GATE"),
            ("Private_Key=PRIVATE_GATE", "PRIVATE_GATE"),
            ("private-key=PRIVATE_GATE", "PRIVATE_GATE"),
            ("sas=SAS_GATE", "SAS_GATE"),
            ("SAS=SAS_GATE", "SAS_GATE"),
            ("pat=PAT_GATE", "PAT_GATE"),
            ("PAT=PAT_GATE", "PAT_GATE"),
            ("password=EXISTING_SECRET", "EXISTING_SECRET"),
        ],
    )
    def test_credential_assignment_vocabulary_is_scrubbed(self, text: str, sentinel: str) -> None:
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        assert sentinel not in blob
        assert sentinel not in result["message"]
        assert sentinel not in result["details"]["exception_message"]
        assert "****" in result["message"]
        assert "****" in result["details"]["exception_message"]

    def test_pat_does_not_match_path_assignment(self) -> None:
        text = "path=/var/tmp/data failed to open"
        result = make_execution_error(text, exception=Exception(text))
        assert result["details"]["exception_message"] == text
        assert result["message"] == text

    def test_combined_credential_assignment_vocabulary_is_scrubbed(self) -> None:
        text = "dsn=DSN_GATE private_key=PK_GATE sas=SAS_GATE pat=PAT_GATE connection_string=CS_GATE"
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        for sentinel in ("DSN_GATE", "PK_GATE", "SAS_GATE", "PAT_GATE", "CS_GATE"):
            assert sentinel not in blob

    @pytest.mark.parametrize(
        "text",
        [
            "sas=sv=2020-08-04&sig=SIG_GATE",
            "SAS=sv=2020-08-04&ss=b&srt=sco&sig=SIG_GATE",
            "storage_sas_token=sv=2020-08-04&sig=SIG_GATE",
            "storage-sas-token=sv=2020-08-04&sig=SIG_GATE",
        ],
    )
    def test_sas_querystring_assignment_masks_ampersand_segments(self, text: str) -> None:
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        assert "SIG_GATE" not in blob
        assert "sv=2020-08-04" not in blob
        assert "****" in result["message"]
        assert "****" in result["details"]["exception_message"]

    def test_unquoted_multiline_private_key_pem_is_scrubbed(self) -> None:
        pem_label = " ".join(("RSA", "PRIVATE", "KEY"))
        begin = f"-----BEGIN {pem_label}-----"
        end = f"-----END {pem_label}-----"
        body = "MIIE_GATE_BLOB"
        text = f"load failed private_key={begin}\n{body}\nmoreBase64Body==\n{end} after"
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        assert body not in blob
        assert "moreBase64Body" not in blob
        assert begin not in blob
        assert "private_key=****" in result["message"]
        assert "private_key=****" in result["details"]["exception_message"]
        assert "after" in result["message"]

    def test_unquoted_multiline_private_key_base64_continuation_is_scrubbed(self) -> None:
        body = "MIIE_GATE_BLOB"
        text = f"private_key={body}\ncontinuedBase64Line\nnot_part_of_key=1"
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        assert body not in blob
        assert "continuedBase64Line" not in blob
        assert "private_key=****" in result["details"]["exception_message"]

    def test_truncated_pem_without_end_is_scrubbed(self) -> None:
        pem_label = " ".join(("RSA", "PRIVATE", "KEY"))
        begin = f"-----BEGIN {pem_label}-----"
        body = "MIIE_GATE_BLOB"
        text = f"private_key={begin}\n{body}\nmoreBase64Body=="
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        assert body not in blob
        assert "moreBase64Body" not in blob
        assert begin not in blob
        assert "private_key=****" in result["details"]["exception_message"]

    def test_crlf_private_key_base64_continuation_is_scrubbed(self) -> None:
        body = "MIIE_GATE_BLOB"
        text = f"private_key={body}\r\ncontinuedBase64Line\r\nnot_part_of_key=1"
        result = make_execution_error(text, exception=Exception(text))
        blob = str(result)
        assert body not in blob
        assert "continuedBase64Line" not in blob
        assert "private_key=****" in result["details"]["exception_message"]


class TestMakeExecutionError:
    def test_basic_execution_error(self):
        result = make_execution_error("Benchmark failed")
        assert result["error"] is True
        assert result["error_code"] == "BENCHMARK_EXECUTION_FAILED"
        assert result["message"] == "Benchmark failed"

    def test_execution_error_with_execution_id(self):
        result = make_execution_error(
            "Query timeout",
            execution_id="mcp_abc123",
        )
        assert result["details"]["execution_id"] == "mcp_abc123"

    def test_execution_error_with_exception(self):
        exc = ValueError("Invalid configuration")
        result = make_execution_error(
            "Configuration error",
            exception=exc,
        )
        assert result["details"]["exception_type"] == "ValueError"
        assert result["details"]["exception_message"] == "Invalid configuration"

    def test_execution_error_with_retry_hint(self):
        result = make_execution_error(
            "Transient failure",
            retry_hint=True,
        )
        assert result["retry_hint"] is True


