from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from benchbox.cli.host_lock_optin import host_guarded
from benchbox.cli.shared import console
from benchbox.core.manifest import (
    ManifestV1,
    ManifestV2,
    load_manifest,
    upgrade_v1_to_v2,
)
from benchbox.core.runner.conversion import FormatConversionOrchestrator
from benchbox.utils.format_converters import ConversionOptions

logger = logging.getLogger(__name__)


def _get_schemas_from_manifest(manifest: ManifestV2, benchmark_name: str | None) -> dict[str, dict[str, Any]]:
    if not benchmark_name:
        benchmark_name = manifest.benchmark

    if not benchmark_name:
        raise click.ClickException(
            "Cannot determine benchmark type. Specify --benchmark or ensure manifest contains benchmark field."
        )

    from benchbox.core.benchmark_loader import get_core_benchmark_class

    try:
        benchmark_class = get_core_benchmark_class(benchmark_name)
        benchmark_instance = benchmark_class()

        schemas = {}
        if hasattr(benchmark_instance, "get_schema"):
            for table_name in manifest.tables.keys():
                try:
                    schema = benchmark_instance.get_schema(table_name)
                    if schema:
                        schemas[table_name] = schema
                except Exception:
                    logger.warning(f"Could not get schema for table: {table_name}")
                    continue

        if not schemas:
            raise click.ClickException(
                f"Could not get schemas from benchmark '{benchmark_name}'. Ensure the benchmark is properly configured."
            )

        return schemas

    except Exception as e:
        raise click.ClickException(f"Failed to load benchmark '{benchmark_name}': {e}") from e


