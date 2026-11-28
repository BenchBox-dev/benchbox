# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time
from pathlib import Path
from typing import Any, Optional, Union

from benchbox.core.results import corpus_rule


def generate_official_benchmark_audit_trail(
    *,
    result: Any,
    benchmark_title: str,
    benchmark_slug: str,
    output_file: Optional[Union[str, Path]] = None,
) -> Path:
    if output_file is None:
        if result.config.output_dir:
            output_dir = result.config.output_dir
        else:
            output_dir = Path.cwd() / "benchmark_results"
        output_file = output_dir / f"{benchmark_slug}_audit_trail_{int(time.time())}.txt"

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(f"{benchmark_title} Benchmark Audit Trail\n")
        file_obj.write("=" * 50 + "\n")
        file_obj.write(f"{corpus_rule.derived_results_disclaimer(benchmark_title)}\n\n")
        file_obj.write(f"Scale Factor: {result.config.scale_factor}\n")
        file_obj.write(f"Number of Streams: {result.config.num_streams}\n")
        file_obj.write(f"Start Time: {result.start_time}\n")
        file_obj.write(f"End Time: {result.end_time}\n")
        file_obj.write(f"Total Time: {result.total_time:.3f} seconds\n\n")

        file_obj.write(f"Power@Size: {result.power_at_size:.2f}\n")
        file_obj.write(f"Throughput@Size: {result.throughput_at_size:.2f}\n\n")

        file_obj.write(f"Success: {result.success}\n")
        if result.errors:
            file_obj.write("Errors:\n")
            for error in result.errors:
                file_obj.write(f"  - {error}\n")

    return output_path


def classify_official_scale_run(
    scale_factor: float,
    *,
    benchmark_id: str,
    official: bool,
    compliance_enum: Any,
) -> Any:
    outcome = corpus_rule.derive_outcome(
        corpus_rule.Facts(benchmark_id, scale_factor, frozenset({corpus_rule.PHASE_POWER}))
    )
    if outcome.class_name == corpus_rule.CLASS_SUBSCALE:
        return compliance_enum.UNOFFICIAL_SUBSCALE
    if outcome.class_name == corpus_rule.CLASS_NONSTANDARD_SCALE:
        return compliance_enum.UNOFFICIAL_NONSTANDARD
    return compliance_enum.OFFICIAL if official else None
