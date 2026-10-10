from __future__ import annotations

import copy
import csv
import io
import json
import logging
import os
import stat
import uuid
from collections.abc import Iterable
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from rich.console import Console

if TYPE_CHECKING:
    from cloudpathlib import CloudPath

    from benchbox.utils.cloud_storage import DatabricksPath

PathLike = Union[Path, "CloudPath", "DatabricksPath"]

from benchbox.core.results.anonymization import (
    AnonymizationConfig,
    AnonymizationManager,
)
from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.normalizer import get_query_map, normalize_result_dict
from benchbox.core.results.platform_options import REDACTED_VALUE, _is_username_key
from benchbox.core.results.schema import (
    SchemaV2ValidationError,
    SchemaV2Validator,
    build_applied_ledger_payload,
    build_plans_payload,
    build_result_payload,
    build_tuning_payload,
    inline_tuning_artifacts,
)
from benchbox.core.results.schema_policy import (
    is_loader_supported_result_schema,
    result_schema_version_value,
)
from benchbox.core.runtime_paths import resolve_results_dir
from benchbox.utils.cloud_storage import create_path_handler, is_cloud_path
from benchbox.validation.bundle import COMPANION_SUFFIXES

logger = logging.getLogger(__name__)

ResultLike = BenchmarkResults
QueryResultLike = "QueryResult | dict[str, Any]"


class ResultExportError(RuntimeError):
    pass


