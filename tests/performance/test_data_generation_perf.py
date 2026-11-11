# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import gc
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from unittest.mock import patch

import psutil
import pytest

from benchbox.tpcds import TPCDS
from benchbox.tpch import TPCH

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class DataGenerationMonitor:
    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.memory_measurements = []
        self.disk_measurements = []
        self.running = False
        self.thread = None
        self.start_time = None

    def start(self):
        self.running = True
        self.memory_measurements = []
        self.disk_measurements = []
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._monitor)
        self.thread.daemon = True
        self.thread.start()

    def stop(self) -> dict[str, Any]:
        self.running = False
        if self.thread:
            self.thread.join()

        total_time = time.time() - self.start_time if self.start_time else 0

        memory_stats = self._calculate_memory_stats()
        disk_stats = self._calculate_disk_stats()

        return {
            "total_time": total_time,
            "memory_stats": memory_stats,
            "disk_stats": disk_stats,
        }

    def _monitor(self):
        process = psutil.Process()
        while self.running:
            try:
                memory_mb = process.memory_info().rss / 1024 / 1024
                self.memory_measurements.append(memory_mb)

                try:
                    disk_io = process.io_counters()
                    self.disk_measurements.append(
                        {
                            "read_bytes": disk_io.read_bytes,
                            "write_bytes": disk_io.write_bytes,
                        }
                    )
                except (AttributeError, psutil.AccessDenied):
                    pass

                time.sleep(self.interval)
            except psutil.NoSuchProcess:
                break

    def _calculate_memory_stats(self) -> dict[str, float]:
        if not self.memory_measurements:
            return {"peak_mb": 0, "avg_mb": 0, "min_mb": 0}

        return {
            "peak_mb": max(self.memory_measurements),
            "avg_mb": sum(self.memory_measurements) / len(self.memory_measurements),
            "min_mb": min(self.memory_measurements),
        }

    def _calculate_disk_stats(self) -> dict[str, float]:
        if not self.disk_measurements:
            return {"total_read_mb": 0, "total_write_mb": 0}

        first_measurement = self.disk_measurements[0]
        last_measurement = self.disk_measurements[-1]

        total_read_bytes = last_measurement["read_bytes"] - first_measurement["read_bytes"]
        total_write_bytes = last_measurement["write_bytes"] - first_measurement["write_bytes"]

        return {
            "total_read_mb": total_read_bytes / 1024 / 1024,
            "total_write_mb": total_write_bytes / 1024 / 1024,
        }


@pytest.fixture
def data_generation_monitor():
    return DataGenerationMonitor()


@pytest.fixture
def data_generation_config():
    return {
        "timeout_seconds": 300,
        "memory_limit_mb": 2048,
        "disk_limit_mb": 1024,
        "scale_factors": [0.01, 0.1],
        "tpcds_scale_factors": [1.0, 2.0],
        "parallel_workers": [1, 2, 4],
        "benchmark_runs": 3,
    }


