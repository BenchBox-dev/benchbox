from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from benchbox.core.dataframe.tuning.profiles import DATAFRAME_PLATFORMS
from benchbox.core.tuning import modes as tuning_modes
from benchbox.core.tuning.capability_registry import DEFAULT_FALLBACK_DESCRIPTION, get_fallback_description
from benchbox.core.tuning.packaged_templates import (
    list_packaged_templates,
    packaged_template_path,
    template_platform_key,
)

if TYPE_CHECKING:
    from logging import Logger

    from benchbox.cli.config import ConfigManager
    from benchbox.core.config import DatabaseConfig
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

_REPO_ROOT = Path(__file__).resolve().parents[2]

UNIFIED_TUNING_KEYS = frozenset(
    {
        "primary_keys",
        "foreign_keys",
        "unique_constraints",
        "check_constraints",
        "platform_optimizations",
        "table_tunings",
    }
)

DATAFRAME_TUNING_KEYS = frozenset(
    {
        "execution",
        "memory",
        "data_types",
        "parallelism",
        "io",
        "gpu",
    }
)


def is_dataframe_tuning_file(path_or_data: Path | str | dict) -> bool:
    if isinstance(path_or_data, (Path, str)):
        from benchbox.core.config_utils import load_config_file

        try:
            data = load_config_file(Path(path_or_data))
        except Exception:
            return False
    else:
        data = path_or_data
    if not isinstance(data, dict):
        return False
    metadata = data.get("_metadata")
    if isinstance(metadata, dict) and metadata.get("format") == "dataframe_tuning":
        return True
    keys = set(data) - {"_metadata"}
    return bool(keys & DATAFRAME_TUNING_KEYS) and not (keys & UNIFIED_TUNING_KEYS)


def promote_tuning_provenance(
    database_config: DatabaseConfig | None,
    tuning_enabled: bool,
    tuning_source: str | None,
    tuning_source_file: str | None,
) -> None:
    if database_config is None:
        return
    database_config.tuning_enabled = tuning_enabled
    database_config.tuning_source = tuning_source
    database_config.tuning_source_file = tuning_source_file


def resolve_template_reference(config_file: Path | None, repo_root: Path | None = None) -> str | None:
    if config_file is None:
        return None
    resolved = Path(config_file).resolve()
    root = repo_root if repo_root is not None else _REPO_ROOT
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()[:16]
    except OSError:
        digest = "unreadable"
    return f"{resolved.name}:{digest}"


class TuningMode(Enum):
    TUNED = "tuned"
    NOTUNING = "notuning"
    AUTO = "auto"
    CUSTOM_FILE = "custom_file"


class TuningSource(Enum):
    EXPLICIT_FILE = "explicit_file"
    AUTO_DISCOVERED = "auto_discovered"
    PACKAGED_RESOURCE = "packaged_resource"
    SMART_DEFAULTS = "smart_defaults"
    BASELINE = "baseline"
    INTERACTIVE_WIZARD = "wizard"
    FALLBACK = "fallback"


@dataclass
class TuningResolution:
    mode: TuningMode
    source: TuningSource
    enabled: bool
    config_file: Path | None = None
    searched_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info_messages: list[str] = field(default_factory=list)
    fallback_description: str = DEFAULT_FALLBACK_DESCRIPTION

    @property
    def canonical_mode(self) -> str:
        if self.mode is TuningMode.TUNED and self.source is TuningSource.FALLBACK:
            return tuning_modes.TUNED_FALLBACK
        if self.mode is TuningMode.CUSTOM_FILE:
            return tuning_modes.CUSTOM
        return self.mode.value

    @property
    def source_description(self) -> str:
        descriptions = {
            TuningSource.EXPLICIT_FILE: f"Loaded from explicit path: {self.config_file}",
            TuningSource.AUTO_DISCOVERED: f"Auto-discovered template: {self.config_file}",
            TuningSource.PACKAGED_RESOURCE: f"Packaged template resource (bundled with benchbox): {self.config_file}",
            TuningSource.SMART_DEFAULTS: "Generated from system profile (auto mode)",
            TuningSource.BASELINE: "Baseline mode (all optimizations disabled)",
            TuningSource.INTERACTIVE_WIZARD: "Configured via interactive wizard",
            TuningSource.FALLBACK: f"Fallback to {self.fallback_description} (no template found)",
        }
        return descriptions.get(self.source, "Unknown source")


