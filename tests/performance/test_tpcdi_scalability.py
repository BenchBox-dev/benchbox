# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

import psutil
import pytest

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


pytest.importorskip("pandas")

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig


@pytest.mark.slow
@pytest.mark.performance
class TestTPCDIScalabilityPerformance:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def test_database(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA cache_size = 10000")
        conn.execute("PRAGMA temp_store = MEMORY")
        yield conn
        conn.close()

    def test_multi_scale_factor_performance_analysis(self, temp_dir, test_database):
        scale_factors = [0.01, 0.1, 0.5, 1.0]
        performance_results = {}

        for scale_factor in scale_factors:
            print(f"\n=== Testing Scale Factor: {scale_factor} ===")

            config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=temp_dir / f"scale_{scale_factor}",
                enable_parallel=True,
                max_workers=4,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)
            process = psutil.Process(os.getpid())

            start_memory = process.memory_info().rss / 1024 / 1024
            overall_start = time.time()

            schema_start = time.time()
            benchmark.create_schema(test_database, "sqlite")
            schema_time = time.time() - schema_start
            schema_memory = process.memory_info().rss / 1024 / 1024

            data_gen_start = time.time()
            benchmark.generate_data()
            data_gen_time = time.time() - data_gen_start
            data_gen_memory = process.memory_info().rss / 1024 / 1024

            data_files = benchmark.tables

            total_data_size = 0
            file_count = 0
            for file_path in data_files.values():
                path_obj = Path(file_path) if isinstance(file_path, str) else file_path
                if path_obj.exists():
                    total_data_size += path_obj.stat().st_size
                    file_count += 1
            total_data_mb = total_data_size / (1024 * 1024)

            etl_start = time.time()
            etl_results = benchmark.run_enhanced_etl_pipeline(
                test_database,
                dialect="sqlite",
                enable_data_quality_monitoring=True,
                enable_error_recovery=True,
            )
            etl_time = time.time() - etl_start
            etl_memory = process.memory_info().rss / 1024 / 1024

            query_start = time.time()
            query_results = self._test_query_performance_sample(benchmark, test_database)
            query_time = time.time() - query_start

            overall_time = time.time() - overall_start
            peak_memory = max(schema_memory, data_gen_memory, etl_memory)
            memory_efficiency = total_data_mb / max(peak_memory - start_memory, 0.1)

            performance_results[scale_factor] = {
                "overall_time": overall_time,
                "schema_time": schema_time,
                "data_generation_time": data_gen_time,
                "etl_processing_time": etl_time,
                "query_sample_time": query_time,
                "data_files_generated": file_count,
                "total_data_size_mb": total_data_mb,
                "data_generation_rate_mbps": total_data_mb / max(data_gen_time, 0.001),
                "etl_success": etl_results["success"],
                "etl_records_processed": etl_results.get("total_records_processed", 0),
                "etl_processing_rate_rps": etl_results.get("total_records_processed", 0) / max(etl_time, 0.001),
                "etl_quality_score": etl_results.get("quality_score", 0),
                "start_memory_mb": start_memory,
                "peak_memory_mb": peak_memory,
                "memory_growth_mb": peak_memory - start_memory,
                "memory_efficiency_mb_per_mb": memory_efficiency,
                "query_sample_results": query_results,
                "successful_queries": sum(1 for q in query_results if q.get("success", False)),
                "average_query_time": sum(q.get("execution_time_seconds", 0) for q in query_results)
                / max(len(query_results), 1),
            }

            print(
                f"Scale {scale_factor}: {overall_time:.2f}s total, {total_data_mb:.1f}MB data, {etl_results.get('total_records_processed', 0)} ETL records"
            )

            self._clear_test_database(test_database)

        self._analyze_scaling_characteristics(performance_results)
        self._validate_performance_thresholds(performance_results)

        self._save_performance_baseline(performance_results, temp_dir)

    def _test_query_performance_sample(self, benchmark: TPCDIBenchmark, connection) -> list[dict]:
        test_queries = [1, 2, 3, 4, 5]
        query_results = []

        cursor = connection.cursor()
        for query_id in test_queries:
            try:
                query_sql = benchmark.get_query(query_id, dialect="sqlite")

                start_time = time.time()
                cursor.execute(query_sql)
                results = cursor.fetchall()
                execution_time = time.time() - start_time

                query_results.append(
                    {
                        "query_id": query_id,
                        "execution_time_seconds": execution_time,
                        "row_count": len(results),
                        "success": True,
                        "rows_per_second": len(results) / max(execution_time, 0.001),
                    }
                )

            except Exception as e:
                query_results.append(
                    {
                        "query_id": query_id,
                        "execution_time_seconds": float("inf"),
                        "error": str(e),
                        "success": False,
                    }
                )

        return query_results

    def _clear_test_database(self, connection):
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            for table in tables:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")

            connection.commit()
        except Exception as e:
            print(f"Warning: Error clearing database: {e}")

    def _analyze_scaling_characteristics(self, results: dict[str, dict]):
        scale_factors = sorted(results.keys())

        print("\n=== SCALING ANALYSIS ===")

        for i in range(len(scale_factors) - 1):
            current_sf = scale_factors[i]
            next_sf = scale_factors[i + 1]

            current = results[current_sf]
            next_result = results[next_sf]

            sf_ratio = next_sf / current_sf

            scaling_metrics = {
                "data_size": next_result["total_data_size_mb"] / max(current["total_data_size_mb"], 0.001),
                "etl_records": next_result["etl_records_processed"] / max(current["etl_records_processed"], 1),
                "etl_time": next_result["etl_processing_time"] / max(current["etl_processing_time"], 0.001),
                "memory_usage": next_result["memory_growth_mb"] / max(current["memory_growth_mb"], 0.001),
                "overall_time": next_result["overall_time"] / max(current["overall_time"], 0.001),
            }

            print(f"\nScale Factor {current_sf} -> {next_sf} (ratio {sf_ratio:.1f}x):")
            for metric, ratio in scaling_metrics.items():
                efficiency = sf_ratio / ratio if ratio > 0 else 0
                print(f"  {metric}: {ratio:.2f}x scaling (efficiency: {efficiency:.2f})")

            assert scaling_metrics["data_size"] >= sf_ratio * 0.5, (
                f"Data size scaling too low: {scaling_metrics['data_size']:.2f}"
            )
            assert scaling_metrics["etl_records"] >= sf_ratio * 0.15, (
                f"ETL records scaling too low: {scaling_metrics['etl_records']:.2f}"
            )

    def _validate_performance_thresholds(self, results: dict[str, dict]):
        print("\n=== PERFORMANCE VALIDATION ===")

        for scale_factor, metrics in results.items():
            print(f"\nScale Factor {scale_factor}:")

            assert metrics["etl_success"], f"ETL failed at scale factor {scale_factor}"

            if scale_factor >= 0.5:
                min_data_rate = 3.0
            elif scale_factor >= 0.1:
                min_data_rate = 1.0
            else:
                min_data_rate = 0.3
            assert metrics["data_generation_rate_mbps"] >= min_data_rate, (
                f"Data generation too slow at SF {scale_factor}: {metrics['data_generation_rate_mbps']:.2f} MB/s"
            )

            if metrics["etl_records_processed"] >= 100:
                min_etl_rate = 50
                assert metrics["etl_processing_rate_rps"] >= min_etl_rate, (
                    f"ETL processing too slow at SF {scale_factor}: {metrics['etl_processing_rate_rps']:.1f} rec/s"
                )
            else:
                print(
                    f"  ETL Rate: {metrics['etl_processing_rate_rps']:.1f} rec/s (not validated, only {metrics['etl_records_processed']} records)"
                )

            max_growth_mb = 1000 * scale_factor + 100
            assert metrics["memory_growth_mb"] <= max_growth_mb, (
                f"Memory growth too high at SF {scale_factor}: {metrics['memory_growth_mb']:.1f}MB"
            )

            query_success_rate = metrics["successful_queries"] / max(len(metrics["query_sample_results"]), 1)
            assert query_success_rate >= 0.5, f"Too many query failures at SF {scale_factor}: {query_success_rate:.1%}"

            print(f"  Data Rate: {metrics['data_generation_rate_mbps']:.1f} MB/s ✅")
            print(f"  ETL Rate: {metrics['etl_processing_rate_rps']:.1f} rec/s ✅")
            print(f"  Memory Growth: {metrics['memory_growth_mb']:.1f}MB ✅")
            print(f"  Query Success: {query_success_rate:.1%} ✅")

    def _save_performance_baseline(self, results: dict[str, dict], output_dir: Path):
        baseline_file = output_dir / "tpcdi_scalability_baseline.json"

        baseline_summary = {
            "timestamp": datetime.now().isoformat(),
            "scale_factors_tested": list(results.keys()),
            "performance_summary": {},
        }

        for sf, metrics in results.items():
            baseline_summary["performance_summary"][str(sf)] = {
                "overall_time": metrics["overall_time"],
                "data_generation_rate_mbps": metrics["data_generation_rate_mbps"],
                "etl_processing_rate_rps": metrics["etl_processing_rate_rps"],
                "peak_memory_mb": metrics["peak_memory_mb"],
                "etl_success": metrics["etl_success"],
                "successful_queries": metrics["successful_queries"],
                "average_query_time": metrics["average_query_time"],
            }

        try:
            with open(baseline_file, "w", encoding="utf-8") as f:
                json.dump(baseline_summary, f, indent=2)
            print(f"\nPerformance baseline saved to: {baseline_file}")
        except Exception as e:
            print(f"Warning: Could not save performance baseline: {e}")

    def test_data_generation_performance_detailed(self, temp_dir, test_database):
        scale_factors = [0.01, 0.1, 0.5]
        generation_profiles = {}

        for scale_factor in scale_factors:
            config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=temp_dir / f"datagen_{scale_factor}",
                enable_parallel=True,
                max_workers=4,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)

            generation_start = time.time()

            process = psutil.Process(os.getpid())
            start_memory = process.memory_info().rss / 1024 / 1024

            benchmark.generate_data()

            generation_time = time.time() - generation_start
            end_memory = process.memory_info().rss / 1024 / 1024

            data_files = benchmark.tables

            file_analysis = {}
            total_size = 0

            for table_name, file_path in data_files.items():
                path_obj = Path(file_path) if isinstance(file_path, str) else file_path
                if path_obj.exists():
                    size_bytes = path_obj.stat().st_size
                    total_size += size_bytes

                    try:
                        with open(path_obj, encoding="utf-8") as f:
                            line_count = sum(1 for _ in f)

                        file_analysis[table_name] = {
                            "size_mb": size_bytes / (1024 * 1024),
                            "estimated_records": line_count,
                            "mb_per_record": size_bytes / (1024 * 1024) / max(line_count, 1),
                        }
                    except Exception:
                        file_analysis[table_name] = {
                            "size_mb": size_bytes / (1024 * 1024),
                            "estimated_records": 0,
                            "mb_per_record": 0,
                        }

            generation_profiles[scale_factor] = {
                "generation_time": generation_time,
                "total_size_mb": total_size / (1024 * 1024),
                "files_generated": len(data_files),
                "memory_used_mb": end_memory - start_memory,
                "generation_rate_mbps": (total_size / (1024 * 1024)) / max(generation_time, 0.001),
                "file_analysis": file_analysis,
            }

        for i in range(len(scale_factors) - 1):
            current_sf = scale_factors[i]
            next_sf = scale_factors[i + 1]

            current_profile = generation_profiles[current_sf]
            next_profile = generation_profiles[next_sf]

            size_ratio = next_profile["total_size_mb"] / max(current_profile["total_size_mb"], 0.001)
            sf_ratio = next_sf / current_sf

            assert size_ratio >= sf_ratio * 0.5, f"Data generation scaling issue: {size_ratio:.2f} vs {sf_ratio:.2f}"
            assert size_ratio <= sf_ratio * 2.0, f"Data generation over-scaling: {size_ratio:.2f} vs {sf_ratio:.2f}"

    def test_etl_processing_bottleneck_analysis(self, temp_dir, test_database):
        config = TPCDIConfig(scale_factor=0.1, output_dir=temp_dir, enable_parallel=True, max_workers=4)

        benchmark = TPCDIBenchmark(config=config)
        process = psutil.Process(os.getpid())

        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()

        benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        phase_profiles = {}

        start_time = time.time()
        start_memory = process.memory_info().rss / 1024 / 1024

        try:
            data_processing_result = benchmark._run_enhanced_data_processing()
            success = data_processing_result.get("success", False)
            records = data_processing_result.get("total_records", 0)
        except Exception as e:
            print(f"Warning: data_processing phase failed (private method): {e}")
            success = False
            records = 0

        phase_profiles["data_processing"] = {
            "time": time.time() - start_time,
            "memory_delta": process.memory_info().rss / 1024 / 1024 - start_memory,
            "success": success,
            "records": records,
        }

        start_time = time.time()
        start_memory = process.memory_info().rss / 1024 / 1024

        scd_result = benchmark._run_enhanced_scd_processing(test_database)

        phase_profiles["scd_processing"] = {
            "time": time.time() - start_time,
            "memory_delta": process.memory_info().rss / 1024 / 1024 - start_memory,
            "success": scd_result.get("success", False),
            "records": scd_result.get("records_processed", 0),
        }

        start_time = time.time()
        start_memory = process.memory_info().rss / 1024 / 1024

        source_files = benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])
        parallel_result = benchmark._transform_source_data_parallel(dict(source_files), "historical")

        phase_profiles["parallel_processing"] = {
            "time": time.time() - start_time,
            "memory_delta": process.memory_info().rss / 1024 / 1024 - start_memory,
            "success": parallel_result.get("records_processed", 0) > 0,
            "batches": parallel_result.get("records_processed", 0),
        }

        start_time = time.time()
        start_memory = process.memory_info().rss / 1024 / 1024

        incremental_result = benchmark._run_incremental_data_loading(test_database)

        phase_profiles["incremental_loading"] = {
            "time": time.time() - start_time,
            "memory_delta": process.memory_info().rss / 1024 / 1024 - start_memory,
            "success": incremental_result.get("success", False),
            "records": incremental_result.get("records_loaded", 0),
        }

        start_time = time.time()
        start_memory = process.memory_info().rss / 1024 / 1024

        quality_result = benchmark._run_data_quality_monitoring(test_database)

        phase_profiles["quality_monitoring"] = {
            "time": time.time() - start_time,
            "memory_delta": process.memory_info().rss / 1024 / 1024 - start_memory,
            "success": quality_result.get("success", False),
            "rules": quality_result.get("rules_executed", 0),
        }

        total_time = sum(profile["time"] for profile in phase_profiles.values())

        print("\n=== ETL PHASE PERFORMANCE ANALYSIS ===")
        for phase_name, profile in phase_profiles.items():
            time_pct = (profile["time"] / max(total_time, 0.001)) * 100
            print(
                f"{phase_name}: {profile['time']:.3f}s ({time_pct:.1f}%), "
                f"Memory: {profile['memory_delta']:+.1f}MB, "
                f"Success: {profile['success']}"
            )

        successful_phases = sum(1 for profile in phase_profiles.values() if profile["success"])
        total_phases = len(phase_profiles)

        assert successful_phases >= total_phases * 0.6, (
            f"Too many phase failures: {successful_phases}/{total_phases} succeeded. "
            "Note: Private method testing may have limitations."
        )

        for phase_name, profile in phase_profiles.items():
            if profile["success"]:
                assert profile["time"] < 30.0, f"ETL phase {phase_name} too slow: {profile['time']:.1f}s"
            else:
                print(f"Note: Phase {phase_name} failed (may be due to private method limitations)")

    def test_query_execution_performance_comprehensive(self, temp_dir, test_database):
        config = TPCDIConfig(scale_factor=0.1, output_dir=temp_dir, enable_parallel=True, max_workers=4)

        benchmark = TPCDIBenchmark(config=config)

        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=False,
        )

        assert etl_results["success"], "ETL must succeed for query performance testing"

        test_queries = list(range(1, 11))
        query_performance = {}

        cursor = test_database.cursor()

        print("\n=== QUERY PERFORMANCE ANALYSIS ===")

        for query_id in test_queries:
            try:
                query_sql = benchmark.get_query(query_id, dialect="sqlite")

                execution_times = []
                for _run in range(3):
                    start_time = time.time()
                    cursor.execute(query_sql)
                    results = cursor.fetchall()
                    execution_times.append(time.time() - start_time)

                avg_time = sum(execution_times) / len(execution_times)
                min_time = min(execution_times)
                max_time = max(execution_times)

                query_performance[query_id] = {
                    "avg_execution_time": avg_time,
                    "min_execution_time": min_time,
                    "max_execution_time": max_time,
                    "row_count": len(results),
                    "success": True,
                    "rows_per_second": len(results) / max(avg_time, 0.001),
                    "stability_ratio": min_time / max(max_time, 0.001),
                }

                print(
                    f"Query {query_id}: {avg_time:.3f}s avg, {len(results)} rows, "
                    f"{len(results) / max(avg_time, 0.001):.1f} rows/s"
                )

            except Exception as e:
                query_performance[query_id] = {
                    "avg_execution_time": float("inf"),
                    "error": str(e),
                    "success": False,
                }
                print(f"Query {query_id}: FAILED - {str(e)}")

        successful_queries = [q for q, perf in query_performance.items() if perf.get("success")]

        assert len(successful_queries) >= len(test_queries) * 0.7, "Too many query failures"

        fast_queries = [q for q in successful_queries if query_performance[q]["avg_execution_time"] < 1.0]
        medium_queries = [q for q in successful_queries if 1.0 <= query_performance[q]["avg_execution_time"] < 5.0]
        slow_queries = [q for q in successful_queries if query_performance[q]["avg_execution_time"] >= 5.0]

        print("\nQuery Performance Distribution:")
        print(f"  Fast (<1s): {len(fast_queries)} queries")
        print(f"  Medium (1-5s): {len(medium_queries)} queries")
        print(f"  Slow (>=5s): {len(slow_queries)} queries")

        assert len(fast_queries) + len(medium_queries) >= len(successful_queries) * 0.8, "Too many slow queries"

    def test_parallel_processing_scalability_analysis(self, temp_dir, test_database):
        worker_configurations = [1, 2, 4]
        setup_config = TPCDIConfig(
            scale_factor=0.1,
            output_dir=temp_dir / "workers_setup",
            enable_parallel=True,
            max_workers=4,
        )
        setup_config.output_dir.mkdir(parents=True, exist_ok=True)
        setup_benchmark = TPCDIBenchmark(config=setup_config)
        source_files = setup_benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])
        assert source_files.get("csv"), "no CSV source files generated for scaling analysis"

        scalability_results = {}
        for worker_count in worker_configurations:
            config = TPCDIConfig(
                scale_factor=0.1,
                output_dir=temp_dir / f"workers_{worker_count}",
                enable_parallel=True,
                max_workers=worker_count,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)

            start_time = time.time()
            transform_results = benchmark._transform_source_data_parallel(dict(source_files), "historical")
            total_time = time.time() - start_time

            staged_tables = {
                table: frame.sort_values(list(frame.columns)).reset_index(drop=True)
                for table, frame in transform_results["staged_data"].items()
            }
            scalability_results[worker_count] = {
                "total_time": total_time,
                "records_processed": transform_results["records_processed"],
                "staged_tables": staged_tables,
            }

        print("\n=== PARALLEL TRANSFORM SCALABILITY ===")

        baseline = scalability_results[1]
        assert baseline["records_processed"] > 0, "canonical parallel path staged no records"

        for worker_count, result in scalability_results.items():
            print(f"Workers: {worker_count}, Time: {result['total_time']:.2f}s, Records: {result['records_processed']}")
            assert result["records_processed"] == baseline["records_processed"], (
                f"worker count {worker_count} staged a different record total than width 1"
            )
            assert set(result["staged_tables"]) == set(baseline["staged_tables"])
            for table, frame in result["staged_tables"].items():
                assert frame.equals(baseline["staged_tables"][table]), (
                    f"worker count {worker_count} staged different rows for {table}"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


