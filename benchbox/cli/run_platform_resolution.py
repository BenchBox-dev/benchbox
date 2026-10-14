from __future__ import annotations

import importlib.util
import logging
import types
from typing import Any

import click
from rich.markup import escape

from benchbox.cli.benchmark_hooks import BenchmarkHookRegistry, BenchmarkOptionError
from benchbox.cli.platform import (
    get_platform_alias_mode,
    normalize_platform_name,
    resolve_platform_selector,
)
from benchbox.cli.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.cli.shared import console, set_quiet_output
from benchbox.cli.verbose_logging import setup_verbose_logging
from benchbox.core.benchmark_registry import get_benchmark_default_scale
from benchbox.core.execution_engine import UnsupportedExecutionEngineError, resolve_requested
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.run_service import warn_polars_streaming_flag
from benchbox.platforms import is_dataframe_platform, list_available_dataframe_platforms
from benchbox.platforms.adapter_factory import _reject_removed_platform

BENCHMARK_ALIASES: dict[str, str] = {
    "tpc-h": "tpch",
    "tpc_h": "tpch",
    "tpc-ds": "tpcds",
    "tpc_ds": "tpcds",
    "tpcdsobt": "tpcds_obt",
    "tpcds-obt": "tpcds_obt",
    "tpc-ds-obt": "tpcds_obt",
    "tpc-ds_obt": "tpcds_obt",
    "tpc_ds_obt": "tpcds_obt",
    "star-schema": "ssb",
    "starschema": "ssb",
    "star_schema": "ssb",
    "star-schema-benchmark": "ssb",
}


def normalize_benchmark_name(name: str) -> str:
    normalized = name.lower()
    return BENCHMARK_ALIASES.get(normalized, normalized)


def _reject_external_tuned(console: Any, logger: logging.Logger | None, ctx: click.Context) -> None:
    console.print("[red]❌ Error: --table-mode external is incompatible with tuning enabled[/red]")
    console.print("[yellow]Use --table-mode native, or --tuning notuning[/yellow]")
    if logger:
        logger.error("Invalid flag combination: --table-mode external with tuning enabled")
    ctx.exit(1)


def _tuning_arg_is_tuning_bearing(tuning_arg: str | None) -> bool:
    if not tuning_arg:
        return False
    return tuning_arg.strip().lower() != "notuning"


def _apply_dataframe_suffix_mode(s: types.SimpleNamespace) -> None:
    if s.mode is None and s.platform:
        s.mode = get_platform_alias_mode(s.platform)


