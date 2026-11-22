import json
from pathlib import Path

import click

from benchbox.cli.shared import console
from benchbox.core.query_plans.comparison import compare_query_plans, generate_plan_comparison_summary
from benchbox.core.query_plans.visualization import render_comparison
from benchbox.core.results.loader import iter_query_results, load_result_file
from benchbox.core.results.query_normalizer import normalize_query_id
from benchbox.core.results.tpcdi_audit import LEGACY_TPCDI_REFUSAL, legacy_tpcdi_note


@click.command(
    "compare-plans",
    hidden=True,
    deprecated=True,
    help=("Compare query plans between benchmark runs. Deprecated; use `benchbox compare --include-plans`."),
)
@click.option("--run1", "run1_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--run2", "run2_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--query-id", help="Query ID to compare")
@click.option(
    "--output", "output_format", type=click.Choice(["text", "json", "html"], case_sensitive=False), default="text"
)
@click.option("--output-file", "output_file", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--threshold", type=float, default=0.0)
@click.option("--summary", "show_summary", is_flag=True)
@click.option("--regression-threshold", type=float, default=20.0)
@click.pass_context
def compare_plans(
    ctx, run1_path, run2_path, query_id, output_format, output_file, threshold, show_summary, regression_threshold
):
    try:
        results1, _ = load_result_file(run1_path)
        results2, _ = load_result_file(run2_path)
        if legacy_tpcdi_note(results1) or legacy_tpcdi_note(results2):
            raise ValueError(LEGACY_TPCDI_REFUSAL)
        if show_summary:
            _run_summary_mode(results1, results2, output_format, output_file, regression_threshold)
            return

        query_ids = _resolve_query_ids(query_id, results1, results2, ctx)
        comparisons = _build_comparisons(query_ids, results1, results2, threshold, query_id)
        if not comparisons:
            console.print(
                f"[green]Success:[/green] All queries have similarity >= {threshold:.1%}"
                if threshold > 0.0
                else "[yellow]No plans available for comparison[/yellow]"
            )
            return
        _emit_comparisons(comparisons, output_format, output_file, query_id, results1, results2)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] Results file not found: {e.filename}")
        ctx.exit(1)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] Invalid JSON in results file: {e}")
        ctx.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if ctx.obj and ctx.obj.get("verbose"):
            raise
        ctx.exit(1)


def _run_summary_mode(
    results1, results2, output_format: str, output_file: Path | None, regression_threshold: float
) -> None:
    summary = generate_plan_comparison_summary(results1, results2, regression_threshold_pct=regression_threshold)
    writers = {
        "json": lambda: _output_summary_json(summary, return_string=output_file is not None),
        "html": lambda: _output_summary_html(summary),
        "text": lambda: _output_summary_text(summary, return_string=output_file is not None),
    }
    output = writers[output_format]()
    if output_file and output is not None:
        output_file.write_text(output, encoding="utf-8")
        console.print(f"[green]Report written to:[/green] {output_file}")


def _collect_query_ids(results) -> set[str]:
    return {str(qr["query_id"]) for qr in iter_query_results(results) if qr.get("query_id") is not None}


def _resolve_query_ids(query_id: str | None, results1, results2, ctx) -> list[str]:
    if query_id:
        return [query_id]
    common = sorted(_collect_query_ids(results1) & _collect_query_ids(results2))
    if not common:
        console.print("[yellow]Warning:[/yellow] No common queries found between runs")
        ctx.exit(1)
    return common


def _build_comparisons(
    query_ids: list[str], results1, results2, threshold: float, explicit_query_id: str | None
) -> list:
    comparisons = []
    for qid in query_ids:
        exec1 = _find_query_execution(results1, qid)
        exec2 = _find_query_execution(results2, qid)
        if not exec1 or not exec2:
            _warn_if_explicit(explicit_query_id, f"Query '{qid}' not found in both runs")
            continue
        plan1 = exec1.get("query_plan")
        plan2 = exec2.get("query_plan")
        if not plan1 or not plan2:
            load_errors = [
                err
                for err in (
                    getattr(results1, "plans_load_error", None),
                    getattr(results2, "plans_load_error", None),
                )
                if err
            ]
            if load_errors:
                _warn_if_explicit(
                    explicit_query_id,
                    f"Query '{qid}': plans file exists but failed to load: {'; '.join(load_errors)}",
                )
            else:
                _warn_if_explicit(explicit_query_id, f"Query '{qid}' missing plan in one or both runs")
            continue
        comparison = compare_query_plans(plan1, plan2)
        if explicit_query_id or comparison.similarity.overall_similarity < threshold or threshold == 0.0:
            comparisons.append((qid, comparison))
    return comparisons


def _warn_if_explicit(explicit_query_id: str | None, message: str) -> None:
    if explicit_query_id:
        console.print(f"[yellow]Warning:[/yellow] {message}")


def _emit_comparisons(comparisons, output_format, output_file, explicit_query_id, results1, results2) -> None:
    writers = {
        "json": lambda: _output_json(comparisons, return_string=output_file is not None),
        "html": lambda: _output_html(comparisons, explicit_query_id is not None, results1, results2),
        "text": lambda: _output_text(comparisons, explicit_query_id is not None, return_string=output_file is not None),
    }
    output = writers[output_format]()
    if output_file and output is not None:
        output_file.write_text(output, encoding="utf-8")
        console.print(f"[green]Report written to:[/green] {output_file}")


