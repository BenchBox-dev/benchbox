# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
import os
import socket
import time
from typing import Any, Optional, Union

from rich.console import Console
from rich.prompt import Confirm, IntPrompt

from benchbox.platforms.credentials.helpers import prompt_secure_field, prompt_with_default
from benchbox.security.credentials import CredentialManager, CredentialStatus
from benchbox.utils.printing import QuietConsoleProxy


def _resolve_auto_config(existing_creds: Optional[dict], console: Union[Console, QuietConsoleProxy]) -> Optional[dict]:
    if existing_creds:
        console.print("ℹ️  [cyan]Existing credentials found - updating configuration[/cyan]\n")
        return None

    try_auto = Confirm.ask("🔍 Attempt auto-detection from environment variables?", default=True)
    if not try_auto:
        return None

    console.print("\n[dim]Checking environment variables...[/dim]")
    return _auto_detect_redshift(console)


def _config_from_auto(auto_config: dict, console: Union[Console, QuietConsoleProxy]) -> dict:
    console.print(f"\n✅ Found cluster endpoint: [cyan]{auto_config.get('host')}[/cyan]")
    console.print(f"✅ Found port: [cyan]{auto_config.get('port')}[/cyan]")
    console.print(f"✅ Found database: [cyan]{auto_config.get('database')}[/cyan]")
    console.print(f"✅ Found username: [cyan]{auto_config.get('username')}[/cyan]")
    if auto_config.get("schema"):
        console.print(f"✅ Found schema: [cyan]{auto_config['schema']}[/cyan]")
    if auto_config.get("s3_bucket"):
        console.print(f"✅ Found S3 bucket: [cyan]{auto_config['s3_bucket']}[/cyan]")
    if auto_config.get("iam_role"):
        console.print(f"✅ Found IAM role: [cyan]{auto_config['iam_role']}[/cyan]")
    return dict(auto_config)


def _prompt_core_credentials(
    console: Union[Console, QuietConsoleProxy], existing_creds: Optional[dict]
) -> Optional[dict]:
    console.print("\n[bold]Redshift Cluster Configuration:[/bold]")
    existing = existing_creds or {}

    host = prompt_with_default(
        "Cluster endpoint (e.g., my-cluster.abc123.us-east-1.redshift.amazonaws.com)",
        current_value=existing.get("host"),
    )
    if not host:
        console.print("[red]❌ Cluster endpoint is required[/red]")
        return None

    current_port = existing.get("port")
    if current_port:
        port = IntPrompt.ask(f"Port (current: {current_port})", default=current_port)
    else:
        port = IntPrompt.ask("Port", default=5439)

    database = prompt_with_default("Database name", current_value=existing.get("database"), default_if_none="dev")
    if not database:
        console.print("[red]❌ Database name is required[/red]")
        return None

    username = prompt_with_default("Username", current_value=existing.get("username"))
    if not username:
        console.print("[red]❌ Username is required[/red]")
        return None

    password = prompt_secure_field("Password", current_value=existing.get("password"), console=console)
    if not password:
        console.print("[red]❌ Password is required[/red]")
        return None

    console.print("\n[bold]Optional Settings:[/bold]")
    schema = prompt_with_default("Schema name", current_value=existing.get("schema"), default_if_none="public")

    return {
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password": password,
        "schema": schema,
    }


