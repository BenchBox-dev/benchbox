# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import importlib

from benchbox.cli.display import create_display_manager
from benchbox.core.schemas import SystemProfile
from benchbox.core.system import SystemProfiler as CoreSystemProfiler
from benchbox.utils.printing import quiet_console

try:
    importlib.import_module("psutil")

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

console = quiet_console


class SystemProfiler(CoreSystemProfiler):
    def __init__(self):
        super().__init__()
        self.console = quiet_console

    def display_profile(self, profile: SystemProfile, detailed: bool = False):
        display = create_display_manager(self.console)
        display.show_system_profile(profile, detailed)

        from benchbox.core.platform_registry import PlatformRegistry

        available_dbs = PlatformRegistry.get_available_platforms()

        db_info = f"[green]✅[/green] {', '.join(available_dbs)}" if available_dbs else "[yellow]None detected[/yellow]"

        self.console.print(f"\n[cyan]Available Databases:[/cyan] {db_info}")

        if not HAS_PSUTIL:
            self.console.print("[dim]Note: Install psutil for more detailed system information[/dim]")

        if not available_dbs:
            console.print("\n[yellow]⚠️️  No database libraries detected. Consider installing:[/yellow]")
            console.print("   • DuckDB: [cyan]uv add duckdb[/cyan] (recommended)")
            console.print("   • SQLite: [cyan]Built into Python[/cyan]")

        if not HAS_PSUTIL:
            console.print("\n[yellow]For detailed system monitoring, install psutil:[/yellow]")
            console.print("   [cyan]uv add psutil[/cyan]")
