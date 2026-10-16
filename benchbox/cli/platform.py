# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import sys
from pathlib import Path
from typing import Any, Optional

import click
import yaml
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, InvalidResponse, Prompt
from rich.table import Table
from rich.text import Text

from benchbox.cli.commands.setup import SUPPORTED_SETUP_PLATFORMS
from benchbox.cli.platform_readiness import (
    PlatformReadinessResult,
    check_platform_readiness,
    has_readiness_failures,
)
from benchbox.core.platform_manifest import DefaultMode, get_platform_alias_modes, get_platform_aliases
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.schemas import LibraryInfo, PlatformInfo
from benchbox.platforms.adapter_factory import _reject_removed_platform
from benchbox.platforms.clickhouse.deployment_mode import CLICKHOUSE_LEGACY_SELECTOR_MAP
from benchbox.utils.printing import quiet_console

console = quiet_console


def _check_removed_platform(platform: str) -> None:
    try:
        _reject_removed_platform(platform)
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        sys.exit(1)


PLATFORM_ALIASES: dict[str, str] = get_platform_aliases("cli")
PLATFORM_ALIAS_MODES: dict[str, DefaultMode] = get_platform_alias_modes("cli")


def normalize_platform_name(name: str) -> str:
    normalized = name.lower()
    return PlatformRegistry.resolve_platform_name(PLATFORM_ALIASES.get(normalized, normalized))


def resolve_platform_selector(selector: str) -> str:
    key = selector.lower()
    if key in CLICKHOUSE_LEGACY_SELECTOR_MAP:
        return CLICKHOUSE_LEGACY_SELECTOR_MAP[key]
    base, separator, deployment = key.partition(":")
    if not separator:
        return key
    base = normalize_platform_name(base)
    if PlatformRegistry.supports_deployment_mode(base, deployment):
        return base
    available = PlatformRegistry.get_available_deployment_modes(base)
    if available:
        raise ValueError(
            f"Platform '{base}' does not support deployment mode '{deployment}'. Available: {', '.join(available)}"
        )
    raise ValueError(f"Platform '{base}' does not support deployment modes. Remove the ':{deployment}' suffix.")


def get_platform_alias_mode(name: str) -> DefaultMode | None:
    return PLATFORM_ALIAS_MODES.get(name.lower())


_SUPPORT_STATUS_STYLES = {
    "stable": "green",
    "beta": "cyan",
    "experimental": "yellow",
    "deprecated": "red",
}


_SUPPORT_STATUS_ORDER = ("stable", "beta", "experimental", "deprecated")


def _support_tier_rank(support_status: str | None) -> tuple[int, str]:
    if not support_status or support_status not in _SUPPORT_STATUS_ORDER:
        return (len(_SUPPORT_STATUS_ORDER), support_status or "")
    return (_SUPPORT_STATUS_ORDER.index(support_status), "")


def _platforms_by_support_tier(platforms: dict) -> list[tuple[str, Any]]:
    return sorted(
        platforms.items(),
        key=lambda item: (_support_tier_rank(item[1].support_status), item[1].display_name.lower()),
    )


