# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from typing import Optional, Union

from rich.console import Console
from rich.prompt import Confirm, Prompt

from benchbox.platforms.credentials.helpers import prompt_secure_field, prompt_with_default
from benchbox.security.credentials import CredentialManager, CredentialStatus
from benchbox.utils.printing import QuietConsoleProxy


def setup_snowflake_credentials(cred_manager: CredentialManager, console: Union[Console, QuietConsoleProxy]) -> None:
    console.print("\n📋 [bold]You'll need:[/bold]")
    console.print("  • Snowflake account identifier")
    console.print("  • Username and password")
    console.print("  • Warehouse name")
    console.print("  • Database name (will be created if it doesn't exist)\n")

    console.print("[dim]Need help? Visit: https://docs.snowflake.com/en/user-guide/admin-user-management[/dim]\n")

    existing_creds = cred_manager.get_platform_credentials("snowflake")

    if existing_creds:
        console.print("ℹ️  [cyan]Existing credentials found - updating configuration[/cyan]\n")
        auto_config = None
    else:
        auto_config = None
        try_auto = Confirm.ask("🔍 Attempt auto-detection from environment variables?", default=True)

        if try_auto:
            console.print("\n[dim]Checking environment variables...[/dim]")
            auto_config = _auto_detect_snowflake(console)

    if auto_config:
        account = auto_config.get("account")
        username = auto_config.get("username")
        password = auto_config.get("password")
        warehouse = auto_config.get("warehouse")
        database = auto_config.get("database")
        schema = auto_config.get("schema")
        role = auto_config.get("role")

        console.print(f"\n✅ Found account: [cyan]{account}[/cyan]")
        console.print(f"✅ Found username: [cyan]{username}[/cyan]")
        console.print(f"✅ Found warehouse: [cyan]{warehouse}[/cyan]")
        console.print(f"✅ Found database: [cyan]{database}[/cyan]")
        if schema:
            console.print(f"✅ Found schema: [cyan]{schema}[/cyan]")
        if role:
            console.print(f"✅ Found role: [cyan]{role}[/cyan]")
    else:
        console.print("\n[bold]Snowflake Configuration:[/bold]")

        current_account = existing_creds.get("account") if existing_creds else None
        current_username = existing_creds.get("username") if existing_creds else None
        current_password = existing_creds.get("password") if existing_creds else None
        current_warehouse = _saved_warehouse(existing_creds) if existing_creds else None
        current_database = existing_creds.get("database") if existing_creds else None
        current_schema = existing_creds.get("schema") if existing_creds else None
        current_role = existing_creds.get("role") if existing_creds else None

        account = prompt_with_default(
            "Account identifier (e.g., myorg-account123 or myorg-account123.snowflakecomputing.com)",
            current_value=current_account,
        )

        if not account:
            console.print("[red]❌ Account identifier is required[/red]")
            return

        if ".snowflakecomputing.com" in account:
            account = account.replace(".snowflakecomputing.com", "")
            console.print(f"[dim]Using account identifier: {account}[/dim]")

        username = prompt_with_default("Username", current_value=current_username)

        if not username:
            console.print("[red]❌ Username is required[/red]")
            return

        password = prompt_secure_field("Password", current_value=current_password, console=console)

        if not password:
            console.print("[red]❌ Password is required[/red]")
            return

        warehouse = prompt_with_default("Warehouse name", current_value=current_warehouse, default_if_none="COMPUTE_WH")

        if not warehouse:
            console.print("[red]❌ Warehouse name is required[/red]")
            return

        database = prompt_with_default("Database name", current_value=current_database, default_if_none="BENCHBOX")

        if not database:
            console.print("[red]❌ Database name is required[/red]")
            return

        console.print("\n[bold]Optional Settings:[/bold]")
        schema = prompt_with_default("Schema name", current_value=current_schema, default_if_none="PUBLIC")
        role = prompt_with_default("Role (leave empty for default)", current_value=current_role, default_if_none="")

    credentials = {
        "account": account,
        "username": username,
        "password": password,
        "compute_resource": warehouse,
        "database": database,
    }

    if schema:
        credentials["schema"] = schema
    if role:
        credentials["role"] = role

    console.print("\n🧪 [bold]Validating credentials...[/bold]")

    cred_manager.set_platform_credentials("snowflake", credentials, CredentialStatus.NOT_VALIDATED)

    success, error = validate_snowflake_credentials(cred_manager)

    if success:
        cred_manager.update_validation_status("snowflake", CredentialStatus.VALID)
        cred_manager.save_credentials()

        console.print("\n[green]✅ Snowflake credentials validated and saved![/green]")
        console.print(f"   Location: [cyan]{cred_manager.credentials_path}[/cyan]")
        console.print("   Status: [green]Ready to use[/green]\n")

        _prompt_default_output_location(cred_manager, console, credentials)

        console.print("[bold]Try it:[/bold]")
        console.print("  benchbox run --platform snowflake --benchmark tpch --scale 0.01")
    else:
        cred_manager.update_validation_status("snowflake", CredentialStatus.INVALID, error)
        cred_manager.save_credentials()

        console.print("\n[red]❌ Validation failed[/red]")
        if error:
            console.print(f"   Error: {error}")
        console.print("\n[yellow]Credentials saved but marked as invalid.[/yellow]")
        console.print("Fix the issues and run: benchbox setup --platform snowflake --validate-only")


