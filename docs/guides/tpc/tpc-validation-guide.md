<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC Test Result Validation System

```{tags} intermediate, guide, validation, tpc-h, tpc-ds
```

A systematic validation system for TPC benchmark test results that ensures compliance with official TPC specification requirements.

## Overview

The TPC Test Result Validation System provides:

- **Comprehensive Validation**: Validates all aspects of TPC test results including completeness, query execution, timing, data integrity, and metrics
- **Specification Checks**: Compares results against TPC specification requirements; passing does not make a result compliant with or comparable to official TPC results
- **Readiness Reports**: Runs the strictest checks (the `CERTIFICATION` level and `CertificationChecker` keep their legacy API names) and reports a readiness status; it is not a TPC certification
- **Audit Trail**: Tracks all validation activities for reproducibility and compliance auditing
- **Multi-Benchmark Support**: Supports TPC-H, TPC-DS, TPC-DI, and other TPC benchmarks
- **Detailed Reporting**: Generates systematic validation reports with issue tracking and metrics

## Architecture

The validation system consists of several key components:

### Core Components

- **`TPCResultValidator`**: Main validation engine that coordinates all validation activities
- **`ValidationReport`**: Comprehensive report containing all validation results, issues, and metrics
- **`AuditTrail`**: Tracks all validation events for reproducibility and compliance auditing

### Validators

- **`CompletenessValidator`**: Ensures all required tests completed successfully
- **`QueryResultValidator`**: Validates query execution results and performance
- **`TimingValidator`**: Validates timing measurements and precision requirements
- **`DataIntegrityValidator`**: Validates data integrity during maintenance operations
- **`MetricsValidator`**: Validates metric calculations and statistical validity
- **`ComplianceChecker`**: Checks results against TPC specification requirements
- **`CertificationChecker`**: Runs the strictest readiness checks (legacy name; not a TPC certification)

## Installation

The validation system is included in the BenchBox library:

```python
from benchbox.core.tpc_validation import TPCResultValidator, ValidationLevel
```

## Quick Start

### Basic Usage

```python
from benchbox.core.tpc_validation import TPCResultValidator, ValidationLevel

validator = TPCResultValidator()

test_results = {
    "benchmark_name": "TPC-H",
    "scale_factor": 1.0,
    "test_start_time": "2023-01-01T10:00:00Z",
    "test_end_time": "2023-01-01T11:00:00Z",
    "query_results": {
        "1": {
            "status": "success",
            "execution_time": 5.2,
            "row_count": 100,
            "results": [{"col1": "value1", "col2": "value2"}]
        }
    },
    "data_generation": {
        "generation_time": 120.5,
        "generated_tables": ["customer", "orders", "lineitem"]
    },
    "metrics": {
        "avg_query_time": 5.2,
        "total_query_time": 5.2
    }
}

report = validator.validate(test_results, ValidationLevel.STANDARD)

print(f"Overall Result: {report.overall_result.value}")
print(f"Validation Score: {report.metrics.get('validation_score', 0):.1f}%")
print(f"Issues Found: {len(report.issues)}")
```

### Configuration

You can customize the validation behavior with configuration:

```python
config = {
    "validators": {
        "completeness": {
            "required_queries": {"TPC-H": list(range(1, 23))},
            "required_tables": ["customer", "orders", "lineitem"]
        },
        "timing": {
            "max_execution_time": 3600,
            "precision_threshold": 0.001
        },
        "certification": {
            "required_documentation": ["test_report", "environment_spec"],
            "performance_thresholds": {
                "avg_query_time": 30.0
            }
        }
    }
}

validator = TPCResultValidator(config)
```

## Test Results Format

The validation system expects test results in the following format. The fields are:

- Required: `benchmark_name` (TPC-H, TPC-DS, or TPC-DI), `scale_factor` (the scale factor used), and `test_start_time` and `test_end_time` (ISO format timestamps).
- Query execution results: `query_results` maps each query ID to its `status` (`success`, `failed`, or `timeout`), `execution_time` (seconds), and `row_count` (rows returned). `results` (the actual query results) and `error` (the error message if the query failed) are optional.
- Data generation information: `data_generation` holds `generation_time` (seconds to generate data) and `generated_tables`.
- Calculated metrics: `metrics`.
- Optional: `maintenance_operations` (for TPC-DS and TPC-DI) and `etl_operations` (for TPC-DI).
- Reproducibility information: `reproducibility`. Test isolation: `test_isolation`. Documentation: `documentation`.

