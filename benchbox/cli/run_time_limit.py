from __future__ import annotations

import functools
import logging
import os
import sys
from collections.abc import Callable
from typing import IO, Any

import click
from rich.markup import escape

from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.shared import console
from benchbox.core.platform_manifest import get_all_platform_aliases, get_platform_manifest_entry
from benchbox.utils.run_deadline import (
    HARD_STOP_GRACE_SECONDS,
    RUN_TIMEOUT_CONFIG_KEY,
    TIMED_OUT_VALIDATION_STATUS,
    RunDeadline,
    active_run_deadline,
    arm_hard_stop,
    current_run_deadline,
    end_descendant_processes,
    remote_cleanup_warning,
    resolve_timeout_seconds,
)

RUN_TIMEOUT_HELP = (
    "Stop the whole run (generate, load and execute) after this many seconds; exits with code 124 and keeps a "
    "partial result marked timed_out. 0 means no limit. Default: no limit, or execution.timeout_seconds."
)


def timeout_option(help_text: str) -> Callable[[Any], Any]:
    return click.option(
        "--timeout", "timeout", type=click.IntRange(min=0), default=None, metavar="SECONDS", help=help_text
    )


def dispatch_run(s: Any) -> None:
    from benchbox.cli.commands import run as run_module

    if s.test_execution_type in {"data_only", "load_only"}:
        run_module._run_data_or_load_only(s)
    elif s.platform and s.benchmark:
        run_module._run_direct(s)
    else:
        from benchbox.cli.run_interactive import _run_interactive

        _run_interactive(s)


def report_timeout(deadline: RunDeadline) -> None:
    console.print(f"\n[red]❌ {escape(str(deadline.record_timeout()))}[/red]")


def report_cleanup_errors(deadline: RunDeadline) -> None:
    for error in deadline.cleanup_errors:
        console.print(f"[red]Remote cleanup failed: {escape(error)}[/red]")


def cloud_cleanup_warning(platform: str | None) -> str | None:
    if not platform:
        return None
    key = platform.lower()
    entry = get_platform_manifest_entry(get_all_platform_aliases().get(key, key))
    if entry is None or entry.metadata_dict().get("category") != "cloud":
        return None
    implemented = bool(entry.capabilities.get("run_timeout_cleanup", False))
    return remote_cleanup_warning(str(entry.metadata_dict().get("display_name") or entry.key), implemented)


def write_hard_stop_result(
    deadline: RunDeadline,
    orchestrator: Any,
    benchmark_config: Any,
    database_config: Any,
    execution_context: Any,
    quiet: bool,
    stdout: IO[str],
) -> None:
    from benchbox.cli.commands.run import _export_orchestrated_result
    from benchbox.core.run_service import build_timed_out_result

    result = build_timed_out_result(benchmark_config, database_config, deadline)
    exported_files = _export_orchestrated_result(
        orchestrator=orchestrator,
        result=result,
        benchmark=benchmark_config.name,
        scale=benchmark_config.scale_factor,
        platform_label=str(result.platform).lower(),
        mode_label=getattr(execution_context, "mode", None) or "sql",
        quiet=quiet,
        export_formats=["json"],
    )
    for filepath in exported_files.values():
        click.echo(filepath, file=stdout)


def hard_stop(deadline: RunDeadline) -> None:
    try:
        report_timeout(deadline)
        console.print(
            f"[red]The run did not stop within {HARD_STOP_GRACE_SECONDS:g} seconds of its limit; BenchBox is exiting. "
            "Work on a remote platform may still be running.[/red]"
        )
        if deadline.write_partial_result is not None:
            try:
                deadline.write_partial_result()
            except Exception as exc:
                console.print(f"[red]The partial result could not be written: {escape(str(exc))}[/red]")
        deadline.cancel_remote_work()
        report_cleanup_errors(deadline)
        deadline.stop_children()
        end_descendant_processes()
        logging.shutdown()
    finally:
        os._exit(ExitCode.TIMEOUT)


def bounded_orchestrated_run(execute: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(execute)
    def bounded(
        orchestrator: Any,
        benchmark_config: Any,
        system_profile: Any,
        database_config: Any,
        phases_to_run: list[str],
        **options: Any,
    ) -> Any:
        run = functools.partial(
            execute, orchestrator, benchmark_config, system_profile, database_config, phases_to_run, **options
        )
        deadline = current_run_deadline()
        if deadline is None:
            return run()

        deadline.write_partial_result = functools.partial(
            write_hard_stop_result,
            deadline,
            orchestrator,
            benchmark_config,
            database_config,
            options.get("execution_context"),
            bool(options.get("quiet")),
            sys.stdout,
        )
        deadline.start()
        disarm = arm_hard_stop(deadline, hard_stop)
        try:
            result = run()
        finally:
            disarm()
        deadline.run_timed_out = getattr(result, "validation_status", None) == TIMED_OUT_VALIDATION_STATUS
        return result

    return bounded


def run_within_time_limit(s: Any) -> None:
    try:
        timeout_seconds = resolve_timeout_seconds(s.timeout, s.config.get(RUN_TIMEOUT_CONFIG_KEY))
    except ValueError as exc:
        raise click.UsageError(f"{RUN_TIMEOUT_CONFIG_KEY}: {exc}") from exc
    if timeout_seconds is None:
        dispatch_run(s)
        return

    warning = cloud_cleanup_warning(s.platform)
    if warning:
        console.print(f"[yellow]{escape(warning)}[/yellow]", soft_wrap=True)
    deadline = RunDeadline(timeout_seconds, started=False)
    try:
        with active_run_deadline(deadline):
            dispatch_run(s)
    except (click.exceptions.Exit, SystemExit):
        if not deadline.run_timed_out:
            raise
    except Exception as exc:
        if not deadline.run_timed_out:
            raise
        console.print(f"[red]❌ {escape(str(exc))}[/red]")
    if deadline.run_timed_out:
        report_timeout(deadline)
        report_cleanup_errors(deadline)
        sys.exit(ExitCode.TIMEOUT)
