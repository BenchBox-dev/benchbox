# Feature-Focused Examples

**Each example demonstrates ONE specific BenchBox capability**

This directory contains focused examples that each teach a single feature or capability of BenchBox. Unlike the getting_started examples which provide end-to-end workflows, these examples isolate specific features to make learning easier.

## Philosophy

- **One feature per example** - Each script focuses on a single capability
- **Practical and runnable** - All examples are complete, working programs
- **Readable** - Names and structure carry the intent; this README carries the explanation
- **Build on basics** - Assumes you've completed getting_started examples

## Available Examples

### 1. Test Types ([test_types.py](test_types.py))
**Feature:** Different test execution types (power, throughput, maintenance)
**Learn:** When to use each test type

```bash
python features/test_types.py
```

**Key Concepts:**
- Power test: Sequential query execution for latency measurement
- Throughput test: Concurrent streams for capacity testing
- Maintenance test: Data modification operations

---

### 2. Maintenance Workflow ([maintenance_workflow.py](maintenance_workflow.py))
**Feature:** Complete workflow with Maintenance Test and database reload
**Learn:** Proper test sequencing and database state management

```bash
python features/maintenance_workflow.py
```

**Key Concepts:**
- Complete Power → Throughput → Maintenance sequence
- Database state verification (row count tracking)
- Why reload is required after Maintenance
- Correct vs incorrect test workflows
- Interactive demonstration of data modifications

**Important:** This example shows the critical reload requirement after Maintenance tests.

---

### 3. Query Subset ([query_subset.py](query_subset.py))
**Feature:** Run specific queries instead of full benchmark suite
**Learn:** Targeted testing for development and debugging

```bash
python features/query_subset.py
```

**Key Concepts:**
- `--queries` flag usage
- Quick smoke tests (2-3 queries)
- Debugging specific slow queries
- CI/CD integration with fast subsets

---

### 3. Tuning Comparison ([tuning_comparison.py](tuning_comparison.py))
**Feature:** Compare tuned vs baseline performance
**Learn:** Quantify optimization impact

```bash
python features/tuning_comparison.py
```

**Key Concepts:**
- Tuning configuration files
- Before/after comparison
- Performance improvement measurement
- Cost/benefit analysis of optimizations

---

### 4. Result Analysis ([result_analysis.py](result_analysis.py))
**Feature:** Load and compare benchmark results
**Learn:** Analyze performance changes over time

```bash
python features/result_analysis.py
```

**Key Concepts:**
- Result JSON format
- Loading previous results
- Query-by-query comparison
- Regression detection
- Statistical analysis

---

### 5. Multi-Platform ([multi_platform.py](multi_platform.py))
**Feature:** Run same benchmark on multiple platforms
**Learn:** Platform comparison workflow

```bash
python features/multi_platform.py
```

**Key Concepts:**
- Platform iteration
- Result collection across platforms
- Performance comparison
- Platform selection decision-making

---

### 6. Export Formats ([export_formats.py](export_formats.py))
**Feature:** Export results in JSON, CSV, HTML
**Learn:** Result export and reporting

```bash
python features/export_formats.py
```

**Key Concepts:**
- `--formats` flag usage
- JSON for programmatic processing
- CSV for spreadsheet analysis
- HTML for stakeholder reports

---

### 7. Data Validation ([data_validation.py](data_validation.py))
**Feature:** Enable data quality checks
**Learn:** Ensure data integrity

```bash
python features/data_validation.py
```

**Key Concepts:**
- Preflight validation (before generation)
- Postgen validation (after generation)
- Postload validation (after database load)
- Row count verification
- Data quality checks

---

### 8. Performance Monitoring ([performance_monitoring.py](performance_monitoring.py))
**Feature:** Monitor system resources during execution
**Learn:** Resource usage tracking

```bash
python features/performance_monitoring.py
```

**Key Concepts:**
- System profiling
- CPU usage tracking
- Memory usage tracking
- Resource requirement estimation

---

## Learning Path

**Recommended order for learning features:**

1. **Start:** `test_types.py` - Understand different execution modes
2. **Next:** `query_subset.py` - Learn targeted testing
3. **Then:** `tuning_comparison.py` - See optimization impact
4. **Advanced:** `result_analysis.py` - Analyze performance changes
5. **Multi-platform:** `multi_platform.py` - Compare databases
6. **Reporting:** `export_formats.py` - Generate reports
7. **Quality:** `data_validation.py` - Ensure correctness
8. **Performance:** `performance_monitoring.py` - Track resources

## Usage Patterns

### Quick Feature Test
```bash
python features/query_subset.py
```

Most examples complete in under a minute.

### Modify and Experiment
```bash
cp features/tuning_comparison.py my_experiment.py
```

Then modify parameters, scale factors, and platforms. All examples are self-contained.

### Integration in Your Code

Feature examples show patterns you can use in your own code. Copy the relevant
sections into your scripts.

## Example Structure

All feature examples follow a consistent structure:

- A setup section built from named helpers.
- A feature demonstration with descriptive names.
- A result display that shows what to look for.
- A tips section with best practices.

## Prerequisites

- Complete [getting_started](../getting_started/) examples first
- Understand basic BenchBox concepts
- Python 3.11+ installed
- BenchBox installed: `uv add benchbox`

## Next Steps

After mastering these features, explore:

- **[use_cases/](../use_cases/)** - Real-world problem-solving patterns
- **[programmatic/](../programmatic/)** - Using BenchBox as a library
- **[PATTERNS.md](../PATTERNS.md)** - Combined workflow patterns
- **[unified_runner.py](../unified_runner.py)** - Production-ready tool

## Tips

1. **Run examples as-is first** - See the feature in action before modifying
2. **Read the README entry** - It explains what each example shows
3. **Experiment with parameters** - Change scale factors, platforms, etc.
4. **Combine features** - Use multiple features together in your workflows
5. **Check exit codes** - Examples return 0 on success for CI/CD integration

## Common Questions

**Q: Can I combine multiple features?**
A: Yes! See [PATTERNS.md](../PATTERNS.md) for combinations.

**Q: How do these relate to unified_runner.py?**
A: These examples show individual features. unified_runner.py combines all features in one tool.

**Q: Should I use these in production?**
A: These are educational examples. For production, use unified_runner.py or build your own based on these patterns.

**Q: Can I modify these examples?**
A: Absolutely! They're designed to be copied and adapted for your needs.

## Contributing

Have a feature that needs a focused example? Submit a PR following this pattern:

1. One feature per example
2. Complete, runnable code
3. No comments or docstrings (see the [comment policy](../../docs/internal/comment-policy.md))
4. Describe the example and its usage in this README
5. Add the example to the list above

---

**Remember:** Each example teaches ONE feature in depth. For complete workflows combining features, see [PATTERNS.md](../PATTERNS.md).
