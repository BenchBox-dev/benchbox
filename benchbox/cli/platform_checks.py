# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Optional

from benchbox.security.credentials import CredentialManager, CredentialStatus


def check_and_setup_platform_credentials(
    platform: str,
    console_obj,
    interactive: bool = True,
) -> bool:
    cred_manager = CredentialManager()

    platform_creds = cred_manager.get_platform_credentials(platform)

    if platform_creds:
        return True

    if not interactive:
        return False

    from rich.prompt import Confirm

    from benchbox.cli.commands.setup import run_platform_credential_setup

    console_obj.print(f"\n[yellow]⚠️  {platform.capitalize()} credentials not found[/yellow]")
    console_obj.print(f"\nTo use {platform.capitalize()}, you need to configure credentials.")

    try:
        wants_setup = Confirm.ask("\n🔧 Would you like to set up credentials now?", default=True)
    except EOFError:
        console_obj.print("[yellow]No terminal to answer on; skipping credential setup[/yellow]")
        console_obj.print(f"\n[dim]To set up later, run: benchbox setup --platform {platform}[/dim]")
        return False

    if not wants_setup:
        console_obj.print("[yellow]Skipping credential setup[/yellow]")
        console_obj.print(f"\n[dim]To set up later, run: benchbox setup --platform {platform}[/dim]")
        return False

    success = run_platform_credential_setup(platform, console_obj, show_welcome=True)

    if success:
        console_obj.print("\n[green]✅ Credentials configured successfully![/green]\n")
        return True
    else:
        console_obj.print("\n[red]❌ Credential setup failed[/red]")
        return False


def check_platform_credential_status(platform: str) -> tuple[bool, Optional[CredentialStatus]]:
    cred_manager = CredentialManager()
    platform_creds = cred_manager.get_platform_credentials(platform)

    if not platform_creds:
        return (False, CredentialStatus.MISSING)

    status_enum = cred_manager.get_credential_status(platform)

    return (True, status_enum)
