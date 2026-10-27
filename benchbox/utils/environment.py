from __future__ import annotations

import json
import os
import platform
import subprocess
from typing import Tuple

_ARM_IMPLEMENTERS: dict[str, str] = {
    "0x41": "ARM",
    "0x43": "Cavium",
    "0x48": "HiSilicon",
    "0x51": "Qualcomm",
    "0x61": "Apple",
    "0xc0": "Ampere",
}


_ARM_PARTS: dict[str, str] = {
    "0xd0c": "Neoverse-N1",
    "0xd40": "Neoverse-V1",
    "0xd49": "Neoverse-N2",
    "0xd4f": "Neoverse-V2",
}

_CPU_ARCHITECTURE_TOKENS = frozenset(
    {
        "aarch64",
        "amd64",
        "arm",
        "arm64",
        "armv6l",
        "armv7l",
        "armv8",
        "i386",
        "i486",
        "i586",
        "i686",
        "mips",
        "mips64",
        "ppc",
        "ppc64",
        "ppc64le",
        "riscv64",
        "s390x",
        "x86",
        "x86_64",
    }
)


def is_cpu_architecture_token(value: str, machine: str) -> bool:
    cleaned = value.strip().lower()
    if not cleaned:
        return True
    if cleaned in {machine.strip().lower(), f"{machine.strip().lower()} cpu", "unknown cpu"}:
        return True
    return cleaned in _CPU_ARCHITECTURE_TOKENS


def detect_cpu_info() -> Tuple[str | None, str | None]:
    sys_name = platform.system()
    if sys_name == "Darwin":
        model, vendor = _detect_darwin_cpu()
    elif sys_name == "Linux":
        model, vendor = _detect_linux_cpu()
    elif sys_name == "Windows":
        model, vendor = _detect_windows_cpu()
    else:
        return None, None
    if model and is_cpu_architecture_token(model, platform.machine()):
        model = None
    return model, vendor


def _detect_darwin_cpu() -> Tuple[str | None, str | None]:
    try:
        res = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            model = res.stdout.strip()
            vendor = None
            if model.startswith("Apple"):
                vendor = "Apple"
            elif "Intel" in model:
                vendor = "Intel"
            elif "AMD" in model:
                vendor = "AMD"
            return model, vendor
    except Exception:
        pass
    return None, None


def _resolve_linux_vendor(vendor_id: str | None, implementer: str | None, model_name: str | None) -> str | None:
    if vendor_id:
        v_lower = vendor_id.lower()
        if "intel" in v_lower:
            return "Intel"
        if "amd" in v_lower:
            return "AMD"
    if implementer:
        arm_vendor = _ARM_IMPLEMENTERS.get(implementer)
        if arm_vendor:
            return arm_vendor
    if model_name:
        for known in ("Intel", "AMD", "Apple"):
            if known in model_name:
                return known
        if "Neoverse" in model_name:
            return "ARM"
    return None


def _detect_linux_cpu() -> Tuple[str | None, str | None]:
    try:
        if not os.path.exists("/proc/cpuinfo"):
            return None, None

        model_name: str | None = None
        vendor_id: str | None = None
        implementer: str | None = None
        part: str | None = None

        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, val = [x.strip() for x in line.split(":", 1)]
                k_lower = key.lower()
                if k_lower == "model name" and not model_name:
                    model_name = val
                elif k_lower == "vendor_id" and not vendor_id:
                    vendor_id = val
                elif k_lower == "cpu implementer" and not implementer:
                    implementer = val.lower()
                elif k_lower == "cpu part" and not part:
                    part = val.lower()

        if not model_name and implementer == "0x41" and part:
            model_name = _ARM_PARTS.get(part)

        vendor = _resolve_linux_vendor(vendor_id, implementer, model_name)
        return model_name, vendor
    except Exception:
        pass
    return None, None


def _detect_windows_cpu() -> Tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Processor | Select-Object -First 1 Name,Manufacturer | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            payload = json.loads(result.stdout)
            model = str(payload.get("Name") or "").strip() or None
            manufacturer = str(payload.get("Manufacturer") or "").strip()
            vendor = None
            if "intel" in manufacturer.lower() or "intel" in (model or "").lower():
                vendor = "Intel"
            elif "amd" in manufacturer.lower() or "amd" in (model or "").lower():
                vendor = "AMD"
            elif manufacturer:
                vendor = manufacturer
            return model, vendor
    except Exception:
        pass
    return None, None


EXECUTION_CLASS_NATIVE = "native"
EXECUTION_CLASS_CONTAINER = "container"
EXECUTION_CLASSES = (EXECUTION_CLASS_NATIVE, EXECUTION_CLASS_CONTAINER)
CONTAINER_HOST_OS_ENV = "BENCHBOX_CONTAINER_HOST_OS"

_CONTAINER_MARKER_FILES = (("/.dockerenv", "docker"), ("/run/.containerenv", "podman"))
_CONTAINER_CGROUP_TOKENS = (
    ("kubepods", "kubernetes"),
    ("docker", "docker"),
    ("libpod", "podman"),
    ("containerd", "containerd"),
    ("lxc", "lxc"),
)


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(65536)
    except OSError:
        return ""


def _container_runtime() -> str | None:
    for marker, runtime in _CONTAINER_MARKER_FILES:
        if os.path.exists(marker):
            return runtime
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    cgroup = _read_text("/proc/1/cgroup").lower()
    for token, runtime in _CONTAINER_CGROUP_TOKENS:
        if token in cgroup:
            return runtime
    return None


def detect_execution_class() -> dict[str, str]:
    if platform.system() != "Linux":
        return {"execution_class": EXECUTION_CLASS_NATIVE, "execution_class_source": "observed"}
    runtime = _container_runtime()
    apple_guest = detect_cpu_info()[1] == "Apple"
    if runtime is None and not apple_guest:
        return {"execution_class": EXECUTION_CLASS_NATIVE, "execution_class_source": "inferred"}
    detected = {
        "execution_class": EXECUTION_CLASS_CONTAINER,
        "execution_class_source": "observed" if runtime else "inferred",
    }
    if runtime:
        detected["container_runtime"] = runtime
    host_os = os.environ.get(CONTAINER_HOST_OS_ENV, "").strip() or ("macOS" if apple_guest else "")
    if host_os:
        detected["container_host_os"] = host_os
    return detected
