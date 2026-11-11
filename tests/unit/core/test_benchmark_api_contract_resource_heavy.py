from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]


def test_benchmark_registry_import_does_not_instantiate_probe_benchmarks() -> None:

    script = r"""
import importlib

orig = importlib.import_module
calls = []

class CallableProxy:
    def __init__(self, cls, module_name, attr):
        self._cls = cls
        self._module_name = module_name
        self._attr = attr
        self.__name__ = getattr(cls, "__name__", attr)
    def __call__(self, *args, **kwargs):
        calls.append((self._module_name, self._attr, kwargs))
        return self._cls(*args, **kwargs)
    def __getattr__(self, name):
        return getattr(self._cls, name)

class ModuleProxy:
    def __init__(self, module):
        self._module = module
        self.__name__ = module.__name__
    def __getattr__(self, name):
        attr = getattr(self._module, name)
        if isinstance(attr, type):
            return CallableProxy(attr, self._module.__name__, name)
        return attr

def wrapped(name, package=None):
    mod = orig(name, package)
    if name.startswith("benchbox.core.") and name.endswith(".benchmark"):
        return ModuleProxy(mod)
    return mod

importlib.import_module = wrapped
import benchbox.core.benchmark_registry
print(len(calls))
"""
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "0"


