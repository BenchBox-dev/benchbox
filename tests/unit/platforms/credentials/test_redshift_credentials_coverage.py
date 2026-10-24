# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import os
import socket
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.credentials.redshift import (
    _auto_detect_redshift,
    _build_redshift_adapter,
    _diagnose_redshift_connectivity,
    _format_diagnostic_output,
    _format_redshift_validation_error,
    _format_remediation_steps,
    _get_public_ip,
    _is_retryable_error,
    _is_timeout_error,
    _probe_redshift_connection,
    _test_tcp_connectivity,
    validate_redshift_credentials,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _console_output(console: MagicMock) -> str:

    return " ".join(str(c) for c in console.print.call_args_list)


def _empty_diagnostics() -> dict:

    return {
        "error": None,
        "workgroup_name": None,
        "cluster_id": None,
        "publicly_accessible": None,
        "vpc_id": None,
        "security_group_ids": [],
    }


class TestIsTimeoutError:
    def test_timeout_keyword(self) -> None:
        assert _is_timeout_error("connection timeout exceeded") is True

    def test_timed_out_keyword(self) -> None:
        assert _is_timeout_error("operation timed out") is True

    def test_case_insensitive(self) -> None:
        assert _is_timeout_error("TIMEOUT error") is True

    def test_non_timeout_error(self) -> None:
        assert _is_timeout_error("authentication failed") is False

    def test_empty_string(self) -> None:
        assert _is_timeout_error("") is False


class TestIsRetryableError:
    def test_timeout_is_retryable(self) -> None:
        assert _is_retryable_error("connection timeout") is True

    def test_timed_out_is_retryable(self) -> None:
        assert _is_retryable_error("timed out connecting") is True

    def test_could_not_connect_is_retryable(self) -> None:
        assert _is_retryable_error("could not connect to server") is True

    def test_connection_refused_is_retryable(self) -> None:
        assert _is_retryable_error("connection refused") is True

    def test_auth_error_not_retryable(self) -> None:
        assert _is_retryable_error("password authentication failed") is False

    def test_cluster_not_found_not_retryable(self) -> None:
        assert _is_retryable_error("cluster not found") is False


class TestFormatRedshiftValidationError:
    def test_timeout_error(self) -> None:
        msg = _format_redshift_validation_error("connection timeout", "dev")
        assert "timeout" in msg.lower()
        assert "VPC" in msg or "security group" in msg

    def test_could_not_connect(self) -> None:
        msg = _format_redshift_validation_error("could not connect to host", "dev")
        assert "refused" in msg.lower() or "connect" in msg.lower()

    def test_connection_refused(self) -> None:
        msg = _format_redshift_validation_error("connection refused", "dev")
        assert "connection refused" in msg.lower()

    def test_password_auth_failed(self) -> None:
        msg = _format_redshift_validation_error("password authentication failed for user", "dev")
        assert "authentication" in msg.lower() or "password" in msg.lower()

    def test_authentication_error(self) -> None:
        msg = _format_redshift_validation_error("authentication error occurred", "dev")
        assert "authentication" in msg.lower()

    def test_cluster_not_found(self) -> None:
        msg = _format_redshift_validation_error("cluster not found in region", "dev")
        assert "cluster" in msg.lower() or "invalid" in msg.lower()

    def test_database_does_not_exist(self) -> None:
        msg = _format_redshift_validation_error("database mydb does not exist", "mydb")
        assert "mydb" in msg

    def test_ssl_refused(self) -> None:
        msg = _format_redshift_validation_error("server refuses ssl connection", "dev")
        assert "SSL" in msg or "ssl" in msg.lower()

    def test_generic_error_includes_message(self) -> None:
        msg = _format_redshift_validation_error("some unexpected driver error", "dev")
        assert "some unexpected driver error" in msg


class TestAutoDetectRedshift:
    def test_all_required_env_vars_present(self) -> None:
        console = MagicMock()
        env = {
            "REDSHIFT_HOST": "my-cluster.abc.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
        }
        with patch.dict(os.environ, env, clear=False):
            result = _auto_detect_redshift(console)

        assert result is not None
        assert result["host"] == "my-cluster.abc.us-east-1.redshift.amazonaws.com"
        assert result["username"] == "admin"
        assert result["password"] == "secret"
        assert result["database"] == "dev"
        assert result["port"] == 5439

    def test_port_converted_to_int(self) -> None:
        console = MagicMock()
        env = {
            "REDSHIFT_HOST": "my-cluster.abc.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_PORT": "5440",
        }
        with patch.dict(os.environ, env, clear=False):
            result = _auto_detect_redshift(console)

        assert result is not None
        assert result["port"] == 5440

    def test_invalid_port_falls_back_to_default(self) -> None:
        console = MagicMock()
        env = {
            "REDSHIFT_HOST": "my-cluster.abc.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_PORT": "not-a-number",
        }
        with patch.dict(os.environ, env, clear=False):
            result = _auto_detect_redshift(console)

        assert result is not None
        assert result["port"] == 5439

    def test_missing_host_returns_none(self) -> None:
        console = MagicMock()
        env = {
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
        }
        with patch.dict(os.environ, env, clear=True):
            result = _auto_detect_redshift(console)

        assert result is None
        console.print.assert_called()

    def test_missing_required_vars_prints_warning(self) -> None:
        console = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            result = _auto_detect_redshift(console)

        assert result is None

        output = _console_output(console)
        assert "Missing" in output or "REDSHIFT_HOST" in output

    def test_s3_bucket_detected_prints_message(self) -> None:
        console = MagicMock()
        env = {
            "REDSHIFT_HOST": "my-cluster.abc.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_S3_BUCKET": "my-bucket",
        }
        with patch.dict(os.environ, env, clear=True):
            result = _auto_detect_redshift(console)

        assert result is not None
        assert result["s3_bucket"] == "my-bucket"
        output = _console_output(console)
        assert "S3" in output or "staging" in output.lower()

    def test_optional_s3_vars_included_when_present(self) -> None:
        console = MagicMock()
        env = {
            "REDSHIFT_HOST": "my-cluster.abc.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_IAM_ROLE": "arn:aws:iam::123:role/MyRole",
            "AWS_ACCESS_KEY_ID": "AKID",
            "AWS_SECRET_ACCESS_KEY": "SAK",
            "AWS_DEFAULT_REGION": "eu-west-1",
        }
        with patch.dict(os.environ, env, clear=True):
            result = _auto_detect_redshift(console)

        assert result is not None
        assert result["iam_role"] == "arn:aws:iam::123:role/MyRole"
        assert result["aws_access_key_id"] == "AKID"
        assert result["aws_secret_access_key"] == "SAK"
        assert result["aws_region"] == "eu-west-1"


class TestTcpConnectivity:
    def test_success_when_connect_returns_zero(self) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0

        with patch("benchbox.platforms.credentials.redshift.socket.socket", return_value=mock_sock):
            success, error = _test_tcp_connectivity("my-cluster.redshift.amazonaws.com", 5439)

        assert success is True
        assert error is None
        mock_sock.close.assert_called_once()

    def test_failure_when_connect_returns_nonzero(self) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111

        with patch("benchbox.platforms.credentials.redshift.socket.socket", return_value=mock_sock):
            success, error = _test_tcp_connectivity("my-cluster.redshift.amazonaws.com", 5439)

        assert success is False
        assert "111" in error

    def test_gaierror_returns_dns_failure(self) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = socket.gaierror("Name or service not known")

        with patch("benchbox.platforms.credentials.redshift.socket.socket", return_value=mock_sock):
            success, error = _test_tcp_connectivity("nonexistent.host.invalid", 5439)

        assert success is False
        assert "DNS" in error

    def test_timeout_error_returns_timeout_message(self) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = TimeoutError("timed out")

        with patch("benchbox.platforms.credentials.redshift.socket.socket", return_value=mock_sock):
            success, error = _test_tcp_connectivity("my-cluster.redshift.amazonaws.com", 5439, timeout=1)

        assert success is False
        assert "timeout" in error.lower()

    def test_generic_exception_returns_network_error(self) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = OSError("Some network error")

        with patch("benchbox.platforms.credentials.redshift.socket.socket", return_value=mock_sock):
            success, error = _test_tcp_connectivity("my-cluster.redshift.amazonaws.com", 5439)

        assert success is False
        assert "Network error" in error


class TestGetPublicIp:
    def test_returns_ip_when_first_service_succeeds(self) -> None:
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"1.2.3.4"

        with patch("urllib.request.urlopen", return_value=mock_response):
            ip = _get_public_ip()

        assert ip == "1.2.3.4"

    def test_falls_through_to_second_service_on_failure(self) -> None:
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"5.6.7.8"

        def urlopen_side_effect(url, timeout=None, context=None):
            if "ipify" in url:
                raise OSError("service down")
            return mock_response

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            ip = _get_public_ip()

        assert ip == "5.6.7.8"

    def test_returns_none_when_all_services_fail(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("all services down")):
            ip = _get_public_ip()

        assert ip is None

    def test_returns_none_on_empty_response(self) -> None:
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b""

        with patch("urllib.request.urlopen", return_value=mock_response):
            ip = _get_public_ip()

        assert ip is None


class TestDiagnoseRedshiftConnectivity:
    def test_serverless_workgroup_endpoint_parsed(self) -> None:
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_workgroup.return_value = {
            "workgroup": {
                "publiclyAccessible": True,
                "endpoint": {"vpcEndpoint": {"vpcId": "vpc-123"}},
                "securityGroupIds": ["sg-abc"],
                "subnetIds": ["subnet-1", "subnet-2"],
            }
        }

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                "my-workgroup.abc.us-east-1.redshift-serverless.amazonaws.com",
                5439,
                None,
                None,
                "us-east-1",
            )

        assert result["workgroup_name"] == "my-workgroup"
        assert result["publicly_accessible"] is True
        assert result["vpc_id"] == "vpc-123"
        assert result["security_group_ids"] == ["sg-abc"]
        assert result["subnet_ids"] == ["subnet-1", "subnet-2"]

    def test_provisioned_cluster_endpoint_parsed(self) -> None:
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.describe_clusters.return_value = {
            "Clusters": [
                {
                    "PubliclyAccessible": False,
                    "VpcId": "vpc-456",
                    "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-xyz"}],
                    "ClusterSubnetGroupName": [],
                }
            ]
        }

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                "my-cluster.abc.us-east-1.redshift.amazonaws.com",
                5439,
                "AKID",
                "SAK",
                "us-east-1",
            )

        assert result.get("cluster_id") == "my-cluster"
        assert result["publicly_accessible"] is False
        assert result["vpc_id"] == "vpc-456"
        assert result["security_group_ids"] == ["sg-xyz"]

    def test_unknown_endpoint_format_returns_error(self) -> None:
        result = _diagnose_redshift_connectivity(
            "unknown-host.example.com",
            5439,
            None,
            None,
            "us-east-1",
        )
        assert result["error"] == "Unknown endpoint format"

    def test_boto3_not_available_returns_error(self) -> None:
        import builtins
        import sys

        real_import = builtins.__import__

        def no_boto3(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("No module named 'boto3'")
            return real_import(name, *args, **kwargs)

        original = sys.modules.pop("boto3", None)
        try:
            with patch("builtins.__import__", side_effect=no_boto3):
                result = _diagnose_redshift_connectivity(
                    "my-workgroup.abc.us-east-1.redshift-serverless.amazonaws.com",
                    5439,
                    None,
                    None,
                    "us-east-1",
                )
        finally:
            if original is not None:
                sys.modules["boto3"] = original

        assert result["error"] == "boto3 not available for diagnostics"

    def test_aws_api_error_stored_in_diagnostics(self) -> None:
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_workgroup.side_effect = Exception("Access Denied")

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                "my-workgroup.abc.us-east-1.redshift-serverless.amazonaws.com",
                5439,
                None,
                None,
                "us-east-1",
            )

        assert result["error"] is not None
        assert "Access Denied" in result["error"]

    def test_credentials_passed_to_client_when_provided(self) -> None:
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.describe_clusters.return_value = {
            "Clusters": [
                {"PubliclyAccessible": True, "VpcId": None, "VpcSecurityGroups": [], "ClusterSubnetGroupName": []}
            ]
        }

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            _diagnose_redshift_connectivity(
                "my-cluster.abc.us-east-1.redshift.amazonaws.com",
                5439,
                "MY_KEY_ID",
                "MY_SECRET",
                "us-west-2",
            )

        call_kwargs = mock_boto3.client.call_args[1]
        assert call_kwargs.get("aws_access_key_id") == "MY_KEY_ID"
        assert call_kwargs.get("aws_secret_access_key") == "MY_SECRET"

    def test_serverless_without_endpoint_in_workgroup(self) -> None:
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_workgroup.return_value = {
            "workgroup": {
                "publiclyAccessible": False,
            }
        }

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                "wg.abc.us-east-1.redshift-serverless.amazonaws.com",
                5439,
                None,
                None,
                "us-east-1",
            )

        assert result["publicly_accessible"] is False
        assert result["vpc_id"] is None
        assert result["security_group_ids"] == []
        assert result["subnet_ids"] == []