def _find_query_execution(results, query_id: str):
    normalized_target = normalize_query_id(query_id)
    return next(
        (qr for qr in iter_query_results(results) if normalize_query_id(qr.get("query_id", "")) == normalized_target),
        None,
    )


def _output_text(comparisons: list, single_query: bool, return_string: bool = False) -> str | None:
    if single_query:
        rendered = str(render_comparison(comparisons[0][1]))
        console.print(rendered) if not return_string else None
        return rendered if return_string else None

    lines = ["Query Plan Comparison Summary", "Query  Similarity  Type  Prop  Struct  Status"]
    for query_id, comparison in comparisons:
        sim = comparison.similarity
        lines.append(
            f"{query_id}  {sim.overall_similarity:.1%}  "
            f"{sim.type_mismatches or '-'}  {sim.property_mismatches or '-'}  "
            f"{sim.structure_mismatches or '-'}  {_status_label(sim.overall_similarity)}"
        )
    lines.extend(["", f"Summary: {len(comparisons)} queries compared"])
    output = "\n".join(lines)
    if return_string:
        return output
    console.print(output)
    return None


def _status_label(similarity: float) -> str:
    labels = ((0.95, "Nearly Identical"), (0.75, "Very Similar"), (0.50, "Somewhat Similar"), (0.0, "Different"))
    return next(label for cutoff, label in labels if similarity >= cutoff)


def _output_json(comparisons: list, return_string: bool = False) -> str | None:
    output = [
        {
            "query_id": query_id,
            "plans_identical": comparison.plans_identical,
            "fingerprints_match": comparison.fingerprints_match,
            "similarity": {
                "overall": comparison.similarity.overall_similarity,
                "structural": comparison.similarity.structural_similarity,
                "operator": comparison.similarity.operator_similarity,
                "property": comparison.similarity.property_similarity,
            },
            "differences": {
                "type_mismatches": comparison.similarity.type_mismatches,
                "property_mismatches": comparison.similarity.property_mismatches,
                "structure_mismatches": comparison.similarity.structure_mismatches,
            },
            "summary": comparison.summary,
        }
        for query_id, comparison in comparisons
    ]
    if return_string:
        return json.dumps(output, indent=2)
    console.print_json(data=output)
    return None


def _output_summary_text(summary, return_string: bool = False) -> str | None:
    lines = [
        "PLAN COMPARISON SUMMARY",
        f"Baseline: {summary.baseline_run_id}",
        f"Current: {summary.current_run_id}",
        f"Plans Compared: {summary.plans_compared}",
        f"Unchanged: {summary.plans_unchanged}",
        f"Changed: {summary.plans_changed}",
    ]
    changed = [diff for diff in summary.structural_differences if diff.change_type != "unchanged"]
    if changed:
        lines.append("Structural Differences:")
        lines.extend(
            f"  {diff.query_id}: {diff.change_type} ({diff.similarity:.1%})\n    {diff.details[:80]}"
            for diff in changed
        )
    lines.extend(_summary_regression_lines([corr for corr in summary.performance_correlations if corr.is_regression]))
    text = "\n".join(lines)
    if return_string:
        return text
    console.print(text)
    return None


def _summary_regression_lines(regressions: list) -> list[str]:
    if not regressions:
        return ["No regressions detected"]
    return [f"REGRESSIONS DETECTED: {len(regressions)}"] + [
        f"  {reg.query_id}: {reg.baseline_time_ms:.2f}ms -> {reg.current_time_ms:.2f}ms (+{reg.perf_change_pct:.1f}%)"
        for reg in regressions
    ]


def _output_summary_json(summary, return_string: bool = False) -> str | None:
    if return_string:
        return json.dumps(summary.to_dict(), indent=2)
    console.print_json(data=summary.to_dict())
    return None


def _output_summary_html(summary) -> str:
    body = _html_table(
        ["Query", "Change", "Similarity", "Details"],
        [
            [diff.query_id, diff.change_type, f"{diff.similarity:.1%}", diff.details[:80]]
            for diff in summary.structural_differences
            if diff.change_type != "unchanged"
        ],
    )
    if regressions := [corr for corr in summary.performance_correlations if corr.is_regression]:
        body += "<h2>Performance Regressions</h2>"
        body += _html_table(
            ["Query", "Baseline (ms)", "Current (ms)", "Change"],
            [
                [
                    reg.query_id,
                    f"{reg.baseline_time_ms:.2f}",
                    f"{reg.current_time_ms:.2f}",
                    f"+{reg.perf_change_pct:.1f}%",
                ]
                for reg in regressions
            ],
        )
    return _html_document("Query Plan Comparison Report", body)


def _output_html(comparisons: list, single_query: bool, results1, results2) -> str:
    body = _html_table(
        ["Query", "Similarity", "Type Diff", "Prop Diff", "Struct Diff", "Status"],
        [
            [
                query_id,
                f"{comparison.similarity.overall_similarity:.1%}",
                comparison.similarity.type_mismatches or "-",
                comparison.similarity.property_mismatches or "-",
                comparison.similarity.structure_mismatches or "-",
                _status_label(comparison.similarity.overall_similarity),
            ]
            for query_id, comparison in comparisons
        ],
    )
    return _html_document("Query Plan Comparison", body)


def _html_document(title: str, body: str) -> str:
    style = "body{font-family:sans-serif;margin:40px}td,th{padding:6px}"
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title><style>{style}</style></head><body><h1>{title}</h1>{body}</body></html>"


def _html_table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return ""
    header_html = "".join(f"<th>{header}</th>" for header in headers)
    row_html = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{row_html}</tbody></table>"