```python
test_results = {
    "benchmark_name": "TPC-H",
    "scale_factor": 1.0,
    "test_start_time": "2023-01-01T10:00:00Z",
    "test_end_time": "2023-01-01T11:00:00Z",

    "query_results": {
        "1": {
            "status": "success",
            "execution_time": 5.2,
            "row_count": 100,
            "results": [...],
            "error": "..."
        }
    },

    "data_generation": {
        "generation_time": 120.5,
        "generated_tables": ["customer", "orders", "lineitem"]
    },

    "metrics": {
        "avg_query_time": 5.2,
        "total_query_time": 5.2,
        "queries_per_second": 0.19
    },

    "maintenance_operations": {
        "insert_operation": {
            "status": "success",
            "start_time": "2023-01-01T10:30:00Z",
            "end_time": "2023-01-01T10:35:00Z",
            "records_affected": 5000
        }
    },

    "etl_operations": {
        "extract_customers": {
            "status": "success",
            "start_time": "2023-01-01T09:00:00Z",
            "end_time": "2023-01-01T09:15:00Z",
            "records_processed": 150000
        }
    },

    "reproducibility": {
        "seed": 12345,
        "timestamp": "2023-01-01T10:00:00Z",
        "environment": "test_env"
    },

    "test_isolation": {
        "isolated": True
    },

    "documentation": {
        "test_report": "path/to/test_report.pdf",
        "environment_spec": "path/to/env_spec.json"
    }
}
```

## Validation Levels

The system supports three validation levels:

### ValidationLevel.BASIC
- Basic completeness checks
- Query execution validation
- Simple timing validation

### ValidationLevel.STANDARD
- All basic validations
- Data integrity validation
- Metrics validation
- Compliance checking

### ValidationLevel.CERTIFICATION
- All standard validations
- Strict readiness checks (not a TPC certification)
- Enhanced documentation requirements
- Performance threshold validation

## Validation Results

### ValidationResult Enum
- `PASSED`: All validations passed
- `WARNING`: Validations passed with warnings
- `FAILED`: One or more validations failed
- `SKIPPED`: Validation was skipped

### ValidationReport
The `ValidationReport` contains:
- Overall validation result
- Individual validator results
- Detailed issues with levels (ERROR, WARNING, INFO)
- Calculated metrics
- Execution summary
- Audit trail
- Readiness status (`certification_status` field; not a TPC certification)

## Individual Validators

### CompletenessValidator
Validates that all required test components are present:
- Required queries executed
- Required tables generated
- Required maintenance operations completed
- Execution metadata present

### QueryResultValidator
Validates query execution results:
- All queries executed successfully
- Execution times within acceptable bounds
- Row counts reasonable
- Result data integrity
- Schema validation (if configured)

### TimingValidator
Validates timing measurements:
- Total test time reasonable
- Query execution times consistent
- Timing precision adequate
- Data generation timing reasonable

### DataIntegrityValidator
Validates data integrity during maintenance operations:
- Maintenance operations successful
- Referential integrity maintained
- Data consistency preserved
- Transaction isolation maintained

### MetricsValidator
Validates calculated metrics:
- Required metrics present
- Metric values within expected ranges
- Calculated metrics match reported values
- Statistical validity of results

### ComplianceChecker
Checks TPC compliance requirements:
- Benchmark-specific requirements (TPC-H: 22 queries, TPC-DS: 99 queries)
- Scale factor compliance
- Test isolation requirements
- Reproducibility requirements

### CertificationChecker
Runs the strictest readiness checks. This is not a TPC certification:
- Performance thresholds met
- Documentation complete
- Test completeness
- Overall readiness assessment

## Integration with Existing Benchmarks

The validation system integrates smoothly with existing TPC benchmarks:

### TPC-H Integration

```python
from benchbox import TPCH
from benchbox.core.tpc_validation import TPCResultValidator

benchmark = TPCH(scale_factor=1.0)

test_results = run_benchmark_and_collect_results(benchmark)

validator = TPCResultValidator()
report = validator.validate(test_results, ValidationLevel.STANDARD)
```

### TPC-DS Integration

The example validates results with a TPC-DS specific configuration:

```python
from benchbox.tpcds import TPCDSBenchmark
from benchbox.core.tpc_validation import TPCResultValidator

benchmark = TPCDSBenchmark(scale_factor=1.0)

test_results = run_benchmark_and_collect_results(benchmark)

config = {
    "validators": {
        "completeness": {
            "required_queries": {"TPC-DS": list(range(1, 100))},
            "required_maintenance_ops": ["insert_sales", "update_inventory"]
        }
    }
}

validator = TPCResultValidator(config)
report = validator.validate(test_results, ValidationLevel.CERTIFICATION)
```

## Custom Validators

You can create custom validators for specific requirements:

```python
from benchbox.core.tpc_validation import BaseValidator, ValidationResult

class CustomBusinessRuleValidator(BaseValidator):
    def validate(self, test_results, report):
        query_results = test_results.get("query_results", {})

        if len(query_results) < 5:
            report.add_issue(
                "ERROR",
                f"Minimum 5 queries required, found {len(query_results)}",
                {"query_count": len(query_results)},
                self.name
            )
            return ValidationResult.FAILED

        return ValidationResult.PASSED

validator = TPCResultValidator()
validator.validators.append(CustomBusinessRuleValidator("custom_business_rules"))
```

