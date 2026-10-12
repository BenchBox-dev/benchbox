# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.core.results.anonymization import (
    AnonymizationConfig,
)
from benchbox.core.results.exporter import ResultExporter as _CoreResultExporter

if TYPE_CHECKING:
    from cloudpathlib import CloudPath

    from benchbox.utils.cloud_storage import DatabricksPath

PathLike = Union[Path, "CloudPath", "DatabricksPath"]
from benchbox.core.results.models import BenchmarkResults
from benchbox.utils import format_bytes, format_duration
from benchbox.utils.printing import quiet_console

console = quiet_console
logger = logging.getLogger(__name__)


class ConsoleResultFormatter:
    @staticmethod
    def display_benchmark_summary(
        results: BenchmarkResults,
        verbose: bool = False,
    ) -> None:
        ConsoleResultFormatter._display_enhanced_result(results, verbose)

    @staticmethod
    def _display_enhanced_result(results: BenchmarkResults, verbose: bool) -> None:
        console.print(f"[bold blue]Benchmark:[/bold blue] {results.benchmark_name}")
        console.print(f"[bold blue]Scale Factor:[/bold blue] {results.scale_factor}")
        console.print(f"[bold blue]Platform:[/bold blue] {results.platform}")
        console.print(f"[bold blue]Database:[/bold blue] {getattr(results, 'database_name', 'in-memory')}")

        total = getattr(results, "total_queries", 0) or 0
        successful = getattr(results, "successful_queries", 0) or 0
        if total:
            success_rate = successful / total * 100.0
            status_color = "green" if successful == total else "yellow" if successful > 0 else "red"
            console.print(
                f"[bold blue]Status:[/bold blue] [{status_color}]{successful}/{total} queries successful ({success_rate:.1f}%)[/{status_color}]"
            )

        if getattr(results, "execution_phases", None):
            console.print("\n[bold yellow]Phase Execution Summary:[/bold yellow]")

        ConsoleResultFormatter._display_resource_utilization(results)
        ConsoleResultFormatter._display_performance_characteristics(results)

        if hasattr(results, "data_size_mb"):
            console.print(f"[bold blue]Data Size:[/bold blue] {format_bytes(results.data_size_mb * 1024 * 1024)}")
        if hasattr(results, "schema_creation_time"):
            console.print(f"[bold blue]Schema Creation:[/bold blue] {format_duration(results.schema_creation_time)}")
        if hasattr(results, "data_loading_time"):
            console.print(f"[bold blue]Data Loading:[/bold blue] {format_duration(results.data_loading_time)}")

        status, status_color = ConsoleResultFormatter._determine_benchmark_status(results)
        console.print(f"[bold blue]Benchmark Status:[/bold blue] [{status_color}]{status}[/{status_color}]")
        console.print(
            f"[bold blue]Queries:[/bold blue] {results.successful_queries}/{results.total_queries} successful"
        )

        validation_details = getattr(results, "validation_details", {}) or {}
        ConsoleResultFormatter._print_validation_stages(validation_details)
        ConsoleResultFormatter._display_validation_status(results, validation_details)

        if results.successful_queries > 0:
            console.print(
                f"[bold blue]Query Execution Time:[/bold blue] {format_duration(results.total_execution_time)}"
            )
            console.print(f"[bold blue]Average Query Time:[/bold blue] {format_duration(results.average_query_time)}")

        if hasattr(results, "tuning_enabled") and results.tuning_enabled:
            console.print("[bold blue]Tuning Configuration:[/bold blue] [green]Enabled[/green]")
            if hasattr(results, "constraints_applied"):
                console.print(f"  [dim]Constraints Applied:[/dim] {results.constraints_applied}")
        else:
            console.print("[bold blue]Tuning Configuration:[/bold blue] [dim]Default (constraints enabled)[/dim]")

        if verbose:
            ConsoleResultFormatter._display_platform_query_details(results)

    @staticmethod
    def _display_resource_utilization(results: BenchmarkResults) -> None:
        if not hasattr(results, "resource_utilization") or not results.resource_utilization:
            return
        console.print("\n[bold yellow]Resource Utilization:[/bold yellow]")
        for metric, value in results.resource_utilization.items():
            if isinstance(value, (int, float)):
                if "memory" in metric.lower():
                    console.print(
                        f"  [bold]{metric.replace('_', ' ').title()}:[/bold] {format_bytes(value * 1024 * 1024)}"
                    )
                elif "cpu" in metric.lower() and value <= 100:
                    console.print(f"  [bold]{metric.replace('_', ' ').title()}:[/bold] {value:.1f}%")
                else:
                    console.print(f"  [bold]{metric.replace('_', ' ').title()}:[/bold] {value}")

    @staticmethod
    def _display_performance_characteristics(results: BenchmarkResults) -> None:
        if not hasattr(results, "performance_characteristics") or not results.performance_characteristics:
            return
        console.print("\n[bold yellow]Performance Characteristics:[/bold yellow]")
        perf_chars = results.performance_characteristics
        if "query_complexity_distribution" in perf_chars:
            console.print(
                f"  [bold]Query Complexity Distribution:[/bold] {perf_chars['query_complexity_distribution']}"
            )
        if "data_access_patterns" in perf_chars:
            console.print(f"  [bold]Data Access Patterns:[/bold] {perf_chars['data_access_patterns']}")
        if "parallel_execution_efficiency" in perf_chars:
            console.print(
                f"  [bold]Parallel Execution Efficiency:[/bold] {perf_chars['parallel_execution_efficiency']:.2f}"
            )

    @staticmethod
    def _determine_benchmark_status(results: BenchmarkResults) -> tuple[str, str]:
        if results.total_queries == 0:
            return "FAILED", "red"
        query_success = results.successful_queries == results.total_queries
        validation_success = getattr(results, "validation_status", "PASSED") in ["PASSED", "PARTIAL"]

        if not validation_success or not query_success:
            return "FAILED", "red"
        if getattr(results, "validation_status", "PASSED") == "PARTIAL":
            return "PARTIAL", "yellow"
        return "PASSED", "green"

    @staticmethod
    def _display_validation_status(results: BenchmarkResults, validation_details: dict[str, Any]) -> None:
        validation_status = getattr(results, "validation_status", "PASSED")

        if validation_status == "FAILED":
            console.print("\n[bold red]❌ Data Validation Failed:[/bold red]")
            if validation_details:
                if "empty_tables" in validation_details and validation_details["empty_tables"]:
                    console.print(f"   [red]• Empty tables:[/red] {', '.join(validation_details['empty_tables'])}")
                if "missing_tables" in validation_details and validation_details["missing_tables"]:
                    console.print(
                        f"   [yellow]• Missing tables:[/yellow] {', '.join(validation_details['missing_tables'])} "
                        "[dim](recoverable — runner will rebuild via --force datagen)[/dim]"
                    )
                if "inaccessible_tables" in validation_details and validation_details["inaccessible_tables"]:
                    console.print(
                        f"   [red]• Inaccessible tables:[/red] {', '.join(validation_details['inaccessible_tables'])}"
                    )
            console.print("   [yellow]💡 Suggestion:[/yellow] Check data generation and loading process")
        elif validation_status == "PARTIAL":
            console.print("\n[bold yellow]⚠️ Data Validation Partial:[/bold yellow]")
            if validation_details:
                if "insufficient_data_tables" in validation_details:
                    for table_info in validation_details["insufficient_data_tables"]:
                        console.print(
                            f"   [yellow]• {table_info['table']}:[/yellow] {table_info['actual']} rows (expected min: {table_info['expected_minimum']})"
                        )
            console.print("   [dim]Benchmark completed with partial data validation[/dim]")

    @staticmethod
    def _print_validation_stages(details: Optional[dict[str, Any]]) -> None:

        if not isinstance(details, dict):
            return

        stages = list(details.get("stages", []) or [])
        if not stages:
            return

        console.print("\n[bold blue]Validation Stages:[/bold blue]")
        for stage_info in stages:
            stage_name = stage_info.get("stage", "unknown").replace("_", " ").title()
            stage_status = stage_info.get("status", "UNKNOWN")
            color = {
                "PASSED": "green",
                "WARNINGS": "yellow",
                "FAILED": "red",
            }.get(stage_status, "blue")
            icon = {
                "PASSED": "✅",
                "WARNINGS": "⚠️",
                "FAILED": "❌",
            }.get(stage_status, "•")
            console.print(f"  [{color}]{icon} {stage_name}: {stage_status}[/{color}]")

            for error in stage_info.get("errors", []) or []:
                console.print(f"    [red]- {error}")
            for warning in stage_info.get("warnings", []) or []:
                console.print(f"    [yellow]- {warning}")

    @staticmethod
    def _display_platform_result(results: BenchmarkResults, verbose: bool) -> None:
        ConsoleResultFormatter._display_platform_header(results)

        status, status_color = ConsoleResultFormatter._determine_benchmark_status(results)
        console.print(f"[bold blue]Benchmark Status:[/bold blue] [{status_color}]{status}[/{status_color}]")
        console.print(
            f"[bold blue]Queries:[/bold blue] {results.successful_queries}/{results.total_queries} successful"
        )

        validation_details = getattr(results, "validation_details", {}) or {}
        ConsoleResultFormatter._display_validation_status(results, validation_details)
        ConsoleResultFormatter._display_timing_and_tuning(results)

        if verbose:
            ConsoleResultFormatter._display_platform_query_details(results)

    @staticmethod
    def _display_platform_header(results: BenchmarkResults) -> None:
        console.print(f"[bold blue]Scale Factor:[/bold blue] {results.scale_factor}")
        console.print(f"[bold blue]Platform:[/bold blue] {results.platform}")
        console.print(f"[bold blue]Database:[/bold blue] {getattr(results, 'database_name', 'in-memory')}")

        if hasattr(results, "data_size_mb"):
            console.print(f"[bold blue]Data Size:[/bold blue] {format_bytes(results.data_size_mb * 1024 * 1024)}")
        if hasattr(results, "schema_creation_time"):
            console.print(f"[bold blue]Schema Creation:[/bold blue] {format_duration(results.schema_creation_time)}")
        if hasattr(results, "data_loading_time"):
            console.print(f"[bold blue]Data Loading:[/bold blue] {format_duration(results.data_loading_time)}")

    @staticmethod
    def _display_timing_and_tuning(results: BenchmarkResults) -> None:
        if results.successful_queries > 0:
            console.print(
                f"[bold blue]Query Execution Time:[/bold blue] {format_duration(results.total_execution_time)}"
            )
            console.print(f"[bold blue]Average Query Time:[/bold blue] {format_duration(results.average_query_time)}")

        if hasattr(results, "tuning_enabled") and results.tuning_enabled:
            console.print("[bold blue]Tuning Configuration:[/bold blue] [green]Enabled[/green]")
            if hasattr(results, "constraints_applied"):
                console.print(f"  [dim]Constraints Applied:[/dim] {results.constraints_applied}")
        else:
            console.print("[bold blue]Tuning Configuration:[/bold blue] [dim]Default (constraints enabled)[/dim]")

    @staticmethod
    def _display_platform_query_details(results: BenchmarkResults) -> None:
        if not hasattr(results, "query_results") or not results.query_results:
            return

        console.print("\n[bold yellow]Query Details:[/bold yellow]")

        normalized: list[dict[str, Any]] = []
        for entry in results.query_results:
            if isinstance(entry, dict):
                normalized.append(entry)
                continue

            execution_time_seconds: Optional[float] = None
            if hasattr(entry, "execution_time") and entry.execution_time is not None:
                execution_time_seconds = entry.execution_time
            elif hasattr(entry, "execution_time_ms") and entry.execution_time_ms is not None:
                execution_time_seconds = entry.execution_time_ms / 1000.0

            normalized.append(
                {
                    "query_id": getattr(entry, "query_id", None),
                    "status": getattr(entry, "status", None),
                    "execution_time_seconds": execution_time_seconds,
                    "error": getattr(entry, "error_message", None),
                }
            )

        successful_queries = [q for q in normalized if q.get("status") == "SUCCESS"]
        failed_queries = [q for q in normalized if q.get("status") != "SUCCESS"]

        if failed_queries:
            console.print(f"[red]Failed Queries ({len(failed_queries)}):[/red]")
            for q in failed_queries[:5]:
                error_msg = q.get("error", "Unknown error")[:100]
                console.print(f"  [red]Query {q.get('query_id', '?')}:[/red] {error_msg}")

        if successful_queries and len(successful_queries) > 1:
            execution_times = [q["execution_time_seconds"] for q in successful_queries]
            slowest_time = max(execution_times)
            fastest_time = min(execution_times)
            console.print(
                f"[bold blue]Query Performance Range:[/bold blue] {format_duration(fastest_time)} - {format_duration(slowest_time)}"
            )

            fastest_queries = sorted(successful_queries, key=lambda x: x["execution_time_seconds"])[:3]
            console.print("[green]Top 3 fastest queries:[/green]")
            for q in fastest_queries:
                console.print(
                    f"  [green]Query {q.get('query_id', '?')}:[/green] {format_duration(q['execution_time_seconds'])}"
                )

            slowest_queries = sorted(successful_queries, key=lambda x: x["execution_time_seconds"], reverse=True)[:3]
            console.print("[yellow]Top 3 slowest queries:[/yellow]")
            for q in slowest_queries:
                console.print(
                    f"  [yellow]Query {q.get('query_id', '?')}:[/yellow] {format_duration(q['execution_time_seconds'])}"
                )

    @staticmethod
    def display_query_performance(
        results: BenchmarkResults,
    ) -> None:
        ConsoleResultFormatter.display_benchmark_summary(results, verbose=True)

    @staticmethod
    def format_execution_statistics(
        results: BenchmarkResults,
    ) -> dict[str, str]:
        stats: dict[str, str] = {}
        stats["benchmark"] = results.benchmark_name
        stats["platform"] = getattr(results, "platform", "unknown")
        stats["scale_factor"] = str(results.scale_factor)

        total_queries = getattr(results, "total_queries", None)
        if total_queries is None and hasattr(results, "query_results"):
            total_queries = len(results.query_results)
        stats["queries_total"] = str(total_queries or 0)

        successful_queries = getattr(results, "successful_queries", 0)
        stats["queries_successful"] = str(successful_queries)
        if successful_queries:
            stats["total_execution_time"] = format_duration(getattr(results, "total_execution_time", 0.0))
            stats["average_query_time"] = format_duration(getattr(results, "average_query_time", 0.0))

        if getattr(results, "validation_status", None):
            stats["validation_status"] = str(results.validation_status)
        _compliance_class = getattr(results, "compliance_class", None)
        _unofficial = _compliance_class in {"unofficial_nonstandard", "unofficial_subscale"}
        if not _unofficial and getattr(results, "power_at_size", None):
            stats["power_at_size"] = f"{results.power_at_size:.2f}"

        return stats

    @staticmethod
    def render_comprehensive_execution_summary(
        results: BenchmarkResults,
        _show_query_details: bool = True,
    ) -> None:
        from rich.table import Table

        console.print("\n[bold cyan]📊 Execution Summary[/bold cyan]\n")

        metrics_table = Table(show_header=False, box=None, padding=(0, 2))
        metrics_table.add_column("Metric", style="cyan bold", width=25)
        metrics_table.add_column("Value", style="white")

        metrics_table.add_row("Benchmark", results.benchmark_name)
        metrics_table.add_row("Scale Factor", str(results.scale_factor))
        metrics_table.add_row("Platform", getattr(results, "platform", "unknown"))

        if hasattr(results, "total_execution_time"):
            metrics_table.add_row("Total Runtime", format_duration(results.total_execution_time))

        if hasattr(results, "average_query_time"):
            metrics_table.add_row("Average Query Time", format_duration(results.average_query_time))

        total_queries = getattr(results, "total_queries", 0)
        successful_queries = getattr(results, "successful_queries", 0)
        failed_queries = total_queries - successful_queries

        if total_queries > 0:
            success_rate = (successful_queries / total_queries) * 100
            metrics_table.add_row(
                "Queries Executed", f"{successful_queries}/{total_queries} ({success_rate:.1f}% success)"
            )

        console.print(metrics_table)

        if hasattr(results, "query_results") and results.query_results:
            ConsoleResultFormatter._render_validation_breakdown(results)

        if failed_queries > 0 and hasattr(results, "query_results"):
            ConsoleResultFormatter._render_top_failures(results, limit=5)

        validation_status = getattr(results, "validation_status", "UNKNOWN")
        ConsoleResultFormatter._render_overall_status(validation_status, successful_queries, total_queries)

    @staticmethod
    def _render_validation_breakdown(results: BenchmarkResults) -> None:
        from rich.table import Table

        console.print("\n[bold yellow]Validation Breakdown[/bold yellow]")

        validation_stats = {"PASSED": 0, "FAILED": 0, "SKIPPED": 0, "UNKNOWN": 0}

        for query in results.query_results:
            if isinstance(query, dict):
                validation_status = query.get("row_count_validation", {}).get("status", "UNKNOWN")
            else:
                validation_status = getattr(getattr(query, "row_count_validation", None), "status", "UNKNOWN")
            validation_stats[validation_status] = validation_stats.get(validation_status, 0) + 1

        val_table = Table(show_header=True, box=None)
        val_table.add_column("Status", style="bold", width=15)
        val_table.add_column("Count", justify="right", style="white", width=10)
        val_table.add_column("Percentage", justify="right", style="dim", width=15)
        val_table.add_column("Description", style="dim", width=40)

        total_val = sum(validation_stats.values())

        if validation_stats["PASSED"] > 0:
            pct = (validation_stats["PASSED"] / total_val) * 100 if total_val > 0 else 0
            val_table.add_row(
                "[green]✓ PASSED[/green]",
                str(validation_stats["PASSED"]),
                f"{pct:.1f}%",
                "Row counts match expected values",
            )

        if validation_stats["FAILED"] > 0:
            pct = (validation_stats["FAILED"] / total_val) * 100 if total_val > 0 else 0
            val_table.add_row(
                "[red]✗ FAILED[/red]",
                str(validation_stats["FAILED"]),
                f"{pct:.1f}%",
                "Row counts do not match expected",
            )

        if validation_stats["SKIPPED"] > 0:
            pct = (validation_stats["SKIPPED"] / total_val) * 100 if total_val > 0 else 0
            val_table.add_row(
                "[yellow]⊘ SKIPPED[/yellow]",
                str(validation_stats["SKIPPED"]),
                f"{pct:.1f}%",
                "Validation skipped (SF≠1.0 or disabled)",
            )

        console.print(val_table)

        if validation_stats["SKIPPED"] > 0:
            console.print("[dim]Note: Validation is typically skipped when scale factor ≠ 1.0[/dim]")

    @staticmethod
    def _render_top_failures(results: BenchmarkResults, limit: int = 5) -> None:
        console.print(f"\n[bold red]Top Query Failures (showing up to {limit})[/bold red]")

        failed_queries = []
        for query in results.query_results:
            if isinstance(query, dict):
                if query.get("status") != "SUCCESS":
                    failed_queries.append(query)
            else:
                if getattr(query, "status", None) != "SUCCESS":
                    failed_queries.append(
                        {
                            "query_id": getattr(query, "query_id", "unknown"),
                            "status": getattr(query, "status", "FAILED"),
                            "error": getattr(query, "error_message", "Unknown error"),
                            "execution_time_seconds": getattr(query, "execution_time", None),
                        }
                    )

        if not failed_queries:
            console.print("[green]No query failures![/green]")
            return

        for i, query in enumerate(failed_queries[:limit], 1):
            query_id = query.get("query_id", "unknown")
            error_msg = str(query.get("error", "Unknown error"))

            if len(error_msg) > 150:
                error_msg = error_msg[:150] + "..."

            console.print(f"\n[bold]  {i}. Query {query_id}[/bold]")
            console.print(f"     [red]Error:[/red] {error_msg}")

            if "row_count_validation" in query:
                validation = query["row_count_validation"]
                if validation.get("status") == "FAILED":
                    expected = validation.get("expected", "?")
                    actual = validation.get("actual", "?")
                    console.print(f"     [yellow]Validation:[/yellow] Expected {expected} rows, got {actual}")

        if len(failed_queries) > limit:
            console.print(f"\n[dim]...and {len(failed_queries) - limit} more failures[/dim]")

    @staticmethod
    def _render_overall_status(validation_status: str, successful_queries: int, total_queries: int) -> None:
        console.print("\n[bold cyan]Overall Status[/bold cyan]")

        if successful_queries == total_queries and validation_status == "PASSED":
            console.print("[bold green]✓ Benchmark completed successfully![/bold green]")
            console.print("[dim]All queries executed and validated correctly.[/dim]")

        elif successful_queries == total_queries and validation_status == "PARTIAL":
            console.print("[bold yellow]⚠ Benchmark completed with partial validation[/bold yellow]")
            console.print("[dim]All queries executed but some validations were skipped.[/dim]")
            console.print("\n[bold]Recommendations:[/bold]")
            console.print("  • Run at scale factor 1.0 for full validation")
            console.print("  • Check validation configuration if unexpected")

        elif successful_queries < total_queries:
            failed = total_queries - successful_queries
            console.print(f"[bold red]✗ Benchmark completed with {failed} failures[/bold red]")
            console.print(f"[dim]{successful_queries}/{total_queries} queries succeeded.[/dim]")
            console.print("\n[bold]Recommendations:[/bold]")
            console.print("  • Review error messages above for specific issues")
            console.print("  • Check database logs for additional details")
            console.print("  • Verify data was loaded correctly")
            console.print("  • Consider tuning configuration adjustments")

        else:
            console.print("[bold yellow]⚠ Benchmark status unclear[/bold yellow]")
            console.print(f"[dim]Validation: {validation_status}, Queries: {successful_queries}/{total_queries}[/dim]")


class ResultExporter(_CoreResultExporter):
    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        anonymize: bool = False,
        anonymization_config: Optional[AnonymizationConfig] = None,
    ) -> None:
        super().__init__(
            output_dir=output_dir,
            anonymize=anonymize,
            anonymization_config=anonymization_config,
            console=console,
        )

    def _sync_console(self) -> None:
        self.console = console

    def export_result(self, result, formats: list[str] | None = None):
        self._sync_console()
        return super().export_result(result, formats=formats)

    def list_results(self) -> list[dict[str, Any]]:
        self._sync_console()
        return super().list_results()

    def show_results_summary(self):
        self._sync_console()
        return super().show_results_summary()

    def export_comparison_report(
        self,
        comparison: dict[str, Any],
        output_path: Optional[PathLike] = None,
    ) -> PathLike:
        self._sync_console()
        return super().export_comparison_report(comparison, output_path)
