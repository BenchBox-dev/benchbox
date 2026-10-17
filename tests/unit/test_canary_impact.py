from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import canary_impact
from scripts.canary_impact import (
    CANT_AFFECT_CANARY,
    MEDIUM_WHOLE_SUITE_PATHS,
    WHOLE_SUITE_PATHS,
    FileDeps,
    _resolve_plugin_file,
    build_dependency_map,
    compute_selection,
    files_from_node_ids,
    medium_relevant_paths,
)
from scripts.release_canary_sharding import MARKER_EXPRESSION as SHARDING_MARKER

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _fixture_root(tmp_path: Path, files: dict[str, str]) -> Path:
    merged = {"tests/conftest.py": "", **files}
    _write_tree(tmp_path, merged)
    return tmp_path


def _select(root: Path, test_files: list[str], node_ids: list[str], changed: list[str]) -> dict:
    dep_map, fallback, _sites = build_dependency_map(root, test_files)
    assert fallback is None, fallback
    return compute_selection(changed, dep_map, node_ids)


def _nodes(test_file: str, names: list[str]) -> list[str]:
    return [f"{test_file}::{name}" for name in names]


class TestDependencyRules:
    def test_static_import_includes_parent_init(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "from . import base\n",
                "mypkg/base.py": "VALUE = 1\n",
                "mypkg/sub.py": "from mypkg import base\n",
                "tests/test_x.py": "import mypkg.sub\n\ndef test_a(): ...\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        deps = dep_map["tests/test_x.py"].deps
        assert "mypkg/sub.py" in deps
        assert "mypkg/__init__.py" in deps

        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/__init__.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_importlib_literal_is_a_dependency(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "",
                "mypkg/sub.py": "VALUE = 1\n",
                "tests/test_x.py": 'import importlib\nmod = importlib.import_module("mypkg.sub")\n',
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert "mypkg/sub.py" in dep_map["tests/test_x.py"].deps

    def test_repo_root_join_is_a_dependency(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "data/spec.yaml": "version: 1\n",
                "tests/test_x.py": (
                    "from pathlib import Path\n"
                    "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
                    'SPEC = REPO_ROOT / "data" / "spec.yaml"\n'
                ),
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert "data/spec.yaml" in dep_map["tests/test_x.py"].deps

        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["data/spec.yaml"])
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]
        reasons = selection["selected"][0]["reasons"]
        assert {"changed_path": "data/spec.yaml", "edge": "dependency"} in reasons

    def test_with_name_selects_sibling(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/helper.py": "VALUE = 1\n",
                "tests/test_x.py": ('from pathlib import Path\nHELPER = Path(__file__).with_name("helper.py")\n'),
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert "tests/helper.py" in dep_map["tests/test_x.py"].deps

    def test_directory_dependency_covers_files_under_it(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "datadir/a.txt": "a\n",
                "datadir/nested/b.txt": "b\n",
                "tests/test_x.py": (
                    "from pathlib import Path\n"
                    "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
                    'DATA = REPO_ROOT / "datadir"\n'
                ),
            },
        )
        selection = _select(
            root,
            ["tests/test_x.py"],
            _nodes("tests/test_x.py", ["test_a"]),
            ["datadir/nested/b.txt"],
        )
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_pytest_plugins_apply_to_every_test(self, tmp_path: Path) -> None:
        root = tmp_path
        _write_tree(
            root,
            {
                "tests/conftest.py": 'pytest_plugins = ["myplug"]\n',
                "myplug.py": "import mypkg.base\n",
                "mypkg/__init__.py": "",
                "mypkg/base.py": "VALUE = 1\n",
                "tests/test_x.py": "def test_a(): ...\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        deps = dep_map["tests/test_x.py"].deps
        assert "myplug.py" in deps
        assert "mypkg/base.py" in deps
        assert "tests/conftest.py" in deps

    def test_same_directory_non_python_selects_importers(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "benchbox/widget/__init__.py": "",
                "benchbox/widget/engine.py": "VALUE = 1\n",
                "benchbox/widget/spec.yaml": "version: 1\n",
                "benchbox/other/__init__.py": "",
                "benchbox/other/engine.py": "VALUE = 2\n",
                "benchbox/other/spec.yaml": "version: 2\n",
                "tests/test_widget.py": "import benchbox.widget.engine\n",
                "tests/test_other.py": "import benchbox.other.engine\n",
            },
        )
        test_files = ["tests/test_widget.py", "tests/test_other.py"]
        node_ids = _nodes("tests/test_widget.py", ["test_a"]) + _nodes("tests/test_other.py", ["test_b"])
        selection = _select(root, test_files, node_ids, ["benchbox/widget/spec.yaml"])
        assert selection["whole_suite"] is False
        selected = {s["node_id"]: s["reasons"] for s in selection["selected"]}
        assert "tests/test_widget.py::test_a" in selected
        assert selected["tests/test_widget.py::test_a"] == [
            {"changed_path": "benchbox/widget/spec.yaml", "edge": "same_directory"}
        ]
        assert "tests/test_other.py::test_b" not in selected

    def test_transitive_imports_are_dependencies(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "",
                "mypkg/leaf.py": "VALUE = 1\n",
                "mypkg/mid.py": "import mypkg.leaf\n",
                "tests/test_x.py": "import mypkg.mid\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert "mypkg/leaf.py" in dep_map["tests/test_x.py"].deps
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/leaf.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_reexported_name_is_a_dependency(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "from mypkg.impl import Thing\n",
                "mypkg/impl.py": "class Thing: ...\n",
                "tests/test_x.py": "from mypkg import Thing\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert not dep_map["tests/test_x.py"].dynamic
        assert "mypkg/impl.py" in dep_map["tests/test_x.py"].deps
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/impl.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_string_script_import_is_a_dependency(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "",
                "mypkg/sub.py": "VALUE = 1\n",
                "tests/test_x.py": (
                    "import subprocess\nimport sys\n"
                    "SCRIPT = '''\nimport mypkg.sub\nprint(mypkg.sub.VALUE)\n'''\n"
                    "def test_a():\n"
                    "    subprocess.run([sys.executable, '-c', SCRIPT], check=True)\n"
                ),
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert not dep_map["tests/test_x.py"].dynamic
        assert "mypkg/sub.py" in dep_map["tests/test_x.py"].deps
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/sub.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_conftest_chain_applies_per_directory(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/unit/conftest.py": "import mypkg.unit_helper\n",
                "mypkg/__init__.py": "",
                "mypkg/unit_helper.py": "VALUE = 1\n",
                "tests/unit/test_x.py": "def test_a(): ...\n",
                "tests/other/test_y.py": "def test_b(): ...\n",
            },
        )
        test_files = ["tests/unit/test_x.py", "tests/other/test_y.py"]
        node_ids = _nodes("tests/unit/test_x.py", ["test_a"]) + _nodes("tests/other/test_y.py", ["test_b"])
        dep_map, fallback, _sites = build_dependency_map(root, test_files)
        assert fallback is None
        assert "tests/unit/conftest.py" in dep_map["tests/unit/test_x.py"].deps
        assert "mypkg/unit_helper.py" in dep_map["tests/unit/test_x.py"].deps
        assert "tests/unit/conftest.py" not in dep_map["tests/other/test_y.py"].deps
        selection = _select(root, test_files, node_ids, ["mypkg/unit_helper.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/unit/test_x.py::test_a"]

    def test_package_plugin_resolves_exact_file(self, tmp_path: Path) -> None:
        root = tmp_path
        _write_tree(
            root,
            {
                "tests/conftest.py": 'pytest_plugins = ["pkg.plug"]\n',
                "pkg/__init__.py": "MARKER = 1\n",
                "pkg/plug.py": "import mypkg.leaf\n",
                "mypkg/__init__.py": "",
                "mypkg/leaf.py": "VALUE = 1\n",
                "tests/test_x.py": "def test_a(): ...\n",
            },
        )
        assert _resolve_plugin_file("pkg.plug", root / "tests" / "conftest.py", root) == "pkg/plug.py"
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        deps = dep_map["tests/test_x.py"].deps
        assert "pkg/plug.py" in deps
        assert "pkg/__init__.py" in deps
        assert "mypkg/leaf.py" in deps

    def test_from_import_submodule_of_regular_package(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "MARKER = 1\n",
                "mypkg/sub.py": "VALUE = 1\n",
                "tests/test_x.py": "from mypkg import sub\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert "mypkg/sub.py" in dep_map["tests/test_x.py"].deps
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/sub.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_changed_canary_test_selects_itself(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {"tests/test_x.py": "def test_a(): ...\n", "tests/test_y.py": "def test_b(): ...\n"},
        )
        test_files = ["tests/test_x.py", "tests/test_y.py"]
        node_ids = _nodes("tests/test_x.py", ["test_a"]) + _nodes("tests/test_y.py", ["test_b"])
        selection = _select(root, test_files, node_ids, ["tests/test_y.py"])
        assert selection["whole_suite"] is False
        selected = {s["node_id"]: s["reasons"] for s in selection["selected"]}
        assert selected == {"tests/test_y.py::test_b": [{"changed_path": "tests/test_y.py", "edge": "self"}]}

    def test_no_changes_selects_nothing(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {"tests/test_x.py": "import importlib\nmod = importlib.import_module(name)\n"},
        )
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), [])
        assert selection["whole_suite"] is False
        assert selection["selected"] == []
        assert selection["selected_count"] == 0


class TestFailSafeRules:
    def test_dynamic_import_always_selects(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "",
                "mypkg/base.py": "VALUE = 1\n",
                "tests/test_x.py": "import importlib\nimport mypkg.base\nmod = importlib.import_module(name)\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert dep_map["tests/test_x.py"].dynamic
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/base.py"])
        assert selection["whole_suite"] is False
        reasons = selection["selected"][0]["reasons"]
        assert {"changed_path": "mypkg/base.py", "edge": "dependency"} in reasons
        assert {"changed_path": "mypkg/base.py", "edge": "unresolvable_dynamic_edge"} in reasons

    def test_dynamic_path_from_constant_selects(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/test_x.py": (
                    "from pathlib import Path\n"
                    "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
                    "def load(name):\n"
                    "    return open(REPO_ROOT / name).read()\n"
                ),
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert dep_map["tests/test_x.py"].dynamic

    def test_transitive_dynamic_import_is_reported_and_selects_test(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "",
                "mypkg/helper.py": "import importlib\nmod = importlib.import_module(name)\n",
                "tests/test_x.py": "import mypkg.helper\n",
            },
        )
        dep_map, fallback, sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert dep_map["tests/test_x.py"].dynamic
        assert any(site.startswith("mypkg/helper.py:") for site in sites)
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["mypkg/__init__.py"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_dynamic_chain_conftest_selects_test(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/unit/conftest.py": "import importlib\nmod = importlib.import_module(name)\n",
                "tests/unit/test_x.py": "def test_a(): ...\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/unit/test_x.py"])
        assert fallback is None
        assert dep_map["tests/unit/test_x.py"].dynamic

    def test_chain_conftest_plugins_apply(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/unit/conftest.py": 'pytest_plugins = ["unitplug"]\n',
                "unitplug.py": "VALUE = 1\n",
                "tests/unit/test_x.py": "def test_a(): ...\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/unit/test_x.py"])
        assert fallback is None
        assert not dep_map["tests/unit/test_x.py"].dynamic
        assert "unitplug.py" in dep_map["tests/unit/test_x.py"].deps

    def test_unresolvable_chain_plugin_selects_test(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/unit/conftest.py": 'pytest_plugins = ["missing_plug"]\n',
                "tests/unit/test_x.py": "def test_a(): ...\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/unit/test_x.py"])
        assert fallback is None
        assert dep_map["tests/unit/test_x.py"].dynamic

    def test_unresolvable_external_import_is_ignored(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/test_x.py": (
                    "try:\n"
                    "    import cupy_xyz_not_a_module\n"
                    "    HAS_CUPY = True\n"
                    "except ImportError:\n"
                    "    HAS_CUPY = False\n"
                ),
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert not dep_map["tests/test_x.py"].dynamic

    def test_unresolvable_repo_named_import_selects(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "tests/helper_repo_named.py": "VALUE = 1\n",
                "tests/test_x.py": "import helper_repo_named\n",
            },
        )
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        assert dep_map["tests/test_x.py"].dynamic

    @pytest.mark.parametrize("trigger", sorted(WHOLE_SUITE_PATHS))
    def test_whole_suite_triggers(self, tmp_path: Path, trigger: str) -> None:
        root = _fixture_root(tmp_path, {"tests/test_x.py": "def test_a(): ...\n"})
        node_ids = _nodes("tests/test_x.py", ["test_a", "test_b"])
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        selection = compute_selection([trigger], dep_map, node_ids)
        assert selection["whole_suite"] is True
        assert trigger in selection["whole_suite_reason"]
        assert selection["selected_count"] == 2
        for selected in selection["selected"]:
            assert {"changed_path": trigger, "edge": "whole_suite"} in selected["reasons"]

    def test_unmapped_path_runs_whole_suite(self, tmp_path: Path) -> None:
        root = _fixture_root(tmp_path, {"tests/test_x.py": "def test_a(): ...\n"})
        node_ids = _nodes("tests/test_x.py", ["test_a"])
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        selection = compute_selection(["some/random/file.txt"], dep_map, node_ids)
        assert selection["whole_suite"] is True
        assert selection["unmapped_changed_paths"] == ["some/random/file.txt"]
        assert selection["selected_count"] == 1

    def test_safe_listed_path_selects_nothing(self, tmp_path: Path) -> None:
        assert CANT_AFFECT_CANARY, "the reviewed safe list must not be empty"
        root = _fixture_root(tmp_path, {"tests/test_x.py": "def test_a(): ...\n"})
        node_ids = _nodes("tests/test_x.py", ["test_a"])
        dep_map, fallback, _sites = build_dependency_map(root, ["tests/test_x.py"])
        assert fallback is None
        changed = sorted((CANT_AFFECT_CANARY - {"_project/audits/"}) | {"_project/audits/2026-01-01-review.md"})
        selection = compute_selection(changed, dep_map, node_ids)
        assert selection["whole_suite"] is False
        assert selection["selected"] == []

    def test_safe_listed_path_with_edge_still_selects(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "CHANGELOG.md": "notable change\n",
                "tests/test_x.py": (
                    "from pathlib import Path\n"
                    "CHANGELOG = Path(__file__).resolve().parents[1] / 'CHANGELOG.md'\n"
                    "def test_a():\n"
                    "    assert CHANGELOG.is_file()\n"
                ),
            },
        )
        selection = _select(root, ["tests/test_x.py"], _nodes("tests/test_x.py", ["test_a"]), ["CHANGELOG.md"])
        assert selection["whole_suite"] is False
        assert [s["node_id"] for s in selection["selected"]] == ["tests/test_x.py::test_a"]

    def test_json_contract_shape(self, tmp_path: Path) -> None:
        root = _fixture_root(
            tmp_path,
            {
                "mypkg/__init__.py": "",
                "mypkg/sub.py": "VALUE = 1\n",
                "tests/test_x.py": "import mypkg.sub\n",
            },
        )
        collection = tmp_path / "nodeids.txt"
        collection.write_text("tests/test_x.py::test_a\ntests/test_x.py::test_b\n", encoding="utf-8")
        output = tmp_path / "selection.json"
        rc = canary_impact.main(
            [
                "--repo-root",
                str(root),
                "--collection-file",
                str(collection),
                "--changed-path",
                "mypkg/sub.py",
                "--output",
                str(output),
            ]
        )
        assert rc == 0
        selection = json.loads(output.read_text(encoding="utf-8"))
        assert selection["marker_expression"] == canary_impact.MARKER_EXPRESSION
        assert selection["whole_suite"] is False
        assert selection["dynamic_library_sites"] == []
        assert selection["selected_count"] == 2
        assert selection["total_count"] == 2
        assert selection["collection"]["node_count"] == 2
        for selected in selection["selected"]:
            assert selected["file"] == "tests/test_x.py"
            for reason in selected["reasons"]:
                assert set(reason) == {"changed_path", "edge"}