def _support_tier_counts(platforms: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _name, info in _platforms_by_support_tier(platforms):
        tier = info.support_status if info.support_status else "unknown"
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _format_support_status(support_status: str | None) -> str:
    if not support_status:
        return "[dim]unknown[/dim]"
    style = _SUPPORT_STATUS_STYLES.get(support_status)
    if style is None:
        return f"[dim]{support_status}[/dim]"
    return f"[{style}]{support_status}[/{style}]"


class NumberedSelectPrompt(Prompt):
    def __init__(
        self,
        prompt: str,
        *,
        options: list[tuple[str, str]],
        default: str | None = None,
        console: Any = None,
    ):
        self.options = options
        self._value_to_number = {value: i + 1 for i, (value, _) in enumerate(options)}
        self._number_to_value = {i + 1: value for i, (value, _) in enumerate(options)}
        self._valid_values = {value for value, _ in options}
        self._default_value = default

        super().__init__(
            prompt,
            console=console or quiet_console,
        )

    def make_prompt(self, default: str) -> Text:
        for i, (value, label) in enumerate(self.options, 1):
            default_marker = " (default)" if value == self._default_value else ""
            self.console.print(f"  [cyan]{i}.[/cyan] {label}{default_marker}")

        self.console.print()

        prompt_text = Text()
        prompt_text.append(self.prompt)
        prompt_text.append(" ")
        prompt_text.append(f"[1-{len(self.options)}]", style="dim")
        if self._default_value:
            default_num = self._value_to_number[self._default_value]
            prompt_text.append(f" ({default_num})", style="dim")
        prompt_text.append(self.prompt_suffix)
        return prompt_text

    def process_response(self, value: str) -> str:
        value = value.strip()

        if not value and self._default_value:
            return self._default_value

        try:
            num = int(value)
            if num in self._number_to_value:
                return self._number_to_value[num]
            raise InvalidResponse(f"[red]Invalid selection: {num}. Enter 1-{len(self.options)}.[/red]")
        except ValueError:
            pass

        value_lower = value.lower()
        for opt_value, _ in self.options:
            if opt_value.lower() == value_lower:
                return opt_value

        valid_names = ", ".join(v for v, _ in self.options)
        raise InvalidResponse(f"[red]Invalid selection: '{value}'. Enter 1-{len(self.options)} or: {valid_names}[/red]")

    @classmethod
    def ask(
        cls,
        prompt: str,
        *,
        options: list[tuple[str, str]],
        default: str | None = None,
        console: Any = None,
    ) -> str:
        _prompt = cls(prompt, options=options, default=default, console=console)
        return _prompt()


def numbered_platform_select(
    prompt: str,
    platforms: dict[str, "PlatformInfo"],
    *,
    filter_func: Any = None,
    group_by_status: bool = True,
    console_instance: Any = None,
) -> str | None:
    _console = console_instance or console

    filtered = {name: info for name, info in platforms.items() if filter_func(info)} if filter_func else platforms

    if not filtered:
        _console.print("[yellow]No platforms match the criteria.[/yellow]")
        return None

    options = _build_platform_options(filtered, group_by_status, _console)

    _console.print()
    return _prompt_platform_selection(prompt, options, filtered, _console)


def _build_platform_options(
    filtered: dict[str, "PlatformInfo"], group_by_status: bool, _console: Any
) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []

    if group_by_status:
        groups = [
            ("Enabled", "bold green", [(n, i) for n, i in filtered.items() if i.enabled]),
            (
                "Available (not enabled)",
                "bold yellow",
                [(n, i) for n, i in filtered.items() if i.available and not i.enabled],
            ),
            ("Missing dependencies", "bold red", [(n, i) for n, i in filtered.items() if not i.available]),
        ]
        for label, style, members in groups:
            if members:
                _console.print(f"\n[{style}]{label}:[/{style}]")
                for name, info in sorted(members, key=lambda x: x[1].display_name):
                    num = len(options) + 1
                    _console.print(f"  [cyan]{num}.[/cyan] {info.display_name} [dim]({name})[/dim]")
                    options.append((name, info.display_name))
    else:
        for name, info in sorted(filtered.items(), key=lambda x: x[1].display_name):
            num = len(options) + 1
            _console.print(f"  [cyan]{num}.[/cyan] {info.display_name} [dim]({name})[/dim]")
            options.append((name, info.display_name))

    return options


def _prompt_platform_selection(
    prompt: str, options: list[tuple[str, str]], filtered: dict[str, Any], _console: Any
) -> str | None:
    prompt_text = f"{prompt} [1-{len(options)}]"

    while True:
        response = Prompt.ask(prompt_text, console=_console)
        response = response.strip()

        if not response:
            return None

        try:
            num = int(response)
            if 1 <= num <= len(options):
                return options[num - 1][0]
            _console.print(f"[red]Invalid selection: {num}. Enter 1-{len(options)}.[/red]")
            continue
        except ValueError:
            pass

        normalized = normalize_platform_name(response)
        if normalized in filtered:
            return normalized

        _console.print(f"[red]Unknown platform: '{response}'. Enter a number or platform name.[/red]")


class PlatformManager:
    def __init__(self, config_path: Optional[Path] = None):
        self.console = quiet_console
        self.config_path = config_path or Path.home() / ".benchbox" / "platforms.yaml"
        self._config = self._load_config()

    @property
    def platform_registry(self) -> dict[str, Any]:
        return PlatformRegistry.get_all_platform_metadata()

    def _detect_library(self, lib_spec: dict[str, Any]) -> LibraryInfo:
        return PlatformRegistry.detect_library(lib_spec)

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"enabled_platforms": PlatformRegistry.get_platform_names()}

        try:
            with open(self.config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to load platform config: {e}[/yellow]")
            return {"enabled_platforms": PlatformRegistry.get_platform_names()}

    def _save_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self._config, f, default_flow_style=False)
        except Exception as e:
            console.print(f"[red]Error: Failed to save platform config: {e}[/red]")

    def detect_platforms(self) -> dict[str, PlatformInfo]:
        platforms = {}
        all_platform_names = list(self.platform_registry.keys())
        available_platform_names = PlatformRegistry.get_available_platforms()

        enabled_platforms = self._config.get("enabled_platforms", available_platform_names)

        for platform_name in all_platform_names:
            platform_info = PlatformRegistry.get_platform_info(platform_name)
            if platform_info:
                platform_info.enabled = platform_name in enabled_platforms and platform_info.available
                platforms[platform_name] = platform_info

        return platforms

    def get_available_platforms(self) -> list[str]:
        detected = self.detect_platforms()
        return [name for name, info in detected.items() if info.available]

    def get_enabled_platforms(self) -> list[str]:
        platforms = self.detect_platforms()
        return [name for name, info in platforms.items() if info.enabled]

    def get_valid_platforms_for_cli(self) -> list[str]:
        return self.get_enabled_platforms()

    def is_platform_available(self, platform_name: str) -> bool:
        return PlatformRegistry.is_platform_available(platform_name)

    def enable_platform(self, platform_name: str) -> bool:
        platform_info = PlatformRegistry.get_platform_info(platform_name)

        if not platform_info:
            return False

        if not platform_info.available:
            return False

        enabled_platforms = set(self._config.get("enabled_platforms", []))
        enabled_platforms.add(platform_name)
        self._config["enabled_platforms"] = list(enabled_platforms)
        self._save_config()
        return True

    def disable_platform(self, platform_name: str) -> bool:
        platform_info = PlatformRegistry.get_platform_info(platform_name)
        if not platform_info:
            return False

        enabled_platforms = set(self._config.get("enabled_platforms", []))
        enabled_platforms.discard(platform_name)
        self._config["enabled_platforms"] = list(enabled_platforms)
        self._save_config()
        return True

    def get_installation_guide(self, platform_name: str) -> Optional[dict[str, Any]]:
        platform_info = PlatformRegistry.get_platform_info(platform_name)
        if not platform_info:
            return None

        missing_libs = [lib for lib in platform_info.libraries if not lib.installed]

        return {
            "platform": platform_info.display_name,
            "description": platform_info.description,
            "installation_command": platform_info.installation_command,
            "requirements": platform_info.requirements,
            "missing_libraries": [lib.name for lib in missing_libs],
            "available": platform_info.available,
            "category": platform_info.category,
        }

    def display_platform_status(self, detail: bool = False):
        platforms = self.detect_platforms()

        table = Table(title="BenchBox Platform Status")
        table.add_column("Platform", style="cyan", no_wrap=True)
        table.add_column("Driver", style="bold")
        table.add_column("Support", style="bold")
        table.add_column("Libraries", style="dim")
        if detail:
            table.add_column("Category", style="magenta")
            table.add_column("Description", style="dim")

        for name, info in _platforms_by_support_tier(platforms):
            if info.enabled:
                status = "[green]✅ Enabled[/green]"
            elif info.available:
                status = "[yellow]○ Available[/yellow]"
            else:
                status = "[red]❌ Missing[/red]"

            support = _format_support_status(info.support_status)

            lib_statuses = []
            for lib in info.libraries:
                if lib.installed:
                    version_str = f" ({lib.version})" if lib.version else ""
                    lib_statuses.append(f"[green]{lib.name}{version_str}[/green]")
                else:
                    lib_statuses.append(f"[red]{lib.name}[/red]")

            libraries = ", ".join(lib_statuses)

            row = [info.display_name, status, support, libraries]
            if detail:
                category = info.category if info.category else "database"
                row.extend([category.title(), info.description])
            table.add_row(*row)

        self.console.print(table)

        total_platforms = len(platforms)
        available_count = sum(1 for p in platforms.values() if p.available)
        enabled_count = sum(1 for p in platforms.values() if p.enabled)

        summary = f"[bold]Summary:[/bold] {enabled_count} enabled, {available_count} available, {total_platforms} total"
        self.console.print(f"\n{summary}")
        tier_counts = _support_tier_counts(platforms)
        if tier_counts:
            tiers = ", ".join(f"{count} {tier}" for tier, count in tier_counts.items())
            self.console.print(f"[bold]By support tier:[/bold] {tiers}")
        if not detail:
            self.console.print("[dim]Run with --detail for category and description.[/dim]")

    def emit_platform_json(self) -> None:
        platforms = self.detect_platforms()
        payload = [
            {
                "name": name,
                "display_name": info.display_name,
                "support_status": info.support_status,
                "category": info.category,
                "description": info.description,
                "enabled": info.enabled,
                "available": info.available,
                "installation_command": info.installation_command,
                "libraries": [
                    {"name": lib.name, "installed": lib.installed, "version": lib.version} for lib in info.libraries
                ],
            }
            for name, info in _platforms_by_support_tier(platforms)
        ]
        console.print(json.dumps({"platforms": payload}, indent=2), markup=False, soft_wrap=True)

    def display_platform_list(self, show_all: bool = True):
        platforms = self.detect_platforms()

        self.console.print("[bold cyan]BenchBox Platforms[/bold cyan]\n")

        for name, info in platforms.items():
            if not show_all and not info.available:
                continue

            status_icon = "✅" if info.enabled else ("○" if info.available else "❌")
            status_color = "green" if info.enabled else ("yellow" if info.available else "red")

            support = _format_support_status(info.support_status)
            self.console.print(
                f"[{status_color}]{status_icon}[/{status_color}] {info.display_name} ({name}) - {support}"
            )
            self.console.print(f"   {info.description}")

            if not info.available:
                self.console.print(f"   [dim]Install: {info.installation_command}[/dim]")

            self.console.print()

    def display_platform_deployments(self, filter_platform: Optional[str] = None):
        platforms = self.detect_platforms()

        table = Table(title="Platform Deployment Modes")
        table.add_column("Platform", style="cyan", no_wrap=True)
        table.add_column("Mode", style="bold")
        table.add_column("Type", style="magenta")
        table.add_column("Default", style="dim")
        table.add_column("Requirements", style="dim")

        has_deployments = False

        for name, info in sorted(platforms.items()):
            if filter_platform and name != filter_platform:
                continue

            caps = PlatformRegistry.get_platform_capabilities(name)
            if not caps or not caps.deployment_modes:
                continue

            has_deployments = True
            default_deployment = caps.default_deployment

            for mode_name, deployment_cap in caps.deployment_modes.items():
                is_default = "✓" if mode_name == default_deployment else ""

                requirements = []
                if deployment_cap.requires_credentials:
                    requirements.append("credentials")
                if deployment_cap.requires_cloud_storage:
                    requirements.append("cloud storage")
                if deployment_cap.requires_network:
                    requirements.append("network")
                req_str = ", ".join(requirements) if requirements else "-"

                cli_name = f"{name}:{mode_name}"

                table.add_row(
                    info.display_name,
                    cli_name,
                    deployment_cap.mode,
                    is_default,
                    req_str,
                )

        if has_deployments:
            self.console.print(table)
            self.console.print()
            self.console.print("[dim]Usage: benchbox run --platform <platform>:<mode> --benchmark tpch[/dim]")
            self.console.print("[dim]Example: benchbox run --platform clickhouse-server --benchmark tpch[/dim]")
        else:
            self.console.print("[yellow]No platforms with deployment modes configured.[/yellow]")