## Report Management

### Saving Reports

```python
from pathlib import Path

validator.save_report(report, Path("validation_report.json"))

report_dir = Path("validation_reports")
report_dir.mkdir(exist_ok=True)
validator.save_report(report, report_dir / "tpch_validation.json")
```

### Loading Reports

```python
loaded_report = validator.load_report(Path("validation_report.json"))

print(f"Report ID: {loaded_report.validation_id}")
print(f"Overall Result: {loaded_report.overall_result.value}")
```

### Report Analysis

```python
errors = report.get_issues_by_level("ERROR")
warnings = report.get_issues_by_level("WARNING")

timing_issues = report.get_issues_by_validator("timing")
compliance_issues = report.get_issues_by_validator("compliance")

validation_score = report.metrics.get("validation_score", 0)
total_queries = report.execution_summary.get("total_queries", 0)
success_rate = report.execution_summary.get("success_rate", 0)
```

## Examples

### Basic Example

```python
from benchbox.core.tpc_validation import create_sample_test_results, TPCResultValidator

test_results = create_sample_test_results()

validator = TPCResultValidator()
report = validator.validate(test_results)

print(f"Result: {report.overall_result.value}")
print(f"Issues: {len(report.issues)}")
```

### Strict Readiness Example

```python
config = {
    "validators": {
        "certification": {
            "required_documentation": ["test_report", "environment_spec"],
            "performance_thresholds": {
                "avg_query_time": 30.0
            }
        }
    }
}

validator = TPCResultValidator(config)
report = validator.validate(test_results, ValidationLevel.CERTIFICATION)

print(f"Readiness Status: {report.certification_status}")
```

### Multi-Benchmark Suite

```python
benchmarks = ["TPC-H", "TPC-DS", "TPC-DI"]
suite_results = {}

for benchmark in benchmarks:
    test_results = run_benchmark(benchmark)
    report = validator.validate(test_results)
    suite_results[benchmark] = report

compliance_summary = {
    benchmark: {
        "compliant": len(report.get_issues_by_level("ERROR")) == 0,
        "score": report.metrics.get("validation_score", 0)
    }
    for benchmark, report in suite_results.items()
}
```

## Best Practices

### 1. Configuration Management
- Use configuration files for complex validation setups
- Store benchmark-specific configurations separately
- Version control your validation configurations

### 2. Error Handling
- Always check validation results before proceeding
- Handle validation failures gracefully
- Log validation issues for debugging

### 3. Performance Considerations
- Use appropriate validation levels for your use case
- Consider caching validation results for repeated runs
- Profile validation performance for large test suites

### 4. Compliance Tracking
- Save validation reports for audit trails
- Track compliance over time
- Generate regular compliance summaries

### 5. Integration
- Integrate validation into your CI/CD pipeline
- Use validation results to gate releases
- Monitor validation trends over time

## Troubleshooting

### Common Issues

1. **Missing Required Fields**
   - Ensure all required fields are present in test results
   - Check field names and formats match specifications

2. **Timing Validation Failures**
   - Verify timestamp formats are ISO 8601 compliant
   - Check for reasonable execution times
   - Ensure timing consistency across measurements

3. **Query Result Validation Issues**
   - Verify all queries completed successfully
   - Check row counts and result formats
   - Validate error handling for failed queries

4. **Compliance Failures**
   - Check benchmark-specific requirements
   - Verify all required queries are present
   - Ensure maintenance operations are included where required

### Debug Mode

Enable debug logging for detailed validation information:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

validator = TPCResultValidator()
report = validator.validate(test_results)
```

## API Reference

### TPCResultValidator
- `__init__(config=None)`: Create validator with optional configuration
- `validate(test_results, validation_level=ValidationLevel.STANDARD)`: Validate test results
- `save_report(report, output_path)`: Save validation report to file
- `load_report(input_path)`: Load validation report from file
- `create_default_config()`: Create default configuration

### ValidationReport
- `add_issue(level, message, details=None, validator_name="")`: Add validation issue
- `get_issues_by_level(level)`: Get issues by severity level
- `get_issues_by_validator(validator_name)`: Get issues by validator
- `to_dict()`: Convert report to dictionary for serialization

### BaseValidator
- `validate(test_results, report)`: Perform validation (abstract method)
- `_check_required_fields(data, required_fields)`: Check for required fields

## Contributing

To contribute to the TPC validation system:

1. Create new validators by inheriting from `BaseValidator`
2. Add benchmark-specific validation rules
3. Extend the configuration system
4. Add new validation levels
5. Improve error reporting and diagnostics

## License

This validation system is part of the BenchBox library and follows the same license terms.