def _redact_usernames(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE if _is_username_key(str(key)) else _redact_usernames(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_usernames(item) for item in value]
    return value


class ResultExporter:
    EXPORTER_NAME = "benchbox-exporter"

    def __init__(
        self,
        output_dir: str | Path | None = None,
        anonymize: bool = False,
        anonymization_config: AnonymizationConfig | None = None,
        console: Console | None = None,
        plan_history_dir: str | Path | None = None,
    ):
        if output_dir is None:
            self.output_dir = resolve_results_dir(env=os.environ)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.is_cloud_output = False
        else:
            if is_cloud_path(str(output_dir)):
                self.output_dir = create_path_handler(output_dir)
                self.is_cloud_output = True
            else:
                self.output_dir = Path(output_dir)
                try:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    raise FileNotFoundError(str(exc)) from exc
                self.is_cloud_output = False

        self.console = console or Console()
        self.anonymize = anonymize
        if anonymize:
            default_config = anonymization_config or AnonymizationConfig.from_public_environ()
            self.anonymization_manager = AnonymizationManager(default_config)
        else:
            self.anonymization_manager = None
        self._validator = SchemaV2Validator()

        resolved_plan_history_dir = plan_history_dir or os.environ.get("BENCHBOX_PLAN_HISTORY_DIR")
        self.plan_history_dir = Path(resolved_plan_history_dir) if resolved_plan_history_dir else None

    def _write_file(self, file_path: Path, content: str, mode: str = "w") -> None:
        if self.is_cloud_output and hasattr(file_path, "write_bytes"):
            file_path.write_bytes(content.encode("utf-8"))
        elif self.is_cloud_output and hasattr(file_path, "write_text"):
            file_path.write_text(content, encoding="utf-8")
        else:
            destination = Path(file_path)
            temporary_path: Path | None = None
            file_descriptor: int | None = None
            for _ in range(10):
                candidate = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
                try:
                    file_descriptor = os.open(
                        candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o666,
                    )
                    temporary_path = candidate
                    break
                except FileExistsError:
                    continue
            if file_descriptor is None or temporary_path is None:
                raise FileExistsError(f"Unable to allocate a unique temporary export path for {destination}")

            try:
                existing_mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else None
                if existing_mode is not None and hasattr(os, "fchmod"):
                    os.fchmod(file_descriptor, existing_mode)
                with os.fdopen(file_descriptor, mode, encoding="utf-8", newline="") as handle:
                    file_descriptor = None
                    handle.write(content)
                os.replace(temporary_path, destination)
                temporary_path = None
            finally:
                if file_descriptor is not None:
                    os.close(file_descriptor)
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def _create_file_path(self, filename: str):
        if self.is_cloud_output:
            return self.output_dir / filename
        file_path = self.output_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    def export_result(
        self,
        result: ResultLike,
        formats: list[str] | None = None,
    ) -> dict[str, Path]:
        if isinstance(result, BenchmarkResults):
            try:
                from benchbox.core.cost.integration import add_cost_estimation_to_results

                result = add_cost_estimation_to_results(result)
            except Exception as e:
                logger.debug(f"Cost estimation skipped: {e}")

        if formats is None:
            formats = ["json"]

        exported_files: dict[str, Path] = {}
        failures: list[str] = []

        timestamp = (
            result.timestamp.strftime("%Y%m%d_%H%M%S")
            if hasattr(result, "timestamp") and result.timestamp
            else datetime.now().strftime("%Y%m%d_%H%M%S")
        )

        explicit_name = getattr(result, "output_filename", None)
        filename_base = Path(explicit_name).stem if explicit_name else self._generate_filename_base(result, timestamp)

        for format_name in formats:
            try:
                if format_name == "json":
                    filepath = self._export_json_v2(result, filename_base)
                elif format_name == "csv":
                    filepath = self._export_csv_detailed(result, filename_base)
                elif format_name == "html":
                    filepath = self._export_html_detailed(result, filename_base)
                else:
                    raise ResultExportError(f"Unknown export format: {format_name}")

                exported_files[format_name] = filepath
                self.console.print(f"[green]Exported {format_name.upper()}:[/green] {filepath}")

            except Exception as exc:
                message = f"Failed to export {format_name}: {exc}"
                logger.error(message)
                logging.error(message)
                logging.critical(message)
                self.console.print(f"[red]Failed to export {format_name}: {exc}[/red]")
                failures.append(message)

        if failures:
            raise ResultExportError("; ".join(failures))

        return exported_files

    def _generate_filename_base(self, result: ResultLike, timestamp: str) -> str:
        from benchbox.core.results.filenames import build_result_filename_base

        benchmark_id = getattr(result, "benchmark_id", None) or getattr(result, "benchmark_name", "unknown")
        platform = getattr(result, "platform", "unknown")
        scale_factor = getattr(result, "scale_factor", 1.0)
        exec_id = getattr(result, "execution_id", None)

        mode = None
        exec_ctx = getattr(result, "execution_context", None)
        if isinstance(exec_ctx, dict):
            mode = exec_ctx.get("mode") or exec_ctx.get("execution_mode")

        return build_result_filename_base(
            benchmark_id=str(benchmark_id).lower(),
            scale_factor=scale_factor,
            platform=str(platform).lower(),
            timestamp=timestamp,
            execution_id=exec_id,
            mode=mode,
        )

    def _export_json_v2(self, result: ResultLike, filename_base: str) -> Path:
        payload = build_result_payload(result, sanitize_platform_secrets=self.anonymize)

        tuning_payload = self._build_export_tuning_payload(result)
        applied_payload = self._build_export_applied_payload(result)

        if self.anonymize and self.anonymization_manager:
            self._apply_anonymization(payload)
            anonymized = True
        else:
            payload = _redact_usernames(payload)
            anonymized = False

        inline_tuning_artifacts(payload, tuning_payload, applied_payload)

        from benchbox.utils.version import get_package_version

        benchbox_version = get_package_version()

        payload["export"] = {
            "timestamp": datetime.now().isoformat(),
            "tool": self.EXPORTER_NAME,
            "benchbox_version": benchbox_version,
            "anonymized": anonymized,
        }

        try:
            self._validator.validate(payload)
        except SchemaV2ValidationError as e:
            raise ResultExportError(f"Schema validation failed: {e}") from e

        filepath = self._create_file_path(f"{filename_base}.json")
        json_content = canonical_json_text(self._convert_datetimes_to_iso(payload))
        self._write_file(filepath, json_content)

        self._write_companion_files(result, filename_base)

        self._record_plan_history(result)

        return filepath

    def _build_export_tuning_payload(self, result: ResultLike) -> dict[str, Any] | None:
        tuning_payload = build_tuning_payload(result)
        if not tuning_payload:
            return None
        if self.anonymize and self.anonymization_manager:
            return self.anonymization_manager.anonymize_tuning_payload(tuning_payload)
        return _redact_usernames(tuning_payload)

    def _build_export_applied_payload(self, result: ResultLike) -> dict[str, Any] | None:
        applied_payload = build_applied_ledger_payload(result)
        if not applied_payload:
            return None
        if self.anonymize and self.anonymization_manager:
            return self._anonymize_applied_payload(applied_payload)
        return _redact_usernames(applied_payload)

    def _write_companion_files(self, result: ResultLike, filename_base: str) -> None:
        plans_payload = build_plans_payload(result)
        if not plans_payload:
            return
        if self.anonymize and self.anonymization_manager:
            plans_payload = self._anonymize_plans_payload(plans_payload)
        plans_path = self._create_file_path(f"{filename_base}.plans.json")
        self._write_file(plans_path, canonical_json_text(plans_payload))
        self.console.print(f"[dim]Exported plans: {plans_path}[/dim]")

    def _record_plan_history(self, result: ResultLike) -> None:
        if not self.plan_history_dir:
            return
        try:
            from benchbox.core.query_plans.history import PlanHistory

            PlanHistory(self.plan_history_dir).add_run(result)
        except Exception as exc:
            logger.warning(f"Failed to record plan history: {exc}")

    def _apply_anonymization(self, payload: dict[str, Any]) -> None:
        if not self.anonymization_manager:
            return

        anonymized_payload = self.anonymization_manager.anonymize_result_payload(payload)
        payload.clear()
        payload.update(anonymized_payload)

    def _anonymize_plans_payload(self, plans_payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = copy.deepcopy(plans_payload)
        queries = sanitized.get("queries")
        if not isinstance(queries, dict):
            return sanitized
        for entry in queries.values():
            if not isinstance(entry, dict):
                continue
            plan = entry.get("plan")
            if not isinstance(plan, dict):
                continue
            if plan.get("raw_explain_output") is not None:
                plan["raw_explain_output"] = None
            self._strip_operator_platform_metadata(plan.get("logical_root"))
        return sanitized

    def _strip_operator_platform_metadata(self, node: Any) -> None:
        if not isinstance(node, dict):
            return
        physical_operator = node.get("physical_operator")
        if isinstance(physical_operator, dict) and physical_operator.get("platform_metadata"):
            physical_operator["platform_metadata"] = {}
        for child in node.get("children") or []:
            self._strip_operator_platform_metadata(child)

    def _anonymize_applied_payload(self, applied_payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = copy.deepcopy(applied_payload)
        statements = sanitized.get("statements")
        if isinstance(statements, list):
            for entry in statements:
                if not isinstance(entry, dict):
                    continue
                entry.pop("statement", None)
                entry.pop("error", None)
                entry.pop("table", None)
                entry["statement_redacted"] = True
        self._sanitize_applied_dropped(sanitized)
        self._sanitize_applied_satisfied(sanitized)
        self._sanitize_applied_receipt(sanitized.get("receipt"))
        self._sanitize_applied_drift_check(sanitized.get("drift_check"))
        return sanitized

    @staticmethod
    def _sanitize_applied_dropped(payload: Any) -> None:
        if not isinstance(payload, dict) or "dropped" not in payload:
            return
        dropped = payload.get("dropped")
        if not dropped:
            return
        count = len(dropped) if isinstance(dropped, list) else 1
        payload["dropped"] = [{"redacted": True} for _ in range(count)]

    @staticmethod
    def _sanitize_applied_satisfied(payload: Any) -> None:
        if not isinstance(payload, dict) or not payload.get("satisfied"):
            return
        satisfied = payload["satisfied"]
        count = len(satisfied) if isinstance(satisfied, list) else 1
        payload["satisfied"] = [{"redacted": True} for _ in range(count)]

    @staticmethod
    def _sanitize_applied_drift_check(drift_check: Any) -> None:
        if not isinstance(drift_check, dict):
            return
        dropped = False
        for key in ("errors", "warnings", "configuration_mismatches", "missing_tables", "extra_tables"):
            if key in drift_check:
                drift_check.pop(key, None)
                dropped = True
        if dropped:
            drift_check["drift_redacted"] = True

    @staticmethod
    def _sanitize_applied_receipt(receipt: Any) -> None:
        if not isinstance(receipt, dict):
            return
        if "error" in receipt:
            receipt.pop("error", None)
            receipt["error_redacted"] = True
        _drop = (
            "statement",
            "diff",
            "detail",
            "evidence",
            "table",
            "name",
            "expected_columns",
            "observed_columns",
        )
        for entry in receipt.get("entries") or []:
            if isinstance(entry, dict):
                for key in _drop:
                    entry.pop(key, None)
                if "reason" in entry:
                    entry.pop("reason", None)
                    entry["reason_redacted"] = True
                entry["statement_redacted"] = True
        for obj in receipt.get("observed") or []:
            if isinstance(obj, dict):
                obj.pop("table", None)
                obj.pop("name", None)
                obj.pop("columns", None)
                obj.pop("evidence", None)
                obj["redacted"] = True
        ResultExporter._sanitize_applied_dropped(receipt)

    def _convert_datetimes_to_iso(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {key: self._convert_datetimes_to_iso(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self._convert_datetimes_to_iso(item) for item in obj]
        return obj

    def _export_csv_detailed(self, result: ResultLike, filename_base: str) -> Path:
        filepath = self._create_file_path(f"{filename_base}.csv")

        headers = [
            "query_id",
            "execution_time_ms",
            "rows_returned",
            "status",
            "error_message",
            "iteration",
            "stream",
        ]

        def _query_exec_time_ms(query: dict[str, Any]) -> float:
            exec_time_ms = query.get("execution_time_ms")
            if exec_time_ms is not None:
                return float(exec_time_ms)
            exec_time_seconds = query.get("execution_time_seconds")
            if exec_time_seconds is not None:
                return float(exec_time_seconds) * 1000.0
            return 0.0

        if self.is_cloud_output:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(headers)

            for query in self._iter_query_results(result):
                writer.writerow(
                    [
                        query.get("query_id", ""),
                        _query_exec_time_ms(query),
                        query.get("rows_returned", 0),
                        query.get("status", "UNKNOWN"),
                        self._anonymize_free_text(query.get("error_message", "")),
                        query.get("iteration", ""),
                        query.get("stream_id", ""),
                    ]
                )

            self._write_file(filepath, buffer.getvalue())
            buffer.close()
            return filepath

        with open(filepath, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)

            for query in self._iter_query_results(result):
                writer.writerow(
                    [
                        query.get("query_id", ""),
                        _query_exec_time_ms(query),
                        query.get("rows_returned", 0),
                        query.get("status", "UNKNOWN"),
                        self._anonymize_free_text(query.get("error") or query.get("error_message", "")),
                        query.get("iteration", ""),
                        query.get("stream_id", ""),
                    ]
                )

        return filepath

    def _export_html_detailed(self, result: ResultLike, filename_base: str) -> Path:
        filepath = self._create_file_path(f"{filename_base}.html")

        benchmark_name = html_escape(str(getattr(result, "benchmark_name", "Unknown Benchmark")), quote=True)
        execution_id = html_escape(str(getattr(result, "execution_id", "")), quote=True)
        timestamp = getattr(result, "timestamp", datetime.now())
        timestamp_text = html_escape(timestamp.isoformat() if timestamp else "N/A", quote=True)
        duration = getattr(result, "duration_seconds", 0.0)
        scale_factor = html_escape(str(getattr(result, "scale_factor", 1.0)), quote=True)
        platform = html_escape(str(getattr(result, "platform", "Unknown")), quote=True)

        total_queries, successful_queries = self._count_queries(result)
        failed_queries = max(total_queries - successful_queries, 0)

        if isinstance(result, BenchmarkResults):
            total_time = result.total_execution_time
            avg_time = result.average_query_time
        else:
            successes = [
                (
                    query.get("execution_time_ms")
                    if query.get("execution_time_ms") is not None
                    else (
                        float(query.get("execution_time_seconds")) * 1000.0
                        if query.get("execution_time_seconds") is not None
                        else 0
                    )
                )
                for query in self._iter_query_results(result)
                if query.get("status") == "SUCCESS"
            ]
            total_time = sum(successes) / 1000 if successes else 0.0
            avg_time = (total_time / len(successes)) if successes else 0.0

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>BenchBox Results - {benchmark_name}</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 24px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        h1 {{ color: #1a1a1a; margin-bottom: 8px; }}
        .meta {{ color: #666; font-size: 0.9em; margin-bottom: 24px; }}
        .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .stat {{ background: #f8f9fa; padding: 16px; border-radius: 6px; text-align: center; }}
        .stat-value {{ font-size: 1.5em; font-weight: 600; color: #1a1a1a; }}
        .stat-label {{ font-size: 0.85em; color: #666; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
        th, td {{ border: 1px solid #e5e5e5; padding: 10px 12px; text-align: left; }}
        th {{ background: #f8f9fa; font-weight: 500; }}
        .success {{ color: #22863a; }}
        .failed {{ color: #cb2431; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{benchmark_name}</h1>
        <div class="meta">
            <strong>Platform:</strong> {platform} |
            <strong>Scale:</strong> {scale_factor} |
            <strong>Run:</strong> {execution_id} |
            <strong>Time:</strong> {timestamp_text}
        </div>
        <div class="stats">
            <div class="stat">
                <div class="stat-value">{total_queries}</div>
                <div class="stat-label">Total Queries</div>
            </div>
            <div class="stat">
                <div class="stat-value success">{successful_queries}</div>
                <div class="stat-label">Passed</div>
            </div>
            <div class="stat">
                <div class="stat-value failed">{failed_queries}</div>
                <div class="stat-label">Failed</div>
            </div>
            <div class="stat">
                <div class="stat-value">{duration:.2f}s</div>
                <div class="stat-label">Duration</div>
            </div>
            <div class="stat">
                <div class="stat-value">{total_time:.3f}s</div>
                <div class="stat-label">Query Time</div>
            </div>
            <div class="stat">
                <div class="stat-value">{avg_time * 1000:.1f}ms</div>
                <div class="stat-label">Avg Query</div>
            </div>
        </div>
        <h2>Query Results</h2>
        <table>
            <tr><th>Query</th><th>Time (ms)</th><th>Rows</th><th>Status</th><th>Error</th></tr>
            {"".join(self._render_query_row(self._anonymize_query_row(query)) for query in self._iter_query_results(result))}
        </table>
        <p style="margin-top: 24px; color: #666; font-size: 0.85em;">
            Generated by BenchBox v2.0 at {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        </p>
    </div>
</body>
</html>"""

        self._write_file(filepath, html_content)
        return filepath

    def _count_queries(self, result: ResultLike) -> tuple[int, int]:
        successful = 0
        total = 0
        for query in self._iter_query_results(result):
            total += 1
            if query.get("status") == "SUCCESS":
                successful += 1
        return total, successful

    def _render_query_row(self, query: dict[str, Any]) -> str:
        status = query.get("status", "UNKNOWN")
        status_class = "success" if status == "SUCCESS" else "failed"
        exec_time_ms = query.get("execution_time_ms")
        exec_time_seconds = query.get("execution_time_seconds")
        if exec_time_ms is None and exec_time_seconds is not None:
            exec_time_ms = float(exec_time_seconds) * 1000.0

        time_display = f"{exec_time_ms:.1f}" if exec_time_ms is not None else ""
        query_id = html_escape(str(query.get("query_id", "")), quote=True)
        rows_returned = html_escape(str(query.get("rows_returned", "")), quote=True)
        status_text = html_escape(str(status), quote=True)
        error_text = html_escape(str(query.get("error") or query.get("error_message", "") or ""), quote=True)

        return (
            "<tr>"
            f"<td>{query_id}</td>"
            f"<td>{html_escape(time_display, quote=True)}</td>"
            f"<td>{rows_returned}</td>"
            f"<td class='{status_class}'>{status_text}</td>"
            f"<td>{error_text}</td>"
            "</tr>"
        )

    def _anonymize_query_row(self, query: dict[str, Any]) -> dict[str, Any]:
        if not (self.anonymize and self.anonymization_manager):
            return query
        scrubbed = dict(query)
        for key in ("error", "error_message"):
            if scrubbed.get(key):
                scrubbed[key] = self._anonymize_free_text(scrubbed[key])
        return scrubbed

    def _anonymize_free_text(self, value: Any) -> Any:
        if not (self.anonymize and self.anonymization_manager) or not value:
            return value
        return self.anonymization_manager.anonymize_result_payload({"error_message": value})["error_message"]

    def _iter_query_results(self, result: ResultLike) -> Iterable[dict[str, Any]]:
        if isinstance(result, BenchmarkResults):
            for query in result.query_results or []:
                yield query
        else:
            for query in getattr(result, "query_results", []) or []:
                if isinstance(query, dict):
                    yield query

    def list_results(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for json_file in self.output_dir.glob("*.json"):
            if json_file.name.endswith(COMPANION_SUFFIXES) or json_file.name.endswith(".submission.json"):
                continue

            try:
                with open(json_file, encoding="utf-8") as handle:
                    data = json.load(handle)

                version = result_schema_version_value(data)
                if not is_loader_supported_result_schema(data):
                    continue

                results.append(
                    {
                        "file": json_file,
                        "version": version,
                        "benchmark": data.get("benchmark", {}).get("name", "Unknown"),
                        "platform": data.get("platform", {}).get("name", "Unknown"),
                        "scale_factor": data.get("benchmark", {}).get("scale_factor", 1.0),
                        "execution_id": data.get("run", {}).get("id", ""),
                        "timestamp": data.get("run", {}).get("timestamp", ""),
                        "duration": data.get("run", {}).get("total_duration_ms", 0) / 1000,
                        "queries": data.get("summary", {}).get("queries", {}).get("total", 0),
                        "status": data.get("summary", {}).get("validation", "unknown"),
                    }
                )

            except Exception as exc:
                logger.debug("Could not read %s: %s", json_file, exc)

        return sorted(results, key=lambda item: item["timestamp"], reverse=True)

    def show_results_summary(self) -> None:
        results = self.list_results()
        if not results:
            self.console.print("[yellow]No exported results found[/yellow]")
            return

        self.console.print(f"\n[bold]Exported Results ({len(results)} total)[/bold]")
        self.console.print(f"Output directory: [cyan]{self.output_dir}[/cyan]")

        from rich.table import Table

        table = Table()
        table.add_column("Benchmark", style="green")
        table.add_column("Platform", style="blue")
        table.add_column("Timestamp", style="dim")
        table.add_column("Duration", style="yellow")
        table.add_column("Queries", style="cyan")
        table.add_column("Version", style="dim")

        for result in results[:10]:
            duration_str = f"{result['duration']:.2f}s"
            timestamp_str = str(result["timestamp"])[:19].replace("T", " ")
            table.add_row(
                result["benchmark"],
                result.get("platform", ""),
                timestamp_str,
                duration_str,
                str(result["queries"]),
                result.get("version", ""),
            )

        self.console.print(table)

        if len(results) > 10:
            self.console.print(f"\n[dim]... and {len(results) - 10} more results[/dim]")

    def load_result_from_file(self, filepath: Path) -> dict[str, Any] | None:
        try:
            with open(filepath, encoding="utf-8") as handle:
                data = json.load(handle)

            version = data.get("result_schema_version") or data.get("version") or "unknown"
            return {"data": data, "version": version, "result_schema_version": version, "filepath": filepath}

        except Exception as exc:
            logger.error("Failed to load result from %s: %s", filepath, exc)
            return None

    @staticmethod
    def _check_generation_compatibility(baseline_data: dict[str, Any], current_data: dict[str, Any]) -> dict[str, Any]:
        outcome: dict[str, Any] = {"status": "compatible", "compatible": True, "warning": None}

        def _provenance(data: dict[str, Any]) -> dict[str, Any]:
            benchmark = data.get("benchmark") if isinstance(data, dict) else None
            if not isinstance(benchmark, dict):
                return {"data_generation_version": None, "data_generation_hash": None}
            return {
                "data_generation_version": benchmark.get("data_generation_version"),
                "data_generation_hash": benchmark.get("data_generation_hash"),
            }

        baseline_prov = _provenance(baseline_data)
        current_prov = _provenance(current_data)
        outcome["baseline"] = baseline_prov
        outcome["current"] = current_prov
        baseline_version = baseline_prov["data_generation_version"]
        current_version = current_prov["data_generation_version"]
        baseline_hash = baseline_prov["data_generation_hash"]
        current_hash = current_prov["data_generation_hash"]
        if baseline_version is None or current_version is None:
            outcome["status"] = "unknown"
            outcome["compatible"] = None
            outcome["warning"] = (
                "One or both results predate data-generation stamping; "
                "generation compatibility is unknown. Timing deltas may reflect dataset differences."
            )
            return outcome
        if baseline_version != current_version:
            outcome["status"] = "incompatible"
            outcome["compatible"] = False
            outcome["warning"] = (
                "Results were generated from different data generations "
                f"(baseline version={baseline_version}, current version={current_version}); "
                "timing deltas may reflect dataset differences rather than performance changes."
            )
            return outcome
        if baseline_hash is None or current_hash is None:
            outcome["status"] = "unknown"
            outcome["compatible"] = None
            outcome["warning"] = (
                f"Both results carry data-generation version {baseline_version}, but at least one "
                "is missing its base-constants fingerprint; generation compatibility is unknown. "
                "Timing deltas may reflect dataset differences."
            )
            return outcome
        if baseline_hash != current_hash:
            outcome["status"] = "incompatible"
            outcome["compatible"] = False
            outcome["warning"] = (
                f"Both results carry data-generation version {baseline_version}, but their "
                f"base-constants fingerprints differ ({str(baseline_hash)[:12]}… vs "
                f"{str(current_hash)[:12]}…): the generator specs likely changed without a "
                "version bump. Timing deltas may reflect dataset differences rather than "
                "performance changes."
            )
        return outcome

    def compare_results(self, baseline_path: Path, current_path: Path) -> dict[str, Any]:
        baseline_result = self.load_result_from_file(baseline_path)
        current_result = self.load_result_from_file(current_path)

        if not baseline_result or not current_result:
            return {
                "error": "Failed to load one or both result files",
                "baseline_loaded": bool(baseline_result),
                "current_loaded": bool(current_result),
            }

        baseline_data = baseline_result["data"]
        current_data = current_result["data"]
        baseline_version = baseline_result.get("result_schema_version") or baseline_result.get("version", "unknown")
        current_version = current_result.get("result_schema_version") or current_result.get("version", "unknown")

        perf_baseline = self._extract_performance_metrics(baseline_data)
        perf_current = self._extract_performance_metrics(current_data)

        comparison: dict[str, Any] = {
            "baseline_file": baseline_path.name if self.anonymize else str(baseline_path),
            "current_file": current_path.name if self.anonymize else str(current_path),
            "baseline_version": baseline_version,
            "current_version": current_version,
            "generation_compatibility": self._check_generation_compatibility(baseline_data, current_data),
            "performance_changes": {},
            "query_comparisons": [],
        }

        for metric in ["total_execution_time", "average_query_time"]:
            if metric in perf_baseline and metric in perf_current:
                baseline_value = perf_baseline[metric]
                current_value = perf_current[metric]
                change = ((current_value - baseline_value) / baseline_value * 100) if baseline_value else 0
                comparison["performance_changes"][metric] = {
                    "baseline": baseline_value,
                    "current": current_value,
                    "change_percent": round(change, 2),
                    "improved": current_value < baseline_value,
                }

        baseline_queries = self._extract_query_map(baseline_data)
        current_queries = self._extract_query_map(current_data)

        for query_id, baseline_query in baseline_queries.items():
            current_query = current_queries.get(query_id)
            if not current_query:
                continue

            baseline_time = baseline_query.get("execution_time_ms") or 0
            current_time = current_query.get("execution_time_ms") or 0
            change = ((current_time - baseline_time) / baseline_time * 100) if baseline_time else 0

            comparison["query_comparisons"].append(
                {
                    "query_id": query_id,
                    "baseline_time_ms": baseline_time,
                    "current_time_ms": current_time,
                    "change_percent": round(change, 2),
                    "improved": current_time < baseline_time,
                }
            )

        if comparison["query_comparisons"]:
            improved = len([q for q in comparison["query_comparisons"] if q["improved"]])
            regressed = len(
                [q for q in comparison["query_comparisons"] if not q["improved"] and q["change_percent"] > 0]
            )
            comparison["summary"] = {
                "total_queries_compared": len(comparison["query_comparisons"]),
                "improved_queries": improved,
                "regressed_queries": regressed,
                "unchanged_queries": len(comparison["query_comparisons"]) - improved - regressed,
                "overall_assessment": self._assess_performance_change(comparison["performance_changes"]),
            }

        return comparison

    def _extract_performance_metrics(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_result_dict(data)
        return {
            "total_queries": normalized.total_queries,
            "successful_queries": normalized.passed_queries,
            "failed_queries": normalized.failed_queries,
            "total_execution_time": (normalized.total_time_ms or 0) / 1000,
            "average_query_time": (normalized.avg_time_ms or 0) / 1000,
        }

    def _extract_query_map(self, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        normalized = normalize_result_dict(data)
        query_map = get_query_map(normalized)
        return {
            query_id: {
                "query_id": query_id,
                "execution_time_ms": q.execution_time_ms or 0,
                "rows_returned": q.rows_returned,
            }
            for query_id, q in query_map.items()
        }

    def _assess_performance_change(self, performance_changes: dict[str, Any]) -> str:
        if not performance_changes:
            return "no_data"

        time_metrics = ["total_execution_time", "average_query_time"]
        time_changes = [performance_changes[m]["change_percent"] for m in time_metrics if m in performance_changes]

        if not time_changes:
            return "unknown"

        avg_change = sum(time_changes) / len(time_changes)

        if avg_change < -10:
            return "significant_improvement"
        if avg_change < -5:
            return "improvement"
        if avg_change > 10:
            return "significant_regression"
        if avg_change > 5:
            return "regression"
        return "no_significant_change"

    def export_comparison_report(
        self,
        comparison: dict[str, Any],
        output_path: PathLike | None = None,
    ) -> PathLike:
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.output_dir / f"comparison_report_{timestamp}.html"

        summary = comparison.get("summary", {})
        performance_changes = comparison.get("performance_changes", {})
        query_comparisons = comparison.get("query_comparisons", [])
        generation = comparison.get("generation_compatibility") or {}
        generation_warning = str(generation.get("warning") or "")
        generation_warning_html = (
            '<div class="metric regressed" style="margin-bottom: 20px;">'
            "<h3>Data generation warning</h3>"
            f"<p>{html_escape(generation_warning, quote=True)}</p></div>"
            if generation_warning
            else ""
        )
        total_queries_compared = html_escape(str(summary.get("total_queries_compared", 0)), quote=True)
        improved_queries = html_escape(str(summary.get("improved_queries", 0)), quote=True)
        regressed_queries = html_escape(str(summary.get("regressed_queries", 0)), quote=True)
        unchanged_queries = html_escape(str(summary.get("unchanged_queries", 0)), quote=True)

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>BenchBox Comparison Report</title>
    <style>
        body {{ font-family: system-ui, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 24px; border-radius: 8px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .metric {{ padding: 16px; border-radius: 8px; text-align: center; }}
        .metric.improved {{ background: #d4edda; border: 1px solid #c3e6cb; }}
        .metric.regressed {{ background: #f8d7da; border: 1px solid #f5c6cb; }}
        .metric.neutral {{ background: #f8f9fa; border: 1px solid #e9ecef; }}
        .metric h3 {{ margin: 0; font-size: 0.85em; text-transform: uppercase; color: #666; }}
        .metric p {{ margin: 8px 0 0 0; font-size: 1.4em; font-weight: bold; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
        th, td {{ border: 1px solid #e5e5e5; padding: 10px 12px; }}
        th {{ background: #f8f9fa; font-weight: 500; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Performance Comparison Report</h1>
            <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        {generation_warning_html}
        <div class="summary">
            <div class="metric neutral">
                <h3>Queries Compared</h3>
                <p>{total_queries_compared}</p>
            </div>
            <div class="metric improved">
                <h3>Improved</h3>
                <p>{improved_queries}</p>
            </div>
            <div class="metric regressed">
                <h3>Regressed</h3>
                <p>{regressed_queries}</p>
            </div>
            <div class="metric neutral">
                <h3>Unchanged</h3>
                <p>{unchanged_queries}</p>
            </div>
        </div>
        <h2>Performance Changes</h2>
        <ul>
            {
            "".join(
                f"<li>{html_escape(str(metric).replace('_', ' ').title(), quote=True)}: {vals['change_percent']:+.1f}% "
                f"({'Improved' if vals['improved'] else 'Regressed'})</li>"
                for metric, vals in performance_changes.items()
            )
        }
        </ul>
        <h2>Query Details</h2>
        <table>
            <tr><th>Query</th><th>Baseline (ms)</th><th>Current (ms)</th><th>Change</th><th>Status</th></tr>
            {
            "".join(
                f"<tr><td>{html_escape(str(q['query_id']), quote=True)}</td><td>{q['baseline_time_ms']:.1f}</td>"
                f"<td>{q['current_time_ms']:.1f}</td><td>{q['change_percent']:+.1f}%</td>"
                f"<td>{'Improved' if q['improved'] else 'Regressed'}</td></tr>"
                for q in query_comparisons
            )
        }
        </table>
    </div>
</body>
</html>"""

        self._write_file(output_path, html_content)
        return output_path