class TestFormatDiagnosticOutput:
    @pytest.fixture(autouse=True)
    def _no_public_ip(self) -> None:
        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value=None):
            yield

    def test_shows_workgroup_name(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": "my-wg",
            "cluster_id": None,
            "publicly_accessible": True,
            "vpc_id": None,
            "security_group_ids": [],
            "error": None,
        }
        _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "my-wg" in output

    def test_shows_cluster_id(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": "my-cluster",
            "publicly_accessible": False,
            "vpc_id": "vpc-123",
            "security_group_ids": ["sg-abc"],
            "error": None,
        }
        _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "my-cluster" in output

    def test_shows_raw_endpoint_when_no_cluster_or_workgroup(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": [],
            "error": None,
        }
        _format_diagnostic_output(console, "my-host.com", 5440, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "my-host.com" in output

    def test_publicly_accessible_true(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": "wg",
            "publicly_accessible": True,
            "vpc_id": None,
            "security_group_ids": [],
            "error": None,
        }
        _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "Yes" in output or "✓" in output

    def test_publicly_accessible_false(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": "wg",
            "publicly_accessible": False,
            "vpc_id": None,
            "security_group_ids": [],
            "error": None,
        }
        _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "No" in output or "✗" in output

    def test_shows_vpc_info(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "publicly_accessible": None,
            "vpc_id": "vpc-999",
            "security_group_ids": ["sg-111", "sg-222"],
            "error": None,
        }
        _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "vpc-999" in output
        assert "sg-111" in output

    def test_shows_error_when_aws_api_failed(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": [],
            "error": "boto3 not available for diagnostics",
        }
        _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "boto3" in output

    def test_shows_public_ip_when_available(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": [],
            "error": None,
        }
        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value="9.10.11.12"):
            _format_diagnostic_output(console, "endpoint", 5439, "us-east-1", diagnostics)
        output = _console_output(console)
        assert "9.10.11.12" in output