_platform_manager: Optional[PlatformManager] = None


def get_platform_manager() -> PlatformManager:
    global _platform_manager
    if _platform_manager is None:
        _platform_manager = PlatformManager()
    return _platform_manager


def _readiness_platform_name(requested_platform: str, normalized_platform: str) -> str:
    requested = requested_platform.lower()
    return requested if requested.endswith("-df") else normalized_platform


def _append_readiness_details(panel_content: list[str], results: tuple[PlatformReadinessResult, ...]) -> None:
    if not results:
        return

    panel_content.append("\n[bold]Readiness:[/bold]")
    for result in results:
        status_text = "Ready" if result.ready else "Environment skip"
        status_color = "green" if result.ready else "yellow"
        panel_content.append(f"  [{status_color}]{status_text}:[/{status_color}] {escape(result.summary)}")
        if result.detail:
            panel_content.append(f"    [dim]{escape(result.detail)}[/dim]")
        if result.remediation and not result.ready:
            panel_content.append(f"    [dim]Fix: {escape(result.remediation)}[/dim]")


def _print_readiness_details(results: tuple[PlatformReadinessResult, ...]) -> None:
    for result in results:
        label = "ready" if result.ready else "environment skip"
        color = "green" if result.ready else "yellow"
        console.print(f"   [{color}]{label}:[/{color}] {escape(result.summary)}")
        if result.detail:
            console.print(f"      [dim]{escape(result.detail)}[/dim]")
        if result.remediation and not result.ready:
            console.print(f"      [dim]Fix: {escape(result.remediation)}[/dim]")


