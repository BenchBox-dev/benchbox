from __future__ import annotations

import logging
import random
import time as time_module
from pathlib import Path
from typing import Any

import psutil

from benchbox.core.tpcdi.financial_data import FinancialDataPatterns
from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin, create_path_handler
from benchbox.utils.compression_mixin import CompressionMixin

from .dimensions import DimensionGenerationMixin
from .facts import FACT_TRADE_GENERATION_ALGORITHM_VERSION, FactGenerationMixin
from .manifest import ManifestMixin
from .monitoring import ResourceMonitoringMixin

try:
    import duckdb
except ImportError:
    duckdb = None


class TPCDIDataGenerator(
    CompressionMixin,
    CloudStorageGeneratorMixin,
    DimensionGenerationMixin,
    FactGenerationMixin,
    ManifestMixin,
    ResourceMonitoringMixin,
):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Path | None = None,
        chunk_size: int = 10000,
        buffer_size: int = 8192,
        max_workers: int | None = None,
        enable_progress: bool = True,
        generation_seed: int = 42,
        *,
        verbose: int | bool = 0,
        quiet: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.scale_factor = scale_factor
        self.output_dir = create_path_handler(output_dir) if output_dir else Path.cwd()
        if isinstance(verbose, bool):
            self.verbose_level = 1 if verbose else 0
        else:
            self.verbose_level = int(verbose or 0)
        self.verbose_enabled = self.verbose_level >= 1 and not quiet
        self.very_verbose = self.verbose_level >= 2 and not quiet
        self.quiet = bool(quiet)
        if self.quiet:
            enable_progress = False

        self.chunk_size = min(chunk_size, 50000)
        self.buffer_size = buffer_size
        self.max_workers = max_workers or min(4, (psutil.cpu_count() or 1))
        self.enable_progress = enable_progress

        self.logger = logging.getLogger(self.__class__.__name__)

        self.memory_threshold = 0.8
        self.initial_memory = psutil.virtual_memory().percent

        self.base_customers = 50000
        self.base_companies = 1000
        self.base_securities = 10000
        self.base_accounts = 100000
        self.base_trades = 1000000

        self.generation_seed = int(generation_seed)
        self._rng = random.Random(self.generation_seed)

        self.financial_patterns = FinancialDataPatterns(seed=self.generation_seed)

        self.generation_stats = {
            "records_generated": 0,
            "chunks_processed": 0,
            "memory_usage_peaks": [],
            "generation_times": {},
            "estimated_completion": None,
        }

        self._industries = [
            "Technology",
            "Healthcare",
            "Financial Services",
            "Manufacturing",
            "Retail",
            "Energy",
            "Telecommunications",
            "Transportation",
            "Real Estate",
            "Media",
            "Utilities",
            "Consumer Goods",
        ]

        self._sp_ratings = [
            "AAA",
            "AA+",
            "AA",
            "AA-",
            "A+",
            "A",
            "A-",
            "BBB+",
            "BBB",
            "BBB-",
            "BB+",
            "BB",
            "BB-",
        ]

        self._statuses = ["Active", "Inactive", "Suspended"]

        self._trade_types = [
            "Market Buy",
            "Market Sell",
            "Limit Buy",
            "Limit Sell",
            "Stop Buy",
            "Stop Sell",
        ]

        self._countries = [
            "USA",
            "Canada",
            "Mexico",
            "United Kingdom",
            "Germany",
            "France",
            "Japan",
            "Australia",
        ]

        self._us_states = [
            "CA",
            "NY",
            "TX",
            "FL",
            "IL",
            "PA",
            "OH",
            "GA",
            "NC",
            "MI",
            "NJ",
            "VA",
            "WA",
            "AZ",
            "MA",
        ]

    def generate_data(self, tables: list[str] | None = None) -> dict[str, str]:
        return self._handle_cloud_or_local_generation(
            self.output_dir,
            lambda output_dir: self._generate_data_local(output_dir, tables),
            self.enable_progress,
        )

    def _generate_data_local(self, output_dir: Path, tables: list[str] | None = None) -> dict[str, str]:
        if tables is None:
            tables = [
                "DimDate",
                "DimTime",
                "DimCompany",
                "DimSecurity",
                "DimCustomer",
                "DimAccount",
                "FactTrade",
                "Industry",
                "StatusType",
                "TaxRate",
                "TradeType",
                "DimBroker",
                "FactCashBalances",
                "FactHoldings",
                "FactMarketHistory",
                "FactWatches",
            ]

        original_output_dir = self.output_dir
        self.output_dir = output_dir
        try:
            self._rng = random.Random(self.generation_seed)
            self.financial_patterns = FinancialDataPatterns(seed=self.generation_seed)
            self.output_dir.mkdir(parents=True, exist_ok=True)

            if self.enable_progress:
                self.logger.info(f"Starting TPC-DI data generation (Scale Factor: {self.scale_factor})")
                self.logger.info(f"Settings: chunk_size={self.chunk_size}, workers={self.max_workers}")

            start_time = time_module.time()
            file_paths = {}

            table_generators = {
                "DimDate": self._generate_dimdate_data,
                "DimTime": self._generate_dimtime_data,
                "DimCompany": self._generate_dimcompany_data,
                "DimSecurity": self._generate_dimsecurity_data,
                "DimCustomer": self._generate_dimcustomer_data,
                "DimAccount": self._generate_dimaccount_data,
                "FactTrade": self._generate_facttrade_data,
                "Industry": self._generate_industry_data,
                "StatusType": self._generate_statustype_data,
                "TaxRate": self._generate_taxrate_data,
                "TradeType": self._generate_tradetype_data,
                "DimBroker": self._generate_dimbroker_data,
                "FactCashBalances": self._generate_factcashbalances_data,
                "FactHoldings": self._generate_factholdings_data,
                "FactMarketHistory": self._generate_factmarkethistory_data,
                "FactWatches": self._generate_factwatches_data,
            }

            for table_name in tables:
                if table_name in table_generators:
                    table_start = time_module.time()
                    if self.enable_progress:
                        self.logger.info(f"Generating {table_name}...")

                    file_paths[table_name] = table_generators[table_name]()

                    table_time = time_module.time() - table_start
                    self.generation_stats["generation_times"][table_name] = table_time

                    if self.enable_progress:
                        self.logger.info(f"Completed {table_name} in {table_time:.2f}s")

                    self._cleanup_memory()

            total_time = time_module.time() - start_time
            if self.enable_progress:
                self.logger.info(f"Data generation completed in {total_time:.2f}s")
                self._log_generation_summary()

            self._validate_file_format_consistency(output_dir)

            self._write_manifest(output_dir, file_paths)

            return file_paths
        finally:
            self.output_dir = original_output_dir

    def get_generation_config(self) -> dict[str, Any]:
        return {
            "scale_factor": self.scale_factor,
            "chunk_size": self.chunk_size,
            "buffer_size": self.buffer_size,
            "max_workers": self.max_workers,
            "generation_seed": self.generation_seed,
            "generation_algorithm_version": FACT_TRADE_GENERATION_ALGORITHM_VERSION,
            "memory_threshold": self.memory_threshold,
            "enable_progress": self.enable_progress,
            "estimated_records": {
                "DimCompany": int(self.base_companies * self.scale_factor),
                "DimSecurity": int(self.base_securities * self.scale_factor),
                "DimCustomer": int(self.base_customers * self.scale_factor),
                "DimAccount": int(self.base_accounts * self.scale_factor),
                "FactTrade": int(self.base_trades * self.scale_factor),
            },
            "estimated_memory_gb": self._estimate_memory_requirements(),
            "estimated_disk_gb": self._estimate_disk_requirements(),
        }


__all__ = ["TPCDIDataGenerator"]