def _split_deployment_selector(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    from benchbox.core.deployment import DEPLOYMENT_ALIAS_KEYS

    key = normalize_platform_name(raw)
    if ":" not in key:
        return key, None
    base, _, suffix = key.partition(":")
    try:
        resolved = resolve_platform_selector(key)
    except ValueError:
        return key, None
    if resolved != base:
        return resolved, None
    if base not in DEPLOYMENT_ALIAS_KEYS:
        return key, None
    return base, suffix


def _apply_deployment_selector_option(s: types.SimpleNamespace) -> None:
    from benchbox.core.deployment import (
        DEPLOYMENT_ALIAS_KEYS,
        normalize_deployment_value,
        note_deprecated_alias,
    )

    base = s.platform_key or ""
    selector = getattr(s, "deployment_selector", None)
    if base not in DEPLOYMENT_ALIAS_KEYS:
        return
    if selector is not None:
        selector = normalize_deployment_value(base, selector)
        s.deployment_selector = selector
    specs = PlatformHookRegistry.list_option_specs(base)
    alias_to_canonical = {alias.lower(): name for name, spec in specs.items() for alias in spec.aliases}

    def _canonical(raw_key: str) -> str:
        lowered = raw_key.lower()
        return alias_to_canonical.get(lowered, lowered)

    raw_keys = [key for key, _value in (s.platform_option_pairs or ())]
    canonical_keys = {_canonical(key) for key in raw_keys}
    spelling: dict[str, str] = {}
    for raw_key in raw_keys:
        spelling[_canonical(raw_key)] = raw_key
    parsed = s.parsed_platform_options or {}
    seen: set[str] = set()
    for alias in DEPLOYMENT_ALIAS_KEYS[base]:
        canonical = alias_to_canonical.get(alias, alias)
        if canonical in seen or canonical not in canonical_keys or parsed.get(canonical) is None:
            continue
        seen.add(canonical)
        typed = spelling.get(canonical, canonical)
        value = normalize_deployment_value(base, parsed[canonical])
        if selector is not None and value != selector:
            console.print(
                f"[red]❌ Deployment selector '{base}:{selector}' conflicts with "
                f"platform option '{typed}={escape(str(parsed[canonical]))}'. Use one deployment selection.[/red]"
            )
            if s.logger:
                s.logger.error("Deployment selector conflicts with deprecated deployment option")
            s.ctx.exit(1)
            return
        note_deprecated_alias(base, typed, value)


def _apply_gateway_option(s: types.SimpleNamespace) -> None:
    gateway = getattr(s, "gateway", None)
    if not gateway and hasattr(s, "parsed_platform_options"):
        gateway = s.parsed_platform_options.get("gateway")
    if not gateway:
        return
    from benchbox.core.gateway import (
        GatewayConfigurationError,
        UnsupportedGatewayError,
        resolve_requested_gateway,
    )

    try:
        if not s.platform_key:
            raise GatewayConfigurationError("--gateway requires --platform")
        s.gateway = resolve_requested_gateway(s.platform_key, gateway)
    except (UnsupportedGatewayError, GatewayConfigurationError) as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if s.logger:
            s.logger.error(f"Gateway option error: {exc}")
        s.ctx.exit(1)


def _parse_plat_bench_options(s: types.SimpleNamespace) -> None:
    s.logger, s.verbosity_settings = setup_verbose_logging(s.verbose, quiet=bool(s.quiet))
    set_quiet_output(s.verbosity_settings.quiet)
    s.ctx.obj["verbosity"] = s.verbosity_settings
    s.verbosity_payload = s.verbosity_settings.to_config()
    if getattr(s, "polars_streaming", False):
        warn_polars_streaming_flag()

    _apply_dataframe_suffix_mode(s)
    s.platform_key, s.deployment_selector = _split_deployment_selector(s.platform)
    s.benchmark = normalize_benchmark_name(s.benchmark) if s.benchmark else None
    s.table_mode = (s.table_mode or "native").lower()
    s.table_mode_cli_supplied = False
    if hasattr(s.ctx, "get_parameter_source"):
        try:
            s.table_mode_cli_supplied = (
                s.ctx.get_parameter_source("table_mode") == click.core.ParameterSource.COMMANDLINE
            )
        except Exception:
            s.table_mode_cli_supplied = False

    if s.platform_option_pairs and not s.platform_key:
        console.print("[red]❌ Platform options require a --platform selection[/red]")
        s.ctx.exit(1)

    s.parsed_platform_options = {}
    s.explicit_platform_option_keys = frozenset()
    if s.platform_key:
        try:
            s.parsed_platform_options = PlatformHookRegistry.parse_options(s.platform_key, s.platform_option_pairs)
            s.explicit_platform_option_keys = PlatformHookRegistry.supplied_option_keys(
                s.platform_key, s.platform_option_pairs
            )
        except PlatformOptionError as exc:
            console.print(f"[red]❌ {exc}[/red]")
            if s.logger:
                s.logger.error(f"Platform option error: {exc}")
            s.ctx.exit(1)
    _apply_deployment_selector_option(s)
    _apply_gateway_option(s)

    if s.benchmark_option_pairs and not s.benchmark:
        console.print("[red]❌ Benchmark options require a --benchmark selection[/red]")
        s.ctx.exit(1)

    s.parsed_benchmark_options = {}
    if s.benchmark and s.benchmark_option_pairs:
        try:
            from benchbox.core.benchmark_loader import get_core_benchmark_class

            get_core_benchmark_class(s.benchmark)
        except ValueError:
            pass
        try:
            s.parsed_benchmark_options = BenchmarkHookRegistry.parse_options(s.benchmark, s.benchmark_option_pairs)
        except BenchmarkOptionError as exc:
            console.print(f"[red]❌ {exc}[/red]")
            if s.logger:
                s.logger.error(f"Benchmark option error: {exc}")
            s.ctx.exit(1)

    if s.logger:
        s.logger.debug("Starting BenchBox CLI run command")
        s.logger.debug(
            f"Arguments: platform={s.platform}, benchmark={s.benchmark}, scale={s.scale}, verbose={s.verbose}"
        )

    if s.table_mode == "external" and _tuning_arg_is_tuning_bearing(s.tuning):
        _reject_external_tuned(console, s.logger, s.ctx)


def _apply_benchmark_default_scale(s: types.SimpleNamespace) -> None:
    if not s.benchmark:
        return

    s.benchmark = normalize_benchmark_name(s.benchmark)
    if not hasattr(s.ctx, "get_parameter_source"):
        return

    try:
        scale_source = s.ctx.get_parameter_source("scale")
    except Exception:
        return

    if scale_source == click.core.ParameterSource.DEFAULT:
        s.scale = get_benchmark_default_scale(s.benchmark, fallback=s.scale)


def _platform_option_sources_for_state(s: types.SimpleNamespace) -> dict[str, str]:
    if not s.platform_key or not s.parsed_platform_options:
        return {}

    explicit_keys = PlatformHookRegistry.supplied_option_keys(s.platform_key, s.platform_option_pairs or ())
    defaults = PlatformHookRegistry.get_default_options(s.platform_key)
    sources: dict[str, str] = {}
    for key in s.parsed_platform_options:
        if key in explicit_keys:
            sources[key] = "cli_option"
        elif key in defaults:
            sources[key] = "registered_default"
        else:
            sources[key] = "requested"
    return sources


def _platform_option_config_entries(s: types.SimpleNamespace) -> dict[str, Any]:
    if not s.parsed_platform_options:
        return {}
    entries: dict[str, Any] = {"platform_options": dict(s.parsed_platform_options)}
    sources = _platform_option_sources_for_state(s)
    if sources:
        entries["platform_option_sources"] = sources
    return entries


def _check_platforms_status(s: types.SimpleNamespace) -> None:
    if not s.check_platforms:
        return
    console.print("\n[bold cyan]Checking Platform Status...[/bold cyan]")
    enabled_platforms = s.platform_manager.get_enabled_platforms()
    if not enabled_platforms:
        console.print("[red]❌ No platforms are enabled![/red]")
        console.print("Run [cyan]benchbox platforms setup[/cyan] to configure platforms.")
        s.ctx.exit(1)

    all_good = True
    for platform_name in enabled_platforms:
        if s.platform_manager.is_platform_available(platform_name):
            console.print(f"[green]✅ {platform_name}: Ready[/green]")
        else:
            console.print(f"[red]❌ {platform_name}: Missing dependencies[/red]")
            all_good = False

    if not all_good:
        console.print(
            "\n[red]Some platforms need attention. Run [cyan]benchbox platforms status[/cyan] for details.[/red]"
        )
        s.ctx.exit(1)
    else:
        console.print("[green]All enabled platforms are ready![/green]")


def _validate_not_removed_platform(s: types.SimpleNamespace) -> bool:
    raw_platform = getattr(s, "platform", None)
    for candidate in (raw_platform, getattr(s, "platform_key", None)):
        if not candidate:
            continue
        try:
            _reject_removed_platform(candidate)
        except ValueError as exc:
            console.print(f"[red]❌ {exc}[/red]")
            if s.logger:
                s.logger.error(str(exc))
            if hasattr(s, "ctx") and s.ctx is not None and hasattr(s.ctx, "exit"):
                s.ctx.exit(1)
                return False
            raise
    return True


def _resolve_execution_engine(s: types.SimpleNamespace, platform_key: str) -> None:
    requested = getattr(s, "execution_engine", None) or DEFAULT_EXECUTION_ENGINE
    try:
        s.resolved_execution_engine = resolve_requested(platform_key, requested)
    except UnsupportedExecutionEngineError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if s.logger:
            s.logger.error(str(exc))
        s.ctx.exit(1)


def _resolve_platform_mode(s: types.SimpleNamespace) -> None:
    s.resolved_mode = None
    s.resolved_execution_engine = getattr(s, "execution_engine", None) or DEFAULT_EXECUTION_ENGINE
    if not _validate_not_removed_platform(s):
        return
    if not s.platform_key:
        return

    try:
        platform_key = resolve_platform_selector(s.platform_key)
    except ValueError as exc:
        console.print(f"[red]❌ {escape(str(exc))}[/red]")
        if s.logger:
            s.logger.error(str(exc))
        s.ctx.exit(1)
        return

    caps = PlatformRegistry.get_platform_capabilities(platform_key)
    if caps is None:
        is_df_platform_legacy = is_dataframe_platform(platform_key)
        is_df_available = is_df_platform_legacy and list_available_dataframe_platforms().get(platform_key, False)
        if not is_df_available and not s.dry_run:
            from benchbox.utils.dependencies import get_install_command

            console.print(f"[red]❌ Platform '{platform_key}' is not available (missing dependencies)[/red]")
            install_cmd = get_install_command(platform_key)
            console.print(f"Run [cyan]{escape(install_cmd)}[/cyan] to install dependencies.")
            s.ctx.exit(1)
        s.resolved_mode = "dataframe"
    else:
        if s.mode is not None:
            if not PlatformRegistry.supports_mode(platform_key, s.mode):
                supported_modes = []
                if caps.supports_sql:
                    supported_modes.append("sql")
                if caps.supports_dataframe:
                    supported_modes.append("dataframe")
                console.print(f"[red]❌ Platform '{platform_key}' does not support {s.mode} mode[/red]")
                console.print(f"[yellow]Supported modes: {', '.join(supported_modes)}[/yellow]")
                if s.logger:
                    s.logger.error(f"Platform {platform_key} does not support mode: {s.mode}")
                s.ctx.exit(1)
            s.resolved_mode = s.mode
        else:
            s.resolved_mode = caps.default_mode

        if s.resolved_mode == "sql":
            if platform_key == "polars":
                is_available = importlib.util.find_spec("polars") is not None
            else:
                is_available = s.platform_manager.is_platform_available(platform_key)
        else:
            is_available = caps.supports_dataframe
            if is_available and platform_key in ["polars", "pandas", "cudf", "dask"]:
                df_platforms = list_available_dataframe_platforms()
                legacy_key = f"{platform_key}-df"
                is_available = df_platforms.get(legacy_key, df_platforms.get(platform_key, False))

        if not is_available and not s.dry_run:
            from benchbox.utils.dependencies import get_install_command

            console.print(f"[red]❌ Platform '{platform_key}' is not available (missing dependencies)[/red]")
            install_cmd = get_install_command(platform_key)
            console.print(f"Run [cyan]{escape(install_cmd)}[/cyan] to install dependencies.")
            s.ctx.exit(1)

    if s.logger and s.resolved_mode:
        s.logger.debug(f"Resolved execution mode for {platform_key}: {s.resolved_mode}")
    _resolve_execution_engine(s, platform_key)


def _check_benchmark_platform_compatibility(s: types.SimpleNamespace) -> None:
    if not s.platform_key or not s.benchmark:
        return

    try:
        gated_platform = resolve_platform_selector(s.platform_key)
    except ValueError:
        gated_platform = s.platform_key

    block_reason = PlatformRegistry.get_benchmark_block_reason(gated_platform, s.benchmark)

    if block_reason is None:
        return

    console.print(f"[red]❌ Benchmark '{s.benchmark}' is not compatible with platform '{s.platform_key}'[/red]")
    console.print(f"[yellow]{block_reason}[/yellow]")
    if s.logger:
        s.logger.error(f"Benchmark '{s.benchmark}' is incompatible with platform '{s.platform_key}': {block_reason}")
    s.ctx.exit(1)


def _interactive_resolve_mode_for_selected_platform(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    platform_type = s.database_config.type
    caps = PlatformRegistry.get_platform_capabilities(platform_type)
    if caps is not None:
        if s.mode is not None:
            if not PlatformRegistry.supports_mode(platform_type, s.mode):
                supported_modes = []
                if caps.supports_sql:
                    supported_modes.append("sql")
                if caps.supports_dataframe:
                    supported_modes.append("dataframe")
                console.print(f"[red]❌ Platform '{platform_type}' does not support {s.mode} mode[/red]")
                console.print(f"[yellow]Supported modes: {', '.join(supported_modes)}[/yellow]")
                ctx.exit(1)
            s.resolved_mode = s.mode
            s.database_config.execution_mode = s.resolved_mode
        elif hasattr(s.database_config, "execution_mode") and s.database_config.execution_mode in ("sql", "dataframe"):
            s.resolved_mode = s.database_config.execution_mode
        else:
            s.resolved_mode = caps.default_mode
            s.database_config.execution_mode = s.resolved_mode
    else:
        s.resolved_mode = s.mode if s.mode is not None else "sql"
        s.database_config.execution_mode = s.resolved_mode
