# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import re
from importlib import resources
from typing import Any, cast

import click
import yaml

from benchbox.cli.logo import styled_logo


def _load_help_catalog() -> dict[str, Any]:
    with resources.files("benchbox.data").joinpath("cli_help_catalog.yaml").open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("cli_help_catalog.yaml must contain a mapping")
    return cast("dict[str, Any]", payload)


_HELP_CATALOG = _load_help_catalog()

HELP_TOPICS = tuple(_HELP_CATALOG["help_topics"])

COMMAND_CATEGORIES: dict[str, tuple[str, list[str]]] = {
    key: (str(value[0]), list(value[1]))
    for key, value in cast("dict[str, list[Any]]", _HELP_CATALOG["command_categories"]).items()
}

COMMAND_EXAMPLES: dict[str, dict[str, list[str]]] = cast(
    "dict[str, dict[str, list[str]]]", _HELP_CATALOG["command_examples"]
)


class BenchBoxHelpFormatter(click.HelpFormatter):
    def __init__(self, *args: Any, show_hidden: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.show_hidden = show_hidden


class BenchBoxCommand(click.Command):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self.params = [p for p in self.params if "--help" not in getattr(p, "opts", [])]

        self.params.append(
            click.Option(
                ["--help", "-h"],
                is_flag=True,
                default=False,
                expose_value=False,
                is_eager=True,
                help="Show help message (use --help-topic all/examples for more)",
                callback=self._handle_help_flag,
            )
        )
        self.params.append(
            click.Option(
                ["--help-topic"],
                type=click.Choice(["all", "examples", "benchmarks"], case_sensitive=False),
                default=None,
                expose_value=False,
                is_eager=True,
                help="Show extended help: 'all' for advanced options, 'examples' for usage examples, 'benchmarks' for benchmark options",
                callback=self._handle_help_topic,
            )
        )

    def _handle_help_flag(self, ctx: click.Context, param: click.Parameter, value: bool) -> None:
        if not value:
            return
        click.echo(ctx.get_help(), color=ctx.color)
        ctx.exit(0)

    def _handle_help_topic(self, ctx: click.Context, param: click.Parameter, value: str | None) -> None:
        if value is None:
            return

        topic = value.lower().strip()

        if topic == "all":
            formatter = ctx.make_formatter()
            self.format_help_all(ctx, formatter)
            click.echo(formatter.getvalue(), color=ctx.color)
            ctx.exit(0)

        elif topic == "examples":
            self._show_examples(ctx)
            ctx.exit(0)

        elif topic == "benchmarks":
            self._show_benchmark_options(ctx)
            ctx.exit(0)

    @staticmethod
    def _show_benchmark_options(ctx: click.Context) -> None:
        from benchbox.cli.benchmark_hooks import BenchmarkHookRegistry
        from benchbox.core.benchmark_loader import list_loader_benchmark_ids

        click.echo(
            click.style("\nBenchmark-specific options (--benchmark-option KEY=VALUE):\n", bold=True),
            color=ctx.color,
        )

        from benchbox.core.benchmark_loader import get_core_benchmark_class

        found_any = False
        for bench_id in sorted(list_loader_benchmark_ids()):
            try:
                get_core_benchmark_class(bench_id)
            except ValueError:
                continue

            lines = BenchmarkHookRegistry.describe_options(bench_id)
            if not lines:
                continue

            found_any = True
            click.echo(click.style(f"  {bench_id}:", fg="cyan", bold=True), color=ctx.color)
            for line in lines:
                click.echo(f"    {line}", color=ctx.color)
            click.echo("", color=ctx.color)

        if not found_any:
            click.echo("  No benchmarks have registered custom options.", color=ctx.color)

        click.echo(
            click.style("Usage: ", fg="yellow", bold=True)
            + "--benchmark-option taxi_types=yellow,green --benchmark-option year=2020",
            color=ctx.color,
        )

        click.echo(
            click.style("\nApproximate aggregate guidance:\n", bold=True),
            color=ctx.color,
        )
        click.echo(
            "  read_primitives covers one-shot approximate aggregates "
            "(approx_count_distinct_*, approx_quantile*, approx_top_k_*).",
            color=ctx.color,
        )
        click.echo(
            "  Cross-engine function reference: https://benchbox.dev/docs/benchmarks/read-primitives-approximate-functions.html",
            color=ctx.color,
        )
        click.echo(
            "  write_primitives sketch category covers persist + merge + requery "
            "for DataSketches Theta / KLL / Top-K (sketch_query_*_merge / _combine).",
            color=ctx.color,
        )
        click.echo(
            "  Cross-engine sketch reference: https://benchbox.dev/docs/benchmarks/write-primitives-sketch-functions.html",
            color=ctx.color,
        )

    def _show_examples(self, ctx: click.Context) -> None:
        cmd_name = self.name or "run"
        examples = COMMAND_EXAMPLES.get(cmd_name, {})

        if not examples:
            click.echo(f"No examples available for '{cmd_name}'", color=ctx.color)
            return

        click.echo(f"\nUsage examples for 'benchbox {cmd_name}':\n", color=ctx.color)

        for category, cmds in examples.items():
            click.echo(click.style(f"{category}:", fg="cyan", bold=True), color=ctx.color)
            for cmd in cmds:
                click.echo(f"  {cmd}", color=ctx.color)
            click.echo("", color=ctx.color)

        click.echo(
            click.style("Tip: ", fg="yellow", bold=True)
            + "Use --help for options, --help-topic all for advanced options.",
            color=ctx.color,
        )

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_help_text(ctx, formatter)
        self.format_options(ctx, formatter, show_hidden=False)
        self.format_epilog(ctx, formatter)

    def format_help_all(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_help_text(ctx, formatter)
        self.format_options(ctx, formatter, show_hidden=True)
        self.format_epilog(ctx, formatter)

    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter, show_hidden: bool = False) -> None:
        core_opts: list[tuple[str, str]] = []
        common_opts: list[tuple[str, str]] = []
        advanced_opts: list[tuple[str, str]] = []

        core_names = {"--platform", "--benchmark", "--scale", "--output"}
        common_names = {
            "--phases",
            "--queries",
            "--tuning",
            "--dry-run",
            "-v",
            "--verbose",
            "-q",
            "--quiet",
            "--force",
            "--help",
            "-h",
            "--non-interactive",
        }

        for param in self.get_params(ctx):
            is_hidden = getattr(param, "hidden", False)

            if is_hidden and not show_hidden:
                continue

            if is_hidden and show_hidden:
                param.hidden = False
                rv = param.get_help_record(ctx)
                param.hidden = True
            else:
                rv = param.get_help_record(ctx)

            if rv is None:
                continue

            if any(name in core_names for name in param.opts):
                core_opts.append(rv)
            elif any(name in common_names for name in param.opts):
                common_opts.append(rv)
            else:
                if is_hidden:
                    advanced_opts.append(rv)
                else:
                    common_opts.append(rv)

        if core_opts:
            with formatter.section("Core Options"):
                formatter.write_dl(core_opts)

        if common_opts:
            with formatter.section("Options"):
                formatter.write_dl(common_opts)

        if show_hidden and advanced_opts:
            with formatter.section("Advanced Options"):
                formatter.write_dl(advanced_opts)


class BenchBoxGroup(click.Group):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def command(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("cls", BenchBoxCommand)
        return super().command(*args, **kwargs)

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        if ctx.parent is None:
            logo = styled_logo()
            if logo:
                formatter.write(logo + "\n\n")
        super().format_help(ctx, formatter)

    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        text = self.help if self.help else ""
        if not text:
            return

        lines = text.split("\n")
        colored_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped == "Quick start:":
                colored_lines.append(line.replace("Quick start:", click.style("Quick start:", fg="yellow", bold=True)))
            elif stripped.startswith("benchbox "):
                indent = len(line) - len(line.lstrip())
                content = line.lstrip()
                match = re.match(r"(benchbox\s+\S+(?:\s+-\S+\s+\S+)*(?:\s+\S+)?)\s{2,}(.+)", content)
                if match:
                    cmd_part = match.group(1)
                    desc_part = match.group(2)
                    colored_lines.append(
                        " " * indent + click.style(cmd_part, fg="cyan") + "  " + click.style(desc_part, dim=True)
                    )
                else:
                    colored_lines.append(" " * indent + click.style(content, fg="cyan"))
            elif "https://" in line or "http://" in line:

                def colorize_url(match: re.Match[str]) -> str:
                    return click.style(match.group(0), fg="blue", underline=True)

                colored_lines.append(re.sub(r"https?://[^\s]+", colorize_url, line))
            else:
                colored_lines.append(line)

        text = "\n".join(colored_lines)
        formatter.write_paragraph()
        with formatter.indentation():
            formatter.write_text(text)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands: list[tuple[str, click.Command]] = []
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            commands.append((subcommand, cmd))

        if not commands:
            return

        cmd_lookup = {name: (name, cmd) for name, cmd in commands}

        categorized: set[str] = set()

        with formatter.section("Commands"):
            for _category_key, (category_name, cmd_names) in COMMAND_CATEGORIES.items():
                category_cmds: list[tuple[str, str]] = []
                for cmd_name in cmd_names:
                    if cmd_name in cmd_lookup:
                        name, cmd = cmd_lookup[cmd_name]
                        help_text = cmd.get_short_help_str(limit=formatter.width)
                        colored_name = click.style(name, fg="green")
                        colored_help = click.style(help_text, dim=True)
                        category_cmds.append((colored_name, colored_help))
                        categorized.add(cmd_name)

                if category_cmds:
                    formatter.write_text(click.style(f"{category_name}:", fg="cyan", bold=True))
                    with formatter.indentation():
                        formatter.write_dl(category_cmds)

            other_cmds: list[tuple[str, str]] = []
            for name, cmd in commands:
                if name not in categorized:
                    help_text = cmd.get_short_help_str(limit=formatter.width)
                    colored_name = click.style(name, fg="green")
                    colored_help = click.style(help_text, dim=True)
                    other_cmds.append((colored_name, colored_help))

            if other_cmds:
                formatter.write_text(click.style("Other:", fg="cyan", bold=True))
                with formatter.indentation():
                    formatter.write_dl(other_cmds)


def handle_help_callback(ctx: click.Context, param: click.Parameter, value: str | None) -> None:
    if value is None:
        return

    topic = value.lower().strip() if value else ""

    if topic == "":
        click.echo(ctx.get_help(), color=ctx.color)
        ctx.exit(0)
    elif topic == "all":
        cmd = ctx.command
        if hasattr(cmd, "format_help_all"):
            formatter = ctx.make_formatter()
            cmd.format_help_all(ctx, formatter)
            click.echo(formatter.getvalue(), color=ctx.color)
        else:
            click.echo(ctx.get_help(), color=ctx.color)
        ctx.exit(0)
    elif topic == "examples":
        cmd = ctx.command
        if hasattr(cmd, "_show_examples"):
            cmd._show_examples(ctx)
        else:
            click.echo("No examples available.", color=ctx.color)
        ctx.exit(0)
    elif topic == "benchmarks":
        cmd = ctx.command
        if hasattr(cmd, "_show_benchmark_options"):
            cmd._show_benchmark_options(ctx)
        else:
            click.echo("No benchmark options available.", color=ctx.color)
        ctx.exit(0)
    else:
        click.echo(
            f"Unknown help topic: '{topic}'\nValid topics: all, examples, benchmarks",
            color=ctx.color,
        )
        ctx.exit(1)


def advanced_option(*args: Any, **kwargs: Any) -> Any:
    kwargs["hidden"] = True
    return click.option(*args, **kwargs)
