from __future__ import annotations

import csv
import json
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Sequence

import numpy as np
import yaml

from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch_skew.distributions import (
    ExponentialDistribution,
    NormalDistribution,
    SkewDistribution,
    UniformDistribution,
    ZipfianDistribution,
)
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.generation_marker import generation_in_progress
from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity

if TYPE_CHECKING:
    from benchbox.core.tpch_skew.skew_config import SkewConfiguration


def _load_generator_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("generator_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_GENERATOR_SPECS = _load_generator_specs()

_TPCH_BASE_ROW_COUNTS = dict(_GENERATOR_SPECS["base_row_counts"])

_COLUMN_INDICES = _GENERATOR_SPECS["column_indices"]


class TPCHSkewDataGenerator(VerbosityMixin):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: str | Path | None = None,
        skew_config: SkewConfiguration | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        parallel: int = 1,
        force_regenerate: bool = False,
        **kwargs,
    ) -> None:
        self.scale_factor = scale_factor
        self.output_dir = normalize_output_dir(output_dir) or Path.cwd() / "tpch_skew_data"

        verbosity_settings = compute_verbosity(verbose, quiet)
        self.apply_verbosity(verbosity_settings)
        self.logger = logging.getLogger("benchbox.core.tpch_skew.generator")

        self.parallel = parallel
        self.force_regenerate = force_regenerate

        from benchbox.core.tpch_skew.skew_config import SkewPreset, get_preset_config

        self.skew_config = skew_config or get_preset_config(SkewPreset.MODERATE)

        self.rng = np.random.default_rng(self.skew_config.seed)

        self.distribution = self._create_distribution()

        self._base_kwargs = kwargs

    def _create_distribution(self) -> SkewDistribution:
        dist_type = self.skew_config.distribution_type.lower()
        skew_factor = self.skew_config.skew_factor

        if dist_type == "zipfian":
            s = skew_factor * 2.0
            return ZipfianDistribution(s=s, num_elements=10000)
        elif dist_type == "normal":
            std = max(0.05, 0.5 * (1 - skew_factor))
            return NormalDistribution(mean=0.5, std=std)
        elif dist_type == "exponential":
            rate = 0.5 + skew_factor * 4.5
            return ExponentialDistribution(rate=rate)
        else:
            return UniformDistribution()

    def generate(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not self.force_regenerate and self._check_existing_data():
            self.log_verbose("✅ Valid skewed TPC-H data found, skipping generation")
            return self._collect_table_files()

        self.log_verbose(f"Generating TPC-H Skew data at scale factor {self.scale_factor}")
        self.log_verbose(f"Skew factor: {self.skew_config.skew_factor}")
        self.log_verbose(f"Distribution: {self.distribution.get_description()}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            self.log_verbose("Step 1/2: Generating base TPC-H data...")
            base_generator = TPCHDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=temp_path,
                verbose=self.verbose,
                quiet=self.quiet,
                parallel=self.parallel,
                force_regenerate=True,
                **self._base_kwargs,
            )
            base_tables = base_generator.generate()
            self.log_verbose(f"Base data generated: {len(base_tables)} tables")

            self.log_verbose("Step 2/2: Applying skew transformations...")
            skewed_tables = self._apply_skew_to_tables(base_tables)
            self._write_manifest(skewed_tables)

        self.log_verbose("✅ Skewed TPC-H data generation complete")
        return skewed_tables

    def _check_existing_data(self) -> bool:
        if generation_in_progress(self.output_dir):
            return False
        from benchbox.utils.datagen_version import manifest_datagen_is_current

        expected_files = [
            "customer.tbl",
            "lineitem.tbl",
            "nation.tbl",
            "orders.tbl",
            "part.tbl",
            "partsupp.tbl",
            "region.tbl",
            "supplier.tbl",
        ]
        for filename in expected_files:
            if not (self.output_dir / filename).exists():
                return False
        manifest_path = self.output_dir / "_datagen_manifest.json"
        if not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).debug("ignoring unreadable skew manifest: %s", exc)
            return False
        return bool(
            manifest_datagen_is_current(manifest, benchmark="tpch_skew")
            and self.manifest_matches_datagen_identity(manifest)
        )

    def manifest_matches_datagen_identity(self, manifest: dict[str, Any]) -> bool:
        from benchbox.utils.datagen_version import compute_datagen_identity_hash

        identity = self.skew_config.datagen_identity()
        parquet_writer = manifest.get("parquet_writer")
        parquet_rg_raw = manifest.get("parquet_row_group_size")
        parquet_row_group_size = (
            int(parquet_rg_raw) if isinstance(parquet_rg_raw, int) and not isinstance(parquet_rg_raw, bool) else None
        )
        backend = manifest.get("generator_backend")
        backend_version = manifest.get("generator_backend_version")
        scale_raw = manifest.get("scale_factor")
        scale_factor = (
            float(scale_raw) if isinstance(scale_raw, (int, float)) and not isinstance(scale_raw, bool) else None
        )
        expected_hash = compute_datagen_identity_hash(
            "tpch_skew",
            identity,
            backend=str(backend) if backend is not None else None,
            backend_version=str(backend_version) if backend_version is not None else None,
            parquet_writer=str(parquet_writer) if parquet_writer is not None else None,
            parquet_row_group_size=parquet_row_group_size,
            scale_factor=scale_factor,
        )
        return (
            manifest.get("skew_configuration") == identity
            and manifest.get("skew_configuration_hash") == self.skew_config.datagen_identity_hash()
            and manifest.get("data_generation_identity_hash") == expected_hash
        )

    def _write_manifest(self, table_paths: dict[str, Path]) -> None:
        from benchbox.utils.datagen_manifest import DataGenerationManifest, require_manifest_files

        identity = self.skew_config.datagen_identity()
        from benchbox.utils.datagen_version import compute_datagen_identity_hash

        manifest = DataGenerationManifest(
            output_dir=self.output_dir,
            benchmark="tpch_skew",
            scale_factor=self.scale_factor,
            seed=self.skew_config.seed,
            extra_metadata={
                "skew_configuration": identity,
                "skew_configuration_hash": self.skew_config.datagen_identity_hash(),
                "data_generation_identity_hash": compute_datagen_identity_hash("tpch_skew", identity),
            },
        )
        for table_name, file_path in table_paths.items():
            base_rows = _TPCH_BASE_ROW_COUNTS.get(table_name, 0)
            manifest.add_entry(table_name, file_path, row_count=int(base_rows * self.scale_factor))
        require_manifest_files(manifest.file_counts()[1], label="TPC-H Skew", output_dir=self.output_dir)
        manifest.write()

    def _collect_table_files(self) -> dict[str, Path]:
        table_files = {
            "customer": "customer.tbl",
            "lineitem": "lineitem.tbl",
            "nation": "nation.tbl",
            "orders": "orders.tbl",
            "part": "part.tbl",
            "partsupp": "partsupp.tbl",
            "region": "region.tbl",
            "supplier": "supplier.tbl",
        }
        return {
            table: self.output_dir / filename
            for table, filename in table_files.items()
            if (self.output_dir / filename).exists()
        }

    def _apply_skew_to_tables(self, base_tables: dict[str, Path | list[Path]]) -> dict[str, Path]:
        skewed_tables = {}

        unchanged = {"nation", "region"}

        for table_name, base_path_or_paths in base_tables.items():
            output_path = self.output_dir / f"{table_name}.tbl"

            base_path = self._normalize_shards(base_path_or_paths, table_name)

            if table_name in unchanged:
                shutil.copy2(base_path, output_path)
                skewed_tables[table_name] = output_path
                self.log_verbose(f"  {table_name}: copied unchanged")
                continue

            if table_name == "customer":
                self._transform_customer(base_path, output_path)
            elif table_name == "supplier":
                self._transform_supplier(base_path, output_path)
            elif table_name == "part":
                self._transform_part(base_path, output_path)
            elif table_name == "partsupp":
                self._transform_partsupp(base_path, output_path)
            elif table_name == "orders":
                self._transform_orders(base_path, output_path)
            elif table_name == "lineitem":
                self._transform_lineitem(base_path, output_path)
            else:
                shutil.copy2(base_path, output_path)

            skewed_tables[table_name] = output_path
            self.log_verbose(f"  {table_name}: skew applied")

        return skewed_tables

    @staticmethod
    def _normalize_shards(path_or_paths: Path | list[Path], table_name: str) -> Path:
        if isinstance(path_or_paths, list):
            if len(path_or_paths) == 1:
                return path_or_paths[0]
            merged = path_or_paths[0].parent / f"{table_name}_merged.tbl"
            with open(merged, "wb") as out:
                for shard in sorted(path_or_paths, key=lambda p: p.name):
                    with open(shard, "rb") as inp:
                        shutil.copyfileobj(inp, out)
            return merged
        return path_or_paths

    def _iter_tbl_rows(self, path: Path) -> Iterator[list[str]]:
        with path.open(encoding="utf-8") as handle:
            for row in csv.reader(handle, delimiter="|"):
                if row and row[-1] == "":
                    row = row[:-1]
                if row:
                    yield row

    def _stream_tbl_file(
        self,
        input_path: Path,
        output_path: Path,
        replacements: dict[int, tuple[np.ndarray, Sequence[str] | None]],
    ) -> None:
        with output_path.open("w", encoding="utf-8", newline="") as output:
            for index, row in enumerate(self._iter_tbl_rows(input_path)):
                for column, (values, choices) in replacements.items():
                    value = int(values[index])
                    row[column] = choices[value] if choices is not None else str(value)
                output.write("|".join(row) + "\n")

    def _count_tbl_rows(self, path: Path) -> int:
        return sum(1 for _ in self._iter_tbl_rows(path))

    def _temporal_day_offsets(self, num_rows: int, skew_factor: float) -> np.ndarray:
        total_days = (datetime(1998, 12, 31) - datetime(1992, 1, 1)).days
        dist = ExponentialDistribution(rate=0.5 + skew_factor * 4)
        samples = dist.sample(num_rows, self.rng)
        concentrated = 1 - samples
        return (concentrated * total_days).astype(int)

    @staticmethod
    def _date_choices() -> list[str]:
        start_date = datetime(1992, 1, 1)
        total_days = (datetime(1998, 12, 31) - start_date).days
        return [(start_date + timedelta(days=day)).strftime("%Y-%m-%d") for day in range(total_days + 1)]

    def _generate_skewed_values(
        self,
        num_values: int,
        min_val: int,
        max_val: int,
        skew_factor: float,
    ) -> np.ndarray:
        if skew_factor <= 0:
            return self.rng.integers(min_val, max_val + 1, size=num_values)

        if self.skew_config.distribution_type == "zipfian":
            num_elements = max_val - min_val + 1
            dist = ZipfianDistribution(s=skew_factor * 2.0, num_elements=num_elements)
        elif self.skew_config.distribution_type == "normal":
            std = max(0.05, 0.5 * (1 - skew_factor))
            dist = NormalDistribution(mean=0.5, std=std)
        elif self.skew_config.distribution_type == "exponential":
            rate = 0.5 + skew_factor * 4.5
            dist = ExponentialDistribution(rate=rate)
        else:
            dist = UniformDistribution()

        samples = dist.sample(num_values, self.rng)
        return dist.map_to_range(samples, min_val, max_val)

    def _transform_customer(self, input_path: Path, output_path: Path) -> None:
        attr_config = self.skew_config.attribute_skew
        num_rows = self._count_tbl_rows(input_path)
        replacements: dict[int, tuple[np.ndarray, Sequence[str] | None]] = {}

        if attr_config.customer_nation_skew > 0 or attr_config.customer_segment_skew > 0:
            nation_col = _COLUMN_INDICES["customer"]["c_nationkey"]
            segment_col = _COLUMN_INDICES["customer"]["c_mktsegment"]

            if attr_config.customer_nation_skew > 0:
                skewed_nations = self._generate_skewed_values(num_rows, 0, 24, attr_config.customer_nation_skew)
                replacements[nation_col] = (skewed_nations, None)

            if attr_config.customer_segment_skew > 0:
                segments = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
                skewed_indices = self._generate_skewed_values(
                    num_rows, 0, len(segments) - 1, attr_config.customer_segment_skew
                )
                replacements[segment_col] = (skewed_indices, segments)

        self._stream_tbl_file(input_path, output_path, replacements)

    def _transform_supplier(self, input_path: Path, output_path: Path) -> None:
        attr_config = self.skew_config.attribute_skew
        num_rows = self._count_tbl_rows(input_path)
        replacements: dict[int, tuple[np.ndarray, Sequence[str] | None]] = {}

        if attr_config.supplier_nation_skew > 0:
            nation_col = _COLUMN_INDICES["supplier"]["s_nationkey"]

            skewed_nations = self._generate_skewed_values(num_rows, 0, 24, attr_config.supplier_nation_skew)
            replacements[nation_col] = (skewed_nations, None)

        self._stream_tbl_file(input_path, output_path, replacements)

    def _transform_part(self, input_path: Path, output_path: Path) -> None:
        attr_config = self.skew_config.attribute_skew

        num_rows = self._count_tbl_rows(input_path)
        brand_col = _COLUMN_INDICES["part"]["p_brand"]
        type_col = _COLUMN_INDICES["part"]["p_type"]
        container_col = _COLUMN_INDICES["part"]["p_container"]
        replacements: dict[int, tuple[np.ndarray, Sequence[str] | None]] = {}

        if attr_config.part_brand_skew > 0:
            brands = [f"Brand#{i}{j}" for i in range(1, 6) for j in range(1, 6)]
            skewed_indices = self._generate_skewed_values(num_rows, 0, len(brands) - 1, attr_config.part_brand_skew)
            replacements[brand_col] = (skewed_indices, brands)

        if attr_config.part_type_skew > 0:
            types = [
                "STANDARD ANODIZED TIN",
                "STANDARD ANODIZED NICKEL",
                "STANDARD ANODIZED BRASS",
                "STANDARD ANODIZED STEEL",
                "STANDARD ANODIZED COPPER",
                "SMALL ANODIZED TIN",
                "SMALL ANODIZED NICKEL",
                "SMALL ANODIZED BRASS",
                "MEDIUM POLISHED TIN",
                "MEDIUM POLISHED NICKEL",
                "MEDIUM POLISHED BRASS",
                "LARGE POLISHED TIN",
                "LARGE POLISHED NICKEL",
                "LARGE POLISHED STEEL",
                "ECONOMY BRUSHED BRASS",
                "ECONOMY BRUSHED COPPER",
                "ECONOMY BRUSHED STEEL",
                "PROMO PLATED TIN",
                "PROMO PLATED NICKEL",
                "PROMO PLATED BRASS",
            ]
            skewed_indices = self._generate_skewed_values(num_rows, 0, len(types) - 1, attr_config.part_type_skew)
            replacements[type_col] = (skewed_indices, types)

        if attr_config.part_container_skew > 0:
            containers = [
                "SM CASE",
                "SM BOX",
                "SM BAG",
                "SM JAR",
                "SM PACK",
                "MED CASE",
                "MED BOX",
                "MED BAG",
                "MED JAR",
                "MED PACK",
                "LG CASE",
                "LG BOX",
                "LG BAG",
                "LG JAR",
                "LG PACK",
                "JUMBO CASE",
                "JUMBO BOX",
                "JUMBO BAG",
                "JUMBO JAR",
                "JUMBO PACK",
                "WRAP CASE",
                "WRAP BOX",
                "WRAP BAG",
                "WRAP JAR",
                "WRAP PACK",
            ]
            skewed_indices = self._generate_skewed_values(
                num_rows, 0, len(containers) - 1, attr_config.part_container_skew
            )
            replacements[container_col] = (skewed_indices, containers)

        self._stream_tbl_file(input_path, output_path, replacements)

    def _transform_partsupp(self, input_path: Path, output_path: Path) -> None:
        shutil.copy2(input_path, output_path)

    def _transform_orders(self, input_path: Path, output_path: Path) -> None:
        attr_config = self.skew_config.attribute_skew
        join_config = self.skew_config.join_skew
        temporal_config = self.skew_config.temporal_skew

        num_rows = self._count_tbl_rows(input_path)
        custkey_col = _COLUMN_INDICES["orders"]["o_custkey"]
        priority_col = _COLUMN_INDICES["orders"]["o_orderpriority"]
        orderdate_col = _COLUMN_INDICES["orders"]["o_orderdate"]
        replacements: dict[int, tuple[np.ndarray, Sequence[str] | None]] = {}

        max_custkey = int(150_000 * self.scale_factor)

        if join_config.customer_order_skew > 0 and self.skew_config.enable_join_skew:
            skewed_custkeys = self._generate_skewed_values(num_rows, 1, max_custkey, join_config.customer_order_skew)
            replacements[custkey_col] = (skewed_custkeys, None)

        if attr_config.order_priority_skew > 0 and self.skew_config.enable_attribute_skew:
            priorities = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
            skewed_indices = self._generate_skewed_values(
                num_rows, 0, len(priorities) - 1, attr_config.order_priority_skew
            )
            replacements[priority_col] = (skewed_indices, priorities)

        if temporal_config.order_date_skew > 0 and self.skew_config.enable_temporal_skew:
            replacements[orderdate_col] = (
                self._temporal_day_offsets(num_rows, temporal_config.order_date_skew),
                self._date_choices(),
            )

        self._stream_tbl_file(input_path, output_path, replacements)

    def _transform_lineitem(self, input_path: Path, output_path: Path) -> None:
        attr_config = self.skew_config.attribute_skew
        join_config = self.skew_config.join_skew
        temporal_config = self.skew_config.temporal_skew

        num_rows = self._count_tbl_rows(input_path)
        partkey_col = _COLUMN_INDICES["lineitem"]["l_partkey"]
        suppkey_col = _COLUMN_INDICES["lineitem"]["l_suppkey"]
        shipmode_col = _COLUMN_INDICES["lineitem"]["l_shipmode"]
        returnflag_col = _COLUMN_INDICES["lineitem"]["l_returnflag"]
        shipdate_col = _COLUMN_INDICES["lineitem"]["l_shipdate"]
        replacements: dict[int, tuple[np.ndarray, Sequence[str] | None]] = {}

        max_partkey = int(200_000 * self.scale_factor)
        max_suppkey = int(10_000 * self.scale_factor)

        if join_config.part_popularity_skew > 0 and self.skew_config.enable_join_skew:
            skewed_partkeys = self._generate_skewed_values(num_rows, 1, max_partkey, join_config.part_popularity_skew)
            replacements[partkey_col] = (skewed_partkeys, None)

        if join_config.supplier_volume_skew > 0 and self.skew_config.enable_join_skew:
            skewed_suppkeys = self._generate_skewed_values(num_rows, 1, max_suppkey, join_config.supplier_volume_skew)
            replacements[suppkey_col] = (skewed_suppkeys, None)

        if attr_config.shipmode_skew > 0 and self.skew_config.enable_attribute_skew:
            shipmodes = ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"]
            skewed_indices = self._generate_skewed_values(num_rows, 0, len(shipmodes) - 1, attr_config.shipmode_skew)
            replacements[shipmode_col] = (skewed_indices, shipmodes)

        if attr_config.returnflag_skew > 0 and self.skew_config.enable_attribute_skew:
            flags = ["N", "R", "A"]
            skewed_indices = self._generate_skewed_values(num_rows, 0, len(flags) - 1, attr_config.returnflag_skew)
            replacements[returnflag_col] = (skewed_indices, flags)

        if temporal_config.ship_date_seasonality > 0 and self.skew_config.enable_temporal_skew:
            replacements[shipdate_col] = (
                self._temporal_day_offsets(num_rows, temporal_config.ship_date_seasonality),
                self._date_choices(),
            )

        self._stream_tbl_file(input_path, output_path, replacements)

    def get_skew_statistics(self) -> dict:
        return {
            "scale_factor": self.scale_factor,
            "skew_factor": self.skew_config.skew_factor,
            "distribution": self.distribution.get_description(),
            "effective_skew": self.distribution.get_skew_factor(),
            "config_summary": self.skew_config.get_skew_summary(),
        }