def _prompt_s3_staging(console: Union[Console, QuietConsoleProxy], existing_creds: Optional[dict]) -> dict:
    empty = {
        "s3_bucket": None,
        "iam_role": None,
        "aws_access_key_id": None,
        "aws_secret_access_key": None,
        "aws_region": None,
    }

    console.print("\n[bold]S3 Staging Configuration (Recommended):[/bold]")
    console.print("[dim]Configuring S3 staging enables efficient data loading via COPY commands.[/dim]")
    console.print("[dim]Without S3, data loading will be slower using direct INSERT statements.[/dim]\n")

    if not Confirm.ask("Configure S3 staging for efficient data loading?", default=True):
        return empty

    existing = existing_creds or {}
    s3_bucket = prompt_with_default(
        "S3 bucket name (e.g., my-benchbox-data)", current_value=existing.get("s3_bucket"), default_if_none=""
    )
    if not s3_bucket:
        return empty

    console.print("\n[bold]S3 Authentication Method:[/bold]")
    console.print("1. IAM Role ARN (recommended for EC2/ECS)")
    console.print("2. AWS Access Keys\n")

    default_method = 1 if existing.get("iam_role") else (2 if existing.get("aws_access_key_id") else 1)
    auth_method = IntPrompt.ask("Choose authentication method [1-2]", default=default_method)

    if auth_method == 1:
        iam_role = prompt_with_default(
            "IAM Role ARN (e.g., arn:aws:iam::123456789012:role/RedshiftS3AccessRole)",
            current_value=existing.get("iam_role"),
            default_if_none="",
        )
        aws_access_key_id = None
        aws_secret_access_key = None
    else:
        iam_role = None
        aws_access_key_id = prompt_with_default(
            "AWS Access Key ID", current_value=existing.get("aws_access_key_id"), default_if_none=""
        )
        aws_secret_access_key = prompt_secure_field(
            "AWS Secret Access Key", current_value=existing.get("aws_secret_access_key"), console=console
        )

    aws_region = prompt_with_default(
        "AWS Region", current_value=existing.get("aws_region"), default_if_none="us-east-1"
    )

    return {
        "s3_bucket": s3_bucket,
        "iam_role": iam_role,
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region": aws_region,
    }


def _build_credentials_dict(config: dict) -> dict:
    credentials: dict[str, Any] = {
        "host": config["host"],
        "port": config["port"],
        "database": config["database"],
        "username": config["username"],
        "password": config["password"],
    }
    for optional_key in ("schema", "s3_bucket", "iam_role", "aws_access_key_id", "aws_secret_access_key", "aws_region"):
        value = config.get(optional_key)
        if value:
            credentials[optional_key] = value
    return credentials


def _finalize_credentials(
    cred_manager: CredentialManager,
    console: Union[Console, QuietConsoleProxy],
    credentials: dict,
) -> None:
    console.print("\n🧪 [bold]Validating credentials...[/bold]")
    cred_manager.set_platform_credentials("redshift", credentials, CredentialStatus.NOT_VALIDATED)

    success, error = validate_redshift_credentials(cred_manager, console)
    s3_bucket = credentials.get("s3_bucket")

    if success:
        cred_manager.update_validation_status("redshift", CredentialStatus.VALID)
        cred_manager.save_credentials()

        console.print("\n[green]✅ Redshift credentials validated and saved![/green]")
        console.print(f"   Location: [cyan]{cred_manager.credentials_path}[/cyan]")
        console.print("   Status: [green]Ready to use[/green]\n")

        if s3_bucket:
            console.print("[green]✅ S3 staging configured for efficient data loading[/green]\n")
        else:
            console.print(
                "[yellow]⚠️  No S3 staging configured - data loading will use direct INSERT (slower)[/yellow]\n"
            )

        _prompt_default_output_location(cred_manager, console, credentials, s3_bucket)

        console.print("[bold]Try it:[/bold]")
        console.print("  benchbox run --platform redshift --benchmark tpch --scale 0.01")
        return

    cred_manager.update_validation_status("redshift", CredentialStatus.INVALID, error)
    cred_manager.save_credentials()

    console.print("\n[red]❌ Validation failed[/red]")
    if error:
        console.print(f"   Error: {error}")
    console.print("\n[yellow]Credentials saved but marked as invalid.[/yellow]")
    console.print("Fix the issues and run: benchbox setup --platform redshift --validate-only")


def setup_redshift_credentials(cred_manager: CredentialManager, console: Union[Console, QuietConsoleProxy]) -> None:
    console.print("\n📋 [bold]You'll need:[/bold]")
    console.print("  • Redshift cluster endpoint")
    console.print("  • Username and password")
    console.print("  • Database name (default: dev)\n")

    console.print(
        "[dim]Need help? Visit: https://docs.aws.amazon.com/redshift/latest/mgmt/connecting-to-cluster.html[/dim]\n"
    )

    existing_creds = cred_manager.get_platform_credentials("redshift")
    auto_config = _resolve_auto_config(existing_creds, console)

    if auto_config:
        config = _config_from_auto(auto_config, console)
    else:
        core = _prompt_core_credentials(console, existing_creds)
        if core is None:
            return
        config = {**core, **_prompt_s3_staging(console, existing_creds)}

    credentials = _build_credentials_dict(config)
    _finalize_credentials(cred_manager, console, credentials)


