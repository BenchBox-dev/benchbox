# BenchBox Version Management Strategy

## Overview

This document outlines the version management strategy for BenchBox, ensuring consistency across all version references and providing clear guidelines for version updates.

Current release: `v0.5.0rc2`.

## Version Sources

BenchBox maintains version information in multiple locations:

1. **Primary Source**: `benchbox/__init__.py` - The canonical version string (`__version__`)
2. **Package Metadata**: `pyproject.toml` - Used by build tools and package managers
3. **CLI Output**: Dynamic version reporting via `benchbox --version`
4. **Documentation Markers**: `README.md`, `docs/README.md`, and this guide publish the current release label for humans.

## Version Consistency Enforcement

### Automatic Validation

The version management system automatically validates consistency between version sources:

- **Import-time Checks**: When `benchbox` is imported, version consistency is validated. Set `BENCHBOX_STRICT_VERSION=1` during CI to turn warnings into hard failures.
- **CLI Version Commands**: `benchbox --version` returns a cached, human-friendly summary. `benchbox --version-json` emits a machine-readable payload that downstream tooling can ingest.
- **Documentation Enforcement**: The README files (including this guide) are scanned for the current release marker so humans see the same version as the package metadata.
- **Error Reporting**: CLI errors and verbosity utilities include version metadata to simplify bug reports.

### Validation Functions

The `benchbox.utils.version` module provides:

- `check_version_consistency()`: Validates versions across all sources
- `format_version_report(as_json=False)`: Generates comprehensive version information (JSON available via `as_json=True`)
- `create_import_error()`: Enhanced import errors with version context
- `is_version_compatible()`: Checks whether the current version satisfies a supported range
- `ensure_version_compatible()`: Raises an actionable error when the version is out of range
- `reset_version_cache()`: Clears cached metadata (handy for long-running processes and tests)

## Version Update Process

### 1. Single Source Update

**Primary Location**: Update version in `benchbox/__init__.py`:

```python
__version__ = "X.Y.Z"
```

### 2. Automatic Propagation

The version management system handles propagation to other locations:

- Build tools read from `pyproject.toml` (should be kept in sync manually)
- CLI commands dynamically read from `__version__`
- Error reports automatically include current version

### 3. Validation

After version updates:

1. Run the automated consistency check (shows all tracked sources):
   ```bash
   uv run -- python scripts/update_version.py --check
   ```

2. Test CLI version output:
   ```bash
   uv run benchbox --version
   uv run benchbox --version-json
   ```

3. Verify no version warnings during import (set `BENCHBOX_STRICT_VERSION=1` if you want failures):
   ```bash
   uv run -- python -c "import benchbox"
   ```

4. Use the automated helper to keep files in sync (supports `--dry-run` for previews):
   ```bash
   uv run -- python scripts/update_version.py --version X.Y.Z --update-pyproject
   ```

The `scripts/update_version.py` utility is idempotent and validates semantic
version formatting before writing changes so accidental typos are caught early.

## Compatibility Checks

Platform adapters and extensions can assert supported BenchBox versions using
the compatibility helpers:

```python
from benchbox.utils.version import ensure_version_compatible

ensure_version_compatible(min_version="0.1.0", max_version="0.2.0")
```

Use `is_version_compatible()` for conditional logic where you want to provide a
custom fallback. Both helpers implement strict semantic version comparison and
support pre-release tags (`-alpha.N`, `-beta.N`, `-rc.N`, `-dev`).

## Version Policy

BenchBox follows SemVer and publishes pre-release builds for major features.
The policy is:

1. **Compatibility**: Public APIs aim to preserve backward compatibility within
   a minor series. Breaking changes trigger a new major version.
2. **Pre-release Stability**: `-alpha` and `-beta` tags may change APIs; `-rc`
   is feature complete with only critical fixes.
3. **Supported Window**: Only the latest minor version within the current major
   release receives fixes. Use `ensure_version_compatible()` in downstream code
   to guard against unsupported versions.

## Deprecation Strategy

1. **Announcement**: Deprecations are announced in the changelog one release
   ahead of removal.
2. **Runtime Warnings**: Deprecated APIs issue `DeprecationWarning` with
   guidance, including the target removal version.
3. **Tracking**: The compatibility helpers document the supported ranges so
   platform adapters can plan migrations.
4. **Removal**: Deprecated functionality is removed at the next major release
   after its announced sunset.

## Version Format

