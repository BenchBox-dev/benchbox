from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable
from pathlib import Path

from benchbox.core.platform_manifest import (
    PLATFORM_MANIFEST,
    PLATFORM_MANIFEST_BY_KEY,
    get_adapter_imports,
    get_all_platform_aliases,
    get_platform_alias_modes,
    get_platform_aliases,
    get_platform_metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


CLI_DESCRIPTION = "Validate platform-manifest registry projections without loading adapter SDKs."


def _adapter_source_path(module: str) -> Path | None:
    module_path = REPO_ROOT.joinpath(*module.split("."))
    module_file = module_path.with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = module_path / "__init__.py"
    return package_file if package_file.is_file() else None


def _declared_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    class ModuleSymbolVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.symbols: set[str] = set()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.symbols.add(node.name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.symbols.add(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.symbols.add(node.name)

        def visit_Import(self, node: ast.Import) -> None:
            self.symbols.update(alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            self.symbols.update(alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names)

        def visit_Assign(self, node: ast.Assign) -> None:
            self.symbols.update(target.id for target in node.targets if isinstance(target, ast.Name))

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name):
                self.symbols.add(node.target.id)

    visitor = ModuleSymbolVisitor()
    visitor.visit(tree)
    return visitor.symbols


def _lazy_source_path(module_path: str) -> Path | None:
    absolute = f"benchbox.platforms{module_path}" if module_path.startswith(".") else module_path
    return _adapter_source_path(absolute)


def _validate_registry_projections(errors: list[str]) -> None:
    from benchbox.cli.platform import PLATFORM_ALIASES
    from benchbox.core.platform_registry import _OPTIONAL_ADAPTERS, PlatformRegistry

    if PlatformRegistry.get_all_platform_metadata() != get_platform_metadata():
        errors.append("PlatformRegistry metadata is not an exact manifest projection")
    if PlatformRegistry.get_all_aliases() != get_platform_aliases("registry"):
        errors.append("PlatformRegistry aliases are not the exact registry-scoped manifest projection")
    if get_platform_aliases("cli") != PLATFORM_ALIASES:
        errors.append("CLI PLATFORM_ALIASES is not an exact manifest projection")
    if get_adapter_imports() != _OPTIONAL_ADAPTERS:
        errors.append("runtime adapter registrations are not an exact manifest projection")


def _validate_adapter_sources(errors: list[str]) -> None:
    for key, module, class_name in get_adapter_imports():
        source_path = _adapter_source_path(module)
        if source_path is None:
            errors.append(f"{key}: adapter module does not exist: {module}")
            continue
        if class_name not in _declared_symbols(source_path):
            errors.append(f"{key}: {source_path.relative_to(REPO_ROOT)} does not declare or export {class_name}")


def _lazy_symbol_is_exported(module_path: str, symbol: str) -> bool:
    import benchbox.platforms.dataframe as dataframe_package

    if module_path in {".dataframe", "benchbox.platforms.dataframe"}:
        target = dataframe_package._LAZY_EXPORTS.get(symbol)
        if target is None:
            return False
        module_path = target[0]
    source = _lazy_source_path(module_path)
    return source is not None and symbol in _declared_symbols(source)


def _validate_dataframe_lazy_refs(
    errors: list[str], platform_package: object, spelling: str, adapter_name: str, availability_name: str
) -> None:
    adapter_module = platform_package._LAZY_ADAPTERS.get(adapter_name)
    if adapter_module is None:
        errors.append(f"DataFrame factory spelling {spelling!r} references unknown lazy adapter {adapter_name!r}")
    elif not _lazy_symbol_is_exported(adapter_module, adapter_name):
        errors.append(f"DataFrame factory spelling {spelling!r} lazy adapter symbol {adapter_name!r} is not exported")

    availability_spec = platform_package._LAZY_CONSTANTS.get(availability_name)
    if availability_spec is None:
        errors.append(
            f"DataFrame factory spelling {spelling!r} references unknown lazy availability constant "
            f"{availability_name!r}"
        )
    elif not _lazy_symbol_is_exported(availability_spec[0], availability_name):
        errors.append(
            f"DataFrame factory spelling {spelling!r} lazy availability symbol {availability_name!r} is not exported"
        )


def _validate_dataframe_spelling(
    errors: list[str], platform_package: object, spelling: str, info: tuple[str, str, str]
) -> None:
    adapter_name, availability_name, install_guidance = info
    canonical = get_platform_aliases("cli").get(spelling)
    if canonical is None:
        errors.append(f"DataFrame factory spelling {spelling!r} is not a CLI-scoped manifest alias")
        return
    if get_platform_alias_modes("cli").get(spelling) != "dataframe":
        errors.append(f"DataFrame factory spelling {spelling!r} does not imply DataFrame mode in the manifest")
    entry = PLATFORM_MANIFEST_BY_KEY.get(canonical)
    if entry is None or not entry.capabilities["supports_dataframe"]:
        errors.append(f"DataFrame factory spelling {spelling!r} does not resolve to a DataFrame-capable platform")

    _validate_dataframe_lazy_refs(errors, platform_package, spelling, adapter_name, availability_name)

    if not install_guidance.strip():
        errors.append(f"DataFrame factory spelling {spelling!r} has empty installation guidance")


def _validate_semantic_surfaces(errors: list[str]) -> None:
    import benchbox.platforms as platform_package
    from benchbox.cli.commands.run import _GUIDED_CREDENTIAL_PLATFORMS
    from benchbox.core.tuning.capability_registry import PLATFORM_ALIASES as TUNING_ALIASES
    from benchbox.platforms.base.format_capabilities import (
        CAPABILITIES_REGISTRY,
        EXTERNAL_PLATFORM_FORMAT_PREFERENCES,
        PLATFORM_FORMAT_PREFERENCES,
    )

    manifest_keys = set(PLATFORM_MANIFEST_BY_KEY)
    manifest_aliases = get_all_platform_aliases()

    dataframe_classes = {row[0] for row in platform_package._DATAFRAME_PLATFORM_INFO.values()}
    runtime_classes = {class_name for _key, _module, class_name in get_adapter_imports()}
    lazy_adapter_classes = {name for name in platform_package._LAZY_ADAPTERS if name.endswith("Adapter")}
    unknown_lazy_classes = sorted(lazy_adapter_classes - runtime_classes - dataframe_classes)
    if unknown_lazy_classes:
        errors.append(f"lazy exports have no runtime/DataFrame semantic owner: {', '.join(unknown_lazy_classes)}")

    for spelling, info in sorted(platform_package._DATAFRAME_PLATFORM_INFO.items()):
        _validate_dataframe_spelling(errors, platform_package, spelling, info)

    hook_keys = set(platform_package.PlatformHookRegistry._option_specs) | set(
        platform_package.PlatformHookRegistry._config_builders
    )
    unknown_hook_keys = sorted(hook_keys - manifest_keys - set(manifest_aliases))
    if unknown_hook_keys:
        errors.append(f"platform hook keys missing from manifest: {', '.join(unknown_hook_keys)}")

    unknown_guided_credentials = sorted(set(_GUIDED_CREDENTIAL_PLATFORMS) - manifest_keys)
    if unknown_guided_credentials:
        errors.append(f"guided credential platforms missing from manifest: {', '.join(unknown_guided_credentials)}")

    format_keys = set(PLATFORM_FORMAT_PREFERENCES) | set(EXTERNAL_PLATFORM_FORMAT_PREFERENCES)
    for capability in CAPABILITIES_REGISTRY.values():
        format_keys.update(capability.supported_platforms)
    unknown_format_keys = sorted(format_keys - manifest_keys - set(manifest_aliases))
    if unknown_format_keys:
        errors.append(f"format capability platforms missing from manifest: {', '.join(unknown_format_keys)}")

    for alias, target in sorted(TUNING_ALIASES.items()):
        canonical_target = manifest_aliases.get(target, target)
        if canonical_target not in manifest_keys:
            errors.append(f"tuning alias {alias!r} targets unknown platform {target!r}")


def validate_platform_surfaces() -> list[str]:
    errors: list[str] = []
    _validate_registry_projections(errors)
    _validate_adapter_sources(errors)
    _validate_semantic_surfaces(errors)
    return sorted(errors)


def _print_errors(errors: Iterable[str]) -> None:
    for error in errors:
        print(f"platform-manifest drift: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--check", action="store_true", help="Validate registry projections")
    parser.parse_args()

    errors = validate_platform_surfaces()
    if errors:
        _print_errors(errors)
        return 1

    print(f"platform-manifest check: {len(PLATFORM_MANIFEST)} canonical records and all projections are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