def _prompt_default_output_location(
    cred_manager: CredentialManager,
    console: Union[Console, QuietConsoleProxy],
    credentials: dict,
    s3_bucket: Optional[str],
) -> None:
    from benchbox.platforms.credentials.shared import prompt_default_output_location

    prompt_default_output_location(
        cred_manager=cred_manager,
        console=console,
        credentials=credentials,
        platform_name="Redshift",
        path_scheme="s3://",
        storage_label="S3 Staging Location",
        permission_note="the cluster IAM role has s3:PutObject permission",
        bucket=s3_bucket,
    )


_REDSHIFT_ADAPTER_CREDENTIAL_KEYS = (
    "host",
    "port",
    "database",
    "username",
    "password",
    "schema",
    "s3_bucket",
    "s3_prefix",
    "staging_root",
    "iam_role",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_region",
    "cluster_identifier",
    "admin_database",
    "connect_timeout",
    "statement_timeout",
    "sslmode",
    "ssl_enabled",
    "ssl_insecure",
    "sslrootcert",
    "wlm_query_slot_count",
    "wlm_query_queue_name",
    "wlm_config",
    "compupdate",
    "auto_vacuum",
    "auto_analyze",
)


def _build_redshift_adapter(creds: dict[str, Any]) -> Any:
    from benchbox.platforms.redshift import RedshiftAdapter

    adapter_kwargs = {key: creds[key] for key in _REDSHIFT_ADAPTER_CREDENTIAL_KEYS if key in creds}
    return RedshiftAdapter(**adapter_kwargs)


def _probe_redshift_connection(adapter: Any, *, database: str, connect_timeout: int) -> None:
    connection = None
    cursor = None

    try:
        connection = adapter._create_direct_connection(database=database, connect_timeout=connect_timeout)
        cursor = connection.cursor()

        cursor.execute("SELECT 1")
        cursor.fetchall()

        cursor.execute("SELECT version()")
        cursor.fetchall()

        cursor.execute("SELECT current_database()")
        cursor.fetchall()
    finally:
        if cursor:
            with contextlib.suppress(Exception):
                cursor.close()
        if connection:
            with contextlib.suppress(Exception):
                connection.close()


def _is_timeout_error(error_msg: str) -> bool:
    error_lower = error_msg.lower()
    return "timeout" in error_lower or "timed out" in error_lower


def _is_retryable_error(error_msg: str) -> bool:
    error_lower = error_msg.lower()
    return _is_timeout_error(error_msg) or "could not connect" in error_lower or "connection refused" in error_lower


def _format_redshift_validation_error(error_msg: str, database: str) -> str:
    error_lower = error_msg.lower()

    if _is_timeout_error(error_msg):
        return "Connection timeout. Check VPC/security group settings and network connectivity."
    if "could not connect" in error_lower or "connection refused" in error_lower:
        return "Connection refused. Check cluster endpoint, VPC/security group settings, and network connectivity."
    if "password authentication failed" in error_lower or "authentication" in error_lower:
        return "Authentication failed. Check your username and password."
    if "cluster" in error_lower and "not found" in error_lower:
        return "Cluster endpoint is invalid or cluster does not exist."
    if "database" in error_lower and "does not exist" in error_lower:
        return f"Database '{database}' not found. It will be created during benchmark setup."
    if "server refuses ssl" in error_lower:
        return "SSL negotiation failed. Check that the endpoint is a Redshift endpoint that accepts TLS on port 5439."

    return f"Connection failed: {error_msg}"


