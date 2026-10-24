# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Optional, Union

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from benchbox.platforms.credentials.helpers import prompt_secure_field, prompt_with_default
from benchbox.security.credentials import CredentialManager, CredentialStatus
from benchbox.utils.printing import QuietConsoleProxy
from benchbox.utils.tls import ca_bundle_path


def setup_databricks_credentials(cred_manager: CredentialManager, console: Union[Console, QuietConsoleProxy]) -> None:
    console.print("\n📋 [bold]You'll need:[/bold]")
    console.print("  • Databricks workspace URL (server hostname)")
    console.print("  • SQL Warehouse HTTP path")
    console.print("  • Personal access token\n")

    console.print("[dim]Need help? Visit: https://docs.databricks.com/dev-tools/auth.html[/dim]\n")

    existing_creds = cred_manager.get_platform_credentials("databricks")

    if existing_creds:
        console.print("ℹ️  [cyan]Existing credentials found - updating configuration[/cyan]\n")
        auto_config = None
    else:
        auto_config = None
        try_auto = Confirm.ask("🔍 Attempt auto-detection using Databricks SDK?", default=True)

        if try_auto:
            console.print("\n[dim]Attempting auto-detection...[/dim]")
            auto_config = _auto_detect_databricks(console)

    if auto_config:
        server_hostname = auto_config.get("server_hostname")
        http_path = auto_config.get("http_path")
        console.print(f"\n✅ Found workspace: [cyan]{server_hostname}[/cyan]")
        if http_path:
            console.print(f"✅ Found warehouse HTTP path: [cyan]{http_path}[/cyan]")
    else:
        console.print("\n[bold]Workspace Configuration:[/bold]")

        current_hostname = existing_creds.get("server_hostname") if existing_creds else None
        current_http_path = existing_creds.get("http_path") if existing_creds else None

        server_hostname = prompt_with_default(
            "Server hostname (e.g., myworkspace.cloud.databricks.com)",
            current_value=current_hostname,
        )

        if not server_hostname:
            console.print("[red]❌ Server hostname is required[/red]")
            return

        http_path = prompt_with_default(
            "SQL Warehouse HTTP path (e.g., /sql/1.0/warehouses/abc123)",
            current_value=current_http_path,
        )

        if not http_path:
            console.print("[red]❌ HTTP path is required[/red]")
            return

    console.print("\n[bold]🔑 Access Token:[/bold]")
    console.print("You can create a token at:")
    console.print(f"  https://{server_hostname}/#settings/account\n")

    current_token = existing_creds.get("access_token") if existing_creds and not auto_config else None
    access_token = prompt_secure_field("Access token", current_value=current_token, console=console)

    if not access_token:
        console.print("[red]❌ Access token is required[/red]")
        return

    credentials = {
        "server_hostname": server_hostname,
        "http_path": http_path,
        "access_token": access_token,
    }

    console.print("\n[bold]Optional Settings:[/bold]")
    current_catalog = existing_creds.get("catalog") if existing_creds else None
    current_schema = existing_creds.get("schema") if existing_creds else None

    catalog = prompt_with_default("Default catalog", current_value=current_catalog, default_if_none="main")
    schema = prompt_with_default("Default schema", current_value=current_schema, default_if_none="benchbox")

    if catalog:
        credentials["catalog"] = catalog
    if schema:
        credentials["schema"] = schema

    console.print("\n🧪 [bold]Validating credentials...[/bold]")

    cred_manager.set_platform_credentials("databricks", credentials, CredentialStatus.NOT_VALIDATED)

    success, error = validate_databricks_credentials(cred_manager)

    if success:
        cred_manager.update_validation_status("databricks", CredentialStatus.VALID)
        cred_manager.save_credentials()

        console.print("\n[green]✅ Databricks credentials validated and saved![/green]")
        console.print(f"   Location: [cyan]{cred_manager.credentials_path}[/cyan]")
        console.print("   Status: [green]Ready to use[/green]\n")

        _prompt_default_output_location(cred_manager, console, credentials)

        console.print("[bold]Try it:[/bold]")
        console.print("  benchbox run --platform databricks --benchmark tpch --scale 0.01")
    else:
        cred_manager.update_validation_status("databricks", CredentialStatus.INVALID, error)
        cred_manager.save_credentials()

        console.print("\n[red]❌ Validation failed[/red]")
        if error:
            console.print(f"   Error: {error}")
        console.print("\n[yellow]Credentials saved but marked as invalid.[/yellow]")
        console.print("Fix the issues and run: benchbox setup --platform databricks --validate-only")


