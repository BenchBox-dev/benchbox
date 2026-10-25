# Platform Support Status

```{tags} intermediate, reference
```

BenchBox platform metadata carries a `support_status` value from the public support taxonomy: `stable`, `beta`, `experimental`, `repo_only`, `deprecated`, or `document_only`.

This status is intentionally separate from local dependency availability. For example, Snowflake can be `beta` product surface while unavailable on a machine that has not installed `snowflake-connector-python`.

## Registry Snapshot

The authoritative source is `benchbox/core/platform_registry.py`.

<!-- benchbox-registry-counts:start -->

Platform registry: **52** metadata entries; **48** SQL-capable; **18** DataFrame-capable; **14** dual-mode; support status counts: stable=5, beta=28, experimental=18, deprecated=1.

<!-- benchbox-registry-counts:end -->

| Status | Entries | SQL-capable | DataFrame-capable | Dependency group | CLI exposure | MCP exposure | Docs exposure |
| --- | ---: | ---: | ---: | --- | --- | --- | --- |
| `stable` | 5 | 3 | 3 | Core or single local extra | Listed by registry; runnable when dependency is installed | Discovery/control-plane metadata via registry | Full platform docs |
| `beta` | 28 | 27 | 3 | Optional extras, credentials, or local services | Listed by registry; may need SDK/credentials | Discovery/control-plane metadata via registry | Platform docs and setup notes |
| `experimental` | 18 | 17 | 12 | Optional, GPU/native, or cloud Spark extras | Listed but labeled experimental in metadata/docs | Discovery metadata only unless tool support is documented | Experimental caveats required |
| `repo_only` | 0 | 0 | 0 | Source checkout only | Hidden from normal runtime | Not exposed | Developer docs only |
| `deprecated` | 1 | 1 | 0 | Compatibility selector | Retained for migration | Existing-client exposure only | Migration docs required |
| `document_only` | 0 | 0 | 0 | None | Not runnable | Not exposed | Docs must say not executable |

## Optional Import Diagnostics

Normal platform discovery remains fail-open for optional dependencies. Missing optional SDKs, unavailable native libraries, and broken adapter imports are recorded only when diagnostics are requested:

```bash
uv run -- python - <<'PY'
from benchbox.core.platform_registry import PlatformRegistry

for platform, diagnostic in PlatformRegistry.diagnose_optional_adapter_imports(["snowflake", "datafusion"]).items():
    print(platform, diagnostic["status"], diagnostic["support_status"])
PY
```

Diagnostic statuses:

| Status | Meaning |
| --- | --- |
| `available` | The adapter module imported and exposed the expected adapter class. |
| `missing_optional_dependency` | A package needed by the adapter is not installed. |
| `native_library_load_failure` | A native library or ABI-dependent module failed to load. |
| `broken_adapter_import` | The BenchBox adapter module imported incorrectly or did not expose the expected class. |
| `deprecated_platform` | The platform selector is retained only for migration. |
| `intentionally_disabled` | The support status indicates a non-runtime surface. |
| `not_configured` | The requested platform is not in optional adapter registration. |