def validate_redshift_credentials(
    cred_manager: CredentialManager, console: Optional[Union[Console, QuietConsoleProxy]] = None
) -> tuple[bool, Optional[str]]:
    creds = cred_manager.get_platform_credentials("redshift")

    if not creds:
        return False, "No credentials found"

    required_fields = ["host", "username", "password"]
    missing = [field for field in required_fields if not creds.get(field)]

    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    host = creds["host"]
    port = creds.get("port", 5439)
    database = creds.get("database", "dev")
    aws_access_key_id = creds.get("aws_access_key_id")
    aws_secret_access_key = creds.get("aws_secret_access_key")
    aws_region = creds.get("aws_region", "us-east-1")

    try:
        adapter = _build_redshift_adapter(creds)
    except ImportError as e:
        return False, str(e)

    if console:
        console.print("\n[dim]Testing network connectivity...[/dim]")

    tcp_reachable, tcp_error = _test_tcp_connectivity(host, port, timeout=10)

    if not tcp_reachable:
        if console:
            console.print(f"[yellow]⚠️  Network connectivity issue: {tcp_error}[/yellow]")

            diagnostics = _diagnose_redshift_connectivity(
                host, port, aws_access_key_id, aws_secret_access_key, aws_region
            )

            _format_diagnostic_output(console, host, port, aws_region, diagnostics)

            _format_remediation_steps(console, host, port, aws_region, diagnostics, tcp_reachable)

        return False, "Connection timeout. Check VPC/security group settings and network connectivity."

    if console:
        console.print("[dim]Network connectivity OK. Testing database connection...[/dim]")

    max_retries = 3
    retry_delays = [0, 2, 5]

    last_error = None

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                if console:
                    console.print(f"[dim]Retry attempt {attempt + 1}/{max_retries}...[/dim]")
                time.sleep(retry_delays[attempt])

            timeout = 10 + (attempt * 10)

            _probe_redshift_connection(adapter, database=database, connect_timeout=timeout)
            return True, None

        except Exception as e:
            last_error = e

            if attempt < max_retries - 1 and _is_retryable_error(str(e)):
                continue

            break

    error_msg = str(last_error)

    if console:
        diagnostics = _diagnose_redshift_connectivity(host, port, aws_access_key_id, aws_secret_access_key, aws_region)
        _format_diagnostic_output(console, host, port, aws_region, diagnostics)
        _format_remediation_steps(console, host, port, aws_region, diagnostics, tcp_reachable)

    return False, _format_redshift_validation_error(error_msg, database)


def _auto_detect_redshift(console: Union[Console, QuietConsoleProxy]) -> Optional[dict]:
    env_vars = {
        "host": os.getenv("REDSHIFT_HOST"),
        "port": os.getenv("REDSHIFT_PORT"),
        "database": os.getenv("REDSHIFT_DATABASE"),
        "username": os.getenv("REDSHIFT_USERNAME"),
        "password": os.getenv("REDSHIFT_PASSWORD"),
        "schema": os.getenv("REDSHIFT_SCHEMA"),
        "s3_bucket": os.getenv("REDSHIFT_S3_BUCKET"),
        "iam_role": os.getenv("REDSHIFT_IAM_ROLE"),
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "aws_region": os.getenv("AWS_DEFAULT_REGION"),
    }

    required = ["host", "username", "password", "database"]
    found_required = all(env_vars.get(field) for field in required)

    if not found_required:
        missing = [f"REDSHIFT_{field.upper()}" for field in required if not env_vars.get(field)]
        console.print(f"  ⚠️  Missing environment variables: {', '.join(missing)}")
        return None

    if env_vars.get("port"):
        try:
            env_vars["port"] = int(env_vars["port"])
        except ValueError:
            env_vars["port"] = 5439
    else:
        env_vars["port"] = 5439

    console.print("  ✓ Found all required environment variables")

    if env_vars.get("s3_bucket"):
        console.print("  ✓ Found S3 staging configuration")

    return env_vars


def _test_tcp_connectivity(host: str, port: int, timeout: int = 5) -> tuple[bool, Optional[str]]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            return True, None
        else:
            return False, f"Cannot establish TCP connection (error code: {result})"

    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"
    except TimeoutError:
        return False, "TCP connection timeout - host unreachable or port blocked"
    except Exception as e:
        return False, f"Network error: {e}"


def _get_public_ip() -> Optional[str]:
    try:
        from benchbox.utils import tls

        services = [
            "https://api.ipify.org",
            "https://checkip.amazonaws.com",
            "https://icanhazip.com",
        ]

        for service in services:
            try:
                with tls.urlopen(service, timeout=3) as response:
                    ip = response.read().decode("utf-8").strip()
                    if ip:
                        return ip
            except Exception:
                continue

        return None
    except Exception:
        return None