def _dataframe_profile_path(platform: str) -> Path | None:
    base = platform.lower().split(":", 1)[0]
    if base.endswith("-df"):
        base = base[: -len("-df")]
    if base not in DATAFRAME_PLATFORMS:
        return None
    return Path(f"examples/tunings/dataframe/{base}_optimized.yaml")


def get_tuning_template_paths(platform: str, benchmark: str, *, mode: str | None = None) -> list[Path]:
    paths = []
    raw_platform = platform.lower()
    template_platform = template_platform_key(platform)
    platform_dirs = [raw_platform] if raw_platform == template_platform else [raw_platform, template_platform]
    template_name = f"{benchmark.lower()}_tuned.yaml"

    env_path = os.environ.get("BENCHBOX_TUNING_PATH")
    if env_path:
        paths.extend(Path(env_path) / directory / template_name for directory in platform_dirs)

    paths.extend(Path(f"examples/tunings/{directory}/{template_name}") for directory in platform_dirs)

    for directory in platform_dirs:
        cwd_template = Path(f"{directory}/{template_name}")
        if cwd_template not in paths:
            paths.append(cwd_template)

    paths.append(packaged_template_path(template_platform, benchmark))

    if mode != "sql":
        dataframe_profile = _dataframe_profile_path(platform)
        if dataframe_profile is not None:
            paths.append(dataframe_profile)

    return paths


def list_available_tuning_templates(
    platform: str | None = None,
    benchmark: str | None = None,
    base_path: Path | None = None,
) -> dict[str, list[Path]]:
    using_default_base = base_path is None
    if base_path is None:
        base_path = Path("examples/tunings")

    templates: dict[str, list[Path]] = {}

    if not base_path.exists():
        if using_default_base:
            return list_packaged_templates(platform=platform, benchmark=benchmark)
        return templates

    for platform_dir in base_path.iterdir():
        if not platform_dir.is_dir():
            continue

        platform_name = platform_dir.name

        if platform and platform.lower() != platform_name.lower():
            continue

        platform_templates = []
        for template_file in platform_dir.glob("*.yaml"):
            if benchmark and not template_file.stem.lower().startswith(benchmark.lower()):
                continue

            platform_templates.append(template_file)

        if platform_templates:
            templates[platform_name] = sorted(platform_templates)

    return templates


def resolve_tuning(
    tuning_arg: str,
    platform: str | None,
    benchmark: str | None,
    config_manager: ConfigManager,
    console: Console,
    logger: Logger | None = None,
    quiet: bool = False,
    non_interactive: bool = False,
    mode: str | None = None,
) -> TuningResolution:
    tuning_lower = tuning_arg.lower()

    if tuning_lower == "notuning":
        return _resolve_notuning(logger)

    if tuning_lower == "auto":
        return _resolve_auto(logger)

    if tuning_lower == "tuned":
        return _resolve_tuned(platform, benchmark, config_manager, logger, mode=mode)

    tuning_path = Path(tuning_arg)
    if tuning_path.exists():
        return _resolve_explicit_file(tuning_path, logger)

    _raise_invalid_tuning_value(tuning_arg)


def _resolve_notuning(logger: Logger | None) -> TuningResolution:
    resolution = TuningResolution(
        mode=TuningMode.NOTUNING,
        source=TuningSource.BASELINE,
        enabled=False,
    )
    resolution.info_messages.append("Tuning disabled: running baseline comparison (no optimizations)")
    if logger:
        logger.debug("Tuning mode: notuning (baseline)")
    return resolution


def _resolve_auto(logger: Logger | None) -> TuningResolution:
    resolution = TuningResolution(
        mode=TuningMode.AUTO,
        source=TuningSource.SMART_DEFAULTS,
        enabled=True,
    )
    resolution.info_messages.append(
        "Tuning mode: auto (smart defaults based on system profile - DataFrame platforms only today)"
    )
    if logger:
        logger.debug("Tuning mode: auto (smart defaults; DataFrame platforms only)")
    return resolution


