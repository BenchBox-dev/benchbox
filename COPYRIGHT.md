# Copyright and License Information

<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

## Project Copyright

**BenchBox** is Copyright © 2026 Joe Harris / BenchBox Project.

All source code, documentation, examples, and configuration files in this project are licensed under the MIT License, unless otherwise specified.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

### MIT License Summary

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## TPC Benchmark Trademark Acknowledgments

This project implements several standardized database benchmarks. The following trademarks and copyrights are acknowledged:

### TPC-H Benchmark

- **TPC Benchmark™ H (TPC-H)** - Copyright © Transaction Processing Performance Council
- Our TPC-H implementation is based on the official TPC-H specification
- TPC-H is a registered trademark of the Transaction Processing Performance Council

### TPC-DS Benchmark

- **TPC Benchmark™ DS (TPC-DS)** - Copyright © Transaction Processing Performance Council
- Our TPC-DS implementation is based on the official TPC-DS specification
- TPC-DS is a registered trademark of the Transaction Processing Performance Council

### TPC-DI Benchmark

- **TPC Benchmark™ DI (TPC-DI)** - Copyright © Transaction Processing Performance Council
- Our TPC-DI implementation is based on the official TPC-DI specification
- TPC-DI is a registered trademark of the Transaction Processing Performance Council

### Derived Benchmarks

The following benchmarks are derived from TPC-H and acknowledge the original TPC copyright:

- **Star Schema Benchmark (SSB)** - Derived from TPC Benchmark™ H (TPC-H)
- **TPC-Havoc** - Derived from TPC Benchmark™ H (TPC-H)
- **Read Primitives Benchmark** - Uses TPC Benchmark™ H (TPC-H) schema and data generation
- **Write Primitives Benchmark** - Uses TPC Benchmark™ H (TPC-H) schema and data generation

## TPC Fair Use Policy Compliance

This project implements TPC benchmarks for educational, research, and development purposes in compliance with the TPC Fair Use Policy. The implementations are:

- Used for legitimate performance evaluation and research
- Not used to create misleading performance claims
- Properly attributed to the Transaction Processing Performance Council
- Based on the official TPC specifications without unauthorized modifications to the core benchmark logic

## Third-Party Dependencies

This project uses several third-party libraries and tools. Each dependency retains its original copyright and license terms:

- **SQLGlot** - Used for SQL dialect translation
- **DuckDB** - Used as a reference database platform
- **PyTest** - Used for testing framework
- **Click** - Used for CLI interface
- **Rich** - Used for console output formatting
- **Pydantic** - Used for data validation
- **PyYAML** - Used for configuration file parsing

See `pyproject.toml` for the complete list of dependencies and their version requirements.

### Third-Party Sources

- **databricks-tpc-di** - Copyright (c) 2023-2026 Shannon Barrow and the databricks-tpc-di contributors. BenchBox's TPC-DI ETL is based on Shannon Barrow's databricks-tpc-di (MIT). The project has no LICENSE file and BenchBox treats it as MIT on the project owner's instruction. The notice is `_sources/tpc-di/databricks-tpc-di/LICENSE` and the source record is `_sources/tpc-di/databricks-tpc-di/PROVENANCE.md`.
- **djouallah/tpcdi** - Copyright (c) 2026 Mimoune Djouallah, MIT. The DuckDB TPC-DI implementation BenchBox compares its results with. The notice and the pinned commit are in `_sources/tpc-di/djouallah-tpcdi/`.
- **DIGen** - Copyright Transaction Processing Performance Council (TPC). The TPC-DI data generation utility, version 1.1.0, shipped unmodified inside `TPC-DI_Tools_v1.1.0.zip` under the TPC End User License Agreement v2.2 (`benchbox/_binaries/tpc-di/tools/EULA.txt`). THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC.
- **PDGF (Parallel Data Generation Framework)** - Copyright bankmark UG (haftungsbeschraenkt). The data generator DIGen runs, shipped unmodified in the same archive with its dictionaries and configuration. It is licensed by bankmark for generating TPC Benchmark DI data and "for internal, academic, and research purposes only"; the licence forbids modifying, decompiling or disassembling it and redistributing it other than as part of the TPC Benchmark DI. The BenchBox owner decided to ship it with the TPC-DI tools. The licence text is `benchbox/_binaries/tpc-di/tools/PDGF-LICENSE.txt`, its third-party component list is `PDGF-THIRD-PARTY-LICENSE.txt`, and the archive's name, size, SHA-256 and conditions are in `benchbox/_binaries/tpc-di/tools/SOURCING.md`. The archive's SHA-256 is `d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b`. The package licence metadata stays MIT only.

## Data Generation

TPC-H and TPC-DS data come from the TPC's `dbgen` and `dsdgen` programs. TPC-DI source data is generated by the TPC's DIGen and PDGF tools: `benchbox/core/tpcdi/sources/digen.py` runs them unmodified, with the user's own Java 8, from a per-run copy of the cache unpack of the shipped archive. BenchBox prints the licence notice on every generation and then answers DIGen's own licence prompt. The earlier Python TPC-DI generators written for this project (`TPCDIDataGenerator` and `TPCDISourceDataGenerator`) are still in the repository for the legacy ETL code and are not used by the DIGen path; TPC-DI is unavailable while BenchBox reworks it. Other benchmarks use generators written for this project that produce synthetic data modelled on the benchmark specifications. No real customer or proprietary data is included or generated by this project.

## Usage Guidelines

### Commercial Use

This project is available for both commercial and non-commercial use under the MIT License. Commercial users should:

- Include the copyright notice and MIT License in any distribution
- Comply with TPC Fair Use Policy when publishing benchmark results
- Properly attribute TPC benchmarks when used in performance evaluations

### Academic Use

Academic researchers may use this project for:

- Performance evaluation studies
- Database system research
- Educational purposes
- Comparative analysis studies

When publishing academic work using this project, please:

- Cite the BenchBox project appropriately
- Acknowledge TPC benchmark trademarks as specified above
- Follow academic standards for reproducible research

### Open Source Contributions

Contributions to this project are welcome and must:

- Be compatible with the MIT License
- Include appropriate copyright headers
- Respect TPC trademark requirements
- Follow the project's coding standards

## Contact Information

For questions about copyright, licensing, or TPC compliance:

- Project Issues: [GitHub Issues](https://github.com/username/benchbox/issues)
- License Questions: See MIT License terms
- TPC Compliance: Refer to [TPC Fair Use Policy](http://www.tpc.org/information/about/fair_use.asp)

## Legal Disclaimer

This project and its maintainers are not affiliated with, endorsed by, or sponsored by the Transaction Processing Performance Council (TPC). TPC, TPC-H, TPC-DS, and TPC-DI are trademarks of the Transaction Processing Performance Council.

The benchmark implementations in this project are independent implementations based on publicly available TPC specifications and are provided "as is" without warranty of any kind.

## Employment Affiliation Disclaimer

The project author is employed by Databricks, Inc., but BenchBox is an independent personal project with no affiliation to, endorsement by, or funding from Databricks. See [DISCLAIMER.md](DISCLAIMER.md) for complete details.

---

Last Updated: October 2025