@click.group(help=("Manage database platform adapters."))
def platforms():
    pass


@platforms.command(
    "list",
    help=(
        "List all available platforms and their status.\n"
        "\n"
        "The default table is ordered by support tier and omits category and\n"
        "description so it stays readable at 80 columns. Use --detail to add them\n"
        "back, or --json/--format json for the full record.\n"
        "\n"
        "Use --show-deployments to see available deployment modes for platforms\n"
        "that support multiple deployment targets (e.g., clickhouse-local, clickhouse-server)."
    ),
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show all platforms including unavailable ones",
)
@click.option(
    "--format",
    type=click.Choice(["table", "simple", "json"]),
    default="table",
    help="Output format",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit the full platform records as JSON",
)
@click.option(
    "--detail",
    is_flag=True,
    help="Add the category and description columns (needs a wide terminal)",
)
@click.option(
    "--show-deployments",
    is_flag=True,
    help="Show available deployment modes (local, server, cloud) per platform",
)
def list_platforms(show_all: bool, format: str, json_output: bool, detail: bool, show_deployments: bool):
    manager = get_platform_manager()

    if show_deployments:
        manager.display_platform_deployments()
    elif json_output or format == "json":
        manager.emit_platform_json()
    elif format == "table":
        manager.display_platform_status(detail=detail)
    else:
        manager.display_platform_list(show_all=show_all)


