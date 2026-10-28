import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _chain_next_sitecustomize():
    for entry in sys.path:
        if os.path.abspath(entry or os.getcwd()) == _here:
            continue
        candidate = os.path.join(entry or os.getcwd(), "sitecustomize.py")
        if os.path.isfile(candidate):
            _load("_chained_sitecustomize", candidate)
            return


def _install_guard():
    if not os.environ.get("BENCHBOX_NETWORK_GUARD_DIR"):
        return
    module = _load("_benchbox_network_guard", os.path.join(_here, "..", "network_guard.py"))
    if module is not None and module.armed_log_path() is not None:
        module.install()


_install_guard()
_chain_next_sitecustomize()