@click.command(
    "convert",
    help=(
        "Convert benchmark data to optimized table formats.\n"
        "\n"
        "Converts TPC benchmark data from TBL (pipe-delimited) format to columnar\n"
        "formats like Parquet, Vortex, Delta Lake, or Apache Iceberg. This enables\n"
        "significant query performance improvements (2-10x faster).\n"
        "\n"
        "The command reads the manifest file in the input directory to discover\n"
        "tables and their source files, then converts each table to the target format.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "\n"
        "\b\n"
        "    # Convert to Parquet with default settings\n"
        "    benchbox convert --input ./data/tpch_sf1 --format parquet\n"
        "\n"
        "\b\n"
        "    # Convert to Vortex for high compression\n"
        "    benchbox convert --input ./data/tpch_sf1 --format vortex\n"
        "\n"
        "\b\n"
        "    # Convert to Delta Lake with Zstd compression\n"
        "    benchbox convert --input ./data/tpch_sf1 --format delta --compression zstd\n"
        "\n"
        "\b\n"
        "    # Convert to partitioned Parquet by date column\n"
        "    benchbox convert --input ./data/tpch_sf1 --format parquet \\\n"
        "        --partition l_shipdate\n"
        "\n"
        "\b\n"
        "    # Convert with multiple partition columns\n"
        "    benchbox convert --input ./data/tpch_sf1 --format parquet \\\n"
        "        --partition l_shipdate --partition l_returnflag\n"
        "\n"
        "\b\n"
        "    # Convert to Iceberg without validation (faster, not TPC compliant)\n"
        "    benchbox convert --input ./data/tpch_sf1 --format iceberg --no-validate\n"
        "\n"
        "\b\n"
        "    # Specify benchmark explicitly for schema lookup\n"
        "    benchbox convert --input ./data/custom --format parquet --benchmark tpch\n"
        "\n"
        "\b\n"
        "Supported Formats:\n"
        "    parquet  - Apache Parquet (columnar, compressed)\n"
        "    vortex   - Vortex (high compression, fast scans)\n"
        "    delta    - Delta Lake (ACID transactions, time travel)\n"
        "    iceberg  - Apache Iceberg (schema evolution, hidden partitioning)\n"
        "    ducklake - DuckLake (DuckDB native, ACID transactions, time travel)\n"
        "\n"
        "\b\n"
        "Compression Options:\n"
        "    snappy   - Fast compression, moderate ratio (default)\n"
        "    gzip     - Better ratio, slower\n"
        "    zstd     - Best ratio, moderate speed\n"
        "    none     - No compression"
    ),
)
@click.option(
    "--input",
    "input_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Input directory containing benchmark data and manifest",
)
@click.option(
    "--format",
    "target_format",
    type=click.Choice(["parquet", "vortex", "delta", "iceberg", "ducklake"], case_sensitive=False),
    required=True,
    help="Target format for conversion",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Output directory (default: same as input)",
)
@click.option(
    "--compression",
    type=click.Choice(["snappy", "gzip", "zstd", "none"], case_sensitive=False),
    default="snappy",
    show_default=True,
    help="Compression codec for converted files",
)
@click.option(
    "--partition",
    "partition_cols",
    multiple=True,
    help="Column(s) to partition by (can be specified multiple times)",
)
@click.option(
    "--benchmark",
    type=str,
    help="Benchmark name for schema lookup (auto-detected from manifest if not specified)",
)
@click.option(
    "--validate/--no-validate",
    default=True,
    show_default=True,
    help="Validate row counts after conversion (TPC compliance)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@host_guarded("convert")
def convert(
    input_dir: Path,
    target_format: str,
    output_dir: Path | None,
    compression: str,
    partition_cols: tuple[str, ...],
    benchmark: str | None,
    validate: bool,
    verbose: bool,
) -> None:
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    manifest_path = input_dir / "_datagen_manifest.json"
    if not manifest_path.exists():
        raise click.ClickException(
            f"Manifest not found: {manifest_path}\nRun data generation first or specify a directory with a manifest."
        )

    console.print(f"[bold blue]Converting to {target_format.upper()}[/bold blue]")
    console.print(f"Input: {input_dir}")

    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        raise click.ClickException(f"Failed to load manifest: {e}") from e

    if isinstance(manifest, ManifestV1):
        console.print("[dim]Upgrading manifest v1 to v2...[/dim]")
        manifest = upgrade_v1_to_v2(manifest)

    try:
        schemas = _get_schemas_from_manifest(manifest, benchmark)
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Failed to get schemas: {e}") from e

    effective_output_dir = output_dir or input_dir
    if output_dir:
        console.print(f"Output: {effective_output_dir}")

    options = ConversionOptions(
        compression=compression,
        partition_cols=list(partition_cols) if partition_cols else [],
        merge_shards=True,
        validate_row_count=validate,
        output_dir=effective_output_dir,
    )

    console.print(f"Compression: {compression}")
    if partition_cols:
        console.print(f"Partitioning by: {', '.join(partition_cols)}")
    console.print(f"Row validation: {'enabled' if validate else 'disabled'}")
    console.print()

    orchestrator = FormatConversionOrchestrator()
    console.print(f"[bold]Converting {len(manifest.tables)} tables...[/bold]")

    try:
        results = orchestrator.convert_benchmark_tables(
            manifest_path=manifest_path,
            output_dir=input_dir,
            target_format=target_format.lower(),
            schemas=schemas,
            options=options,
        )
    except Exception as e:
        raise click.ClickException(f"Conversion failed: {e}") from e

    console.print()
    console.print("[bold green]Conversion complete![/bold green]")
    console.print()

    total_rows = 0
    total_source_size = 0
    total_output_size = 0

    for table_name, result in results.items():
        total_rows += result.row_count
        total_source_size += result.source_size_bytes
        total_output_size += result.output_size_bytes

        compression_ratio = f"{result.compression_ratio:.2f}x" if result.compression_ratio > 0 else "N/A"
        console.print(f"  [green]✓[/green] {table_name}: {result.row_count:,} rows, compression: {compression_ratio}")

    console.print()
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Tables converted: {len(results)}")
    console.print(f"  Total rows: {total_rows:,}")

    if total_source_size > 0 and total_output_size > 0:
        overall_ratio = total_source_size / total_output_size
        source_mb = total_source_size / (1024 * 1024)
        output_mb = total_output_size / (1024 * 1024)
        console.print(f"  Source size: {source_mb:.1f} MB")
        console.print(f"  Output size: {output_mb:.1f} MB")
        console.print(f"  Overall compression: {overall_ratio:.2f}x")

    console.print(f"  Manifest updated: {manifest_path}")