class TestFormatRemediationSteps:
    @pytest.fixture(autouse=True)
    def _no_public_ip(self) -> None:
        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value=None):
            yield

    def test_publicly_not_accessible_serverless_workgroup(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": "my-wg",
            "cluster_id": None,
            "publicly_accessible": False,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "public" in output.lower()
        assert "my-wg" in output

    def test_publicly_not_accessible_provisioned_cluster(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": "my-cluster",
            "publicly_accessible": False,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "my-cluster" in output

    def test_publicly_not_accessible_no_cluster_or_workgroup(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": False,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "AWS Console" in output

    def test_step_numbering_starts_at_1_when_accessible(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": "wg",
            "cluster_id": None,
            "publicly_accessible": True,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)

        assert "1. Configure" in output or "1." in output

    def test_security_group_ids_listed(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": ["sg-abc123"],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "sg-abc123" in output

    def test_no_security_groups_shows_generic_nav(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "eu-west-1", diagnostics, True)
        output = _console_output(console)
        assert "Security Group" in output or "security group" in output.lower()

    def test_vpc_id_step_shown(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": None,
            "vpc_id": "vpc-77777",
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "vpc-77777" in output
        assert "Internet Gateway" in output

    def test_publicly_not_accessible_without_vpc_shows_igw_step(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": False,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "Internet Gateway" in output

    def test_user_ip_shown_when_available(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": [],
        }
        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value="10.0.0.1"):
            _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "10.0.0.1" in output

    def test_no_ip_shows_placeholder(self) -> None:
        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": None,
            "publicly_accessible": None,
            "vpc_id": None,
            "security_group_ids": [],
        }
        _format_remediation_steps(console, "endpoint", 5439, "us-east-1", diagnostics, False)
        output = _console_output(console)
        assert "your-ip" in output or "<your-ip>" in output


class TestBuildRedshiftAdapter:
    def test_builds_adapter_with_required_creds(self) -> None:
        creds = {
            "host": "my-cluster.abc.redshift.amazonaws.com",
            "port": 5439,
            "database": "dev",
            "username": "admin",
            "password": "secret",
        }
        mock_adapter_cls = MagicMock()
        mock_adapter_cls.return_value = MagicMock()

        mock_module = MagicMock()
        mock_module.RedshiftAdapter = mock_adapter_cls

        with patch.dict("sys.modules", {"benchbox.platforms.redshift": mock_module}):
            adapter = _build_redshift_adapter(creds)

        mock_adapter_cls.assert_called_once()
        assert adapter is mock_adapter_cls.return_value

    def test_only_known_keys_passed_to_adapter(self) -> None:
        creds = {
            "host": "my-cluster.abc.redshift.amazonaws.com",
            "port": 5439,
            "database": "dev",
            "username": "admin",
            "password": "secret",
            "unknown_extra_key": "should-be-ignored",
        }
        mock_adapter_cls = MagicMock()
        mock_adapter_cls.return_value = MagicMock()
        mock_module = MagicMock()
        mock_module.RedshiftAdapter = mock_adapter_cls

        with patch.dict("sys.modules", {"benchbox.platforms.redshift": mock_module}):
            _build_redshift_adapter(creds)

        call_kwargs = mock_adapter_cls.call_args[1]
        assert "unknown_extra_key" not in call_kwargs
        assert "host" in call_kwargs
        assert "password" in call_kwargs


class TestProbeRedshiftConnection:
    def test_executes_smoke_queries_and_closes(self) -> None:
        mock_cursor = MagicMock()
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_adapter = MagicMock()
        mock_adapter._create_direct_connection.return_value = mock_connection

        _probe_redshift_connection(mock_adapter, database="dev", connect_timeout=10)

        assert mock_cursor.execute.call_count == 3
        assert mock_cursor.fetchall.call_count == 3
        mock_cursor.close.assert_called_once()
        mock_connection.close.assert_called_once()

    def test_closes_cursor_and_connection_on_exception(self) -> None:
        mock_cursor = MagicMock()
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("query failed")
        mock_adapter = MagicMock()
        mock_adapter._create_direct_connection.return_value = mock_connection

        with pytest.raises(Exception, match="query failed"):
            _probe_redshift_connection(mock_adapter, database="dev", connect_timeout=10)

        mock_cursor.close.assert_called_once()
        mock_connection.close.assert_called_once()

    def test_suppresses_close_errors(self) -> None:
        mock_cursor = MagicMock()
        mock_cursor.close.side_effect = Exception("close failed")
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.close.side_effect = Exception("conn close failed")
        mock_cursor.execute.side_effect = Exception("query failed")
        mock_adapter = MagicMock()
        mock_adapter._create_direct_connection.return_value = mock_connection

        with pytest.raises(Exception, match="query failed"):
            _probe_redshift_connection(mock_adapter, database="dev", connect_timeout=10)


class TestValidateRedshiftCredentials:
    @pytest.fixture(autouse=True)
    def _no_sleep(self) -> None:

        with patch("benchbox.platforms.credentials.redshift.time.sleep"):
            yield

    def test_no_credentials_returns_false(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = None

        success, error = validate_redshift_credentials(mock_manager)

        assert success is False
        assert "No credentials" in error

    def test_missing_required_fields_returns_false(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {"host": "my-cluster.redshift.amazonaws.com"}

        success, error = validate_redshift_credentials(mock_manager)

        assert success is False
        assert "Missing required fields" in error

    def test_import_error_on_adapter_build_returns_false(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        with patch(
            "benchbox.platforms.credentials.redshift._build_redshift_adapter", side_effect=ImportError("no module")
        ):
            success, error = validate_redshift_credentials(mock_manager)

        assert success is False
        assert "no module" in error

    def test_tcp_failure_returns_timeout_error(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(False, "TCP error")),
            patch(
                "benchbox.platforms.credentials.redshift._diagnose_redshift_connectivity",
                return_value=_empty_diagnostics(),
            ),
            patch("benchbox.platforms.credentials.redshift._format_diagnostic_output"),
            patch("benchbox.platforms.credentials.redshift._format_remediation_steps"),
        ):
            console = MagicMock()
            success, error = validate_redshift_credentials(mock_manager, console)

        assert success is False
        assert "timeout" in error.lower() or "connectivity" in error.lower()

    def test_tcp_failure_without_console_no_diagnostics(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(False, "TCP error")),
        ):
            success, error = validate_redshift_credentials(mock_manager)

        assert success is False

    def test_first_attempt_success(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch("benchbox.platforms.credentials.redshift._probe_redshift_connection"),
        ):
            success, error = validate_redshift_credentials(mock_manager)

        assert success is True
        assert error is None

    def test_retry_on_transient_error_succeeds_on_second_attempt(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        call_count = 0

        def probe_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("connection timeout")

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch("benchbox.platforms.credentials.redshift._probe_redshift_connection", side_effect=probe_side_effect),
        ):
            success, error = validate_redshift_credentials(mock_manager)

        assert success is True
        assert call_count == 2

    def test_all_retries_exhausted_returns_false(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch(
                "benchbox.platforms.credentials.redshift._probe_redshift_connection",
                side_effect=Exception("connection timeout"),
            ),
            patch(
                "benchbox.platforms.credentials.redshift._diagnose_redshift_connectivity",
                return_value=_empty_diagnostics(),
            ),
            patch("benchbox.platforms.credentials.redshift._format_diagnostic_output"),
            patch("benchbox.platforms.credentials.redshift._format_remediation_steps"),
        ):
            console = MagicMock()
            success, error = validate_redshift_credentials(mock_manager, console)

        assert success is False
        assert error is not None

    def test_non_retryable_error_fails_immediately(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        call_count = 0

        def probe_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("password authentication failed")

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch("benchbox.platforms.credentials.redshift._probe_redshift_connection", side_effect=probe_side_effect),
            patch(
                "benchbox.platforms.credentials.redshift._diagnose_redshift_connectivity",
                return_value=_empty_diagnostics(),
            ),
            patch("benchbox.platforms.credentials.redshift._format_diagnostic_output"),
            patch("benchbox.platforms.credentials.redshift._format_remediation_steps"),
        ):
            success, error = validate_redshift_credentials(mock_manager)

        assert success is False
        assert call_count == 1

    def test_retry_shows_attempt_message_with_console(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_platform_credentials.return_value = {
            "host": "my-cluster.redshift.amazonaws.com",
            "username": "admin",
            "password": "secret",
        }

        call_count = 0

        def probe_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("connection timeout")

        console = MagicMock()

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=MagicMock()),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch("benchbox.platforms.credentials.redshift._probe_redshift_connection", side_effect=probe_side_effect),
        ):
            success, error = validate_redshift_credentials(mock_manager, console)

        assert success is True
        output = _console_output(console)
        assert "Retry" in output or "retry" in output
