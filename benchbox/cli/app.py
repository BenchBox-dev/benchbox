import json

import click

import benchbox
from benchbox.cli.commands import register_commands
from benchbox.cli.help import BenchBoxGroup
from benchbox.cli.logo import styled_logo

CLI_HELP = f"""\
BenchBox {benchbox.__version__} - Interactive database benchmark runner.

BenchBox provides comprehensive benchmarking capabilities for OLAP databases
including TPC-H, TPC-DS, ClickBench, and many other benchmark suites.

Supports local databases (DuckDB, SQLite) and cloud platforms (Databricks,
BigQuery, Snowflake, Redshift, ClickHouse) with automatic data generation,
SQL dialect translation, and performance measurement.

\b
Quick start:
  benchbox run                                     Interactive benchmark runner
  benchbox run --platform duckdb --benchmark tpch  Direct execution
  benchbox check-deps                              Check platform dependencies
  benchbox platforms list                          View available platforms

For complete documentation: https://benchbox.dev/docs/
"""


def version_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return

    logo = styled_logo()
    if logo:
        click.echo(logo + "\n")

    try:
        from benchbox.utils.version import format_version_report

        click.echo(format_version_report())
    except ImportError:
        click.echo(f"BenchBox Version: {benchbox.__version__}")

    ctx.exit()


def version_json_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return

    try:
        from benchbox.utils.version import get_version_info

        payload = get_version_info()
        click.echo(json.dumps(payload, indent=2))
    except ImportError:
        click.echo(json.dumps({"benchbox_version": benchbox.__version__}))

    ctx.exit()


@click.group(cls=BenchBoxGroup, help=CLI_HELP)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=version_callback,
    help="Show version information and exit.",
)
@click.option(
    "--version-json",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=version_json_callback,
    help="Show version information as JSON and exit.",
)
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.ensure_object(dict)
    import importlib

    _cli_main = importlib.import_module("benchbox.cli.main")
    config_manager = _cli_main.get_config_manager()
    ctx.obj["config"] = config_manager

    from benchbox.cli.config import install_cli_config_provider

    install_cli_config_provider(config_manager)


register_commands(cli)


def main() -> None:
    cli()


__all__ = ["cli", "main", "version_callback", "CLI_HELP"]
