<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-DI Deployment Guide

```{tags} advanced, guide, tpc-di, cloud-platform
```

## Overview

The TPC-DI (Transaction Processing Performance Council - Data Integration) benchmark implementation in BenchBox provides a systematic ETL testing framework for data warehousing scenarios. This guide covers deployment options from development environments to production-grade installations.

### Key Features

- **Complete ETL Pipeline**: Extract, Transform, Load, and Validate processes
- **Multi-Format Support**: CSV, XML, JSON, and fixed-width file processing
- **Parallel Processing**: Configurable parallel execution for improved performance
- **Data Quality Validation**: Comprehensive data quality checks and metrics
- **Scalable Architecture**: Support for various scale factors and data volumes
- **Database Agnostic**: Works with SQLite, DuckDB, PostgreSQL, and other databases
- **Real-time Monitoring**: ETL metrics and progress tracking

## System Requirements

### Minimum Requirements

- **Python**: 3.8 or higher
- **Memory**: 2 GB RAM (for scale factor 0.1)
- **Storage**: 5 GB available disk space
- **CPU**: 2 cores (single-threaded execution)

### Recommended Requirements

- **Python**: 3.11 or higher
- **Memory**: 8 GB RAM (for scale factor 1.0)
- **Storage**: 50 GB available disk space (for larger scale factors)
- **CPU**: 4+ cores (parallel execution)
- **Network**: High-speed connection for distributed deployments

### Production Requirements

- **Python**: 3.11 or higher
- **Memory**: 16+ GB RAM (for scale factor 10+)
- **Storage**: 500+ GB available disk space
- **CPU**: 8+ cores with high clock speed
- **Network**: Dedicated network for database connections
- **Monitoring**: System monitoring tools (htop, iostat, etc.)

### Operating System Support

- **Linux**: Ubuntu 20.04+, CentOS 8+, RHEL 8+
- **macOS**: 11.0+ (Big Sur)
- **Windows**: 10/11 with WSL2 recommended

### Database Support

**Currently Supported**:
- **DuckDB**: 0.8.0+ (recommended for analytics)
- **SQLite**: 3.25+ (included with Python)
- **ClickHouse**: 20.0+
- **Snowflake**: Latest
- **BigQuery**: Latest
- **Databricks**: Latest
- **Redshift**: Latest

**Planned Support** (see [Development Roadmap](../../development/roadmap.md)):
- **PostgreSQL**: 12+
- **MySQL**: 8.0+
- **SQL Server**: 2019+

## Installation

### Standard Installation

#### Using pip (Recommended)

```bash
uv add benchbox

python -c "from benchbox import TPCDI; print('TPC-DI installation successful')"
```

#### Using conda

```bash
conda create -n benchbox python=3.11
conda activate benchbox

conda install numpy pytest

uv add benchbox
```

#### From Source

```bash
git clone https://github.com/your-org/benchbox.git
cd benchbox

python -m venv venv
source venv/bin/activate

uv pip install -e .

python -m pytest tests/test_tpcdi.py -v
```

On Windows, activate the environment with `venv\Scripts\activate` instead of `source venv/bin/activate`.

### Docker Installation

#### Using Pre-built Image

```bash
docker pull benchbox/tpcdi:latest

docker run -it --rm \
  -v $(pwd)/data:/app/data \
  benchbox/tpcdi:latest \
  python -c "from benchbox import TPCDI; print('Docker installation successful')"
```

#### Building Custom Image

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install benchbox

COPY . .

ENV PYTHONPATH=/app
ENV TPCDI_DATA_DIR=/app/data
ENV TPCDI_SCALE_FACTOR=1.0

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "-m", "benchbox.tpcdi.server"]
```

```bash
docker build -t my-tpcdi-benchmark .

docker run -it --rm \
  -e TPCDI_SCALE_FACTOR=0.1 \
  -v $(pwd)/data:/app/data \
  my-tpcdi-benchmark
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tpcdi-benchmark
  labels:
    app: tpcdi-benchmark
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tpcdi-benchmark
  template:
    metadata:
      labels:
        app: tpcdi-benchmark
    spec:
      containers:
      - name: tpcdi
        image: benchbox/tpcdi:latest
        env:
        - name: TPCDI_SCALE_FACTOR
          value: "1.0"
        - name: TPCDI_PARALLEL_WORKERS
          value: "4"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-secret
              key: url
        resources:
          requests:
            memory: "4Gi"
            cpu: "2"
          limits:
            memory: "8Gi"
            cpu: "4"
        volumeMounts:
        - name: data-volume
          mountPath: /app/data
      volumes:
      - name: data-volume
        persistentVolumeClaim:
          claimName: tpcdi-data-pvc
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tpcdi-data-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 100Gi
```

## Configuration

### Environment Variables

```bash
export TPCDI_SCALE_FACTOR=1.0
export TPCDI_OUTPUT_DIR=/path/to/data
export TPCDI_PARALLEL_WORKERS=4

