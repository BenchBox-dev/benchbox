from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


class TestLazyImports:
    def test_package_import_does_not_load_gizmosql_or_adbc(self):
        code = """
import json, sys
import benchbox, benchbox.platforms
print(json.dumps(sorted(m for m in sys.modules if m.startswith(('adbc_driver', 'gizmosql')))))
"""
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

        assert json.loads(completed.stdout) == []

    def test_adapter_module_import_does_not_load_adbc(self):
        code = """
import json, sys
import benchbox.platforms.gizmosql
print(json.dumps(sorted(m for m in sys.modules if m.startswith(('adbc_driver', 'gizmosql_')))))
"""
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

        assert json.loads(completed.stdout) == []