@platforms.command("status", help=("Show detailed status for all platforms or a specific platform."))
@click.argument("platform", required=False)
def platform_status(platform: Optional[str]):
    manager = get_platform_manager()

    if platform:
        requested_platform = platform
        platform = normalize_platform_name(platform)
        platforms_info = manager.detect_platforms()

        if platform not in platforms_info:
            _check_removed_platform(requested_platform)
            _check_removed_platform(platform)
            console.print(f"[red]❌ Unknown platform: {platform}[/red]")
            available = list(platforms_info.keys())
            console.print(f"Available platforms: {', '.join(available)}")
            sys.exit(1)

        info = platforms_info[platform]

        status_color = "green" if info.enabled else ("yellow" if info.available else "red")
        status_text = "Enabled" if info.enabled else ("Available" if info.available else "Missing Dependencies")

        panel_content = []
        panel_content.append(f"[bold]Name:[/bold] {info.display_name}")
        panel_content.append(f"[bold]Description:[/bold] {info.description}")
        panel_content.append(f"[bold]Status:[/bold] [{status_color}]{status_text}[/{status_color}]")
        panel_content.append(f"[bold]Category:[/bold] {info.category.title()}")

        panel_content.append("\n[bold]Libraries:[/bold]")
        for lib in info.libraries:
            lib_status = "✅" if lib.installed else "❌"
            lib_color = "green" if lib.installed else "red"
            version_info = f" (v{lib.version})" if lib.version else ""
            panel_content.append(f"  [{lib_color}]{lib_status} {lib.name}{version_info}[/{lib_color}]")
            if not lib.installed and lib.import_error:
                panel_content.append(f"    [dim]Error: {lib.import_error}[/dim]")

        if not info.available:
            panel_content.append("\n[bold]Installation:[/bold]")
            panel_content.append(f"  {info.installation_command}")
            panel_content.append("\n[bold]Requirements:[/bold]")
            for req in info.requirements:
                panel_content.append(f"  • {req}")

        readiness_platform = _readiness_platform_name(requested_platform, platform)
        _append_readiness_details(panel_content, check_platform_readiness(readiness_platform))

        console.print(
            Panel(
                "\n".join(panel_content),
                title=f"Platform: {info.display_name}",
                border_style=status_color,
            )
        )
    else:
        manager.display_platform_status()