export DATABASE_URL="postgresql://user:pass@localhost:5432/tpcdi"
export DATABASE_TIMEOUT=30

export TPCDI_BATCH_SIZE=10000
export TPCDI_MEMORY_LIMIT_MB=8192
export TPCDI_ENABLE_PARALLEL_ETL=true
export TPCDI_ENABLE_PARALLEL_QUERIES=true

export TPCDI_LOG_LEVEL=INFO
export TPCDI_LOG_FILE=/var/log/tpcdi.log
export TPCDI_ENABLE_METRICS=true
```

### Configuration File

Create a `tpcdi_config.yaml` file with supported ``TPCDIConfig`` fields:

```yaml
scale_factor: 1.0
output_dir: "/data/tpcdi"
enable_parallel: true
max_workers: 4
chunk_size: 50000
enable_validation: true
strict_validation: false
optimize_memory: true
log_level: "INFO"
```

Load the mapping with ``TPCDIConfig.from_dict``. It converts a string
``output_dir`` to a path and filters out unknown keys.

```python
from pathlib import Path

import yaml

from benchbox.core.tpcdi.config import TPCDIConfig

payload = yaml.safe_load(Path("tpcdi_config.yaml").read_text())
config = TPCDIConfig.from_dict(payload)
```

### Python Configuration

```python
from pathlib import Path

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig

config = TPCDIConfig(
    scale_factor=1.0,
    output_dir=Path('/data/tpcdi'),
    enable_parallel=True,
    max_workers=4,
)

benchmark = TPCDIBenchmark(config=config)
```

## Deployment Scenarios

### Development Environment

```python
import tempfile
from pathlib import Path
from benchbox import TPCDI

temp_dir = Path(tempfile.mkdtemp())
benchmark = TPCDI(scale_factor=0.01, output_dir=temp_dir)

data_paths = benchmark.generate_data()
print(f"Generated {len(data_paths)} data files in {temp_dir}")

import sqlite3
with sqlite3.connect(':memory:') as conn:
    benchmark.load_data_to_database(conn)
    result = benchmark.run_benchmark(conn, iterations=1)
    print(f"Benchmark completed: {len(result['queries'])} queries executed")
```

### Testing Environment

```python
import os
from pathlib import Path

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig

test_dir = Path(os.getenv('TEST_DATA_DIR', '/tmp/tpcdi_test'))
test_dir.mkdir(exist_ok=True)

config = TPCDIConfig(
    scale_factor=0.1,
    output_dir=test_dir,
    max_workers=2,
    enable_parallel=True,
)

benchmark = TPCDIBenchmark(config=config)

def run_test_suite():
    print("Testing data generation...")
    data_paths = benchmark.generate_data()
    assert len(data_paths) > 0, "Data generation failed"

    print("Testing ETL pipeline...")
    import duckdb
    with duckdb.connect(':memory:') as conn:
        etl_result = benchmark.run_etl_pipeline(conn, validate_data=True)
        assert etl_result['success'], f"ETL failed: {etl_result.get('error')}"

        print("Testing query execution...")
        query_result = benchmark.run_benchmark(conn, iterations=2)
        successful_queries = sum(1 for q in query_result['queries'].values()
                               if q.get('avg_time', 0) > 0)
        assert successful_queries > 0, "No queries executed successfully"

    print("All tests passed!")

if __name__ == "__main__":
    run_test_suite()
```

### Staging Environment

```python
import logging
import os
from pathlib import Path

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/tpcdi_staging.log'),
        logging.StreamHandler()
    ]
)

staging_dir = Path(os.getenv('STAGING_DATA_DIR', '/data/staging/tpcdi'))
staging_dir.mkdir(parents=True, exist_ok=True)

config = TPCDIConfig(
    scale_factor=float(os.getenv('TPCDI_SCALE_FACTOR', '0.5')),
    output_dir=staging_dir,
    max_workers=int(os.getenv('TPCDI_WORKERS', '4')),
    enable_parallel=True,
)

