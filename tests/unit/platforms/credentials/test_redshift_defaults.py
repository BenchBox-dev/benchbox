# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.credentials.redshift import setup_redshift_credentials

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestRedshiftCredentialDefaults:
    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_shows_existing_values_as_defaults(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "host": "my-cluster.abc123.us-east-1.redshift.amazonaws.com",
            "port": 5439,
            "database": "mydb",
            "username": "admin",
            "password": "secret",
            "schema": "public",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 5439
        mock_prompt_default.side_effect = [
            "my-cluster.abc123.us-east-1.redshift.amazonaws.com",
            "mydb",
            "admin",
            "public",
        ]
        mock_prompt_secure.return_value = "secret"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] == "my-cluster.abc123.us-east-1.redshift.amazonaws.com"
        assert calls[1][1]["current_value"] == "mydb"
        assert calls[2][1]["current_value"] == "admin"
        assert calls[3][1]["current_value"] == "public"

        mock_prompt_secure.assert_called_once_with("Password", current_value="secret", console=console)

    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_works_with_no_existing_credentials(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.side_effect = [
            False,
            False,
        ]
        mock_int_prompt.return_value = 5439
        mock_prompt_default.side_effect = [
            "new-cluster.xyz.us-east-1.redshift.amazonaws.com",
            "dev",
            "newuser",
            "public",
        ]
        mock_prompt_secure.return_value = "newpassword"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[0][1]["current_value"] is None
        assert calls[1][1]["current_value"] is None
        assert calls[1][1]["default_if_none"] == "dev"
        assert calls[2][1]["current_value"] is None
        assert calls[3][1]["current_value"] is None
        assert calls[3][1]["default_if_none"] == "public"

        mock_prompt_secure.assert_called_once_with("Password", current_value=None, console=console)

    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_password_preserved_on_empty_input(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
    ):

        mock_manager = Mock()
        existing_creds = {
            "host": "my-cluster.redshift.amazonaws.com",
            "port": 5439,
            "database": "mydb",
            "username": "admin",
            "password": "existing_password",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 5439
        mock_prompt_default.side_effect = [
            "my-cluster.redshift.amazonaws.com",
            "mydb",
            "admin",
            "public",
        ]
        mock_prompt_secure.return_value = "existing_password"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        saved_creds = mock_manager.set_platform_credentials.call_args[0][1]
        assert saved_creds["password"] == "existing_password"

    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_s3_credentials_show_existing_values(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
    ):
        mock_manager = Mock()
        existing_creds = {
            "host": "my-cluster.redshift.amazonaws.com",
            "port": 5439,
            "database": "mydb",
            "username": "admin",
            "password": "secret",
            "s3_bucket": "my-benchbox-data",
            "iam_role": "arn:aws:iam::123456789012:role/RedshiftS3AccessRole",
            "aws_region": "us-east-1",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = True
        mock_int_prompt.side_effect = [
            5439,
            1,
        ]
        mock_prompt_default.side_effect = [
            "my-cluster.redshift.amazonaws.com",
            "mydb",
            "admin",
            "public",
            "my-benchbox-data",
            "arn:aws:iam::123456789012:role/RedshiftS3AccessRole",
            "us-east-1",
        ]
        mock_prompt_secure.return_value = "secret"

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        calls = mock_prompt_default.call_args_list
        assert calls[4][1]["current_value"] == "my-benchbox-data"
        assert calls[5][1]["current_value"] == "arn:aws:iam::123456789012:role/RedshiftS3AccessRole"
        assert calls[6][1]["current_value"] == "us-east-1"

    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_s3_access_keys_show_existing_values(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
    ):
        mock_manager = Mock()
        existing_creds = {
            "host": "my-cluster.redshift.amazonaws.com",
            "port": 5439,
            "database": "mydb",
            "username": "admin",
            "password": "secret",
            "s3_bucket": "my-bucket",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "secret_key",
            "aws_region": "us-west-2",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = True
        mock_int_prompt.side_effect = [
            5439,
            2,
        ]
        mock_prompt_default.side_effect = [
            "my-cluster.redshift.amazonaws.com",
            "mydb",
            "admin",
            "public",
            "my-bucket",
            "AKIAIOSFODNN7EXAMPLE",
            "us-west-2",
        ]
        mock_prompt_secure.side_effect = [
            "secret",
            "secret_key",
        ]

        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        default_calls = mock_prompt_default.call_args_list
        assert default_calls[5][1]["current_value"] == "AKIAIOSFODNN7EXAMPLE"

        secure_calls = mock_prompt_secure.call_args_list
        assert secure_calls[1][0][0] == "AWS Secret Access Key"
        assert secure_calls[1][1]["current_value"] == "secret_key"

    @patch("benchbox.platforms.credentials.redshift._auto_detect_redshift")
    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_auto_detection_bypasses_existing_defaults(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        existing_creds = {
            "host": "old-cluster.redshift.amazonaws.com",
            "port": 5439,
            "database": "olddb",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 5439
        mock_prompt_default.side_effect = [
            "old-cluster.redshift.amazonaws.com",
            "olddb",
            "admin",
            "public",
        ]
        mock_prompt_secure.return_value = "password"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        assert mock_confirm.call_count == 1
        storage_call = mock_confirm.call_args_list[0]
        assert "s3" in str(storage_call).lower()
        mock_auto_detect.assert_not_called()

    @patch("benchbox.platforms.credentials.redshift._auto_detect_redshift")
    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_skips_auto_detection_when_credentials_exist(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        existing_creds = {
            "host": "my-cluster.redshift.amazonaws.com",
            "port": 5439,
            "database": "mydb",
            "username": "admin",
            "password": "secret",
        }
        mock_manager.get_platform_credentials.return_value = existing_creds

        mock_confirm.return_value = False
        mock_int_prompt.return_value = 5439
        mock_prompt_default.side_effect = [
            "my-cluster.redshift.amazonaws.com",
            "mydb",
            "admin",
            "public",
        ]
        mock_prompt_secure.return_value = "secret"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        assert mock_confirm.call_count == 1
        mock_auto_detect.assert_not_called()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" in console_output
        assert "updating configuration" in console_output

    @patch("benchbox.platforms.credentials.redshift._auto_detect_redshift")
    @patch("benchbox.platforms.credentials.redshift._prompt_default_output_location")
    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    @patch("benchbox.platforms.credentials.redshift.prompt_secure_field")
    @patch("benchbox.platforms.credentials.redshift.prompt_with_default")
    @patch("rich.prompt.IntPrompt.ask")
    @patch("rich.prompt.Confirm.ask")
    def test_offers_auto_detection_when_no_credentials_exist(
        self,
        mock_confirm,
        mock_int_prompt,
        mock_prompt_default,
        mock_prompt_secure,
        mock_validate,
        mock_output_location,
        mock_auto_detect,
    ):

        mock_manager = Mock()
        mock_manager.get_platform_credentials.return_value = None

        mock_confirm.side_effect = [False, False]
        mock_int_prompt.return_value = 5439
        mock_prompt_default.side_effect = [
            "new-cluster.redshift.amazonaws.com",
            "dev",
            "newuser",
            "public",
        ]
        mock_prompt_secure.return_value = "newpassword"
        mock_validate.return_value = (True, None)
        console = Mock()

        setup_redshift_credentials(mock_manager, console)

        auto_detect_call = mock_confirm.call_args_list[0]
        assert "auto-detection" in str(auto_detect_call).lower()

        console_output = " ".join(str(call) for call in console.print.call_args_list)
        assert "Existing credentials found" not in console_output


class TestAutoDetectRedshift:
    def _console(self):
        from unittest.mock import MagicMock

        return MagicMock()

    def test_returns_dict_when_all_required_vars_set(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        env = {
            "REDSHIFT_HOST": "cluster.abc123.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_redshift(self._console())

        assert result is not None
        assert result["host"] == "cluster.abc123.us-east-1.redshift.amazonaws.com"
        assert result["username"] == "admin"
        assert result["database"] == "dev"

    def test_returns_none_when_required_vars_missing(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        with patch.dict("os.environ", {}, clear=True):
            result = _auto_detect_redshift(self._console())

        assert result is None

    def test_port_defaults_to_5439_when_not_set(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        env = {
            "REDSHIFT_HOST": "cluster.example.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_redshift(self._console())

        assert result is not None
        assert result["port"] == 5439

    def test_port_parsed_from_env(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        env = {
            "REDSHIFT_HOST": "cluster.example.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_PORT": "5440",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_redshift(self._console())

        assert result is not None
        assert result["port"] == 5440

    def test_iam_role_captured_from_env(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        env = {
            "REDSHIFT_HOST": "cluster.example.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_IAM_ROLE": "arn:aws:iam::123456789:role/RedshiftS3Access",
        }
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_redshift(self._console())

        assert result is not None
        assert result["iam_role"] == "arn:aws:iam::123456789:role/RedshiftS3Access"

    def test_auto_detect_prints_success_message(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        env = {
            "REDSHIFT_HOST": "myhost.us-east-1.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "mydb",
        }
        console = MagicMock()
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_redshift(console)

        assert result is not None
        assert console.print.called
        output = " ".join(str(c) for c in console.print.call_args_list)
        assert "Found" in output or "✓" in output

    def test_auto_detect_prints_s3_message_when_s3_bucket_set(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        env = {
            "REDSHIFT_HOST": "cluster.example.redshift.amazonaws.com",
            "REDSHIFT_USERNAME": "admin",
            "REDSHIFT_PASSWORD": "secret",
            "REDSHIFT_DATABASE": "dev",
            "REDSHIFT_S3_BUCKET": "my-benchbox-bucket",
        }
        console = MagicMock()
        with patch.dict("os.environ", env, clear=True):
            result = _auto_detect_redshift(console)

        assert result is not None
        output = " ".join(str(c) for c in console.print.call_args_list)
        assert "S3" in output or "staging" in output.lower()


class TestTcpConnectivity:
    def test_returns_true_on_successful_connection(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _test_tcp_connectivity

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0

        with patch("benchbox.platforms.credentials.redshift.socket") as mock_socket_mod:
            mock_socket_mod.AF_INET = 2
            mock_socket_mod.SOCK_STREAM = 1
            mock_socket_mod.socket.return_value = mock_sock

            ok, err = _test_tcp_connectivity("host.example.com", 5439)

        assert ok is True
        assert err is None

    def test_returns_false_on_non_zero_connect_result(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _test_tcp_connectivity

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111

        with patch("benchbox.platforms.credentials.redshift.socket") as mock_socket_mod:
            mock_socket_mod.AF_INET = 2
            mock_socket_mod.SOCK_STREAM = 1
            mock_socket_mod.socket.return_value = mock_sock

            ok, err = _test_tcp_connectivity("host.example.com", 5439)

        assert ok is False
        assert err is not None
        assert "111" in err or "TCP" in err

    def test_returns_false_on_gaierror(self):
        import socket as real_socket
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _test_tcp_connectivity

        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = real_socket.gaierror("Name not found")

        with patch("benchbox.platforms.credentials.redshift.socket") as mock_socket_mod:
            mock_socket_mod.AF_INET = 2
            mock_socket_mod.SOCK_STREAM = 1
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.gaierror = real_socket.gaierror

            ok, err = _test_tcp_connectivity("no-such-host.invalid", 5439)

        assert ok is False
        assert err is not None

    def test_returns_false_on_timeout_error(self):
        import socket as real_socket
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _test_tcp_connectivity

        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = TimeoutError("timed out")

        with patch("benchbox.platforms.credentials.redshift.socket") as mock_socket_mod:
            mock_socket_mod.AF_INET = 2
            mock_socket_mod.SOCK_STREAM = 1
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.gaierror = real_socket.gaierror

            ok, err = _test_tcp_connectivity("host.example.com", 5439)

        assert ok is False
        assert err is not None
        assert "timeout" in err.lower() or "unreachable" in err.lower()


class TestDiagnoseRedshiftConnectivity:
    def test_provisioned_cluster_path(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _diagnose_redshift_connectivity

        mock_redshift_client = MagicMock()
        mock_redshift_client.describe_clusters.return_value = {
            "Clusters": [
                {
                    "PubliclyAccessible": True,
                    "VpcId": "vpc-abc123",
                    "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-111"}],
                    "ClusterSubnetGroupName": [],
                }
            ]
        }
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_redshift_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                host="mycluster.abc123.us-east-1.redshift.amazonaws.com",
                port=5439,
                aws_access_key_id=None,
                aws_secret_access_key=None,
                aws_region="us-east-1",
            )

        assert result["publicly_accessible"] is True
        assert result["vpc_id"] == "vpc-abc123"
        assert "sg-111" in result["security_group_ids"]

    def test_serverless_workgroup_path(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _diagnose_redshift_connectivity

        mock_serverless_client = MagicMock()
        mock_serverless_client.get_workgroup.return_value = {
            "workgroup": {
                "publiclyAccessible": False,
                "endpoint": {"vpcEndpoint": {"vpcId": "vpc-serverless"}},
                "securityGroupIds": ["sg-222"],
                "subnetIds": ["subnet-aaa"],
            }
        }
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_serverless_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                host="myworkgroup.123456789.us-east-1.redshift-serverless.amazonaws.com",
                port=5439,
                aws_access_key_id=None,
                aws_secret_access_key=None,
                aws_region="us-east-1",
            )

        assert result["publicly_accessible"] is False
        assert result["vpc_id"] == "vpc-serverless"
        assert "sg-222" in result["security_group_ids"]

    def test_access_denied_returns_diagnostic_error_not_exception(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _diagnose_redshift_connectivity

        mock_client = MagicMock()
        mock_client.describe_clusters.side_effect = Exception("AccessDenied")
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _diagnose_redshift_connectivity(
                host="cluster.abc123.us-east-1.redshift.amazonaws.com",
                port=5439,
                aws_access_key_id="AKIA",
                aws_secret_access_key="secret",
                aws_region="us-east-1",
            )

        assert result is not None
        assert result.get("error") is not None

    def test_unknown_endpoint_format_returns_error(self):

        from benchbox.platforms.credentials.redshift import _diagnose_redshift_connectivity

        result = _diagnose_redshift_connectivity(
            host="some.random.host.example.com",
            port=5439,
            aws_access_key_id=None,
            aws_secret_access_key=None,
            aws_region="us-east-1",
        )

        assert result["error"] == "Unknown endpoint format"


class TestFormatRemediationSteps:
    def test_publicly_accessible_false_includes_enable_public_access_step(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _format_remediation_steps

        console = MagicMock()
        diagnostics = {
            "workgroup_name": "my-workgroup",
            "cluster_id": None,
            "publicly_accessible": False,
            "vpc_id": "vpc-abc",
            "security_group_ids": ["sg-111"],
        }

        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value=None):
            _format_remediation_steps(console, "host.example.com", 5439, "us-east-1", diagnostics, False)

        output = " ".join(str(c) for c in console.print.call_args_list)
        assert "public" in output.lower() or "Enable" in output

    def test_publicly_accessible_true_omits_enable_public_access_step(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _format_remediation_steps

        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": "mycluster",
            "publicly_accessible": True,
            "vpc_id": "vpc-xyz",
            "security_group_ids": [],
        }

        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value="1.2.3.4"):
            _format_remediation_steps(console, "host.example.com", 5439, "us-east-1", diagnostics, True)

        output = " ".join(str(c) for c in console.print.call_args_list)
        assert "Troubleshooting" in output or "security" in output.lower() or "Configure" in output

    def test_publicly_not_accessible_provisioned_cluster_step(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import _format_remediation_steps

        console = MagicMock()
        diagnostics = {
            "workgroup_name": None,
            "cluster_id": "my-cluster-id",
            "publicly_accessible": False,
            "vpc_id": None,
            "security_group_ids": [],
        }

        with patch("benchbox.platforms.credentials.redshift._get_public_ip", return_value=None):
            _format_remediation_steps(console, "host.example.com", 5439, "us-east-1", diagnostics, False)

        output = " ".join(str(c) for c in console.print.call_args_list)
        assert "my-cluster-id" in output


class TestValidateRedshiftCredentials:
    def _make_cred_manager(self, creds=None):
        from unittest.mock import MagicMock

        mgr = MagicMock()
        mgr.get_platform_credentials.return_value = creds
        return mgr

    def test_returns_false_when_no_credentials(self):
        from benchbox.platforms.credentials.redshift import validate_redshift_credentials

        mgr = self._make_cred_manager(None)
        ok, err = validate_redshift_credentials(mgr)
        assert ok is False
        assert err is not None

    def test_returns_false_when_missing_required_fields(self):
        from benchbox.platforms.credentials.redshift import validate_redshift_credentials

        mgr = self._make_cred_manager({"host": "h"})
        ok, err = validate_redshift_credentials(mgr)
        assert ok is False
        assert "Missing required fields" in err

    def test_returns_false_on_tcp_failure(self):
        from unittest.mock import patch

        from benchbox.platforms.credentials.redshift import validate_redshift_credentials

        creds = {"host": "unreachable.example.com", "username": "u", "password": "p"}
        mgr = self._make_cred_manager(creds)

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=None),
            patch(
                "benchbox.platforms.credentials.redshift._test_tcp_connectivity",
                return_value=(False, "Connection timed out"),
            ),
        ):
            ok, err = validate_redshift_credentials(mgr)

        assert ok is False
        assert err is not None

    def test_returns_true_on_successful_probe(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import validate_redshift_credentials

        creds = {"host": "cluster.example.com", "username": "u", "password": "p", "database": "dev"}
        mgr = self._make_cred_manager(creds)

        mock_adapter = MagicMock()

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=mock_adapter),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch("benchbox.platforms.credentials.redshift._probe_redshift_connection", return_value=None),
        ):
            ok, err = validate_redshift_credentials(mgr)

        assert ok is True
        assert err is None

    def test_returns_false_on_probe_exception(self):
        from unittest.mock import MagicMock, patch

        from benchbox.platforms.credentials.redshift import validate_redshift_credentials

        creds = {"host": "cluster.example.com", "username": "u", "password": "p", "database": "dev"}
        mgr = self._make_cred_manager(creds)

        mock_adapter = MagicMock()

        with (
            patch("benchbox.platforms.credentials.redshift._build_redshift_adapter", return_value=mock_adapter),
            patch("benchbox.platforms.credentials.redshift._test_tcp_connectivity", return_value=(True, None)),
            patch(
                "benchbox.platforms.credentials.redshift._probe_redshift_connection",
                side_effect=RuntimeError("auth error"),
            ),
        ):
            ok, err = validate_redshift_credentials(mgr)

        assert ok is False
        assert err is not None