@platforms.command("enable", help=("Enable a database platform."))
@click.argument("platform")
@click.option("--force", is_flag=True, help="Enable platform even if dependencies are missing")
def enable_platform(platform: str, force: bool):
    platform = normalize_platform_name(platform)
    manager = get_platform_manager()
    platforms_info = manager.detect_platforms()

    if platform not in platforms_info:
        _check_removed_platform(platform)
        console.print(f"[red]❌ Unknown platform: {platform}[/red]")
        available = list(platforms_info.keys())
        console.print(f"Available platforms: {', '.join(available)}")
        sys.exit(1)

    info = platforms_info[platform]

    if info.enabled:
        console.print(f"[yellow]Platform {info.display_name} is already enabled[/yellow]")
        sys.exit(0)

    if not info.available and not force:
        console.print(f"[red]❌ Cannot enable {info.display_name}: missing required dependencies[/red]")
        console.print("\nTo install dependencies:")
        console.print(f"  {info.installation_command}")
        console.print("\nOr use --force to enable anyway (may cause runtime errors)")
        sys.exit(1)

    if manager.enable_platform(platform):
        if info.available:
            console.print(f"[green]✅ Enabled platform: {info.display_name}[/green]")
        else:
            console.print(f"[yellow]⚠️ Enabled platform: {info.display_name} (dependencies missing)[/yellow]")
        sys.exit(0)
    else:
        console.print(f"[red]❌ Failed to enable platform: {info.display_name}[/red]")
        sys.exit(1)


@platforms.command("disable", help=("Disable a database platform."))
@click.argument("platform")
def disable_platform(platform: str):
    platform = normalize_platform_name(platform)
    manager = get_platform_manager()
    platforms_info = manager.detect_platforms()

    if platform not in platforms_info:
        _check_removed_platform(platform)
        console.print(f"[red]❌ Unknown platform: {platform}[/red]")
        available = list(platforms_info.keys())
        console.print(f"Available platforms: {', '.join(available)}")
        sys.exit(1)

    info = platforms_info[platform]

    if not info.enabled:
        console.print(f"[yellow]Platform {info.display_name} is already disabled[/yellow]")
        sys.exit(0)

    if not Confirm.ask(f"Disable platform {info.display_name}?"):
        console.print("Cancelled")
        sys.exit(0)

    if manager.disable_platform(platform):
        console.print(f"[yellow]○ Disabled platform: {info.display_name}[/yellow]")
        sys.exit(0)
    else:
        console.print(f"[red]❌ Failed to disable platform: {info.display_name}[/red]")
        sys.exit(1)


