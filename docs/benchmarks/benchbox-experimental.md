---
myst:
  enable_extensions:
    - deflist
---

# BenchBox Experimental

```{tags} advanced, reference
```

Emerging benchmarks for specialized testing and novel workloads.

## What Makes a Benchmark "Experimental"?

Experimental benchmarks in BenchBox share one or more of these characteristics:

**Newly developed**
: Recently created benchmarks that haven't yet been validated across many platforms or use cases. They may evolve as we learn from real-world usage.

**Limited adoption**
: Benchmarks that address real needs but haven't achieved widespread industry acceptance. They may become standards or remain niche tools.

**Specialized focus**
: Benchmarks targeting emerging workloads (AI/ML, time-series, metadata) that don't fit traditional OLAP categories. The methodology for testing these workloads is still evolving.

**Research-oriented**
: Benchmarks designed to explore database behavior under unusual conditions (skewed data, adversarial queries) rather than measure typical performance.

## Why Include Experimental Benchmarks?

The database landscape evolves rapidly. Workloads that seemed exotic five years ago are now common:

- **AI/ML integration** - Vector similarity, embedding storage, feature serving
- **Time-series analytics** - IoT data, observability, financial markets
- **Metadata-heavy workloads** - Data catalogs, schema evolution, lineage tracking
- **Adversarial conditions** - Skewed data, optimizer-hostile queries, chaos testing

Experimental benchmarks let BenchBox stay ahead of these trends. Some will prove their worth and graduate to standard benchmarks. Others will inform the design of better benchmarks. All contribute to understanding database performance in emerging scenarios.

## Using Experimental Benchmarks

When working with experimental benchmarks, keep these considerations in mind:

**Expect change**
: Schemas, queries, and methodologies may evolve. Pin to specific BenchBox versions for reproducible results.

**Validate relevance**
: Check whether the benchmark's assumptions match your use case. A skew or
optimizer-stress benchmark may not tell you much about production dashboard
workloads.

**Contribute feedback**
: Experimental benchmarks improve through usage. Report issues, suggest improvements, and share results to help refine them.

**Interpret cautiously**
: Results may be less reliable than established benchmarks. Use them for directional guidance, not definitive platform selection.

## Experimental Benchmarks in BenchBox

```{list-table}
:header-rows: 1
:widths: 25 35 40

* - Benchmark
  - Focus
  - Status
* - **TPC-HAVOC**
  - Optimizer stress testing with 220 TPC-H syntax variants
  - Research prototype
* - **TPC-H Skew**
  - Data skew effects on query performance
  - Methodology validation
* - **TPC-DS-OBT**
  - TPC-DS on a single denormalized "One Big Table" schema
  - Active development
* - **Data Vault**
  - Data Vault 2.0 modeling patterns
  - Schema finalization
```

### Graduation Criteria

Experimental benchmarks may graduate to standard categories when they meet these criteria:

1. **Stable specification** - No significant methodology changes for 6+ months
2. **Platform coverage** - Tested on 5+ platforms with consistent results
3. **Community validation** - External usage and feedback confirming utility
4. **Documentation complete** - Full specification, data generation, and analysis guides

## Included Benchmarks

```{toctree}
:maxdepth: 1

tpc-havoc
tpch-skew
tpc-ds-obt
tpc-ds-obt-lineage
datavault
```

## See Also

- {doc}`benchbox-primitives` - Stable BenchBox-created benchmarks (Read/Write/Transaction/Metadata/AI)
- {doc}`real-world-benchmarks` - Public-dataset benchmarks (NYC Taxi, Flight Data)
- {doc}`time-series-benchmarks` - Time-series workloads (TSBS DevOps)
- {doc}`ai-ml-benchmarks` - AI/ML workloads (Vector Search)
- {doc}`academic-benchmarks` - Research benchmarks with established methodology