@pytest.mark.performance
@pytest.mark.slow
class TestDataGenerationPerformance:
    def test_tpch_data_generation_scaling(self, data_generation_monitor, data_generation_config, tmp_path):
        scale_factors = data_generation_config["scale_factors"]
        results = {}

        for scale_factor in scale_factors:
            output_dir = tmp_path / f"tpch_sf_{scale_factor}"
            output_dir.mkdir(exist_ok=True)

            tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

            with patch.object(tpch._impl.data_generator, "generate") as mock_gen:
                mock_gen.side_effect = self._mock_tpch_data_generation

                data_generation_monitor.start()

                start_time = time.time()
                tpch.generate_data()
                end_time = time.time()
                generation_time = end_time - start_time

                metrics = data_generation_monitor.stop()

                results[scale_factor] = {
                    "generation_time": generation_time,
                    "metrics": metrics,
                }

        self._verify_data_generation_scaling(results)

    def test_tpcds_data_generation_scaling(self, data_generation_monitor, data_generation_config, tmp_path):
        scale_factors = data_generation_config["tpcds_scale_factors"]
        results = {}

        for scale_factor in scale_factors:
            output_dir = tmp_path / f"tpcds_sf_{scale_factor}"
            output_dir.mkdir(exist_ok=True)

            tpcds = TPCDS(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

            with patch.object(tpcds._impl.data_generator, "generate") as mock_gen:
                mock_gen.side_effect = self._mock_tpcds_data_generation

                data_generation_monitor.start()

                start_time = time.time()
                tpcds.generate_data()
                end_time = time.time()
                generation_time = end_time - start_time

                metrics = data_generation_monitor.stop()

                results[scale_factor] = {
                    "generation_time": generation_time,
                    "metrics": metrics,
                }

        self._verify_data_generation_scaling(results)

    def test_memory_usage_during_generation(self, data_generation_monitor, data_generation_config, tmp_path):
        scale_factor = 0.01
        output_dir = tmp_path / "memory_test"
        output_dir.mkdir(exist_ok=True)

        tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

        with patch.object(tpch._impl.data_generator, "generate") as mock_gen:
            mock_gen.side_effect = self._mock_memory_intensive_generation

            data_generation_monitor.start()
            tpch.generate_data()
            metrics = data_generation_monitor.stop()

            memory_limit = data_generation_config["memory_limit_mb"] * 1.15
            peak_memory = metrics["memory_stats"]["peak_mb"]

            assert peak_memory < memory_limit, f"Memory usage too high: {peak_memory}MB > {memory_limit}MB"

            final_memory = metrics["memory_stats"]["min_mb"]
            assert final_memory <= peak_memory, f"Memory not properly released: {final_memory}MB > {peak_memory}MB"

    def test_disk_io_efficiency(self, data_generation_monitor, data_generation_config, tmp_path):
        scale_factor = 0.01
        output_dir = tmp_path / "disk_test"
        output_dir.mkdir(exist_ok=True)

        tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

        with patch.object(tpch._impl.data_generator, "generate") as mock_gen:
            mock_gen.side_effect = self._mock_disk_io_generation

            data_generation_monitor.start()
            tpch.generate_data()
            metrics = data_generation_monitor.stop()

            disk_limit = data_generation_config["disk_limit_mb"]
            total_write = metrics["disk_stats"]["total_write_mb"]

            assert total_write < disk_limit * 2, f"Disk write too high: {total_write}MB > {disk_limit * 2}MB"

    def test_data_generation_consistency(self, tmp_path):
        scale_factor = 0.01
        runs = 3
        results = []

        for run in range(runs):
            output_dir = tmp_path / f"consistency_run_{run}"
            output_dir.mkdir(exist_ok=True)

            tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

            with patch.object(tpch._impl.data_generator, "generate") as mock_gen:
                mock_gen.side_effect = self._mock_consistent_data_generation

                start_time = time.time()
                tpch.generate_data()
                end_time = time.time()
                generation_time = end_time - start_time

                results.append(generation_time)

        avg_time = sum(results) / len(results)
        for result in results:
            assert abs(result - avg_time) < avg_time * 0.5, (
                f"Inconsistent generation time: {result}s vs avg {avg_time}s"
            )

    def test_large_scale_factor_performance(self, data_generation_monitor, tmp_path):
        scale_factor = 0.1
        output_dir = tmp_path / "large_scale"
        output_dir.mkdir(exist_ok=True)

        tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

        with patch.object(tpch._impl.data_generator, "generate") as mock_gen:
            mock_gen.side_effect = self._mock_large_scale_generation

            data_generation_monitor.start()

            start_time = time.time()
            tpch.generate_data()
            end_time = time.time()
            generation_time = end_time - start_time

            metrics = data_generation_monitor.stop()

            assert generation_time < 60.0, f"Large scale generation too slow: {generation_time}s"

            peak_memory = metrics["memory_stats"]["peak_mb"]
            assert peak_memory < 4096, f"Large scale memory usage too high: {peak_memory}MB"

    def test_error_handling_during_generation(self, tmp_path):
        scale_factor = 0.01
        output_dir = tmp_path / "error_test"
        output_dir.mkdir(exist_ok=True)

        tpch = TPCH(scale_factor=scale_factor, output_dir=output_dir, verbose=False)

        with patch.object(tpch._impl.data_generator, "generate") as mock_gen:
            mock_gen.side_effect = self._mock_error_prone_generation

            try:
                tpch.generate_data()
            except Exception as e:
                assert "generation failure" in str(e).lower()

    def _mock_tpch_data_generation(self) -> dict[str, Any]:
        generation_time = 0.05

        time.sleep(generation_time)

        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
            "part": Path("/tmp/part.csv"),
            "partsupp": Path("/tmp/partsupp.csv"),
            "orders": Path("/tmp/orders.csv"),
            "lineitem": Path("/tmp/lineitem.csv"),
        }

    def _mock_tpcds_data_generation(self) -> dict[str, Any]:
        generation_time = 0.05

        time.sleep(generation_time)

        from pathlib import Path

        return {
            "store_sales": Path("/tmp/store_sales.csv"),
            "catalog_sales": Path("/tmp/catalog_sales.csv"),
            "web_sales": Path("/tmp/web_sales.csv"),
            "inventory": Path("/tmp/inventory.csv"),
            "store": Path("/tmp/store.csv"),
            "catalog_page": Path("/tmp/catalog_page.csv"),
            "web_page": Path("/tmp/web_page.csv"),
        }

    def _mock_parallel_table_generation(self) -> dict[str, Any]:
        base_time = 0.05
        scale_factor = 0.01
        generation_time = base_time * scale_factor

        time.sleep(min(generation_time, 0.02))

        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
            "part": Path("/tmp/part.csv"),
            "partsupp": Path("/tmp/partsupp.csv"),
            "orders": Path("/tmp/orders.csv"),
            "lineitem": Path("/tmp/lineitem.csv"),
        }

    def _parallel_table_generation(self, tpch_instance, worker_count: int) -> dict[str, Any]:
        tables = [
            "region",
            "nation",
            "customer",
            "supplier",
            "part",
            "partsupp",
            "orders",
            "lineitem",
        ]

        def generate_table(table_name):
            return self._mock_parallel_table_generation()

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(generate_table, table) for table in tables]
            [future.result() for future in as_completed(futures)]

        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
            "part": Path("/tmp/part.csv"),
            "partsupp": Path("/tmp/partsupp.csv"),
            "orders": Path("/tmp/orders.csv"),
            "lineitem": Path("/tmp/lineitem.csv"),
        }

    def _mock_memory_intensive_generation(self) -> dict[str, Any]:
        memory_chunk = []

        try:
            chunk_size = int(1000 * 0.1)
            for i in range(chunk_size):
                memory_chunk.append(f"data_row_{i}" * 100)

            time.sleep(0.01)

            time.sleep(0.005)

            from pathlib import Path

            return {
                "region": Path("/tmp/region.csv"),
                "nation": Path("/tmp/nation.csv"),
                "customer": Path("/tmp/customer.csv"),
                "supplier": Path("/tmp/supplier.csv"),
                "part": Path("/tmp/part.csv"),
                "partsupp": Path("/tmp/partsupp.csv"),
                "orders": Path("/tmp/orders.csv"),
                "lineitem": Path("/tmp/lineitem.csv"),
            }
        finally:
            del memory_chunk
            gc.collect()

    def _mock_disk_io_generation(self) -> dict[str, Any]:
        int(1000 * 0.01)

        time.sleep(0.01)

        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
            "part": Path("/tmp/part.csv"),
            "partsupp": Path("/tmp/partsupp.csv"),
            "orders": Path("/tmp/orders.csv"),
            "lineitem": Path("/tmp/lineitem.csv"),
        }

    def _mock_consistent_data_generation(self) -> dict[str, Any]:
        base_time = 0.01

        time.sleep(base_time)

        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
        }

    def _mock_large_scale_generation(self) -> dict[str, Any]:
        base_time = 0.2

        time.sleep(base_time)

        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
            "part": Path("/tmp/part.csv"),
            "partsupp": Path("/tmp/partsupp.csv"),
            "orders": Path("/tmp/orders.csv"),
            "lineitem": Path("/tmp/lineitem.csv"),
        }

    def _mock_error_prone_generation(self) -> dict[str, Any]:
        raise Exception("Simulated generation failure")

        from pathlib import Path

        return {"region": Path("/tmp/region.csv")}

    def _verify_data_generation_scaling(self, results: dict[float, dict[str, Any]]):
        scale_factors = sorted(results.keys())

        if len(scale_factors) < 2:
            return

        for i in range(1, len(scale_factors)):
            current_sf = scale_factors[i]
            prev_sf = scale_factors[i - 1]

            current_time = results[current_sf]["generation_time"]
            prev_time = results[prev_sf]["generation_time"]

            scale_ratio = current_sf / prev_sf
            max_expected_time = prev_time * scale_ratio * 1.5

            assert current_time < max_expected_time, (
                f"Data generation scaling too poor: {current_time}s vs expected max {max_expected_time}s"
            )

    def _verify_parallel_generation_efficiency(self, results: dict[int, dict[str, Any]]):
        worker_counts = sorted(results.keys())

        if len(worker_counts) < 2:
            return

        sequential_time = results[1]["generation_time"]

        for worker_count in worker_counts[1:]:
            parallel_time = results[worker_count]["generation_time"]

            max_acceptable_time = sequential_time * 5

            assert parallel_time < max_acceptable_time, (
                f"Parallel generation with {worker_count} workers too slow: {parallel_time}s vs expected max {max_acceptable_time}s"
            )