BenchBox follows semantic versioning (SemVer):

- **Format**: `MAJOR.MINOR.PATCH`
- **Pre-release**: `MAJOR.MINOR.PATCH-alpha.N`, `MAJOR.MINOR.PATCH-beta.N`
- **Development**: `MAJOR.MINOR.PATCH-dev`

### Examples

- Release: `1.0.0`
- Pre-release: `1.1.0-beta.1`
- Development: `1.1.0-dev`

## Debug and Development

### Environment Flags

- `BENCHBOX_STRICT_VERSION=1` escalates version mismatches from warnings to import-time `RuntimeError`s. Useful for CI pipelines that must fail fast when metadata is misaligned.
- `BENCHBOX_DEBUG_IMPORTS=1` enables detailed logging from the lazy loader so you can diagnose optional dependency/import issues during development.
- `BENCHBOX_DRIVER_AUTO_INSTALL=1` allows the runtime version manager to install requested driver builds automatically (for example, `uv pip install duckdb==1.1.0`).

### Version Information in Logs

When debugging is enabled (verbose mode), `log_debug_info()` automatically includes version information in its output:

```python
from benchbox.utils.verbosity import VerbosityMixin

class MyClass(VerbosityMixin):
    def debug_operation(self):
        self.log_debug_info("Operation Context")
```

### Error Context

All CLI errors automatically include version information in their error details:

```python
from benchbox.cli.exceptions import BenchboxCLIError

raise BenchboxCLIError("Something went wrong")
```

## Integration Points

### CLI Commands

The CLI system automatically includes version information:

- `benchbox --version`: Comprehensive version report
- Error messages: Version context for bug reports
- Debug output: Version details in verbose mode

### Platform Adapters

Platform adapters can access version information:

```python
import benchbox
from benchbox.utils.version import format_version_report

def report_platform_info():
    return {
        "benchbox_version": benchbox.__version__,
        "version_report": format_version_report()
    }
```

### Error Handling

Enhanced error handling with version context:

```python
from benchbox.cli.exceptions import ErrorContext, BenchboxCLIError

context = ErrorContext(
    operation="benchmark_execution",
    stage="data_loading",
    include_version_info=True
)
```

Setting `include_version_info=True` adds the version information automatically.

## Best Practices

### For Developers

1. **Single Source**: Only update version in `benchbox/__init__.py`
2. **Validation**: Always run consistency checks after version updates
3. **Testing**: Verify CLI version output and import behavior
4. **Documentation**: Update changelog and release notes

### For Contributors

1. **Version References**: Never hardcode version strings in code
2. **Import Pattern**: Use `import benchbox; benchbox.__version__` for version access
3. **Error Handling**: Leverage automatic version inclusion in errors
4. **Debug Output**: Use provided debug utilities for version context

### For Package Maintenance

1. **Release Process**: Update `__version__` first, then build and test
2. **Consistency Monitoring**: Set up CI checks for version consistency
3. **Documentation Updates**: Ensure version references in docs are current
4. **Tag Management**: Create git tags matching version strings

## Troubleshooting

### Version Inconsistency Warnings

If you see version inconsistency warnings:

1. Check `benchbox/__init__.py` for the current version
2. Verify `pyproject.toml` matches the primary version
3. Run consistency check to identify discrepancies
4. Update mismatched versions manually

### Import Failures with Version Context

When imports fail, version information helps identify compatibility issues:

1. Review the version context in error messages
2. Check for dependency version conflicts
3. Verify Python version compatibility
4. Consult installation documentation for version requirements

### Debug Information

For comprehensive debugging, enable debug logging, run with verbose output (`-vv`), and check version consistency:

```bash
export BENCHBOX_LOG_LEVEL=DEBUG

uv run benchbox run --benchmark tpch --scale 0.01 -vv

uv run -- python -c "from benchbox.utils.version import format_version_report; print(format_version_report())"
```

## Future Enhancements

### Planned Improvements

1. **Automated Sync**: Scripts to automatically sync versions across files
2. **CI Integration**: Automated version consistency checks in CI/CD
3. **Release Automation**: Automated tagging and version bumping
4. **Compatibility Matrix**: Version compatibility tracking for dependencies

### Monitoring

1. **Version Drift Detection**: Automated detection of version inconsistencies
2. **Dependency Tracking**: Monitor dependency version changes
3. **Compatibility Testing**: Automated testing across version combinations