def _diagnose_redshift_connectivity(
    host: str, port: int, aws_access_key_id: Optional[str], aws_secret_access_key: Optional[str], aws_region: str
) -> dict:
    diagnostics = {
        "workgroup_name": None,
        "publicly_accessible": None,
        "vpc_id": None,
        "subnet_ids": [],
        "security_group_ids": [],
        "error": None,
    }

    try:
        if ".redshift-serverless.amazonaws.com" in host:
            workgroup_name = host.split(".")[0]
            diagnostics["workgroup_name"] = workgroup_name
        elif ".redshift.amazonaws.com" in host:
            cluster_id = host.split(".")[0]
            diagnostics["cluster_id"] = cluster_id
            diagnostics["is_serverless"] = False
        else:
            diagnostics["error"] = "Unknown endpoint format"
            return diagnostics

        try:
            import boto3
        except ImportError:
            diagnostics["error"] = "boto3 not available for diagnostics"
            return diagnostics

        client_kwargs = {"region_name": aws_region}
        if aws_access_key_id and aws_secret_access_key:
            client_kwargs["aws_access_key_id"] = aws_access_key_id
            client_kwargs["aws_secret_access_key"] = aws_secret_access_key

        try:
            if diagnostics.get("is_serverless") is False:
                redshift_client = boto3.client("redshift", **client_kwargs)
                response = redshift_client.describe_clusters(ClusterIdentifier=diagnostics["cluster_id"])
                cluster = response["Clusters"][0]

                diagnostics["publicly_accessible"] = cluster.get("PubliclyAccessible", False)
                diagnostics["vpc_id"] = cluster.get("VpcId")
                diagnostics["subnet_ids"] = [sg["SubnetIdentifier"] for sg in cluster.get("ClusterSubnetGroupName", [])]
                diagnostics["security_group_ids"] = [
                    sg["VpcSecurityGroupId"] for sg in cluster.get("VpcSecurityGroups", [])
                ]

            else:
                redshift_serverless_client = boto3.client("redshift-serverless", **client_kwargs)
                response = redshift_serverless_client.get_workgroup(workgroupName=workgroup_name)
                workgroup = response["workgroup"]

                diagnostics["publicly_accessible"] = workgroup.get("publiclyAccessible", False)

                if "endpoint" in workgroup:
                    endpoint = workgroup["endpoint"]
                    diagnostics["vpc_id"] = endpoint.get("vpcEndpoint", {}).get("vpcId")

                if "securityGroupIds" in workgroup:
                    diagnostics["security_group_ids"] = workgroup["securityGroupIds"]

                if "subnetIds" in workgroup:
                    diagnostics["subnet_ids"] = workgroup["subnetIds"]

        except Exception as e:
            diagnostics["error"] = f"AWS API error: {str(e)}"

    except Exception as e:
        diagnostics["error"] = f"Diagnostic error: {str(e)}"

    return diagnostics


def _format_diagnostic_output(
    console: Union[Console, QuietConsoleProxy], host: str, port: int, aws_region: str, diagnostics: dict
) -> None:
    console.print("\n[bold cyan]🔍 Connection Diagnostics[/bold cyan]\n")

    if diagnostics.get("workgroup_name"):
        console.print(f"  • Workgroup: [cyan]{diagnostics['workgroup_name']}[/cyan] ({aws_region})")
    elif diagnostics.get("cluster_id"):
        console.print(f"  • Cluster: [cyan]{diagnostics['cluster_id']}[/cyan] ({aws_region})")
    else:
        console.print(f"  • Endpoint: [cyan]{host}:{port}[/cyan]")

    if diagnostics.get("publicly_accessible") is not None:
        if diagnostics["publicly_accessible"]:
            console.print("  • Publicly Accessible: [green]✓ Yes[/green]")
        else:
            console.print("  • Publicly Accessible: [red]✗ No[/red]")

    user_ip = _get_public_ip()
    if user_ip:
        console.print(f"  • Your Public IP: [cyan]{user_ip}[/cyan]")

    if diagnostics.get("vpc_id"):
        console.print(f"  • VPC: {diagnostics['vpc_id']}")
    if diagnostics.get("security_group_ids"):
        console.print(f"  • Security Groups: {', '.join(diagnostics['security_group_ids'])}")

    if diagnostics.get("error"):
        console.print(f"\n[dim]Note: {diagnostics['error']}[/dim]")


