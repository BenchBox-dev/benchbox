from __future__ import annotations

import gc

import psutil

from benchbox.core.tpcdi.scaling import scaled_rows


class ResourceMonitoringMixin:
    def _log_simple_progress(self, current: int, total: int, table_name: str) -> None:
        if not self.enable_progress:
            return

        progress_pct = (current / total) * 100
        if current % max(1, total // 10) == 0:
            self.logger.info(f"{table_name}: {progress_pct:.1f}% ({current:,}/{total:,})")

    def _check_memory_usage(self) -> bool:
        current_memory = psutil.virtual_memory().percent
        return current_memory > (self.memory_threshold * 100)

    def _cleanup_memory(self) -> None:
        gc.collect()

    def _log_generation_summary(self) -> None:
        self.logger.info("Generation Summary:")
        self.logger.info(f"Total records generated: {self.generation_stats['records_generated']:,}")
        files_written = self.generation_stats.get(
            "files_written", len(self.generation_stats.get("generation_times", {}))
        )
        self.logger.info(f"Total files written: {files_written:,}")

        self.logger.info("Generation times by table:")
        for table, time_taken in self.generation_stats["generation_times"].items():
            self.logger.info(f"  {table}: {time_taken:.2f}s")

        self.logger.info(f"Scale Factor: {self.scale_factor}, Workers: {self.max_workers}")

    def _estimate_records_for_table(self, table_name: str) -> int:
        base_counts = {
            "DimDate": 5844,
            "DimTime": 288,
            "DimCompany": self.base_companies,
            "DimSecurity": self.base_securities,
            "DimCustomer": self.base_customers,
            "DimAccount": self.base_accounts,
            "FactTrade": self.base_trades,
        }

        base_count = base_counts.get(table_name, 1000)
        if table_name in ["DimDate", "DimTime"]:
            return base_count
        return int(base_count * self.scale_factor)

    def _estimate_memory_requirements(self) -> float:
        avg_record_size = 200
        chunk_memory = (self.chunk_size * avg_record_size) / (1024**3)
        return chunk_memory * 2

    def _estimate_disk_requirements(self) -> float:
        total_records = (
            5844
            + 288
            + scaled_rows(self.base_companies, self.scale_factor)
            + scaled_rows(self.base_securities, self.scale_factor)
            + scaled_rows(self.base_customers, self.scale_factor)
            + scaled_rows(self.base_accounts, self.scale_factor)
            + scaled_rows(self.base_trades, self.scale_factor)
            + sum(self._secondary_fact_row_counts().values())
        )

        avg_record_size = 150
        return (total_records * avg_record_size) / (1024**3)


__all__ = ["ResourceMonitoringMixin"]