benchmark = TPCDIBenchmark(config=config)

import psycopg
from psycopg.rows import dict_row

def get_database_connection():
    return psycopg.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', '5432')),
        dbname=os.getenv('DB_NAME', 'tpcdi_staging'),
        user=os.getenv('DB_USER', 'tpcdi'),
        password=os.getenv('DB_PASSWORD'),
        row_factory=dict_row,
    )

def run_staging_benchmark():
    logger = logging.getLogger(__name__)
    logger.info("Starting TPC-DI staging benchmark")

    try:
        logger.info("Generating data...")
        data_paths = benchmark.generate_data()
        logger.info(f"Generated {len(data_paths)} data files")

        with get_database_connection() as conn:
            logger.info("Running ETL pipeline...")
            etl_result = benchmark.run_etl_pipeline(
                conn, batch_type='historical', validate_data=True
            )

            if etl_result['success']:
                logger.info(f"ETL completed in {etl_result['total_duration']:.2f}s")
                logger.info(f"Data quality score: {etl_result['validation_results'].get('data_quality_score', 'N/A')}")
            else:
                logger.error(f"ETL failed: {etl_result.get('error')}")
                return False

            logger.info("Running benchmark queries...")
            query_result = benchmark.run_benchmark(conn, iterations=3)

            successful_queries = sum(1 for q in query_result['queries'].values()
                                   if q.get('avg_time', 0) > 0)
            logger.info(f"Executed {successful_queries} queries successfully")

            return True

    except Exception as e:
        logger.error(f"Staging benchmark failed: {e}")
        return False

if __name__ == "__main__":
    success = run_staging_benchmark()
    exit(0 if success else 1)
```

### Production Environment

```python
import os
import sys
import logging
import signal
import json
from pathlib import Path
from datetime import datetime
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig

log_dir = Path('/var/log/tpcdi')
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - [%(process)d] - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'tpcdi_production.log'),
        logging.StreamHandler()
    ]
)

class ProductionTPCDI:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.benchmark = None
        self.shutdown_requested = False

        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        self.setup_benchmark()

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown_requested = True

    def setup_benchmark(self):
        data_dir = Path(os.getenv('TPCDI_DATA_DIR', '/data/tpcdi'))
        data_dir.mkdir(parents=True, exist_ok=True)

        config = TPCDIConfig(
            scale_factor=float(os.getenv('TPCDI_SCALE_FACTOR', '10.0')),
            output_dir=data_dir,
            max_workers=int(os.getenv('TPCDI_WORKERS', '8')),
            enable_parallel=True,
            chunk_size=20000,
            optimize_memory=True,
        )

        self.benchmark = TPCDIBenchmark(config=config)

        self.logger.info(f"Production TPC-DI benchmark initialized with scale factor {self.benchmark.scale_factor}")

    def get_database_connection(self):
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise ValueError("DATABASE_URL environment variable not set")

        if db_url.startswith('postgresql://'):
            import psycopg

            return psycopg.connect(db_url)
        elif db_url.startswith('duckdb://'):
            import duckdb
            db_path = db_url.replace('duckdb://', '')
            return duckdb.connect(db_path)

        else:
            raise ValueError(f"Unsupported database URL: {db_url}")

    def run_production_benchmark(self):
        self.logger.info("Starting production TPC-DI benchmark")

        results = {
            'start_time': datetime.now().isoformat(),
            'scale_factor': self.benchmark.scale_factor,
            'configuration': {
                'workers': self.benchmark.max_workers,
                'parallel_enabled': self.benchmark.enable_parallel,
            },
            'phases': {},
            'metrics': {},
            'success': False
        }

        try:
            if self.shutdown_requested:
                return results

            self.logger.info("Phase 1: Generating data")
            gen_start = datetime.now()
            data_paths = self.benchmark.generate_data()
            gen_time = (datetime.now() - gen_start).total_seconds()

            results['phases']['data_generation'] = {
                'duration': gen_time,
                'files_generated': len(data_paths)
            }
            self.logger.info(f"Data generation completed in {gen_time:.2f}s")

            if self.shutdown_requested:
                return results

            with self.get_database_connection() as conn:
                self.logger.info("Phase 2: Running ETL pipeline")
                etl_start = datetime.now()

                etl_result = self.benchmark.run_etl_pipeline(
                    conn, batch_type='historical', validate_data=True
                )

                etl_time = (datetime.now() - etl_start).total_seconds()
                results['phases']['etl'] = {
                    'duration': etl_time,
                    'success': etl_result['success'],
                    'data_quality_score': etl_result.get('validation_results', {}).get('data_quality_score', 0)
                }

                if not etl_result['success']:
                    self.logger.error(f"ETL pipeline failed: {etl_result.get('error')}")
                    return results

                self.logger.info(f"ETL pipeline completed in {etl_time:.2f}s")

                if self.shutdown_requested:
                    return results

                self.logger.info("Phase 3: Running query benchmark")
                query_start = datetime.now()

                query_result = self.benchmark.run_benchmark(
                    conn, iterations=int(os.getenv('TPCDI_ITERATIONS', '5'))
                )

                query_time = (datetime.now() - query_start).total_seconds()
                results['phases']['queries'] = {
                    'duration': query_time,
                    'queries_executed': len(query_result['queries']),
                    'successful_queries': sum(1 for q in query_result['queries'].values()
                                            if q.get('avg_time', 0) > 0)
                }

                self.logger.info(f"Query benchmark completed in {query_time:.2f}s")

                results['metrics'] = self.benchmark.get_enhanced_etl_status()
                results['success'] = True
                results['end_time'] = datetime.now().isoformat()

                total_time = gen_time + etl_time + query_time
                results['total_duration'] = total_time

                self.logger.info(f"Production benchmark completed successfully in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Production benchmark failed: {e}", exc_info=True)
            results['error'] = str(e)
            results['end_time'] = datetime.now().isoformat()

        results_file = Path('/var/log/tpcdi') / f"benchmark_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)

        self.logger.info(f"Results saved to {results_file}")
        return results