def _format_remediation_steps(
    console: Union[Console, QuietConsoleProxy],
    host: str,
    port: int,
    aws_region: str,
    diagnostics: dict,
    tcp_reachable: bool,
) -> None:
    console.print("\n[bold yellow]📋 Troubleshooting Steps[/bold yellow]\n")

    workgroup_name = diagnostics.get("workgroup_name")
    cluster_id = diagnostics.get("cluster_id")
    publicly_accessible = diagnostics.get("publicly_accessible")
    user_ip = _get_public_ip()

    if publicly_accessible is False:
        console.print("[bold]1. Enable public access to your Redshift workgroup/cluster[/bold]")

        if workgroup_name:
            console_url = f"https://console.aws.amazon.com/redshiftv2/home?region={aws_region}#serverless-workgroup-configuration?workgroup={workgroup_name}"
            console.print(f"   → Open AWS Console: [link={console_url}]{console_url}[/link]")
            console.print("   → Click 'Actions' → 'Edit'")
            console.print("   → Under 'Network and security', enable 'Turn on Publicly accessible'")
            console.print("   → Click 'Save changes'\n")
        elif cluster_id:
            console_url = f"https://console.aws.amazon.com/redshiftv2/home?region={aws_region}#cluster-details?cluster={cluster_id}"
            console.print(f"   → Open AWS Console: [link={console_url}]{console_url}[/link]")
            console.print("   → Click 'Actions' → 'Modify publicly accessible setting'")
            console.print("   → Enable 'Publicly accessible'")
            console.print("   → Click 'Confirm'\n")
        else:
            console.print("   → Log into AWS Console")
            console.print(f"   → Navigate to Redshift service in region: {aws_region}")
            console.print("   → Find your workgroup/cluster and enable 'Publicly accessible'\n")

        step_num = 2
    else:
        step_num = 1

    console.print(f"[bold]{step_num}. Configure security group to allow inbound connections[/bold]")

    if diagnostics.get("security_group_ids"):
        sg_ids = diagnostics["security_group_ids"]
        for sg_id in sg_ids:
            sg_url = f"https://console.aws.amazon.com/vpc/home?region={aws_region}#SecurityGroup:groupId={sg_id}"
            console.print(f"   → Open Security Group: [link={sg_url}]{sg_id}[/link]")
    else:
        console.print(f"   → Navigate to VPC → Security Groups in AWS Console ({aws_region})")
        console.print("   → Find the security group attached to your Redshift workgroup/cluster")

    console.print("   → Click 'Inbound rules' → 'Edit inbound rules' → 'Add rule'")
    console.print("   → Type: Redshift (or Custom TCP)")
    console.print(f"   → Port: {port}")

    if user_ip:
        console.print(f"   → Source: Custom → {user_ip}/32  [dim](your current IP)[/dim]")
    else:
        console.print("   → Source: Custom → <your-ip>/32  [dim](find your IP at https://whatismyip.com)[/dim]")

    console.print("   → Description: BenchBox access")
    console.print("   → Click 'Save rules'\n")

    step_num += 1

    if publicly_accessible is False or diagnostics.get("vpc_id"):
        console.print(f"[bold]{step_num}. Verify VPC has an Internet Gateway attached[/bold]")
        if diagnostics.get("vpc_id"):
            vpc_url = f"https://console.aws.amazon.com/vpc/home?region={aws_region}#vpcs:VpcId={diagnostics['vpc_id']}"
            console.print(f"   → Open VPC: [link={vpc_url}]{diagnostics['vpc_id']}[/link]")
        else:
            console.print(f"   → Navigate to VPC service in AWS Console ({aws_region})")
        console.print("   → Check that an Internet Gateway is attached to the VPC")
        console.print("   → Verify route table has route to 0.0.0.0/0 via Internet Gateway\n")

        step_num += 1

    console.print(f"[bold]{step_num}. Test the connection[/bold]")
    console.print("   → After making the above changes, run:")
    console.print("   [cyan]benchbox setup --platform redshift --validate-only[/cyan]\n")

    console.print("[bold]📚 Additional Resources[/bold]")
    console.print("   → AWS Docs: https://docs.aws.amazon.com/redshift/latest/mgmt/managing-cluster-cross-vpc.html")
    console.print("   → Network troubleshooting: https://repost.aws/knowledge-center/redshift-cluster-private-public")


__all__ = ["setup_redshift_credentials", "validate_redshift_credentials"]
