from __future__ import annotations

import ipaddress
import os
import socket
import threading
from typing import Any

ENV_DIR = "BENCHBOX_NETWORK_GUARD_DIR"
SWITCH_NAME = "armed"

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
_originals: dict[str, Any] = {}
_lock = threading.Lock()
_violations: list[str] = []


class NetworkAccessBlocked(RuntimeError):
    pass


def _message(action: str, target: str) -> str:
    return (
        f"BenchBox network guard: {action} to non-loopback target {target!r} is blocked in unit/medium tests. "
        "Fake the dependency at a boundary the code under test owns (an injectable URL, source, or function) "
        "or serve it from a real loopback server; do not patch stdlib or third-party internals. "
        "Tests that truly need the network belong in the integration or live_integration tiers."
    )


def _host_is_local(host: Any) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    name = host.strip().strip("[]").split("%", 1)[0].lower().rstrip(".")
    if name == "" or name in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified


def _address_is_local(address: Any) -> bool:
    if isinstance(address, (str, bytes)):
        return True
    if isinstance(address, tuple) and address:
        return _host_is_local(address[0])
    return True


def _block(action: str, target: Any) -> None:
    text = _message(action, str(target))
    with _lock:
        _violations.append(text)
    log = armed_log_path()
    if log:
        try:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass
    raise NetworkAccessBlocked(text)


def _wrap_address_method(name: str) -> None:
    original = getattr(socket.socket, name)
    _originals[f"socket.{name}"] = original

    def guarded(self: socket.socket, *args: Any, **kwargs: Any) -> Any:
        address = args[-1] if args else kwargs.get("address")
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _address_is_local(address):
            _block(name, address)
        return original(self, *args, **kwargs)

    guarded.__name__ = name
    setattr(socket.socket, name, guarded)


def _wrap_host_function(name: str) -> None:
    original = getattr(socket, name)
    _originals[f"module.{name}"] = original

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _host_is_local(host):
            _block(name, host)
        return original(host, *args, **kwargs)

    guarded.__name__ = name
    setattr(socket, name, guarded)


def install() -> None:
    if _originals:
        return
    for method in ("connect", "connect_ex", "sendto"):
        _wrap_address_method(method)
    for function in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        _wrap_host_function(function)


def uninstall() -> None:
    for key, original in _originals.items():
        kind, name = key.split(".", 1)
        setattr(socket.socket if kind == "socket" else socket, name, original)
    _originals.clear()


def take_violations() -> list[str]:
    with _lock:
        taken = list(_violations)
        _violations.clear()
    return taken


def armed_log_path() -> str | None:
    directory = os.environ.get(ENV_DIR)
    if not directory:
        return None
    try:
        with open(os.path.join(directory, SWITCH_NAME), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def site_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "network_guard_site")