def test_marker_expression_matches_release_canary_workflow() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release-canary.yml").read_text(encoding="utf-8"))
    shards = workflow["jobs"]["credential-free-non-fast"]
    run_text = "\n".join(str(step.get("run", "")) for step in shards["steps"])
    assert canary_impact.MARKER_EXPRESSION in run_text
    assert canary_impact.MARKER_EXPRESSION == SHARDING_MARKER


def test_medium_preflight_ignores_non_product_paths() -> None:
    changed = [
        "docs/readme.md",
        ".github/workflows/pr.yml",
        "tests/unit/test_canary_impact.py",
        "Makefile",
        "benchbox/monitoring/performance.py",
    ]
    assert medium_relevant_paths(changed) == ["benchbox/monitoring/performance.py"]
    assert medium_relevant_paths(["tests/conftest.py", "uv.lock"]) == ["tests/conftest.py", "uv.lock"]
    assert medium_relevant_paths(["examples/demo.py", "docker/compose.yml", "docs/conf.py", "docs/notes.md"]) == [
        "examples/demo.py",
        "docker/compose.yml",
        "docs/conf.py",
    ]


def test_medium_preflight_skips_collection_and_mapping_without_product_paths(tmp_path: Path) -> None:
    output = tmp_path / "selection.json"
    rc = canary_impact.main(
        [
            "--repo-root",
            str(tmp_path),
            "--changed-path",
            "tests/test_x.py",
            "--product-code-only",
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    selection = json.loads(output.read_text(encoding="utf-8"))
    assert selection["collection"]["source"] == "skipped_no_product_code"
    assert selection["whole_suite"] is False
    assert selection["selected_count"] == 0


def test_medium_profile_parameters_select_only_affected_nodes() -> None:
    node_ids = ["tests/test_x.py::test_a", "tests/test_y.py::test_b"]
    dep_map = {
        "tests/test_x.py": FileDeps(frozenset({"benchbox/x.py"}), False, ()),
        "tests/test_y.py": FileDeps(frozenset({"benchbox/y.py"}), False, ()),
    }
    selection = compute_selection(
        ["benchbox/x.py"],
        dep_map,
        node_ids,
        marker_expression="medium",
        cant_affect=frozenset(),
        whole_suite_paths=MEDIUM_WHOLE_SUITE_PATHS,
    )
    assert selection["marker_expression"] == "medium"
    assert [entry["node_id"] for entry in selection["selected"]] == ["tests/test_x.py::test_a"]


def test_cant_affect_list_is_parameterized() -> None:
    nodes = ["tests/test_x.py::test_a"]
    dep_map = {"tests/test_x.py": FileDeps(frozenset({"benchbox/x.py"}), False, ())}
    ignored = compute_selection(["docs/note.md"], dep_map, nodes, cant_affect=frozenset({"docs/"}))
    assert ignored["selected_count"] == 0
    assert ignored["whole_suite"] is False
    fallback = compute_selection(["docs/note.md"], dep_map, nodes, cant_affect=frozenset())
    assert fallback["whole_suite_reason"] == "unmapped_path:docs/note.md"


def test_shared_dynamic_import_does_not_select_every_test(tmp_path: Path) -> None:
    root = _fixture_root(
        tmp_path,
        {
            "tests/conftest.py": "import pkg.registry\n",
            "pkg/__init__.py": "",
            "pkg/registry.py": "import importlib\nmod = importlib.import_module(name)\n",
            "pkg/a.py": "VALUE = 1\n",
            "pkg/b.py": "VALUE = 2\n",
            "tests/test_a.py": "import pkg.a\n",
            "tests/test_b.py": "import pkg.b\n",
        },
    )
    files = ["tests/test_a.py", "tests/test_b.py"]
    nodes = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
    dep_map, fallback, _sites = build_dependency_map(root, files)
    assert fallback is None
    selection = compute_selection(["pkg/a.py"], dep_map, nodes)
    assert [entry["node_id"] for entry in selection["selected"]] == ["tests/test_a.py::test_a"]


def test_unresolved_library_import_selects_lazy_export_test(tmp_path: Path) -> None:
    root = _fixture_root(
        tmp_path,
        {
            "pkg/__init__.py": (
                "import importlib\n"
                "def __getattr__(name):\n"
                "    module_name = 'backing' if name == 'Resource' else name\n"
                "    return getattr(importlib.import_module(f'{__name__}.{module_name}'), name)\n"
            ),
            "pkg/backing.py": "class Resource: ...\n",
            "tests/test_lazy.py": "from pkg import Resource\n\ndef test_lazy(): ...\n",
            "tests/test_direct.py": "import pkg.backing\n\ndef test_direct(): ...\n",
        },
    )
    files = ["tests/test_lazy.py", "tests/test_direct.py"]
    nodes = ["tests/test_lazy.py::test_lazy", "tests/test_direct.py::test_direct"]
    dep_map, fallback, _sites = build_dependency_map(root, files)
    assert fallback is None
    assert "pkg/backing.py" not in dep_map["tests/test_lazy.py"].deps
    selection = compute_selection(["pkg/backing.py"], dep_map, nodes)
    assert {entry["node_id"] for entry in selection["selected"]} == set(nodes)


def test_medium_runner_uses_file_targets_and_scrubs_changed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture_root(
        tmp_path,
        {"benchbox/x.py": "VALUE = 1\n", "tests/test_x.py": "import benchbox.x\n\ndef test_x(): ...\n"},
    )
    collection = tmp_path / "nodes.txt"
    collection.write_text("tests/test_x.py::test_x\n", encoding="utf-8")
    monkeypatch.setenv("BENCHBOX_MEDIUM_CHANGED_PATHS_JSON", json.dumps(["benchbox/x.py"]))
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(canary_impact.subprocess, "run", fake_run)
    rc = canary_impact.main(
        [
            "--repo-root",
            str(root),
            "--collection-file",
            str(collection),
            "--changed-json-env",
            "BENCHBOX_MEDIUM_CHANGED_PATHS_JSON",
            "--marker-expression",
            "medium",
            "--cant-affect-list",
            "empty",
            "--product-code-only",
            "--run-selected",
            "--output",
            str(tmp_path / "selection.json"),
        ]
    )
    assert rc == 0
    assert len(calls) == 1
    command, env = calls[0]
    assert "--dist=loadfile" in command
    assert command[-1] == "tests/test_x.py"
    assert "BENCHBOX_MEDIUM_CHANGED_PATHS_JSON" not in env