def _prompt_default_output_location(
    cred_manager: CredentialManager, console: Union[Console, QuietConsoleProxy], credentials: dict
) -> None:
    from benchbox.utils.cloud_storage import is_cloud_path

    console.print("\n[bold]Default Output Location (Optional):[/bold]")
    console.print("Configure a default cloud path for benchmark data storage.")
    console.print("This prevents needing to specify --output for every run.\n")

    wants_default = Confirm.ask("Configure default output location?", default=True)

    if not wants_default:
        console.print("[dim]You can add --output <cloud-path> when running benchmarks[/dim]\n")
        return

    console.print("\n[bold cyan]Example paths for Databricks:[/bold cyan]")
    console.print("  • [dim]dbfs:/Volumes/main/benchbox/data[/dim]")
    console.print("  • [dim]s3://my-bucket/benchbox-data[/dim]")
    console.print("  • [dim]abfss://container@storage.dfs.core.windows.net/benchbox[/dim]")
    console.print("  • [dim]gs://my-bucket/benchbox-data[/dim]")

    console.print("\n[bold]Unity Catalog Volume:[/bold]")
    catalog = credentials.get("catalog", "main")
    schema = credentials.get("schema", "benchbox")
    console.print(f"  dbfs:/Volumes/{catalog}/{schema}/<volume>/<path>")
    console.print("\n[dim]Note: Ensure the volume/bucket exists and has proper permissions[/dim]\n")

    while True:
        cloud_path = Prompt.ask("[bold]Enter default cloud storage path[/bold]", default="")

        if not cloud_path:
            console.print("[yellow]Skipping default output location[/yellow]\n")
            return

        if not is_cloud_path(cloud_path):
            console.print(f"[yellow]⚠️  Warning: '{cloud_path}' doesn't look like a cloud path[/yellow]")
            console.print("[dim]Expected formats: s3://, gs://, abfss://, dbfs:/Volumes/...[/dim]")
            proceed = Confirm.ask("Use this path anyway?", default=False)
            if not proceed:
                continue

        console.print(f"\n[green]✓[/green] Will use: [cyan]{cloud_path}[/cyan]")
        confirmed = Confirm.ask("Is this correct?", default=True)
        if confirmed:
            credentials["default_output_location"] = cloud_path
            cred_manager.set_platform_credentials("databricks", credentials, CredentialStatus.VALID)
            cred_manager.save_credentials()
            console.print("[green]✅ Default output location saved![/green]\n")
            return


def validate_databricks_credentials(cred_manager: CredentialManager) -> tuple[bool, Optional[str]]:
    creds = cred_manager.get_platform_credentials("databricks")

    if not creds:
        return False, "No credentials found"

    required_fields = ["server_hostname", "http_path", "access_token"]
    missing = [field for field in required_fields if not creds.get(field)]

    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    try:
        from databricks import sql as databricks_sql
    except ImportError:
        return False, "Databricks SQL connector not installed. Run: pip install databricks-sql-connector"

    try:
        connection = databricks_sql.connect(
            server_hostname=creds["server_hostname"],
            http_path=creds["http_path"],
            access_token=creds["access_token"],
            user_agent_entry="BenchBox/1.0",
            _tls_trusted_ca_file=ca_bundle_path(),
        )

        cursor = connection.cursor()

        cursor.execute("SELECT 1")
        cursor.fetchall()

        if creds.get("catalog"):
            cursor.execute(f"USE CATALOG {creds['catalog']}")

        cursor.close()
        connection.close()

        return True, None

    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower() or "token" in error_msg.lower():
            return False, "Authentication failed. Check your access token."
        elif "warehouse" in error_msg.lower() or "http_path" in error_msg.lower():
            return False, "SQL Warehouse connection failed. Check your HTTP path."
        elif "catalog" in error_msg.lower():
            return False, f"Catalog '{creds.get('catalog')}' not found or not accessible."
        else:
            return False, f"Connection failed: {error_msg}"


def _auto_detect_databricks(console: Union[Console, QuietConsoleProxy]) -> Optional[dict]:
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import WarehousesAPI

        workspace = WorkspaceClient()
        server_hostname = workspace.config.host.replace("https://", "")
        access_token = workspace.config.token

        console.print(f"  ✓ Found workspace: {server_hostname}")

        warehouses = list(WarehousesAPI(workspace.api_client).list())

        if not warehouses:
            console.print("  ⚠️  No SQL warehouses found")
            return {
                "server_hostname": server_hostname,
                "http_path": None,
                "access_token": access_token,
            }

        console.print(f"  ✓ Found {len(warehouses)} SQL Warehouse(s)\n")

        running_warehouses = [wh for wh in warehouses if str(wh.state) == "RUNNING"]
        available_warehouses = [wh for wh in warehouses if str(wh.state) not in ["DELETING", "DELETED"]]

        if running_warehouses:
            console.print("[bold]Running Warehouses:[/bold]")
            for i, wh in enumerate(running_warehouses, 1):
                console.print(f"  {i}. {wh.name} ({wh.cluster_size}, ID: {wh.id})")
        elif available_warehouses:
            console.print("[bold]Available Warehouses (not running):[/bold]")
            for i, wh in enumerate(available_warehouses, 1):
                console.print(f"  {i}. {wh.name} (State: {wh.state}, Size: {wh.cluster_size})")

        if running_warehouses or available_warehouses:
            warehouses_to_choose = running_warehouses if running_warehouses else available_warehouses
            max_choice = len(warehouses_to_choose)

            choice = IntPrompt.ask(
                f"\nSelect warehouse [1-{max_choice}]",
                default=1,
                show_default=True,
            )

            if 1 <= choice <= max_choice:
                selected = warehouses_to_choose[choice - 1]
                http_path = f"/sql/1.0/warehouses/{selected.id}"

                console.print(f"\n✅ Selected: [cyan]{selected.name}[/cyan]")
                console.print(f"   HTTP Path: [cyan]{http_path}[/cyan]")

                return {
                    "server_hostname": server_hostname,
                    "http_path": http_path,
                    "access_token": access_token,
                }

        return {
            "server_hostname": server_hostname,
            "http_path": None,
            "access_token": access_token,
        }

    except ImportError:
        console.print("  ⚠️  Databricks SDK not installed (optional)")
        console.print("     Install with: pip install databricks-sdk")
        return None
    except Exception as e:
        console.print(f"  ⚠️  Auto-detection failed: {e}")
        return None


__all__ = ["setup_databricks_credentials", "validate_databricks_credentials"]