def _resolve_explicit_file(tuning_path: Path, logger: Logger | None) -> TuningResolution:
    resolution = TuningResolution(
        mode=TuningMode.CUSTOM_FILE,
        source=TuningSource.EXPLICIT_FILE,
        enabled=True,
        config_file=tuning_path.resolve(),
    )
    resolution.info_messages.append(f"Tuning: loading configuration from {resolution.config_file}")
    if logger:
        logger.debug(f"Tuning mode: explicit file ({resolution.config_file})")
    return resolution


def _resolve_tuned(
    platform: str | None,
    benchmark: str | None,
    config_manager: ConfigManager,
    logger: Logger | None,
    *,
    mode: str | None = None,
) -> TuningResolution:
    resolution = TuningResolution(
        mode=TuningMode.TUNED,
        source=TuningSource.FALLBACK,
        enabled=True,
    )

    default_config = config_manager.get("tuning.default_config_file")
    if default_config:
        default_path = Path(default_config)
        if default_path.exists():
            resolution.source = TuningSource.EXPLICIT_FILE
            resolution.config_file = default_path.resolve()
            resolution.info_messages.append(
                f"Tuning: using default config from benchbox.yaml: {resolution.config_file}"
            )
            if logger:
                logger.debug(f"Tuning mode: tuned (config file default: {resolution.config_file})")
            return resolution
        else:
            resolution.warnings.append(f"Default tuning config '{default_config}' from benchbox.yaml not found")

    if platform and benchmark:
        search_paths = get_tuning_template_paths(platform, benchmark, mode=mode)
        resolution.searched_paths = search_paths

        packaged_candidate = packaged_template_path(template_platform_key(platform), benchmark)

        for path in search_paths:
            if path.exists():
                resolution.config_file = path.resolve()
                if path == packaged_candidate:
                    resolution.source = TuningSource.PACKAGED_RESOURCE
                    template_ref = resolve_template_reference(resolution.config_file)
                    resolution.info_messages.append(
                        f"Tuning: using packaged template resource at {resolution.config_file} (ref: {template_ref})"
                    )
                    if logger:
                        logger.debug(
                            f"Tuning mode: tuned (packaged resource: {resolution.config_file}, ref={template_ref})"
                        )
                else:
                    resolution.source = TuningSource.AUTO_DISCOVERED
                    resolution.info_messages.append(f"Tuning: auto-discovered template at {resolution.config_file}")
                    if logger:
                        logger.debug(f"Tuning mode: tuned (auto-discovered: {resolution.config_file})")
                return resolution

        resolution.warnings.append(
            f"No tuning template found for {platform}/{benchmark}. Searched: {', '.join(str(p) for p in search_paths)}"
        )
        resolution.fallback_description = get_fallback_description(platform)
        resolution.info_messages.append(
            f"Tuning: using {resolution.fallback_description} (no optimized template available)"
        )
        if logger:
            logger.debug("Tuning mode: tuned (fallback - no template found)")
    else:
        resolution.warnings.append("Cannot auto-discover tuning template without platform and benchmark specified")
        resolution.fallback_description = get_fallback_description(platform)
        resolution.info_messages.append(f"Tuning: using {resolution.fallback_description}")
        if logger:
            logger.debug("Tuning mode: tuned (fallback - no platform/benchmark)")

    return resolution


def _raise_invalid_tuning_value(tuning_arg: str) -> None:
    if "/" in tuning_arg or "\\" in tuning_arg or tuning_arg.endswith(".yaml"):
        raise ValueError(
            f"Tuning file not found: '{tuning_arg}'\n"
            f"Please verify the file exists at the specified path.\n"
            f"Use 'benchbox tuning list' to see available templates."
        )
    else:
        raise ValueError(
            f"Invalid tuning value: '{tuning_arg}'\n"
            f"Valid options:\n"
            f"  'tuned'    - Enable optimizations (auto-discovers template)\n"
            f"  'notuning' - Disable all optimizations (baseline mode)\n"
            f"  'auto'     - Use smart defaults based on system profile\n"
            f"  PATH       - Path to custom YAML config file\n"
            f"\nUse 'benchbox tuning list' to see available templates."
        )