def main():
    production_benchmark = ProductionTPCDI()
    results = production_benchmark.run_production_benchmark()

    if results['success']:
        print("Production benchmark completed successfully")
        sys.exit(0)
    else:
        print("Production benchmark failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

## Performance Tuning

### Memory Optimization

```python
import os
import psutil

def get_appropriate_memory_config():
    memory = psutil.virtual_memory()
    available_gb = memory.available / (1024**3)

    usable_gb = available_gb * 0.75
    usable_mb = int(usable_gb * 1024)

    if usable_mb >= 16384:
        batch_size = 50000
        memory_limit = min(usable_mb, 32768)
    elif usable_mb >= 8192:
        batch_size = 25000
        memory_limit = usable_mb
    elif usable_mb >= 4096:
        batch_size = 10000
        memory_limit = usable_mb
    else:
        batch_size = 5000
        memory_limit = max(usable_mb, 2048)

    return {
        'memory_limit_mb': memory_limit,
        'batch_size': batch_size,
        'recommended_scale_factor': min(10.0, usable_gb / 2)
    }

memory_config = get_appropriate_memory_config()
print(f"Recommended configuration: {memory_config}")
```

The function reserves 25% of available memory for the system and sizes the rest in tiers:

- 16 GB or more usable: batch size 50,000, memory limit capped at 32 GB.
- 8 to 16 GB: batch size 25,000.
- 4 to 8 GB: batch size 10,000.
- Under 4 GB: batch size 5,000, memory limit of at least 2 GB.

### CPU Optimization

```python
import multiprocessing
import os

from benchbox.core.tpcdi.config import TPCDIConfig

def get_appropriate_cpu_config():
    cpu_count = multiprocessing.cpu_count()

    if cpu_count >= 16:
        workers = min(12, cpu_count - 4)
    elif cpu_count >= 8:
        workers = min(6, cpu_count - 2)
    elif cpu_count >= 4:
        workers = min(3, cpu_count - 1)
    else:
        workers = 1

    return TPCDIConfig(
        enable_parallel=workers > 1,
        max_workers=workers,
    )

cpu_config = get_appropriate_cpu_config()
print(f"Recommended parallel config: {cpu_config}")
```

The function picks the worker count from the core count:

- 16 or more cores (high-end servers): `min(12, cpu_count - 4)`, which leaves 4 cores for the system.
- 8 to 15 cores (mid-range): `min(6, cpu_count - 2)`.
- 4 to 7 cores (standard): `min(3, cpu_count - 1)`.
- Fewer than 4 cores: 1 worker.

### Storage Optimization

```python
import shutil
import os
from pathlib import Path

def optimize_storage_layout(base_dir: Path):

    directories = {
        'source': base_dir / 'source',
        'staging': base_dir / 'staging',
        'warehouse': base_dir / 'warehouse',
        'temp': base_dir / 'temp',
        'logs': base_dir / 'logs',
        'metrics': base_dir / 'metrics'
    }

    for name, path in directories.items():
        path.mkdir(parents=True, exist_ok=True)

        os.chmod(path, 0o755)

        disk_usage = shutil.disk_usage(path)
        free_gb = disk_usage.free / (1024**3)

        print(f"{name}: {path} ({free_gb:.1f}GB free)")

        if free_gb < 10:
            print(f"WARNING: Low disk space for {name}")

    return directories

storage_settings = {
    'csv_buffer_size': 8192,
    'xml_buffer_size': 16384,
    'compression': 'gzip',
    'temp_cleanup': True,
    'batch_write_size': 10000
}
```

`optimize_storage_layout` creates each directory with mode 755, reports its free space and warns when less than 10 GB is free. The storage settings mean:

- `csv_buffer_size`: 8 KB buffer for CSV operations.
- `xml_buffer_size`: 16 KB buffer for XML operations.
- `compression`: gzip-compress intermediate files.
- `temp_cleanup`: remove temporary files when done.
- `batch_write_size`: write in batches of 10,000 for better I/O performance.

### Database Optimization

```sql

SET shared_buffers = '4GB';
SET effective_cache_size = '12GB';
SET maintenance_work_mem = '1GB';
SET work_mem = '256MB';
SET random_page_cost = 1.1;
SET effective_io_concurrency = 200;

SET max_parallel_workers_per_gather = 4;
SET max_parallel_workers = 8;
SET parallel_tuple_cost = 0.1;
SET parallel_setup_cost = 1000.0;

SET wal_buffers = '16MB';
SET checkpoint_segments = 32;
SET checkpoint_completion_target = 0.9;

SET constraint_exclusion = partition;
SET default_statistics_target = 100;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dimcustomer_sk ON DimCustomer(SK_CustomerID);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dimcustomer_id ON DimCustomer(CustomerID);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_facttrade_customer ON FactTrade(SK_CustomerID);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_facttrade_security ON FactTrade(SK_SecurityID);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_facttrade_date ON FactTrade(SK_CreateDateID);

CREATE TABLE FactTrade_2023 PARTITION OF FactTrade
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');

ANALYZE DimCustomer;
ANALYZE DimAccount;
ANALYZE DimSecurity;
ANALYZE DimCompany;
ANALYZE FactTrade;
ANALYZE DimDate;
ANALYZE DimTime;
```

The script is `postgresql_optimization.sql`, PostgreSQL-specific tuning for TPC-DI. In order, it sets:

- Connection and memory settings.
- Parallel processing settings.
- WAL and checkpoint settings.
- Optimizer settings.
- Indexes on the TPC-DI tables.
- Partitioning for large fact tables.
- A final `ANALYZE` of the tables so the planner has current statistics.

## Monitoring and Maintenance

### System Monitoring

```python
import psutil
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

class TPCDIMonitor:
    def __init__(self, log_dir: Path = Path('/var/log/tpcdi')):
        self.log_dir = log_dir
        self.log_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger('tpcdi_monitor')
        handler = logging.FileHandler(log_dir / 'monitor.log')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def collect_system_metrics(self) -> Dict[str, Any]:
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        load_avg = psutil.getloadavg()

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        disk_usage = psutil.disk_usage('/')
        disk_io = psutil.disk_io_counters()

        network_io = psutil.net_io_counters()

        return {
            'timestamp': datetime.now().isoformat(),
            'cpu': {
                'percent': cpu_percent,
                'count': cpu_count,
                'load_avg': list(load_avg)
            },
            'memory': {
                'total_gb': memory.total / (1024**3),
                'available_gb': memory.available / (1024**3),
                'percent': memory.percent,
                'swap_percent': swap.percent
            },
            'disk': {
                'total_gb': disk_usage.total / (1024**3),
                'free_gb': disk_usage.free / (1024**3),
                'percent': (disk_usage.used / disk_usage.total) * 100,
                'read_mb': disk_io.read_bytes / (1024**2) if disk_io else 0,
                'write_mb': disk_io.write_bytes / (1024**2) if disk_io else 0
            },
            'network': {
                'bytes_sent_mb': network_io.bytes_sent / (1024**2),
                'bytes_recv_mb': network_io.bytes_recv / (1024**2)
            }
        }

    def monitor_benchmark(self, benchmark, duration_minutes: int = 60):
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)

        metrics_log = []

        while time.time() < end_time:
            try:
                system_metrics = self.collect_system_metrics()

                benchmark_metrics = benchmark.get_enhanced_etl_status()
                etl_metrics = benchmark.get_etl_status()

                combined_metrics = {
                    'system': system_metrics,
                    'benchmark': benchmark_metrics,
                    'etl': etl_metrics
                }

                metrics_log.append(combined_metrics)

                self.check_alerts(system_metrics)

                time.sleep(10)

            except Exception as e:
                self.logger.error(f"Error collecting metrics: {e}")

        metrics_file = self.log_dir / f"metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(metrics_file, 'w') as f:
            json.dump(metrics_log, f, indent=2)

        self.logger.info(f"Monitoring completed. Metrics saved to {metrics_file}")

    def check_alerts(self, metrics: Dict[str, Any]):
        if metrics['cpu']['percent'] > 90:
            self.logger.warning(f"High CPU usage: {metrics['cpu']['percent']:.1f}%")

        if metrics['memory']['percent'] > 85:
            self.logger.warning(f"High memory usage: {metrics['memory']['percent']:.1f}%")

        if metrics['disk']['percent'] > 80:
            self.logger.warning(f"High disk usage: {metrics['disk']['percent']:.1f}%")

        cpu_count = metrics['cpu']['count']
        load_5min = metrics['cpu']['load_avg'][1]
        if load_5min > cpu_count * 2:
            self.logger.warning(f"High load average: {load_5min:.2f} (cores: {cpu_count})")

if __name__ == "__main__":
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    benchmark = TPCDIBenchmark(scale_factor=1.0)
    monitor = TPCDIMonitor()

    monitor.monitor_benchmark(benchmark, duration_minutes=30)
```

### Log Management

```bash

LOG_DIR="/var/log/tpcdi"
RETENTION_DAYS=30
MAX_LOG_SIZE="100M"

cat > /etc/logrotate.d/tpcdi << EOF
$LOG_DIR/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 tpcdi tpcdi
    size $MAX_LOG_SIZE
    postrotate
        # Send SIGHUP to applications to reopen log files
        pkill -HUP -f "python.*tpcdi" || true
    endscript
}
EOF

find $LOG_DIR -name "metrics_*.json" -mtime +$RETENTION_DAYS -delete
find $LOG_DIR -name "benchmark_results_*.json" -mtime +$RETENTION_DAYS -delete

find $LOG_DIR -name "*.log.*" -mtime +1 ! -name "*.gz" -exec gzip {} \;

echo "Log management tasks completed"
```

### Health Checks

```python
import time
import tempfile
from pathlib import Path
from benchbox import TPCDI

def health_check() -> bool:
    try:
        print("✅ Import test passed")

        with tempfile.TemporaryDirectory() as temp_dir:
            benchmark = TPCDI(scale_factor=0.001, output_dir=temp_dir)
            print("✅ Instantiation test passed")

            schema = benchmark.get_schema()
            assert len(schema) >= 5, "Schema should have at least 5 tables"
            print("✅ Schema validation passed")

            queries = benchmark.get_queries()
            assert len(queries) >= 3, "Should have at least 3 queries"
            print("✅ Query validation passed")

            start_time = time.time()
            data_paths = benchmark.generate_data()
            generation_time = time.time() - start_time

            assert len(data_paths) > 0, "Data generation should produce files"
            assert generation_time < 30, "Data generation should complete quickly"
            print(f"✅ Data generation test passed ({generation_time:.2f}s)")

            import sqlite3
            with sqlite3.connect(':memory:') as conn:
                benchmark.load_data_to_database(conn)

                first_query_id = list(queries.keys())[0]
                result = benchmark.execute_query(first_query_id, conn)
                assert result is not None, "Query execution should return result"
                print("✅ Database operations test passed")

            print("\n All health checks passed!")
            return True

    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False

if __name__ == "__main__":
    success = health_check()
    exit(0 if success else 1)
```

## Troubleshooting

### Common Issues

#### 1. Memory Issues

**Problem**: Out of memory errors during large scale factor runs
```
MemoryError: Unable to allocate array
```

**Solutions**:
```python
benchmark = TPCDIBenchmark(scale_factor=0.1)

config = TPCDIConfig(
    scale_factor=0.1,
    optimize_memory=True,
    max_workers=2,
    enable_parallel=True,
    chunk_size=5000,
)
benchmark = TPCDIBenchmark(config=config)
```

Use a scale factor of 0.1 instead of 1.0. Reduce workers and set `chunk_size=5000` so data is processed in smaller batches.

#### 2. Performance Issues

**Problem**: Slow execution times
```
ETL pipeline taking hours to complete
```

**Solutions**:
```python
import multiprocessing

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig

config = TPCDIConfig(
    enable_parallel=True,
    max_workers=multiprocessing.cpu_count(),
)
benchmark = TPCDIBenchmark(config=config)
```

#### 3. File Permission Issues

**Problem**: Permission denied errors
```
PermissionError: [Errno 13] Permission denied: '/data/tpcdi'
```

**Solutions**:
```bash
sudo mkdir -p /data/tpcdi
sudo chown -R $USER:$USER /data/tpcdi
sudo chmod -R 755 /data/tpcdi

export TPCDI_DATA_DIR=$HOME/tpcdi_data
```

Fix the directory permissions as shown, or point `TPCDI_DATA_DIR` at a user-writable directory instead.

#### 4. Database Connection Issues

**Problem**: Database connection failures
```
psycopg.OperationalError: could not connect to server
```

**Solutions**:
```python
import os
import psycopg

os.environ['DATABASE_URL'] = 'postgresql://user:pass@localhost:5432/tpcdi'
try:
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        print("Connection successful")
except psycopg.OperationalError as e:
    print(f"Connection failed: {e}")
```

#### 5. Query Execution Errors

**Problem**: SQL syntax errors
```
ProgrammingError: syntax error at or near "LIMIT"
```

**Solutions**:
```python
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

benchmark = TPCDIBenchmark(scale_factor=0.01)
query_sql = benchmark.get_query("V1")
translated = benchmark.translate_query_text(query_sql, "postgres")

import logging
logging.basicConfig(level=logging.DEBUG)
```

Translate the query to the correct SQL dialect, then enable debug logging to check the database-specific syntax.

### Debug Mode

The debug run uses a small scale factor of 0.01 with Python logging at DEBUG level:

```python
import logging
import sqlite3
from pathlib import Path
from benchbox import TPCDI

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

benchmark = TPCDI(
    scale_factor=0.01,
    output_dir=Path('/tmp/tpcdi_debug')
)

try:
    with sqlite3.connect(':memory:') as conn:
        result = benchmark.run_etl_pipeline(conn, validate_data=True)
    print(f"Debug run completed: {result['success']}")
except Exception:
    logging.exception("Debug run failed")
    raise
```

### Diagnostic Tools

```python
import sys
import subprocess
import importlib
from pathlib import Path

def run_diagnostics():
    print("TPC-DI Diagnostic Report")
    print("=" * 50)

    print(f"Python version: {sys.version}")
    print(f"Python executable: {sys.executable}")

    packages = ['benchbox', 'numpy', 'benchbox._vendor.sqlglot', 'pytest']
    for package in packages:
        try:
            module = importlib.import_module(package)
            version = getattr(module, '__version__', 'unknown')
            print(f"✅ {package}: {version}")
        except ImportError:
            print(f"❌ {package}: not installed")

    import psutil
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    print(f"\nSystem Resources:")
    print(f"CPU cores: {psutil.cpu_count()}")
    print(f"Memory: {memory.total / (1024**3):.1f}GB total, {memory.available / (1024**3):.1f}GB available")
    print(f"Disk: {disk.total / (1024**3):.1f}GB total, {disk.free / (1024**3):.1f}GB free")

    print(f"\nDatabase Connectivity:")
    try:
        import sqlite3
        sqlite3.connect(':memory:').close()
        print("✅ SQLite: available")
    except Exception as e:
        print(f"❌ SQLite: {e}")

    try:
        import duckdb
        duckdb.connect(':memory:').close()
        print("✅ DuckDB: available")
    except Exception as e:
        print(f"❌ DuckDB: {e}")

    test_dirs = ['/tmp', '/var/log', '/data']
    print(f"\nDirectory Permissions:")
    for test_dir in test_dirs:
        path = Path(test_dir)
        if path.exists():
            if os.access(path, os.W_OK):
                print(f"✅ {test_dir}: writable")
            else:
                print(f"⚠️ {test_dir}: not writable")
        else:
            print(f"- {test_dir}: does not exist")

if __name__ == "__main__":
    run_diagnostics()
```

## Security Considerations

### Access Control

```python
import os
import stat
from pathlib import Path

def secure_directory_setup(base_dir: Path):

    directories = {
        'data': base_dir / 'data',
        'logs': base_dir / 'logs',
        'temp': base_dir / 'temp',
        'config': base_dir / 'config'
    }

    for name, path in directories.items():
        path.mkdir(parents=True, exist_ok=True)

        if name == 'config':
            os.chmod(path, stat.S_IRWXU)
        elif name == 'logs':
            os.chmod(path, stat.S_IRWXU | stat.S_IRGRP)
        else:
            os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)

def validate_database_connection(connection_string: str) -> bool:

    if connection_string.startswith('postgresql://'):
        if 'sslmode=require' not in connection_string:
            print("WARNING: PostgreSQL connection should use SSL in production")
            return False

    if '@' in connection_string:
        parts = connection_string.split('@')
        if len(parts) > 1:
            print("WARNING: Credentials in connection string. Use environment variables.")
            return False

    return True

def validate_environment():

    required_vars = ['DATABASE_URL', 'TPCDI_DATA_DIR']
    optional_vars = ['TPCDI_LOG_LEVEL', 'TPCDI_WORKERS']

    for var in required_vars:
        if not os.getenv(var):
            print(f"ERROR: Required environment variable {var} not set")
            return False

    db_url = os.getenv('DATABASE_URL')
    if not validate_database_connection(db_url):
        return False

    return True
```

The directory modes are:

- `config`: owner read/write/execute only (700).
- `logs`: owner full access, group read (740).
- Data and temp directories: owner full access, group read and execute (750).

`validate_database_connection` warns and returns `False` when a PostgreSQL URL lacks `sslmode=require` (SSL is needed in production) or when the connection string embeds credentials (use environment variables instead). `secure_file_write` sets written files to owner read/write only (600).

### Data Protection

```python
import hashlib
import json
from pathlib import Path
from typing import Dict, Any

class DataProtection:
    def __init__(self, encryption_key: str = None):
        self.encryption_key = encryption_key

    def hash_sensitive_data(self, data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def sanitize_logs(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = log_data.copy()

        sensitive_fields = [
            'password', 'secret', 'key', 'token', 'credential',
            'DATABASE_URL', 'connection_string'
        ]

        def sanitize_dict(d):
            if isinstance(d, dict):
                for key, value in d.items():
                    if any(field in key.lower() for field in sensitive_fields):
                        d[key] = self.hash_sensitive_data(str(value))
                    elif isinstance(value, (dict, list)):
                        sanitize_dict(value)
            elif isinstance(d, list):
                for item in d:
                    sanitize_dict(item)

        sanitize_dict(sanitized)
        return sanitized

    def secure_file_write(self, file_path: Path, data: Any):

        with open(file_path, 'w') as f:
            if isinstance(data, dict):
                json.dump(data, f, indent=2)
            else:
                f.write(str(data))

        os.chmod(file_path, 0o600)
```

## Best Practices

### Development Best Practices

1. **Version Control**: Always use version control for configuration files
2. **Testing**: Implement systematic testing at all levels
3. **Documentation**: Maintain up-to-date documentation
4. **Code Review**: Require code reviews for production changes
5. **Continuous Integration**: Use CI/CD pipelines for deployment

### Deployment Best Practices

1. **Environment Separation**: Use separate environments for dev/test/prod
2. **Configuration Management**: Use configuration management tools
3. **Monitoring**: Implement systematic monitoring and alerting
4. **Backup Strategy**: Regular backups of data and configurations
5. **Disaster Recovery**: Have disaster recovery procedures

### Performance Best Practices

1. **Resource Planning**: Plan resources based on scale factor requirements
2. **Parallel Processing**: Use parallel processing for large scale factors
3. **Database Optimization**: Optimize database settings for workload
4. **Storage Optimization**: Use fast storage (SSD) for data directories
5. **Network Optimization**: Ensure high-speed network for database connections

### Security Best Practices

1. **Principle of Least Privilege**: Grant minimum required permissions
2. **Environment Variables**: Use environment variables for sensitive data
3. **Secure Communications**: Use encrypted connections (SSL/TLS)
4. **Regular Updates**: Keep dependencies and systems updated
5. **Audit Logging**: Implement systematic audit logging

### Maintenance Best Practices

1. **Regular Health Checks**: Implement automated health checks
2. **Log Rotation**: Configure log rotation to prevent disk space issues
3. **Performance Monitoring**: Monitor performance trends over time
4. **Capacity Planning**: Plan for future capacity needs
5. **Documentation Updates**: Keep documentation current with changes

---

This deployment guide provides systematic information for deploying TPC-DI benchmarks in various environments. For additional support, refer to the project documentation or contact the development team.