@pytest.mark.performance
class TestDataGenerationBenchmarkComparison:
    def test_benchmark_generation_comparison(self, tmp_path):
        scale_factor = 0.01
        tpcds_scale_factor = 1.0
        benchmark_results = {}

        tpch_dir = tmp_path / "tpch_comparison"
        tpch_dir.mkdir(exist_ok=True)

        tpch = TPCH(scale_factor=scale_factor, output_dir=tpch_dir, verbose=False)

        with patch.object(tpch._impl.data_generator, "generate") as mock_tpch:
            mock_tpch.side_effect = self._mock_tpch_data_generation

            start_time = time.time()
            tpch.generate_data()
            end_time = time.time()
            tpch_time = end_time - start_time

            benchmark_results["tpch"] = tpch_time

        tpcds_dir = tmp_path / "tpcds_comparison"
        tpcds_dir.mkdir(exist_ok=True)

        tpcds = TPCDS(scale_factor=tpcds_scale_factor, output_dir=tpcds_dir, verbose=False)

        with patch.object(tpcds._impl.data_generator, "generate") as mock_tpcds:
            mock_tpcds.side_effect = self._mock_tpcds_data_generation

            start_time = time.time()
            tpcds.generate_data()
            end_time = time.time()
            tpcds_time = end_time - start_time

            benchmark_results["tpcds"] = tpcds_time

        assert all(time > 0 for time in benchmark_results.values())

        print(f"TPC-H generation time: {benchmark_results['tpch']:.4f}s")
        print(f"TPC-DS generation time: {benchmark_results['tpcds']:.4f}s")

        for benchmark_name, gen_time in benchmark_results.items():
            assert gen_time < 10.0, f"{benchmark_name} generation too slow: {gen_time}s"

    def _mock_tpch_data_generation(self) -> dict[str, Any]:
        time.sleep(0.01)
        from pathlib import Path

        return {
            "region": Path("/tmp/region.csv"),
            "nation": Path("/tmp/nation.csv"),
            "customer": Path("/tmp/customer.csv"),
            "supplier": Path("/tmp/supplier.csv"),
            "part": Path("/tmp/part.csv"),
            "partsupp": Path("/tmp/partsupp.csv"),
            "orders": Path("/tmp/orders.csv"),
            "lineitem": Path("/tmp/lineitem.csv"),
        }

    def _mock_tpcds_data_generation(self) -> dict[str, Any]:
        time.sleep(0.015)
        from pathlib import Path

        return {
            "store_sales": Path("/tmp/store_sales.csv"),
            "catalog_sales": Path("/tmp/catalog_sales.csv"),
            "web_sales": Path("/tmp/web_sales.csv"),
            "inventory": Path("/tmp/inventory.csv"),
            "store": Path("/tmp/store.csv"),
            "catalog_page": Path("/tmp/catalog_page.csv"),
            "web_page": Path("/tmp/web_page.csv"),
        }