def display_tuning_resolution(
    resolution: TuningResolution,
    console: Console,
    verbose: bool = False,
) -> None:
    for msg in resolution.info_messages:
        if resolution.source == TuningSource.BASELINE:
            console.print(f"[dim]{msg}[/dim]")
        elif resolution.source in (
            TuningSource.AUTO_DISCOVERED,
            TuningSource.EXPLICIT_FILE,
            TuningSource.PACKAGED_RESOURCE,
        ):
            console.print(f"[green]{msg}[/green]")
        else:
            console.print(f"[blue]{msg}[/blue]")

    for warning in resolution.warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")

    if verbose and resolution.searched_paths:
        console.print("[dim]Searched paths:[/dim]")
        for path in resolution.searched_paths:
            exists_marker = "[green]found[/green]" if path.exists() else "[dim]not found[/dim]"
            console.print(f"  [dim]{path}[/dim] ({exists_marker})")


def warn_sql_auto_mode(
    resolution: TuningResolution,
    resolved_mode: str | None,
    console: Console,
    logger: Logger | None = None,
    quiet: bool = False,
) -> None:
    if logger:
        logger.debug(f"Using basic unified config for mode: {resolution.mode.value}")
    if resolution.mode != TuningMode.AUTO or resolved_mode == "dataframe":
        return
    if not quiet:
        console.print(
            "[yellow]Warning: --tuning auto smart defaults are DataFrame-only today; "
            "this SQL run proceeds with a basic constraints-only configuration "
            "(primary/foreign/unique constraints enabled, plus check constraints except on DuckDB; "
            "no other tunings applied), "
            "not an untuned baseline.[/yellow]"
        )
    if logger:
        logger.debug("Tuning mode auto on a non-DataFrame platform: using basic unified config")


def display_tuning_list(
    console: Console,
    platform: str | None = None,
    benchmark: str | None = None,
) -> None:
    templates = list_available_tuning_templates(platform, benchmark)

    if not templates:
        if platform or benchmark:
            console.print(
                f"[yellow]No tuning templates found"
                f"{f' for platform {platform}' if platform else ''}"
                f"{f' and benchmark {benchmark}' if benchmark else ''}[/yellow]"
            )
        else:
            console.print("[yellow]No tuning templates found in examples/tunings/[/yellow]")

        console.print("\n[dim]Templates should be placed in examples/tunings/<platform>/<benchmark>_tuned.yaml[/dim]")
        return

    table = Table(title="Available Tuning Templates", show_header=True)
    table.add_column("Platform", style="cyan")
    table.add_column("Template", style="green")
    table.add_column("Path", style="dim")

    for platform_name, template_files in sorted(templates.items()):
        for template_file in template_files:
            template_name = template_file.stem
            table.add_row(
                platform_name,
                template_name,
                str(template_file),
            )

    console.print(table)

    console.print("\n[bold]Usage:[/bold]")
    console.print("  [cyan]benchbox run --tuning tuned[/cyan]   Auto-discovers template for platform/benchmark")
    console.print("  [cyan]benchbox run --tuning <path>[/cyan]  Uses specific template file")
    console.print("\n[dim]Template discovery pattern: examples/tunings/<platform>/<benchmark>_tuned.yaml[/dim]")


def display_tuning_show(
    console: Console,
    config: UnifiedTuningConfiguration | None,
    resolution: TuningResolution,
) -> None:
    import yaml
    from rich.syntax import Syntax

    content_lines = [
        f"[bold]Source:[/bold] {resolution.source_description}",
        f"[bold]Mode:[/bold] {resolution.mode.value}",
        f"[bold]Enabled:[/bold] {'Yes' if resolution.enabled else 'No'}",
    ]

    if resolution.config_file:
        content_lines.append(f"[bold]File:[/bold] {resolution.config_file}")

    panel = Panel(
        "\n".join(content_lines),
        title="Tuning Resolution",
        border_style="cyan",
    )
    console.print(panel)

    if config is not None:
        console.print("\n[bold]Configuration:[/bold]")
        config_dict = config.to_dict()
        config_yaml = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)
        syntax = Syntax(config_yaml, "yaml", theme="monokai", line_numbers=True)
        console.print(syntax)