def _prompt_default_output_location(
    cred_manager: CredentialManager, console: Union[Console, QuietConsoleProxy], credentials: dict
) -> None:
    from benchbox.core.platform_registry import SNOWFLAKE_DEFAULT_OUTPUT_LOCATION, PlatformRegistry
    from benchbox.utils.cloud_storage import is_cloud_path

    console.print("\n[bold]Default Output Location (Optional):[/bold]")
    console.print("Configure a default cloud path for benchmark data storage.")
    console.print("This prevents needing to specify --output for every run.\n")

    wants_default = Confirm.ask("Configure default output location?", default=True)

    if not wants_default:
        console.print("[dim]You can add --output <cloud-path> when running benchmarks[/dim]\n")
        return

    examples = PlatformRegistry.get_cloud_path_examples("snowflake")
    stage_examples = [e for e in examples if e.startswith("@")]
    external_examples = [e for e in examples if not e.startswith("@")]

    if stage_examples:
        console.print("\n[bold cyan]Recommended: User Stage (easiest)[/bold cyan]")
        for example in stage_examples:
            console.print(f"  • [dim]{example}[/dim]")
        console.print("\n[dim]User stages (@~) are private to your account and require no setup[/dim]")

    if external_examples:
        console.print("\n[bold cyan]Alternative: External Stage Locations[/bold cyan]")
        for example in external_examples:
            console.print(f"  • [dim]{example}[/dim]")
        console.print("\n[dim]Note: External stages require cloud storage setup[/dim]\n")

    while True:
        cloud_path = Prompt.ask("[bold]Enter default storage path[/bold]", default=SNOWFLAKE_DEFAULT_OUTPUT_LOCATION)

        if not cloud_path:
            console.print("[yellow]Skipping default output location[/yellow]\n")
            return

        if not is_cloud_path(cloud_path):
            console.print(f"[yellow]⚠️  Warning: '{cloud_path}' doesn't look like a valid path[/yellow]")
            console.print(
                "[dim]Expected formats: @~/path (user stage) or s3://, azure://, gcs:// (external stage)[/dim]"
            )
            proceed = Confirm.ask("Use this path anyway?", default=False)
            if not proceed:
                continue

        console.print(f"\n[green]✓[/green] Will use: [cyan]{cloud_path}[/cyan]")
        confirmed = Confirm.ask("Is this correct?", default=True)
        if confirmed:
            credentials["default_output_location"] = cloud_path
            cred_manager.set_platform_credentials("snowflake", credentials, CredentialStatus.VALID)
            cred_manager.save_credentials()
            console.print("[green]✅ Default output location saved![/green]\n")
            return


def validate_snowflake_credentials(cred_manager: CredentialManager) -> tuple[bool, Optional[str]]:
    creds = cred_manager.get_platform_credentials("snowflake")

    if not creds:
        return False, "No credentials found"

    required_fields = ["account", "username", "password", "compute_resource", "database"]
    values = {**creds, "compute_resource": _saved_warehouse(creds)}
    missing = [field for field in required_fields if not values.get(field)]

    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    try:
        import snowflake.connector
    except ImportError:
        return False, "Snowflake connector not installed. Run: pip install snowflake-connector-python"

    try:
        connection = snowflake.connector.connect(
            account=creds["account"],
            user=creds["username"],
            password=creds["password"],
            warehouse=values["compute_resource"],
            database=creds["database"],
            schema=creds.get("schema", "PUBLIC"),
            role=creds.get("role") if creds.get("role") else None,
            application="BenchBox",
        )

        cursor = connection.cursor()

        cursor.execute("SELECT 1")
        cursor.fetchall()

        cursor.execute("SELECT CURRENT_WAREHOUSE()")
        cursor.fetchall()

        cursor.execute("SELECT CURRENT_DATABASE()")
        cursor.fetchall()

        cursor.close()
        connection.close()

        return True, None

    except Exception as e:
        error_msg = str(e)
        if "incorrect username or password" in error_msg.lower() or "authentication" in error_msg.lower():
            return False, "Authentication failed. Check your username and password."
        elif "account" in error_msg.lower() and "does not exist" in error_msg.lower():
            return False, "Account identifier is invalid. Check your account name."
        elif "warehouse" in error_msg.lower():
            return False, f"Warehouse '{values['compute_resource']}' not found or not accessible."
        elif "database" in error_msg.lower() and "does not exist" in error_msg.lower():
            return False, f"Database '{creds.get('database')}' not found. It will be created during benchmark setup."
        elif "role" in error_msg.lower():
            return False, f"Role '{creds.get('role')}' not found or not accessible."
        else:
            return False, f"Connection failed: {error_msg}"


def _saved_warehouse(creds: dict) -> Optional[str]:
    return creds.get("compute_resource") or creds.get("warehouse")


def _auto_detect_snowflake(console: Union[Console, QuietConsoleProxy]) -> Optional[dict]:
    env_vars = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "username": os.getenv("SNOWFLAKE_USERNAME"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA"),
        "role": os.getenv("SNOWFLAKE_ROLE"),
    }

    required = ["account", "username", "password", "warehouse", "database"]
    found_required = all(env_vars.get(field) for field in required)

    if not found_required:
        missing = [f"SNOWFLAKE_{field.upper()}" for field in required if not env_vars.get(field)]
        console.print(f"  ⚠️  Missing environment variables: {', '.join(missing)}")
        return None

    account = env_vars["account"]
    if account and ".snowflakecomputing.com" in account:
        env_vars["account"] = account.replace(".snowflakecomputing.com", "")

    console.print("  ✓ Found all required environment variables")
    return env_vars


__all__ = ["setup_snowflake_credentials", "validate_snowflake_credentials"]