@platforms.command("install", help=("Guide installation of platform dependencies."))
@click.argument("platform")
@click.option("--dry-run", is_flag=True, help="Show installation commands without executing")
def install_platform(platform: str, dry_run: bool):
    platform = normalize_platform_name(platform)
    manager = get_platform_manager()
    guide = manager.get_installation_guide(platform)

    if not guide:
        _check_removed_platform(platform)
        console.print(f"[red]❌ Unknown platform: {platform}[/red]")
        platforms_info = manager.detect_platforms()
        available = list(platforms_info.keys())
        console.print(f"Available platforms: {', '.join(available)}")
        sys.exit(1)

    assert guide is not None

    console.print(
        Panel.fit(
            Text(f"Installation Guide: {guide['platform']}", style="bold cyan"),
            style="cyan",
        )
    )

    console.print(f"\n[bold]Platform:[/bold] {guide['platform']}")
    console.print(f"[bold]Description:[/bold] {guide['description']}")
    console.print(f"[bold]Category:[/bold] {guide['category'].title()}")

    if guide["available"]:
        console.print("[bold]Status:[/bold] [green]Already installed and available[/green]")
        console.print(f"\nUse [cyan]benchbox platforms enable {platform}[/cyan] to enable this platform.")
        sys.exit(0)

    console.print("[bold]Status:[/bold] [red]Missing dependencies[/red]")

    if guide["missing_libraries"]:
        console.print("\n[bold]Missing Libraries:[/bold]")
        for lib in guide["missing_libraries"]:
            console.print(f"  • {lib}")

    console.print("\n[bold]Installation Command:[/bold]")
    console.print(f"  [cyan]{guide['installation_command']}[/cyan]")

    console.print("\n[bold]Requirements:[/bold]")
    for req in guide["requirements"]:
        console.print(f"  • {req}")

    if dry_run:
        console.print("\n[yellow]Dry run mode: No installation performed[/yellow]")
        sys.exit(0)

    console.print(f"\nAfter installation, run: [cyan]benchbox platforms enable {platform}[/cyan]")
    sys.exit(0)


@platforms.command("check", help=("Check platform availability and configuration."))
@click.argument("platforms_to_check", nargs=-1)
@click.option("--enabled-only", is_flag=True, help="Check only enabled platforms")
def check_platforms(platforms_to_check: tuple, enabled_only: bool):
    manager = get_platform_manager()
    platforms_info = manager.detect_platforms()

    if not platforms_to_check:
        selected_platforms = (
            tuple((p, p) for p in manager.get_enabled_platforms())
            if enabled_only
            else tuple((p, p) for p in platforms_info.keys())
        )
    else:
        selected_platforms = tuple((p, normalize_platform_name(p)) for p in platforms_to_check)

    if not selected_platforms:
        console.print("[yellow]No platforms to check[/yellow]")
        sys.exit(0)

    console.print("[bold cyan]Platform Check Results[/bold cyan]\n")

    all_good = True
    for requested_platform, platform in selected_platforms:
        if platform not in platforms_info:
            console.print(f"[red]❌ {platform}: Unknown platform[/red]")
            all_good = False
            continue

        info = platforms_info[platform]
        readiness_platform = _readiness_platform_name(requested_platform, platform)
        readiness_results = check_platform_readiness(readiness_platform)
        readiness_failed = has_readiness_failures(readiness_results)

        if info.available and not info.enabled:
            console.print(f"[yellow]○ {info.display_name}: Available but disabled[/yellow]")
        elif info.available and readiness_failed:
            console.print(f"[yellow]⚠️ {info.display_name}: Environment not ready[/yellow]")
            all_good = False
        elif info.enabled and info.available:
            console.print(f"[green]✅ {info.display_name}: Ready[/green]")
        else:
            console.print(f"[red]❌ {info.display_name}: Missing dependencies[/red]")
            console.print(f"   Install: {info.installation_command}")
            all_good = False

        _print_readiness_details(readiness_results)

    if all_good:
        console.print("\n[green]All checked platforms are ready![/green]")
        sys.exit(0)
    else:
        console.print("\n[red]Some platforms need attention[/red]")
        sys.exit(1)


