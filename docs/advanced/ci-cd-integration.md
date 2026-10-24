# CI/CD Integration Guide

```{tags} advanced, guide, testing
```

Complete guide for integrating BenchBox into CI/CD pipelines for automated performance testing and regression detection.

## Overview

BenchBox can be integrated into CI/CD pipelines to:

- **Automate Performance Testing**: Run benchmarks on every commit or pull request
- **Detect Regressions**: Automatically identify performance degradations
- **Track Performance Trends**: Build historical performance baselines
- **Validate Optimizations**: Verify that code changes improve performance
- **Generate Reports**: Create automated performance reports

## Table of Contents

- [GitHub Actions](#github-actions)
- [GitLab CI](#gitlab-ci)
- [Jenkins](#jenkins)
- [CircleCI](#circleci)
- [Azure Pipelines](#azure-pipelines)
- [Best Practices](#best-practices)
- [Performance Baselines](#performance-baselines)
- [Regression Detection](#regression-detection)
- [Reporting](#reporting)

## GitHub Actions

### Basic Workflow

Create `.github/workflows/benchmarks.yml`:

```yaml
name: Benchmark Tests

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  schedule:
    - cron: '0 2 * * *'

jobs:
  benchmark:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install UV
        uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78

      - name: Install BenchBox
        run: uv pip install benchbox[all]

      - name: Run TPC-H Benchmark
        run: |
          benchbox run \
            --platform duckdb \
            --benchmark tpch \
            --scale 0.01 \
            --output results/

      - name: Upload Results
        uses: actions/upload-artifact@v4
        with:
          name: benchmark-results
          path: results/*.json
```

The schedule trigger runs the workflow daily at 2 AM UTC.

### Regression Detection Workflow

Create `.github/workflows/benchmark-regression.yml`:

```yaml
name: Performance Regression Check

on:
  pull_request:
    branches: [main]

jobs:
  benchmark-comparison:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install UV
        uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78

      - name: Install BenchBox
        run: uv pip install benchbox[all]

      - name: Download Baseline
        uses: actions/download-artifact@v4
        with:
          name: baseline-results
          path: baseline/
        continue-on-error: true

      - name: Run Current Benchmark
        run: |
          benchbox run \
            --platform duckdb \
            --benchmark tpch \
            --scale 0.01 \
            --output current/

      - name: Compare Results
        id: compare
        run: |
          python scripts/compare_benchmarks.py \
            --baseline baseline/tpch_sf001_duckdb.json \
            --current current/tpch_sf001_duckdb.json \
            --threshold 10

      - name: Comment PR
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('comparison_report.md', 'utf8');

            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: report
            });

      - name: Fail on Regression
        if: steps.compare.outputs.regression == 'true'
        run: |
          echo "Performance regression detected!"
          exit 1
```

### Benchmark Comparison Script

Create `scripts/compare_benchmarks.py`:

```python

import argparse
import json
import sys
from pathlib import Path


def compare_benchmarks(baseline_path: Path, current_path: Path, threshold: float = 10.0):
    with open(baseline_path) as f:
        baseline = json.load(f)

    with open(current_path) as f:
        current = json.load(f)

    baseline_timing = baseline.get("results", {}).get("timing", {})
    current_timing = current.get("results", {}).get("timing", {})

    baseline_avg = baseline_timing.get("avg_ms", 0) / 1000
    current_avg = current_timing.get("avg_ms", 0) / 1000

    if baseline_avg == 0:
        print("⚠️  No baseline data available")
        return False

    change_pct = ((current_avg - baseline_avg) / baseline_avg) * 100

    report = f"""## Benchmark Comparison Report

### Overall Performance

- **Baseline**: {baseline_avg:.3f}s average query time
- **Current**: {current_avg:.3f}s average query time
- **Change**: {change_pct:+.2f}%

"""

    is_regression = change_pct > threshold

    if is_regression:
        report += f"### ❌ Regression Detected\n\n"
        report += f"Performance degraded by {change_pct:.2f}% (threshold: {threshold}%)\n"
    elif change_pct < -threshold:
        report += f"### ✅ Performance Improvement\n\n"
        report += f"Performance improved by {abs(change_pct):.2f}%\n"
    else:
        report += f"### ✓ No Significant Change\n\n"
        report += f"Performance change within acceptable range\n"

    Path("comparison_report.md").write_text(report)

    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"regression={'true' if is_regression else 'false'}\n")

    print(report)

    return is_regression


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=10.0)

    args = parser.parse_args()

    is_regression = compare_benchmarks(args.baseline, args.current, args.threshold)
    sys.exit(1 if is_regression else 0)
```

## GitLab CI

Create `.gitlab-ci.yml`:

```yaml
stages:
  - benchmark
  - compare
  - report

variables:
  BENCHMARK_SCALE: "0.01"
  REGRESSION_THRESHOLD: "10"

benchmark:
  stage: benchmark
  image: python:3.11
  before_script:
    - curl -LsSf https://astral.sh/uv/0.13.5/install.sh | sh
    - export PATH="$HOME/.local/bin:$PATH"
    - uv pip install benchbox[all]
  script:
    - |
      benchbox run \
        --platform duckdb \
        --benchmark tpch \
        --scale ${BENCHMARK_SCALE} \
        --output results/
  artifacts:
    paths:
      - results/*.json
    expire_in: 30 days

compare:
  stage: compare
  image: python:3.11
  dependencies:
    - benchmark
  script:
    - |
      if [ -f "baseline/tpch_sf001_duckdb.json" ]; then
        python scripts/compare_benchmarks.py \
          --baseline baseline/tpch_sf001_duckdb.json \
          --current results/*.json \
          --threshold ${REGRESSION_THRESHOLD}
      else
        echo "No baseline found, skipping comparison"
      fi
  allow_failure: false
  artifacts:
    paths:
      - comparison_report.md
    when: always

report:
  stage: report
  image: python:3.11
  dependencies:
    - compare
  script:
    - |
      if [ -f "comparison_report.md" ]; then
        cat comparison_report.md
      fi
  when: always
```

## Jenkins

Create `Jenkinsfile`. Benchmark parameter values are passed as individual arguments through environment variables; shell expressions in those values are treated as data:

```groovy
pipeline {
    agent any

    parameters {
        string(name: 'BENCHMARK', defaultValue: 'tpch', description: 'Benchmark to run')
        string(name: 'SCALE_FACTOR', defaultValue: '0.01', description: 'Scale factor')
        string(name: 'PLATFORM', defaultValue: 'duckdb', description: 'Database platform')
    }

    environment {
        UV_HOME = "${WORKSPACE}/.uv"
    }

    stages {
        stage('Setup') {
            steps {
                sh '''
                    curl -LsSf https://astral.sh/uv/0.13.5/install.sh | sh
                    export PATH="$HOME/.local/bin:$PATH"
                    uv pip install benchbox[all]
                '''
            }
        }

        stage('Run Benchmark') {
            steps {
                withEnv([
                    "BENCHBOX_PLATFORM=${params.PLATFORM}",
                    "BENCHBOX_BENCHMARK=${params.BENCHMARK}",
                    "BENCHBOX_SCALE_FACTOR=${params.SCALE_FACTOR}"
                ]) {
                    sh '''
                        benchbox run --platform "$BENCHBOX_PLATFORM" --benchmark "$BENCHBOX_BENCHMARK" --scale "$BENCHBOX_SCALE_FACTOR" --output results/
                        benchbox export --last --format html --output-dir results/
                    '''
                }
            }
        }

        stage('Compare with Baseline') {
            when {
                expression {
                    fileExists('baseline/tpch_sf001_duckdb.json')
                }
            }
            steps {
                sh '''
                    python scripts/compare_benchmarks.py --baseline baseline/*.json --current results/*.json --threshold 10
                '''
            }
        }

        stage('Archive Results') {
            steps {
                archiveArtifacts artifacts: 'results/*.json', fingerprint: true
                archiveArtifacts artifacts: 'results/*.html', fingerprint: true, allowEmptyArchive: true
            }
        }
    }

    post {
        always {
            cleanWs()
        }
        failure {
            emailext(
                subject: "Benchmark Failed: ${env.JOB_NAME} - ${env.BUILD_NUMBER}",
                body: "Benchmark test failed. Check ${env.BUILD_URL} for details.",
                to: '${DEFAULT_RECIPIENTS}'
            )
        }
    }
}
```

## CircleCI

Create `.circleci/config.yml`:

```yaml
version: 2.1

executors:
  python-executor:
    docker:
      - image: cimg/python:3.11
    working_directory: ~/project

jobs:
  run-benchmark:
    executor: python-executor
    steps:
      - checkout

      - run:
          name: Install UV
          command: curl -LsSf https://astral.sh/uv/0.13.5/install.sh | sh

      - run:
          name: Install BenchBox
          command: |
            export PATH="$HOME/.local/bin:$PATH"
            uv pip install benchbox[all]

      - run:
          name: Run TPC-H Benchmark
          command: |
            benchbox run \
              --platform duckdb \
              --benchmark tpch \
              --scale 0.01 \
              --output results/

      - store_artifacts:
          path: results/
          destination: benchmark-results

      - persist_to_workspace:
          root: .
          paths:
            - results/*.json

  compare-results:
    executor: python-executor
    steps:
      - checkout

      - attach_workspace:
          at: .

      - run:
          name: Compare with Baseline
          command: |
            if [ -f "baseline/tpch_sf001_duckdb.json" ]; then
              python scripts/compare_benchmarks.py \
                --baseline baseline/tpch_sf001_duckdb.json \
                --current results/*.json \
                --threshold 10
            fi

      - store_artifacts:
          path: comparison_report.md

workflows:
  version: 2
  benchmark-workflow:
    jobs:
      - run-benchmark
      - compare-results:
          requires:
            - run-benchmark
```

## Azure Pipelines

Create `azure-pipelines.yml`:

```yaml
trigger:
  branches:
    include:
      - main
  paths:
    include:
      - src/*

pr:
  branches:
    include:
      - main

pool:
  vmImage: 'ubuntu-latest'

variables:
  BENCHMARK: 'tpch'
  SCALE_FACTOR: '0.01'
  PLATFORM: 'duckdb'

stages:
  - stage: Benchmark
    jobs:
      - job: RunBenchmark
        steps:
          - task: UsePythonVersion@0
            inputs:
              versionSpec: '3.11'

          - script: |
              curl -LsSf https://astral.sh/uv/0.13.5/install.sh | sh
              export PATH="$HOME/.local/bin:$PATH"
              uv pip install benchbox[all]
            displayName: 'Install Dependencies'

          - script: |
              benchbox run \
                --platform $(PLATFORM) \
                --benchmark $(BENCHMARK) \
                --scale $(SCALE_FACTOR) \
                --output $(Build.ArtifactStagingDirectory)/results/
            displayName: 'Run Benchmark'

          - task: PublishBuildArtifacts@1
            inputs:
              PathtoPublish: '$(Build.ArtifactStagingDirectory)/results'
              ArtifactName: 'benchmark-results'

  - stage: Compare
    dependsOn: Benchmark
    condition: succeeded()
    jobs:
      - job: CompareResults
        steps:
          - task: DownloadBuildArtifacts@1
            inputs:
              buildType: 'current'
              downloadType: 'single'
              artifactName: 'benchmark-results'

          - script: |
              python scripts/compare_benchmarks.py \
                --baseline baseline/tpch_sf001_duckdb.json \
                --current $(Build.ArtifactStagingDirectory)/benchmark-results/*.json \
                --threshold 10
            displayName: 'Compare with Baseline'
            condition: and(succeeded(), exists('baseline/tpch_sf001_duckdb.json'))
```

## Best Practices

### 1. Small Scale Factors

Use small scale factors in CI/CD to keep execution times reasonable. These
are command-option fragments:

```text
# Fast feedback in CI
--scale 0.01  # ~10 MB data, ~30 seconds

# Nightly builds can use larger scales
--scale 0.1   # ~100 MB data, ~5 minutes
```

### 2. Baseline Management

Maintain stable baselines:

```bash
git add baseline/tpch_sf001_duckdb.json
git commit -m "Update performance baseline"

aws s3 cp results/*.json s3://benchmarks/baselines/$(git rev-parse HEAD)/
```

The first two commands store the baseline in version control. The ``aws s3 cp`` command is an alternative that stores baselines in artifact storage.

### 3. Conditional Execution

Run benchmarks only when relevant:

```yaml
on:
  push:
    paths:
      - 'src/**'
      - 'benchmarks/**'
```

This GitHub Actions trigger runs the workflow only when files under the listed paths change.

### 4. Parallel Execution

Run multiple benchmarks in parallel:

```yaml
strategy:
  matrix:
    benchmark: [tpch, ssb, clickbench]
    scale: [0.01, 0.1]
```

### 5. Caching

Cache dependencies and data:

```yaml
- name: Cache BenchBox data
  uses: actions/cache@v4
  with:
    path: ~/.benchbox/data
    key: benchbox-data-${{ hashFiles('**/benchmark-config.yml') }}
```

## Performance Baselines

### Creating Baselines

Create initial baseline:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.results.exporter import ResultExporter
from pathlib import Path

benchmark = TPCH(scale_factor=0.01)
adapter = DuckDBAdapter()
results = adapter.run_benchmark(benchmark)

exporter = ResultExporter(output_dir="baseline")
baseline_files = exporter.export_result(results, formats=["json"])

print(f"Baseline created: {baseline_files['json']}")
```

The script runs a benchmark and exports its result as the baseline.

### Updating Baselines

Update baseline after verified improvements:

```bash
python scripts/compare_benchmarks.py \
  --baseline baseline/tpch_sf001_duckdb.json \
  --current results/tpch_sf001_duckdb.json

cp results/tpch_sf001_duckdb.json baseline/tpch_sf001_duckdb.json
git add baseline/tpch_sf001_duckdb.json
git commit -m "Update baseline with verified improvements"
```

Run the comparison first. Copy the new result over the baseline only if the improvement is verified.

## Regression Detection

### Advanced Comparison Script

Create `scripts/regression_detector.py`:

```python

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List


class RegressionDetector:
    def __init__(self, threshold: float = 10.0):
        self.threshold = threshold

    def load_results(self, path: Path) -> dict:
        with open(path) as f:
            return json.load(f)

    def extract_query_timings(self, results: dict) -> Dict[str, float]:
        query_timings = {}

        queries = results.get("results", {}).get("queries", {}).get("details", [])
        for query in queries:
            query_id = query.get("id") or query.get("query_id")
            exec_time_ms = query.get("execution_time_ms", 0)
            query_timings[query_id] = exec_time_ms / 1000

        return query_timings

    def compare_queries(
        self,
        baseline_timings: Dict[str, float],
        current_timings: Dict[str, float]
    ) -> List[dict]:
        comparisons = []

        for query_id in baseline_timings.keys():
            if query_id not in current_timings:
                continue

            baseline_time = baseline_timings[query_id]
            current_time = current_timings[query_id]

            if baseline_time == 0:
                continue

            change_pct = ((current_time - baseline_time) / baseline_time) * 100

            comparisons.append({
                "query_id": query_id,
                "baseline_time": baseline_time,
                "current_time": current_time,
                "change_pct": change_pct,
                "is_regression": change_pct > self.threshold,
                "is_improvement": change_pct < -self.threshold
            })

        return comparisons

    def generate_report(
        self,
        baseline: dict,
        current: dict,
        comparisons: List[dict]
    ) -> str:
        baseline_timing = baseline.get("results", {}).get("timing", {})
        current_timing = current.get("results", {}).get("timing", {})

        baseline_avg = baseline_timing.get("avg_ms", 0) / 1000
        current_avg = current_timing.get("avg_ms", 0) / 1000
        overall_change = ((current_avg - baseline_avg) / baseline_avg) * 100 if baseline_avg else 0

        regressions = [c for c in comparisons if c["is_regression"]]
        improvements = [c for c in comparisons if c["is_improvement"]]
        unchanged = [c for c in comparisons if not c["is_regression"] and not c["is_improvement"]]

        report = f"""# Benchmark Regression Report

## Overall Performance

| Metric | Baseline | Current | Change |
|--------|----------|---------|--------|
| Average Query Time | {baseline_avg:.3f}s | {current_avg:.3f}s | {overall_change:+.2f}% |

"""

        if overall_change > self.threshold:
            report += f"### ❌ Regression Detected\n\n"
            report += f"Overall performance degraded by {overall_change:.2f}%\n\n"
        elif overall_change < -self.threshold:
            report += f"### ✅ Performance Improvement\n\n"
            report += f"Overall performance improved by {abs(overall_change):.2f}%\n\n"
        else:
            report += f"### ✓ Performance Stable\n\n"
            report += f"No significant overall change ({overall_change:+.2f}%)\n\n"

        report += f"""## Query-Level Analysis

- **Total Queries**: {len(comparisons)}
- **Regressions**: {len(regressions)}
- **Improvements**: {len(improvements)}
- **Unchanged**: {len(unchanged)}

"""

        if regressions:
            report += "### Regressed Queries\n\n"
            report += "| Query | Baseline | Current | Change |\n"
            report += "|-------|----------|---------|--------|\n"
            for r in sorted(regressions, key=lambda x: x["change_pct"], reverse=True)[:10]:
                report += f"| {r['query_id']} | {r['baseline_time']:.3f}s | {r['current_time']:.3f}s | {r['change_pct']:+.2f}% |\n"
            report += "\n"

        if improvements:
            report += "### Improved Queries\n\n"
            report += "| Query | Baseline | Current | Change |\n"
            report += "|-------|----------|---------|--------|\n"
            for i in sorted(improvements, key=lambda x: x["change_pct"])[:10]:
                report += f"| {i['query_id']} | {i['baseline_time']:.3f}s | {i['current_time']:.3f}s | {i['change_pct']:+.2f}% |\n"
            report += "\n"

        return report

    def detect_regressions(
        self,
        baseline_path: Path,
        current_path: Path
    ) -> bool:
        baseline = self.load_results(baseline_path)
        current = self.load_results(current_path)

        baseline_timings = self.extract_query_timings(baseline)
        current_timings = self.extract_query_timings(current)

        comparisons = self.compare_queries(baseline_timings, current_timings)
        report = self.generate_report(baseline, current, comparisons)

        Path("regression_report.md").write_text(report)
        print(report)

        has_regressions = any(c["is_regression"] for c in comparisons)

        return has_regressions


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=10.0)

    args = parser.parse_args()

    detector = RegressionDetector(threshold=args.threshold)
    has_regressions = detector.detect_regressions(args.baseline, args.current)

    sys.exit(1 if has_regressions else 0)
```

## Reporting

### HTML Report Generation

Generate HTML reports with formatted tables:

```python
from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter(output_dir="reports")

files = exporter.export_result(results, formats=["html"])

comparison = exporter.compare_results(baseline_path, current_path)
report_path = exporter.export_comparison_report(comparison)

print(f"Report: {report_path}")
```

### ASCII Chart Generation

Generate ASCII visualization charts for CI logs or text-based reports:

```bash
benchbox visualize results/*.json --no-color > comparison_charts.txt

benchbox visualize results/*.json --chart-type performance_bar --no-color
```

```python
from benchbox.core.visualization import ResultPlotter

plotter = ResultPlotter.from_sources(["results/tpch_duckdb.json"])

charts = plotter.generate_charts(output_dir="reports/charts")
print(f"Charts exported: {charts}")
```

### Slack Notifications

Send results to Slack:

```python
import requests
import json

def send_slack_notification(webhook_url: str, results: dict):
    benchmark_name = results.get("benchmark", {}).get("name", "Unknown")
    execution_id = results.get("execution", {}).get("id", "")
    timing = results.get("results", {}).get("timing", {})

    avg_time = timing.get("avg_ms", 0) / 1000
    total_queries = results.get("results", {}).get("queries", {}).get("total", 0)

    message = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Benchmark Results: {benchmark_name}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Execution ID:*\n{execution_id}"},
                    {"type": "mrkdwn", "text": f"*Total Queries:*\n{total_queries}"},
                    {"type": "mrkdwn", "text": f"*Average Time:*\n{avg_time:.3f}s"},
                ]
            }
        ]
    }

    response = requests.post(webhook_url, json=message)
    response.raise_for_status()

webhook = os.environ.get("SLACK_WEBHOOK_URL")
if webhook:
    send_slack_notification(webhook, results)
```

## Troubleshooting

### Long Execution Times

**Problem**: Benchmarks take too long in CI

**Solution**: Use smaller scale factors and query subsets:

```bash
benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 0.001 \
  --queries 1,6,12
```

``--scale 0.001`` is a very small scale, and ``--queries 1,6,12`` runs a subset of queries.

### Resource Constraints

**Problem**: CI runners run out of memory

**Solution**: Use resource-appropriate configurations. The fragments below
show a runner setting (use a larger runner) and a command option (limit the benchmark scope), not a complete workflow:

```text
# GitHub Actions - use larger runners
runs-on: ubuntu-latest-8-cores

# Or limit benchmark scope
--scale 0.01  # Smaller data size
```

### Flaky Results

**Problem**: Benchmark results vary between runs

**Solution**: Run multiple iterations and use median:

```python
results = []
for i in range(3):
    result = adapter.run_benchmark(benchmark)
    results.append(result.average_query_time)

median_time = sorted(results)[len(results) // 2]
```

The loop runs the benchmark several times. Using the median gives a stable result.

## See Also

- [Testing Guide](../development/testing.md) - Testing strategies
- [Performance Guide](./performance.md) - Performance optimization
- [Result Analysis API](../reference/python-api/result-analysis.md) - Result analysis utilities
- [Examples](../usage/examples.md) - Usage examples
