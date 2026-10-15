import logging
from typing import Any, Optional

from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from benchbox.cli.system import SystemProfiler
from benchbox.core.benchmark_registry import (
    BENCHMARK_ORDER,
    CATEGORY_ORDER,
    get_all_benchmarks,
    get_benchmark_surface,
    validate_scale_factor as core_validate_scale_factor,
)
from benchbox.core.constants import VALID_PHASES
from benchbox.core.schemas import MIN_THROUGHPUT_STREAMS, BenchmarkConfig
from benchbox.utils.printing import quiet_console
from benchbox.utils.verbosity import VerbositySettings

logger = logging.getLogger(__name__)
console = quiet_console

_SUPPORT_STATUS_LABELS = {
    "stable": "Stable",
    "beta": "Beta",
    "experimental": "Experimental",
    "repo_only": "Repo-only",
    "deprecated": "Deprecated",
    "document_only": "Document-only",
}


class BenchmarkManager:
    CATEGORY_ORDER = CATEGORY_ORDER
    BENCHMARK_ORDER = BENCHMARK_ORDER

    def __init__(self):
        self.benchmarks = self._get_available_benchmarks()
        self.verbosity = VerbositySettings.default()

    def set_verbosity(self, settings: VerbositySettings) -> None:

        self.verbosity = settings

    def _get_available_benchmarks(self) -> dict[str, dict[str, Any]]:
        return get_all_benchmarks()

    def _is_public_benchmark(self, benchmark_id: str, benchmark_info: dict[str, Any]) -> bool:
        surface = benchmark_info.get("surface")
        if surface is None:
            surface = get_benchmark_surface(benchmark_id)
        return str(surface) == "public"

    def _get_public_benchmarks(self) -> dict[str, dict[str, Any]]:
        return {
            bench_id: bench_info
            for bench_id, bench_info in self.benchmarks.items()
            if self._is_public_benchmark(bench_id, bench_info)
        }

    def _support_status_label(self, benchmark_info: dict[str, Any]) -> str:
        status = str(benchmark_info["support_status"])
        return _SUPPORT_STATUS_LABELS.get(status, status.replace("_", "-"))

    def validate_scale_factor(self, benchmark_id: str, scale_factor: float) -> None:
        core_validate_scale_factor(benchmark_id, scale_factor)

    def list_available_benchmarks(self):
        categories = {}
        for bench_id, bench_info in self._get_public_benchmarks().items():
            category = bench_info["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append((bench_id, bench_info))

        tree = Tree("Available Benchmarks")

        for category, benchmarks in categories.items():
            category_tree = tree.add(f"[bold blue]{category}[/bold blue]")

            for bench_id, bench_info in benchmarks:
                desc = bench_info["query_description"] if bench_info["num_queries"] > 0 else bench_info["description"]
                bench_tree = category_tree.add(f"[green]{bench_info['display_name']}[/green] - {desc}")
                bench_tree.add(f"Support: {self._support_status_label(bench_info)}")
                bench_tree.add(f"Complexity: {bench_info['complexity']}")
                bench_tree.add(
                    f"Estimated Time: {bench_info['estimated_time_range'][0]}-{bench_info['estimated_time_range'][1]} min"
                )

        console.print(tree)

    def _filter_benchmarks(
        self, category: Optional[str] = None, search_term: Optional[str] = None
    ) -> dict[str, dict[str, Any]]:
        filtered = self._get_public_benchmarks()

        if category:
            filtered = {k: v for k, v in filtered.items() if v["category"] == category}

        if search_term:
            search_lower = search_term.lower()
            filtered = {
                k: v
                for k, v in filtered.items()
                if search_lower in v["display_name"].lower()
                or search_lower in v["description"].lower()
                or search_lower in v["category"].lower()
                or search_lower in k.lower()
            }

        return filtered

    def _display_all_benchmarks(
        self, filter_category: Optional[str] = None, search_term: Optional[str] = None
    ) -> dict[str, dict[str, Any]]:
        benchmarks_to_show = self._filter_benchmarks(category=filter_category, search_term=search_term)

        if not benchmarks_to_show:
            console.print("[yellow]No benchmarks match the current filters.[/yellow]")
            return benchmarks_to_show

        title = "Available Benchmarks"
        if filter_category:
            title += f" - {filter_category}"
        if search_term:
            title += f" (search: '{search_term}')"

        table = Table(title=title, show_header=True)
        table.add_column("ID", style="cyan bold", width=3, justify="right")
        table.add_column("Category", style="blue", width=14)
        table.add_column("Name", style="green bold", width=16)
        table.add_column("Status", style="cyan", width=13)
        table.add_column("Queries", style="yellow", width=7, justify="right")
        table.add_column("Complexity", style="magenta", width=10)
        table.add_column("Description", style="white", width=50)

        sorted_benchmarks = sorted(benchmarks_to_show.items(), key=lambda x: (x[1]["category"], x[1]["display_name"]))

        for i, (bench_id, bench_info) in enumerate(sorted_benchmarks, start=1):
            if bench_info["num_queries"] > 0:
                query_text = str(bench_info["num_queries"])
            else:
                query_text = "-"

            table.add_row(
                str(i),
                bench_info["category"],
                bench_info["display_name"],
                self._support_status_label(bench_info),
                query_text,
                bench_info["complexity"],
                bench_info["description"],
            )

        console.print(table)

        console.print()
        controls = Text()
        controls.append("Controls: ", style="bold")
        controls.append("[c]", style="cyan bold")
        controls.append("ategory | ", style="dim")
        controls.append("[s]", style="cyan bold")
        controls.append("earch | ", style="dim")
        controls.append("[a]", style="cyan bold")
        controls.append("ll | ", style="dim")
        controls.append("[p]", style="cyan bold")
        controls.append("review | ", style="dim")
        controls.append("[?]", style="cyan bold")
        controls.append("help | ", style="dim")
        controls.append("[#]", style="cyan bold")
        controls.append(" select", style="dim")
        console.print(controls)
        console.print()

        return benchmarks_to_show

    def _show_benchmark_preview(self, benchmark_id: str, benchmark_info: dict[str, Any]) -> None:
        preview_text = Text()

        preview_text.append(f"{benchmark_info['display_name']}\n", style="bold green")
        preview_text.append(f"{benchmark_info['description']}\n\n", style="white")

        preview_text.append("Category: ", style="cyan")
        preview_text.append(f"{benchmark_info['category']}\n", style="white")

        preview_text.append("Support Status: ", style="cyan")
        preview_text.append(f"{self._support_status_label(benchmark_info)}\n", style="white")

        preview_text.append("Complexity: ", style="cyan")
        preview_text.append(f"{benchmark_info['complexity']}\n", style="white")

        if benchmark_info["num_queries"] > 0:
            preview_text.append("Queries: ", style="cyan")
            preview_text.append(f"{benchmark_info['num_queries']} queries\n", style="white")
        else:
            preview_text.append("Workload: ", style="cyan")
            preview_text.append(f"{benchmark_info['query_description']}\n", style="white")

        preview_text.append("Supported Scale Factors: ", style="cyan")
        scale_str = ", ".join(str(s) for s in benchmark_info["scale_options"])
        preview_text.append(f"{scale_str}\n", style="white")

        preview_text.append("Default Scale: ", style="cyan")
        preview_text.append(f"{benchmark_info['default_scale']}\n", style="white")

        preview_text.append("\nFeatures:\n", style="cyan")
        if benchmark_info.get("supports_streams"):
            preview_text.append("  ✓ Concurrent streams supported\n", style="green")
        else:
            preview_text.append("  - Concurrent streams not supported\n", style="dim")

        preview_text.append("\nEstimated Resources:\n", style="cyan")
        time_low, time_high = benchmark_info["estimated_time_range"]
        preview_text.append("  Runtime: ", style="white")
        preview_text.append(f"{time_low}-{time_high} minutes (at default scale)\n", style="yellow")

        est_mem = self._estimate_memory_usage(benchmark_id, benchmark_info["default_scale"])
        preview_text.append("  Memory: ", style="white")
        preview_text.append(f"~{est_mem:.1f} GB (at SF={benchmark_info['default_scale']})\n", style="yellow")

        console.print(Panel(preview_text, title=f"Preview: {benchmark_id}", border_style="cyan"))

    def _show_benchmark_selection_help(self) -> None:
        help_text = Text()
        help_text.append("Benchmark Selection Help\n\n", style="bold cyan")

        help_text.append("Available Commands:\n", style="bold yellow")
        help_text.append("  [c] Category filter", style="white")
        help_text.append(
            " - Filter benchmarks by category (TPC, Academic, Industry, Primitives, Experimental)\n", style="dim"
        )

        help_text.append("  [s] Search", style="white")
        help_text.append(" - Search benchmarks by name, description, or category\n", style="dim")

        help_text.append("  [a] All (clear filters)", style="white")
        help_text.append(" - Remove all active filters and show all benchmarks\n", style="dim")

        help_text.append("  [p] Preview", style="white")
        help_text.append(" - View detailed information about a benchmark before selecting\n", style="dim")

        help_text.append("  [#] Select by number", style="white")
        help_text.append(" - Enter a benchmark number to select it\n", style="dim")

        help_text.append("  [?] Help", style="white")
        help_text.append(" - Show this help message\n\n", style="dim")

        help_text.append("Tips:\n", style="bold yellow")
        help_text.append("  • Start with TPC-H for general OLAP testing\n", style="dim")
        help_text.append("  • Use preview (p) to see query counts and resource requirements\n", style="dim")
        help_text.append("  • Filter by category (c) to focus on specific benchmark types\n", style="dim")
        help_text.append("  • Search (s) to quickly find benchmarks by name\n", style="dim")

        console.print()
        console.print(Panel(help_text, title="Help", border_style="blue"))
        console.print()

    def _show_sample_queries(self, benchmark_id: str, limit: int = 3) -> None:
        try:
            from benchbox.core.benchmark_loader import get_core_benchmark_class

            benchmark_class = get_core_benchmark_class(benchmark_id)
            default_scale = self.benchmarks.get(benchmark_id, {}).get("default_scale", 0.01)
            benchmark = benchmark_class(scale_factor=default_scale)

            if not hasattr(benchmark, "queries") or not benchmark.queries:
                console.print("[yellow]No queries available for preview[/yellow]")
                return

            console.print(
                f"\n[bold cyan]Sample Queries (showing {min(limit, len(benchmark.queries))} of {len(benchmark.queries)})[/bold cyan]\n"
            )

            for i, query_id in enumerate(list(benchmark.queries.keys())[:limit], 1):
                query_sql = benchmark.queries[query_id]

                if len(query_sql) > 500:
                    query_sql = query_sql[:500] + "\n... (truncated)"

                console.print(f"[bold]Query {query_id}:[/bold]")
                console.print(Syntax(query_sql, "sql", theme="monokai", line_numbers=False))
                console.print()

        except Exception as e:
            console.print(f"[yellow]Could not load sample queries: {e}[/yellow]")

    def select_benchmark(self) -> BenchmarkConfig:
        selected_category = self._prompt_category_selection()

        benchmark_id, benchmark_info = self._prompt_benchmark_in_category(selected_category)

        console.print(f"\n[green]✓ Selected: {benchmark_info['display_name']} ({benchmark_info['category']})[/green]")

        return self._configure_benchmark(benchmark_id, benchmark_info)

    def _prompt_category_selection(self) -> str:
        categories_info = {}
        public_benchmarks = self._get_public_benchmarks()
        for bench_info in public_benchmarks.values():
            category = bench_info["category"]
            if category not in categories_info:
                categories_info[category] = {"count": 0, "benchmarks": []}
            categories_info[category]["count"] += 1
            categories_info[category]["benchmarks"].append(bench_info["display_name"])

        descriptions = {
            "TPC": "Official industry standards for comparing databases",
            "Academic": "Research benchmarks from academia",
            "Industry": "Real-world benchmarks from practitioners",
            "Primitives": "Fundamental database operation testing",
            "Experimental": "Experimental benchmarks for specialized testing",
            "Time Series": "Time-series workloads for temporal data",
            "Real World": "Real-world datasets for realistic testing",
        }

        ordered_categories = [c for c in self.CATEGORY_ORDER if c in categories_info]
        for cat in categories_info:
            if cat not in ordered_categories:
                ordered_categories.append(cat)

        table = Table(title="Benchmark Categories", show_header=True)
        table.add_column("ID", style="cyan bold", width=3, justify="right")
        table.add_column("Category", style="green bold", width=14)
        table.add_column("Benchmarks", style="yellow", width=7, justify="right")
        table.add_column("Description", style="white", width=50)

        for i, category in enumerate(ordered_categories, start=1):
            info = categories_info[category]
            table.add_row(
                str(i),
                category,
                str(info["count"]),
                descriptions.get(category, "Specialized benchmark suite"),
            )

        console.print(table)
        console.print()

        choice_map = {str(i + 1): cat for i, cat in enumerate(ordered_categories)}
        valid_choices = list(choice_map.keys())

        selection = Prompt.ask("Select category", choices=valid_choices, default="1")
        return choice_map[selection]

    def _prompt_benchmark_in_category(self, category: str) -> tuple[str, dict[str, Any]]:
        category_benchmarks = {
            bench_id: info for bench_id, info in self._get_public_benchmarks().items() if info["category"] == category
        }

        popularity_order = self.BENCHMARK_ORDER.get(category, [])

        def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
            bench_id, info = item
            try:
                position = popularity_order.index(bench_id)
            except ValueError:
                position = 999
            return (position, info["display_name"])

        sorted_benchmarks = sorted(category_benchmarks.items(), key=sort_key)

        table = Table(title=f"{category} Benchmarks", show_header=True)
        table.add_column("ID", style="cyan bold", width=3, justify="right")
        table.add_column("Name", style="green bold", width=18)
        table.add_column("Queries", style="yellow", width=7, justify="right")
        table.add_column("Complexity", style="magenta", width=10)
        table.add_column("Description", style="white", width=45)

        for i, (_bench_id, bench_info) in enumerate(sorted_benchmarks, start=1):
            query_text = str(bench_info["num_queries"]) if bench_info["num_queries"] > 0 else "-"

            table.add_row(
                str(i),
                bench_info["display_name"],
                query_text,
                bench_info["complexity"],
                bench_info["description"],
            )

        console.print(table)
        console.print()

        choice_map = {str(i + 1): bench_id for i, (bench_id, _) in enumerate(sorted_benchmarks)}
        valid_choices = list(choice_map.keys())

        selection = Prompt.ask("Select benchmark", choices=valid_choices, default="1")
        benchmark_id = choice_map[selection]
        return benchmark_id, category_benchmarks[benchmark_id]

    def _display_benchmark_categories(self):
        categories = {}
        for bench_info in self._get_public_benchmarks().values():
            category = bench_info["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append(bench_info)

        table = Table(title="Benchmark Categories")
        table.add_column("Category", style="cyan")
        table.add_column("Count", style="yellow")
        table.add_column("Description", style="white")

        descriptions = {
            "TPC": "Official industry standards for comparing databases",
            "Academic": "Research benchmarks from academia",
            "Industry": "Real-world benchmarks from practitioners",
            "Primitives": "Fundamental database operation testing",
            "Experimental": "Experimental benchmarks for specialized testing",
            "Time Series": "Time-series workloads for temporal data",
            "Real World": "Real-world datasets for realistic testing",
        }

        for category, benchmarks in categories.items():
            table.add_row(
                category,
                str(len(benchmarks)),
                descriptions.get(category, "Specialized benchmark suite"),
            )

        console.print(table)

    def _select_category(self, categories: list[str]) -> str:
        console.print("\n[bold]Available Categories:[/bold]")

        choice_map = {str(i + 1): cat for i, cat in enumerate(categories)}
        for i, category in enumerate(categories):
            console.print(f"  {i + 1}. {category}")

        selection = Prompt.ask("Select category", choices=list(choice_map.keys()), default="1")

        return choice_map[selection]

    def _select_specific_benchmark(self, available_benchmarks: dict[str, dict[str, Any]]) -> str:
        if len(available_benchmarks) == 1:
            benchmark_id = list(available_benchmarks.keys())[0]
            console.print(f"\n[green]✅ Selected {available_benchmarks[benchmark_id]['display_name']}[/green]")
            return benchmark_id

        table = Table(title="Available Benchmarks")
        table.add_column("ID", style="cyan", width=3)
        table.add_column("Name", style="green")
        table.add_column("Status", style="cyan")
        table.add_column("Queries", style="yellow")
        table.add_column("Complexity", style="blue")
        table.add_column("Est. Time", style="magenta")

        benchmark_list = list(available_benchmarks.items())
        for i, (_bench_id, bench_info) in enumerate(benchmark_list):
            query_text = (
                str(bench_info["num_queries"]) if bench_info["num_queries"] > 0 else bench_info["query_description"]
            )
            time_range = bench_info["estimated_time_range"]
            table.add_row(
                str(i + 1),
                bench_info["display_name"],
                self._support_status_label(bench_info),
                query_text,
                bench_info["complexity"],
                f"{time_range[0]}-{time_range[1]} min",
            )

        console.print(table)

        choice_map = {str(i + 1): bench_id for i, (bench_id, _) in enumerate(benchmark_list)}
        selection = Prompt.ask("Select benchmark", choices=list(choice_map.keys()), default="1")

        return choice_map[selection]

    def _configure_benchmark(self, benchmark_id: str, benchmark_info: dict[str, Any]) -> BenchmarkConfig:
        console.print(f"\n[bold]Configuring {benchmark_info['display_name']}[/bold]")

        system_profile = self._get_system_profile()

        scale_factor = self._prompt_scale(benchmark_id, benchmark_info, system_profile)
        concurrency = self._prompt_concurrency(benchmark_info, system_profile)
        queries = None
        compress_data, compression_type, compression_level = self._prompt_compression()

        self._display_configuration_summary(
            benchmark_id,
            benchmark_info,
            scale_factor,
            concurrency,
            queries,
            compress_data,
            compression_type,
            compression_level,
        )

        config = BenchmarkConfig(
            name=benchmark_id,
            display_name=benchmark_info["display_name"],
            scale_factor=scale_factor,
            queries=queries,
            concurrency=concurrency,
            options={
                "recommended_scale": self._get_recommended_scale(benchmark_info, system_profile),
                "estimated_time_range": benchmark_info["estimated_time_range"],
                "complexity": benchmark_info["complexity"],
            },
            compress_data=compress_data,
            compression_type=compression_type,
            compression_level=compression_level,
        )

        config.options.update(self.verbosity.to_config())
        return config

    def _prompt_scale(
        self,
        benchmark_id: str,
        benchmark_info: dict[str, Any],
        system_profile: dict[str, Any],
    ) -> float:
        scale_options = benchmark_info["scale_options"]
        recommended_scale = self._get_recommended_scale(benchmark_info, system_profile)

        if len(scale_options) > 1:
            while True:
                console.print("\n[bold cyan]Scale Factor Selection[/bold cyan]")
                console.print(f"Available options: {scale_options}")
                console.print(f"• [green]Recommended for your system: {recommended_scale}[/green]")

                self._display_scale_estimates(benchmark_id, benchmark_info, scale_options, system_profile)

                scale_factor = FloatPrompt.ask("Scale factor", default=recommended_scale)

                try:
                    self._validate_scale_choice(scale_factor, benchmark_id, benchmark_info, system_profile)
                    break
                except ValueError:
                    continue
        else:
            scale_factor = scale_options[0]
            console.print(f"Scale factor: {scale_factor} (fixed for this benchmark)")

        return scale_factor

    def _prompt_concurrency(self, benchmark_info: dict[str, Any], system_profile: dict[str, Any]) -> int | None:
        if not benchmark_info.get("supports_streams", False):
            return None

        recommended_concurrency = max(
            MIN_THROUGHPUT_STREAMS, min(MIN_THROUGHPUT_STREAMS, system_profile.get("cpu_cores", 2) // 2)
        )
        console.print("\n[bold cyan]Concurrency Options[/bold cyan]")
        console.print(
            f"• [green]Recommended streams: {recommended_concurrency}[/green] (based on {system_profile.get('cpu_cores', 'unknown')} CPU cores)"
        )

        if not Confirm.ask("Enable concurrent execution?", default=False):
            return None

        while True:
            concurrency = IntPrompt.ask("Number of concurrent streams", default=recommended_concurrency)
            if concurrency >= MIN_THROUGHPUT_STREAMS:
                break
            console.print(f"[red]Concurrent streams must be at least {MIN_THROUGHPUT_STREAMS} (TPC minimum).[/red]")

        if concurrency > system_profile.get("cpu_cores", 4):
            console.print(
                f"[yellow]⚠️ Warning: {concurrency} streams may exceed your {system_profile.get('cpu_cores', 'unknown')} CPU cores[/yellow]"
            )

        return concurrency

    def _prompt_compression(self) -> tuple[bool, str, Optional[int]]:
        console.print("\n[bold cyan]Data Compression Options[/bold cyan]")
        if not Confirm.ask("Enable data compression?", default=False):
            return False, "zstd", None

        compression_type = Prompt.ask(
            "Compression type",
            choices=["zstd", "gzip", "snappy", "lz4"],
            default="zstd",
        )

        compression_level = None
        if compression_type in ["zstd", "gzip"]:
            compression_level = IntPrompt.ask(f"{compression_type} compression level (1-22)", default=3)

        return True, compression_type, compression_level

    def _display_configuration_summary(
        self,
        benchmark_id: str,
        benchmark_info: dict[str, Any],
        scale_factor: float,
        concurrency: int | None,
        queries: Optional[list[str]],
        compress_data: bool,
        compression_type: str,
        compression_level: Optional[int],
    ):
        console.print("\n[bold green]Configuration Summary[/bold green]")

        table = Table(show_header=False, box=None)
        table.add_column("Setting", style="cyan", min_width=15)
        table.add_column("Value", style="white")

        table.add_row("Benchmark:", benchmark_info["display_name"])
        table.add_row("Scale Factor:", str(scale_factor))
        table.add_row("Complexity:", benchmark_info["complexity"])

        if concurrency is not None:
            table.add_row("Concurrency:", f"{concurrency} streams")

        if queries:
            table.add_row(
                "Queries:",
                f"{len(queries)} of {benchmark_info['num_queries']} (subset)",
            )
        else:
            table.add_row("Queries:", benchmark_info["query_description"])

        if compress_data:
            level_str = f" (level {compression_level})" if compression_level else ""
            table.add_row("Compression:", f"{compression_type}{level_str}")

        est_memory = self._estimate_memory_usage(benchmark_id, scale_factor)
        est_time = self._estimate_execution_time(benchmark_id, scale_factor)

        table.add_row("Est. Memory:", f"~{est_memory:.1f}GB")
        table.add_row("Est. Time:", f"~{est_time:.0f} minutes")

        console.print(table)
        console.print()

    def _get_system_profile(self) -> dict[str, Any]:
        try:
            profiler = SystemProfiler()
            profile = profiler.get_system_profile()
            return {
                "memory_gb": getattr(profile, "memory_total_gb", 8),
                "cpu_cores": getattr(profile, "cpu_cores_logical", 4),
                "architecture": getattr(profile, "architecture", "unknown"),
            }
        except Exception as e:
            console.print(f"[yellow]⚠️ System profiling failed: {e}[/yellow]")
            return {"memory_gb": 8, "cpu_cores": 4, "architecture": "unknown"}

    def _get_recommended_scale(self, benchmark_info: dict[str, Any], system_profile: dict[str, Any]) -> float:
        memory_gb = system_profile["memory_gb"]
        scale_options = benchmark_info["scale_options"]

        if memory_gb >= 32:
            return max([s for s in scale_options if s <= 1.0], default=scale_options[0])
        elif memory_gb >= 16:
            return max([s for s in scale_options if s <= 0.1], default=scale_options[0])
        else:
            return min(scale_options)

    def _display_scale_estimates(
        self,
        benchmark_id: str,
        benchmark_info: dict[str, Any],
        scale_options: list[float],
        system_profile: dict[str, Any],
    ):
        table = Table(title="Scale Factor Resource Estimates")
        table.add_column("Scale", style="cyan")
        table.add_column("Est. Memory", style="yellow")
        table.add_column("Est. Time", style="green")
        table.add_column("Recommendation", style="white")

        memory_gb = system_profile["memory_gb"]

        for scale in scale_options:
            est_memory = self._estimate_memory_usage(benchmark_id, scale)
            est_time = self._estimate_execution_time(benchmark_id, scale)

            if est_memory > memory_gb * 0.8:
                recommendation = "May exceed memory"
            elif scale <= 0.1:
                recommendation = "Good for testing"
            elif scale >= 1.0:
                recommendation = "Production benchmark"
            else:
                recommendation = "Moderate load"

            table.add_row(
                str(scale),
                f"~{est_memory:.1f}GB",
                f"~{est_time:.0f}min",
                recommendation,
            )

        console.print(table)

    def _estimate_memory_usage(self, benchmark_id: str, scale: float) -> float:
        base = float(self.benchmarks[benchmark_id]["base_memory_gb"])
        return base * scale

    def _estimate_execution_time(self, benchmark_id: str, scale: float) -> float:
        low, high = self.benchmarks[benchmark_id]["estimated_time_range"]
        base_avg = (low + high) / 2
        return base_avg * (0.5 + 0.5 * scale)

    def _validate_scale_choice(
        self,
        scale_factor: float,
        benchmark_id: str,
        benchmark_info: dict[str, Any],
        system_profile: dict[str, Any],
    ):
        from rich.prompt import Confirm

        min_scale = benchmark_info.get("min_scale")
        if min_scale is not None and scale_factor < min_scale:
            console.print(
                f"[red]Error: {benchmark_id.upper()} requires scale_factor >= {min_scale} (got {scale_factor})[/red]"
            )
            console.print("[yellow]The native data generator crashes with fractional scale factors.[/yellow]")
            raise ValueError(f"Scale factor {scale_factor} is below minimum {min_scale} for {benchmark_id}")

        est_memory = self._estimate_memory_usage(benchmark_id, scale_factor)
        available_memory = system_profile["memory_gb"]
        recommended_scale = self._get_recommended_scale(benchmark_info, system_profile)

        if est_memory > available_memory * 0.9:
            console.print(
                f"[red]⚠️  WARNING: This scale may require {est_memory:.1f}GB memory, but you have {available_memory:.1f}GB[/red]"
            )
            console.print("[yellow]This is likely to cause out-of-memory errors during execution[/yellow]")
            console.print(f"[dim]Recommended scale for your system: {recommended_scale}[/dim]")

            if not Confirm.ask("\n[bold]Do you want to proceed with this scale factor anyway?[/bold]", default=False):
                console.print("[green]Scale factor selection cancelled. Please choose a safer value.[/green]")
                raise ValueError(f"Scale factor {scale_factor} exceeds safe memory limits")

        elif est_memory > available_memory * 0.7:
            console.print(
                f"[yellow]INFO: This will use ~{est_memory:.1f}GB of your {available_memory:.1f}GB memory[/yellow]"
            )


def prompt_phases(default_phases: list[str] | None = None) -> list[str]:
    if default_phases is None:
        default_phases = ["power"]

    console.print("\n[bold cyan]Benchmark Phases[/bold cyan]")
    console.print("Select which phases to run:")

    presets = {
        "1": ("Quick Test (power only)", ["power"]),
        "2": ("Full Benchmark (generate, load, power)", ["generate", "load", "power"]),
        "3": ("Data Generation Only", ["generate"]),
        "4": ("Load Only (requires existing data)", ["load"]),
        "5": ("All Phases", ["generate", "load", "warmup", "power", "throughput", "maintenance"]),
        "6": ("Custom selection...", None),
    }

    for key, (label, _) in presets.items():
        console.print(f"  {key}. {label}")

    default_choice = "1"
    for key, (_, phases) in presets.items():
        if phases == default_phases:
            default_choice = key
            break

    choice = Prompt.ask("Select option", choices=list(presets.keys()), default=default_choice)

    selected_label, selected_phases = presets[choice]

    if selected_phases is not None:
        console.print(f"[green]✓ {selected_label}: {', '.join(selected_phases)}[/green]")
        return selected_phases

    valid_phases = list(VALID_PHASES)
    unique_phases = _prompt_custom_phases(valid_phases)

    console.print(f"[green]✓ Selected phases: {', '.join(unique_phases)}[/green]")
    return unique_phases


def _prompt_custom_phases(valid_phases: list[str]) -> list[str]:
    console.print("\n[bold]Available phases:[/bold]")
    console.print(
        f"[dim]Enter phase numbers (1-{len(valid_phases)}) or phase names - not query numbers. "
        "If you select power or standard query phases, query subset selection comes after this step.[/dim]"
    )
    descriptions = {
        "generate": "Generate benchmark data files",
        "load": "Load data into the database",
        "statistics": "Gather optimizer statistics (opt-in; supported benchmarks only)",
        "warmup": "Warm up the database cache",
        "power": "Execute power test (single stream)",
        "throughput": "Execute throughput test (concurrent streams)",
        "maintenance": "Execute maintenance operations",
    }
    for i, phase in enumerate(valid_phases, 1):
        console.print(f"  {i}. {phase} - {descriptions[phase]}")

    console.print("\n[dim]Enter comma-separated numbers or phase names (e.g., '1,2' or 'generate,load,power')[/dim]")
    custom_input = Prompt.ask("Phases", default="generate,load,power")

    selected = _parse_phase_input(custom_input, valid_phases)
    if not selected:
        console.print(
            f"[yellow]No valid phases selected from '{custom_input}' "
            f"(valid: 1-{len(valid_phases)} or phase names). "
            "If you meant query IDs, query selection is only shown later for power or standard phases. "
            "Defaulting to 'power'.[/yellow]"
        )
        return ["power"]
    return selected


def _parse_phase_input(raw: str, valid_phases: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in raw.split(","):
        item = item.strip().lower()
        phase: str | None = None
        if item.isdigit():
            idx = int(item) - 1
            if 0 <= idx < len(valid_phases):
                phase = valid_phases[idx]
        elif item in valid_phases:
            phase = item
        if phase and phase not in seen:
            seen.add(phase)
            result.append(phase)
    return result


def prompt_force_regeneration() -> str | None:
    from rich.prompt import Confirm, Prompt

    console.print("\n[bold cyan]Force Regeneration[/bold cyan]")
    console.print("[dim]Force regeneration bypasses cached data and re-runs data generation or upload.[/dim]")

    if not Confirm.ask("Force regeneration of existing data?", default=False):
        return None

    console.print("\nForce options:")
    console.print("  1. All - Regenerate data AND re-upload to cloud")
    console.print("  2. Data Generation - Regenerate data files only")
    console.print("  3. Upload - Re-upload existing data to cloud storage")

    choice = Prompt.ask("Select option", choices=["1", "2", "3"], default="1")

    force_map = {"1": "all", "2": "datagen", "3": "upload"}
    selected = force_map[choice]

    console.print(f"[green]✓ Force mode: {selected}[/green]")
    return selected


def prompt_official_mode(benchmark_id: str, scale_factor: float) -> tuple[bool, float | None]:
    from rich.prompt import Confirm, FloatPrompt

    if not benchmark_id.lower().startswith("tpc"):
        return False, None

    console.print("\n[bold cyan]TPC Official Mode[/bold cyan]")
    console.print("[dim]Official mode ensures TPC-compliant benchmark runs with validated scale factors.[/dim]")

    if not Confirm.ask("Enable TPC-compliant official mode?", default=False):
        return False, None

    tpc_allowed_scales = {1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000}

    if scale_factor not in tpc_allowed_scales:
        console.print(f"\n[yellow]⚠️  Scale factor {scale_factor} is not TPC-compliant.[/yellow]")
        console.print(f"[dim]Allowed scale factors: {sorted(tpc_allowed_scales)}[/dim]")

        if scale_factor < 1:
            nearest = 1
        else:
            nearest = min(tpc_allowed_scales, key=lambda x: abs(x - scale_factor))

        if Confirm.ask(f"Use nearest TPC-compliant scale factor ({nearest})?", default=True):
            console.print(f"[green]✓ Using TPC-compliant scale factor: {nearest}[/green]")
            return True, float(nearest)
        else:
            new_scale = FloatPrompt.ask(
                "Enter TPC-compliant scale factor",
                default=float(nearest),
            )
            if new_scale not in tpc_allowed_scales:
                console.print(f"[red]Scale factor {new_scale} is not TPC-compliant.[/red]")
                console.print("[yellow]Disabling official mode.[/yellow]")
                return False, None
            return True, new_scale

    console.print(f"[green]✓ TPC Official Mode enabled (scale factor {scale_factor} is compliant)[/green]")
    return True, None


def prompt_seed() -> int | None:
    import random

    from rich.prompt import Confirm, IntPrompt

    console.print("\n[bold cyan]Reproducibility Options[/bold cyan]")
    console.print("[dim]Set a seed for reproducible query parameter generation.[/dim]")

    if not Confirm.ask("Set a specific seed for reproducibility?", default=False):
        console.print("[dim]Using random seed (non-reproducible)[/dim]")
        return None

    suggested_seed = random.randint(1, 999999)
    seed = IntPrompt.ask("Enter seed value", default=suggested_seed)

    console.print(f"[green]✓ Using seed: {seed}[/green]")
    return seed


def prompt_validation_mode() -> str | None:
    from rich.prompt import Confirm, Prompt

    console.print("\n[bold cyan]Result Validation[/bold cyan]")
    console.print("[dim]Validation ensures query results match expected values.[/dim]")

    if not Confirm.ask("Configure validation mode?", default=False):
        console.print("[dim]Using default validation (exact matching)[/dim]")
        return None

    console.print("\nValidation modes:")
    console.print("  1. Exact - Strict result matching (default)")
    console.print("  2. Loose - Tolerant matching for floating point differences")
    console.print("  3. Range - Accept results within expected range")
    console.print("  4. Disabled - Skip validation (faster, for development)")
    console.print("  5. Full - All validation checks enabled")

    choice = Prompt.ask("Select validation mode", choices=["1", "2", "3", "4", "5"], default="1")

    mode_map = {"1": "exact", "2": "loose", "3": "range", "4": "disabled", "5": "full"}
    selected = mode_map[choice]

    console.print(f"[green]✓ Validation mode: {selected}[/green]")
    return selected


def prompt_capture_plans(platform: str) -> bool:
    from rich.prompt import Confirm

    supported_platforms = {"duckdb", "postgresql", "datafusion", "polars", "spark", "pyspark"}

    platform_lower = platform.lower()
    if platform_lower not in supported_platforms:
        return False

    console.print("\n[bold cyan]Query Plan Capture[/bold cyan]")
    console.print(f"[dim]{platform.title()} supports capturing query execution plans.[/dim]")
    console.print("[dim]This adds 3-8% overhead but enables performance analysis.[/dim]")

    if Confirm.ask("Capture query execution plans?", default=False):
        console.print("[green]✓ Query plan capture enabled[/green]")
        console.print("[dim]Plans will be saved with benchmark results[/dim]")
        return True

    return False


def prompt_output_location(default_output: str | None = None) -> str | None:
    from pathlib import Path

    from rich.prompt import Confirm, Prompt

    console.print("\n[bold cyan]Output Location[/bold cyan]")
    console.print(f"[dim]Default output directory: {default_output or 'benchmark_runs/'}[/dim]")

    if not Confirm.ask("Use a custom output location?", default=False):
        console.print("[dim]Using default output location[/dim]")
        return None

    console.print("\n[dim]Enter a local path for benchmark output.[/dim]")
    console.print("[dim]Examples: /tmp/benchbox, ~/benchmarks, ./results[/dim]")

    custom_path = Prompt.ask("Output directory")
    if not custom_path.strip():
        console.print("[dim]Using default output location[/dim]")
        return None

    expanded_path = Path(custom_path).expanduser().resolve()

    if not expanded_path.parent.exists():
        console.print(f"[yellow]⚠️  Parent directory doesn't exist: {expanded_path.parent}[/yellow]")
        if Confirm.ask("Create directory?", default=True):
            try:
                expanded_path.parent.mkdir(parents=True, exist_ok=True)
                console.print(f"[green]✓ Created directory: {expanded_path.parent}[/green]")
            except OSError as e:
                console.print(f"[red]Failed to create directory: {e}[/red]")
                console.print("[dim]Using default output location[/dim]")
                return None
        else:
            console.print("[dim]Using default output location[/dim]")
            return None

    console.print(f"[green]✓ Output location: {expanded_path}[/green]")
    return str(expanded_path)


def get_platform_format_recommendation(platform: str) -> tuple[str | None, str]:
    platform_lower = platform.lower()

    recommendations = {
        "databricks": ("delta", "Delta Lake is Databricks' native format"),
        "snowflake": ("iceberg", "Iceberg has native Snowflake support"),
        "redshift": ("iceberg", "Iceberg has native Redshift support"),
        "bigquery": ("iceberg", "Iceberg has native BigQuery support"),
        "duckdb": ("parquet", "DuckDB has excellent Parquet performance"),
        "clickhouse": ("parquet", "ClickHouse has native Parquet support"),
        "spark": ("delta", "Delta Lake integrates well with Spark"),
        "pyspark": ("delta", "Delta Lake integrates well with Spark"),
        "datafusion": ("parquet", "DataFusion has native Parquet support"),
        "polars": ("parquet", "Polars has excellent Parquet performance"),
        "athena": ("parquet", "Parquet is efficient for Athena"),
        "presto": ("parquet", "Parquet is efficient for Presto"),
        "trino": ("iceberg", "Iceberg has native Trino support"),
    }

    if platform_lower in recommendations:
        return recommendations[platform_lower]

    no_format_support = {"sqlite", "postgresql", "mysql", "sqlite3"}
    if platform_lower in no_format_support:
        return (None, "This platform uses its native storage format")

    return ("parquet", "Parquet is a widely compatible format")


def prompt_table_mode(default_mode: str = "native") -> str:
    from rich.prompt import Prompt

    normalized_default = default_mode.strip().lower() if default_mode else "native"
    if normalized_default not in {"native", "external"}:
        normalized_default = "native"

    console.print("\n[bold cyan]Table Mode[/bold cyan]")
    console.print("[dim]Choose how benchmark data is exposed to the platform.[/dim]")
    native_default = " (default)" if normalized_default == "native" else ""
    external_default = " (default)" if normalized_default == "external" else ""
    console.print(f"  1. native{native_default} - materialized tables loaded into the database")
    console.print(f"  2. external{external_default} - external table/view references over source files")

    default_choice = "1" if normalized_default == "native" else "2"
    choice = Prompt.ask("Select table mode", choices=["1", "2"], default=default_choice)
    selected_mode = "native" if choice == "1" else "external"

    console.print(f"[green]✓ Table mode: {selected_mode}[/green]")
    return selected_mode


def prompt_table_format(platform: str) -> tuple[str | None, str | None]:
    from rich.prompt import Confirm, Prompt

    recommended_format, recommendation_reason = get_platform_format_recommendation(platform)

    if recommended_format is None:
        return None, None

    console.print("\n[bold cyan]Data Format Selection[/bold cyan]")
    console.print("[dim]Choose a data format for benchmark data generation.[/dim]")

    if not Confirm.ask("Configure data format? (default: CSV)", default=False):
        console.print("[dim]Using CSV format (default)[/dim]")
        return None, None

    console.print("\nAvailable formats:")
    console.print("  1. CSV (default) - Simple, universal compatibility")
    console.print("  2. Parquet - Columnar, compressed, excellent query performance")
    console.print("  3. Vortex - Columnar format with high compression and fast scans")
    console.print("  4. Delta Lake - ACID transactions, time travel, schema evolution")
    console.print("  5. Apache Iceberg - Open standard, multi-engine support")

    format_display = {"parquet": "Parquet", "vortex": "Vortex", "delta": "Delta Lake", "iceberg": "Iceberg"}
    if recommended_format:
        display_name = format_display.get(recommended_format, recommended_format)
        console.print(f"\n[green]💡 Recommended for {platform.title()}: {display_name}[/green]")
        console.print(f"[dim]   {recommendation_reason}[/dim]")

    default_choice = "1"
    if recommended_format == "parquet":
        default_choice = "2"
    elif recommended_format == "vortex":
        default_choice = "3"
    elif recommended_format == "delta":
        default_choice = "4"
    elif recommended_format == "iceberg":
        default_choice = "5"

    choice = Prompt.ask("Select format", choices=["1", "2", "3", "4", "5"], default=default_choice)

    format_map = {"1": None, "2": "parquet", "3": "vortex", "4": "delta", "5": "iceberg"}
    selected_format = format_map[choice]

    if selected_format is None:
        console.print("[green]✓ Using CSV format[/green]")
        return None, None

    compression = None
    if selected_format in {"parquet", "vortex", "delta", "iceberg"}:
        console.print("\n[bold cyan]Compression[/bold cyan]")
        console.print("[dim]Compression reduces file size and improves query performance.[/dim]")

        compression_options = {
            "parquet": ["snappy", "zstd", "gzip", "none"],
            "vortex": ["zstd", "lz4", "none"],
            "delta": ["snappy", "zstd", "none"],
            "iceberg": ["zstd", "snappy", "gzip", "none"],
        }

        default_compression = {"parquet": "snappy", "vortex": "zstd", "delta": "snappy", "iceberg": "zstd"}

        options = compression_options.get(selected_format, ["snappy", "zstd", "none"])
        default = default_compression.get(selected_format, "snappy")

        for i, opt in enumerate(options, start=1):
            marker = "(default)" if opt == default else ""
            console.print(f"  {i}. {opt} {marker}")

        choice_map = {str(i + 1): opt for i, opt in enumerate(options)}
        default_choice = str(options.index(default) + 1)
        valid_choices = list(choice_map.keys())

        selection = Prompt.ask("Select compression", choices=valid_choices, default=default_choice)
        compression = choice_map[selection]

        if compression == "none":
            compression = None

    format_display_name = format_display.get(selected_format, selected_format)
    if compression:
        console.print(f"[green]✓ Format: {format_display_name} with {compression} compression[/green]")
    else:
        console.print(f"[green]✓ Format: {format_display_name}[/green]")

    return selected_format, compression


def prompt_verbose_output() -> int:
    from rich.prompt import Confirm, Prompt

    console.print("\n[bold cyan]Output Verbosity[/bold cyan]")
    console.print("[dim]Control the level of detail in benchmark output.[/dim]")

    if not Confirm.ask("Enable verbose output?", default=False):
        console.print("[dim]Using normal output level[/dim]")
        return 0

    console.print("\nVerbosity levels:")
    console.print("  1. Verbose - Detailed execution logs")
    console.print("  2. Debug - Maximum detail (includes internal debugging)")

    choice = Prompt.ask("Select level", choices=["1", "2"], default="1")

    level = int(choice)
    level_names = {1: "Verbose", 2: "Debug"}
    console.print(f"[green]✓ Output level: {level_names[level]}[/green]")

    return level


_SENTINEL = object()


def _prompt_single_option(name: str, spec: Any) -> Any:
    from rich.prompt import Confirm, Prompt

    description = spec.help or f"Configure {name}"
    default_display = f" [default: {spec.default}]" if spec.default is not None else ""
    console.print(f"\n[cyan]{name}[/cyan]: {description}{default_display}")

    if spec.choices:
        choice_str = ", ".join(spec.choices)
        console.print(f"[dim]Options: {choice_str}[/dim]")
        default_str = str(spec.default) if spec.default is not None else None
        if default_str is not None and default_str not in spec.choices:
            logger.warning(
                "Option %r default %r not in choices %r, falling back to %r",
                name,
                default_str,
                spec.choices,
                spec.choices[0],
            )
        value = Prompt.ask(
            f"Enter {name}",
            choices=spec.choices,
            default=default_str if default_str is not None and default_str in spec.choices else spec.choices[0],
        )
        return spec.parse(value)

    if isinstance(spec.default, bool):
        return Confirm.ask(f"Enable {name}?", default=spec.default)

    if isinstance(spec.default, int):
        from rich.prompt import IntPrompt

        return IntPrompt.ask(f"Enter {name}", default=spec.default)

    if spec.default is None and name in {"threads", "port"}:
        value_str = Prompt.ask(f"Enter {name} (leave empty for auto)", default="")
        if value_str.strip():
            try:
                return int(value_str)
            except ValueError:
                console.print(f"[yellow]Invalid integer '{value_str}', using auto[/yellow]")
                return None
        return None

    default_str = str(spec.default) if spec.default is not None else ""
    value = Prompt.ask(f"Enter {name}", default=default_str)
    if value.strip():
        return spec.parse(value)
    if spec.default is not None:
        return spec.default
    return _SENTINEL


def prompt_platform_options(platform: str) -> dict[str, Any]:
    from rich.prompt import Confirm

    from benchbox.cli.platform_hooks import PlatformHookRegistry

    specs = PlatformHookRegistry.list_option_specs(platform.lower())

    configurable_specs = {
        name: spec
        for name, spec in specs.items()
        if name not in {"driver_version", "driver_auto_install", "force_recreate"}
    }

    if not configurable_specs:
        return {}

    console.print("\n[bold cyan]Platform Configuration[/bold cyan]")
    console.print(f"[dim]Configure {platform.title()}-specific options.[/dim]")

    if not Confirm.ask("Configure platform options?", default=False):
        console.print("[dim]Using default platform configuration[/dim]")
        return {}

    options: dict[str, Any] = {}

    for name, spec in sorted(configurable_specs.items()):
        result = _prompt_single_option(name, spec)
        if result is not _SENTINEL:
            options[name] = result

    if options:
        console.print("\n[green]✓ Platform options configured:[/green]")
        for name, value in options.items():
            console.print(f"  {name}: {value}")
    else:
        console.print("[dim]Using default platform configuration[/dim]")

    return options


def prompt_execution_engine(platform: str) -> str:
    from benchbox.core.execution_engine import supported_execution_engines
    from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE

    engines = {
        name: capability for name, capability in supported_execution_engines(platform).items() if capability.selectable
    }
    if not engines:
        return DEFAULT_EXECUTION_ENGINE

    console.print("\n[bold cyan]Execution Engine[/bold cyan]")
    console.print("[dim]Which execution machinery runs the query.[/dim]")
    for name, capability in sorted(engines.items()):
        label = capability.display_name or name
        console.print(f"  {name} - {label}")

    selected = Prompt.ask(
        "Select execution engine",
        choices=[DEFAULT_EXECUTION_ENGINE, *sorted(engines)],
        default=DEFAULT_EXECUTION_ENGINE,
    )
    console.print(f"[green]✓ Execution engine: {selected}[/green]")
    return selected


def prompt_query_subset(benchmark_id: str, num_queries: int) -> list[str] | None:
    console.print("\n[bold cyan]Query Selection[/bold cyan]")
    console.print(f"The benchmark has {num_queries} queries.")

    if not Confirm.ask("Run all queries?", default=True):
        console.print("\n[dim]Enter query IDs separated by commas (e.g., 'Q1,Q6,Q17' or '1,6,17')[/dim]")

        if benchmark_id.lower() == "tpch":
            console.print("[dim]TPC-H queries: Q1-Q22[/dim]")
        elif benchmark_id.lower() == "tpcds":
            console.print("[dim]TPC-DS queries: Q1-Q99[/dim]")
        elif benchmark_id.lower() == "ssb":
            console.print("[dim]SSB queries: Q1.1-Q4.3 (or 1-13)[/dim]")

        query_input = Prompt.ask("Queries")
        if query_input.strip():
            queries = []
            for q in query_input.split(","):
                q = q.strip().upper()
                if q.isdigit() or not q.startswith("Q") and q[0].isdigit():
                    q = f"Q{q}"
                queries.append(q)

            if queries:
                console.print(
                    f"[green]✓ Running {len(queries)} queries: {', '.join(queries[:5])}"
                    f"{'...' if len(queries) > 5 else ''}[/green]"
                )
                return queries

    console.print(f"[green]✓ Running all {num_queries} queries[/green]")
    return None