@platforms.command(
    "setup",
    help=(
        "Enable and install local platform adapters, interactively.\n"
        "\n"
        "This is about which adapters are available on this machine. For cloud\n"
        "CREDENTIALS -- Databricks, Snowflake, BigQuery, Redshift, Athena,\n"
        "MotherDuck, SingleStore -- use `benchbox setup --platform <name>`.\n"
        "\n"
        'The two commands are both spelled "setup", and adapter error messages have\n'
        "repeatedly sent users to `benchbox platforms setup --platform <name>`,\n"
        "which had no such option and exited 2. Rather than leave that a dead end,\n"
        "`--platform` here delegates to `benchbox setup`, which is what the user\n"
        "meant. `benchbox setup` rejects a non-cloud platform with its own list."
    ),
)
@click.option("--interactive/--non-interactive", default=True, help="Interactive setup mode")
@click.option(
    "--platform",
    "credential_platform",
    type=click.Choice(SUPPORTED_SETUP_PLATFORMS, case_sensitive=False),
    help="Configure credentials for one cloud platform (delegates to `benchbox setup`)",
)
@click.pass_context
def setup_platforms(ctx: click.Context, interactive: bool, credential_platform: str | None):
    if credential_platform is not None:
        from benchbox.cli.commands.setup import setup_credentials

        ctx.invoke(setup_credentials, platform=credential_platform)
        return

    manager = get_platform_manager()
    platforms_info = manager.detect_platforms()

    console.print(Panel.fit(Text("BenchBox Platform Setup", style="bold cyan"), style="cyan"))

    if not interactive:
        console.print("\n[yellow]Non-interactive mode: Enabling all available platforms[/yellow]")
        enabled_count = 0
        for name, info in platforms_info.items():
            if info.available and not info.enabled and manager.enable_platform(name):
                console.print(f"[green]✅ Enabled: {info.display_name}[/green]")
                enabled_count += 1

        console.print(f"\n[bold]Summary:[/bold] Enabled {enabled_count} platforms")
        sys.exit(0)

    console.print("\nThis wizard will help you set up database platforms for BenchBox.")
    console.print("You can enable/disable platforms and get installation guidance.\n")

    enabled_count = sum(1 for info in platforms_info.values() if info.enabled)
    available_count = sum(1 for info in platforms_info.values() if info.available)
    missing_count = sum(1 for info in platforms_info.values() if not info.available)

    console.print(
        f"[bold]Current Status:[/bold] {enabled_count} enabled, {available_count} available, {missing_count} missing dependencies\n"
    )

    action_options = [
        ("enable", "Enable a platform"),
        ("disable", "Disable a platform"),
        ("install", "Get installation guide"),
        ("status", "Show detailed status"),
        ("done", "Done - exit setup"),
    ]

    while True:
        action = NumberedSelectPrompt.ask(
            "What would you like to do?",
            options=action_options,
            default="done",
            console=console,
        )

        if action == "done":
            break
        elif action == "status":
            manager.display_platform_status()
        elif action == "install":
            console.print("\n[bold]Select platform for installation guide:[/bold]")
            platform = numbered_platform_select(
                "Platform",
                platforms_info,
                filter_func=lambda info: not info.available,
                group_by_status=False,
                console_instance=console,
            )
            if platform:
                assert install_platform.callback is not None
                install_platform.callback(platform, dry_run=False)
            else:
                console.print("[yellow]No platforms with missing dependencies.[/yellow]")
        elif action == "enable":
            console.print("\n[bold]Select platform to enable:[/bold]")
            platform = numbered_platform_select(
                "Platform",
                platforms_info,
                filter_func=lambda info: info.available and not info.enabled,
                group_by_status=False,
                console_instance=console,
            )
            if platform:
                assert enable_platform.callback is not None
                enable_platform.callback(platform, force=False)
            else:
                console.print("[yellow]No available platforms to enable.[/yellow]")
        elif action == "disable":
            console.print("\n[bold]Select platform to disable:[/bold]")
            platform = numbered_platform_select(
                "Platform",
                platforms_info,
                filter_func=lambda info: info.enabled,
                group_by_status=False,
                console_instance=console,
            )
            if platform:
                assert disable_platform.callback is not None
                disable_platform.callback(platform)
            else:
                console.print("[yellow]No enabled platforms to disable.[/yellow]")

        platforms_info = manager.detect_platforms()
        console.print()

    console.print("[green]Platform setup complete![/green]")

    enabled_count = sum(1 for info in platforms_info.values() if info.enabled)
    available_count = sum(1 for info in platforms_info.values() if info.available)

    console.print(f"\n[bold]Final Status:[/bold] {enabled_count} enabled, {available_count} available")
