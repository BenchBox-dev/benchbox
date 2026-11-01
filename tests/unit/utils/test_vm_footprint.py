from __future__ import annotations

import io
import os

import psutil
import pytest

from benchbox.utils import heavy_watchdog as hw, process_tree as pt, vm_footprint as vf

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GIB = 2**30
APP = "/Users/joe/Library/Application Support/com.apple.container/plugin-state/machine-apiserver/machines"
WRAPPER_ARGV = ["bash", "scripts/linux_machine_run.sh", "benchbox-agent", "--", "pytest"]


def listing(machine: str) -> list[str]:
    return [
        "/System/Library/Frameworks/Virtualization.framework/Versions/A/XPCServices/vm.xpc/Contents/MacOS/vm",
        f"{APP}/{machine}/rootfs.ext4",
        f"{APP}/{machine}",
        f"{APP}/{machine}/sbin.machine/init",
        "/Users/joe/Developer/BenchBox/scripts/linux_machine_run.sh",
    ]


def attributor(
    paths: dict[int, list[str] | None],
    footprints: dict[int, int | None],
    cmdlines: dict[int, list[str]] | None = None,
    configured: float | None = 8.0,
) -> vf.VmAttributor:
    return vf.VmAttributor(
        process_lister=lambda: [vf.VmProcess(pid, 1.0) for pid in paths],
        open_paths=lambda pid: paths[pid],
        footprint_reader=lambda pid: footprints.get(pid),
        configured_gib=lambda machine: configured,
        cmdline_reader=lambda pid: (cmdlines or {}).get(pid),
    )


def test_lsof_names_are_parsed_from_field_output() -> None:
    assert vf.parse_lsof_names("p49810\nfcwd\nn/\nftxt\nn/a/b\nf3\n") == ["/", "/a/b"]


def test_machine_is_taken_from_the_exact_path_segment() -> None:
    attr = attributor({1: listing("benchbox-agent")}, {1: 5 * GIB})
    assert attr.machine_pids("benchbox-agent") == (1,)
    assert attr.machine_pids("benchbox") == ()
    assert attr.machine_pids("benchbox-agent-2") == ()
    assert attr.machine_pids("ss-dev") == ()


def test_prefix_similar_machine_is_not_matched() -> None:
    attr = attributor({1: listing("benchbox-agent-cbd539")}, {1: GIB})
    assert attr.machine_pids("benchbox-agent") == ()
    assert attr.machine_pids("benchbox-agent-cbd539") == (1,)


def test_a_vm_naming_two_machines_is_rejected() -> None:
    attr = attributor({1: listing("benchbox-agent") + listing("ss-dev")}, {1: GIB})
    assert attr.machine_pids("benchbox-agent") == ()
    assert attr.machine_pids("ss-dev") == ()


def test_paths_outside_the_apiserver_layout_are_rejected() -> None:
    attr = attributor({1: ["/tmp/machines/benchbox-agent/rootfs.ext4", "/machines/benchbox-agent/x"]}, {1: GIB})
    assert attr.machine_pids("benchbox-agent") == ()


def test_an_unreadable_lsof_is_not_cached_and_matches_nothing() -> None:
    paths: dict[int, list[str] | None] = {1: None}
    attr = attributor(paths, {1: GIB})
    assert attr.machine_pids("benchbox-agent") == ()
    paths[1] = listing("benchbox-agent")
    assert attr.machine_pids("benchbox-agent") == (1,)


def test_mapping_is_cached_per_process_identity() -> None:
    calls: list[int] = []

    def open_paths(pid: int) -> list[str]:
        calls.append(pid)
        return listing("benchbox-agent")

    attr = vf.VmAttributor(process_lister=lambda: [vf.VmProcess(7, 1.0)], open_paths=open_paths)
    attr.machine_pids("benchbox-agent")
    attr.machine_pids("benchbox-agent")
    assert calls == [7]


def test_two_machines_get_their_own_footprint() -> None:
    attr = attributor(
        {1: listing("benchbox-agent"), 2: listing("ss-dev")},
        {1: 7 * GIB, 2: 3 * GIB},
        {10: WRAPPER_ARGV},
    )
    reading = attr.attribute([10], "benchbox-agent")
    assert reading is not None
    assert reading.pids == (1,)
    assert reading.footprint_bytes == 7 * GIB
    assert attr.attribute([10], "ss-dev") is None


def test_only_a_tree_containing_the_wrapper_for_the_machine_is_attributed() -> None:
    other_machine = ["bash", "scripts/linux_machine_run.sh", "ss-dev", "--", "pytest"]
    unrelated = ["container", "machine", "run", "-n", "benchbox-agent", "--", "pytest"]
    attr = attributor(
        {1: listing("benchbox-agent")},
        {1: GIB},
        {10: WRAPPER_ARGV, 11: other_machine, 12: unrelated, 13: ["python"]},
    )
    assert attr.attribute([10, 13], "benchbox-agent") is not None
    assert attr.attribute([11, 13], "benchbox-agent") is None
    assert attr.attribute([12, 13], "benchbox-agent") is None
    assert attr.attribute([13], "benchbox-agent") is None
    assert attr.attribute([], "benchbox-agent") is None


def test_allowance_defaults_to_configured_memory_plus_ten_percent() -> None:
    attr = attributor({1: listing("benchbox-agent")}, {1: 9 * GIB})
    reading = attr.read("benchbox-agent", {})
    assert reading is not None
    assert reading.allowance_bytes == int(8.0 * 1.1 * GIB)
    assert reading.over_allowance


def test_allowance_env_overrides_the_default() -> None:
    attr = attributor({1: listing("benchbox-agent")}, {1: 9 * GIB})
    reading = attr.read("benchbox-agent", {vf.VM_ALLOWANCE_GIB_ENV: "12"})
    assert reading is not None
    assert reading.allowance_bytes == 12 * GIB
    assert not reading.over_allowance


def test_invalid_allowance_env_falls_back_to_the_default() -> None:
    attr = attributor({1: listing("benchbox-agent")}, {1: GIB})
    reading = attr.read("benchbox-agent", {vf.VM_ALLOWANCE_GIB_ENV: "-3"})
    assert reading is not None
    assert reading.allowance_bytes == int(8.0 * 1.1 * GIB)


def test_unknown_configured_memory_never_breaches() -> None:
    attr = attributor({1: listing("benchbox-agent")}, {1: 64 * GIB}, configured=None)
    reading = attr.read("benchbox-agent", {})
    assert reading is not None
    assert reading.allowance_bytes is None
    assert not reading.over_allowance
    assert vf.vm_breach_detail(reading) is None
    assert "allowance unknown" in vf.vm_footprint_line(reading)


def test_an_unmeasurable_vm_yields_no_reading() -> None:
    assert attributor({1: listing("benchbox-agent")}, {1: None}).read("benchbox-agent", {}) is None


def test_no_vm_process_yields_no_reading() -> None:
    assert attributor({}, {}).read("benchbox-agent", {}) is None


def test_footprint_line_names_the_machine_and_the_budget_exclusion() -> None:
    reading = vf.VmReading("benchbox-agent", (49810,), int(7.83 * GIB), int(8.8 * GIB))
    line = vf.vm_footprint_line(reading)
    assert "VM benchbox-agent footprint 7.83 GiB" in line
    assert "pid 49810" in line
    assert "not counted in the shared budget" in line


def sample(total: int) -> pt.TreeSample:
    return pt.TreeSample(1, (pt.TreeMember(1, 0, "holder", 1.0, 0, total),), total)


def policy(budget_gib: float, mode: str = "kill") -> hw.WatchPolicy:
    return hw.WatchPolicy(
        holder_pid=os.getpid(),
        holder_created=psutil.Process().create_time(),
        budget_bytes=int(budget_gib * GIB),
        mode=mode,
        kill_free_disk_gib=0.0,
    )


def test_vm_over_allowance_breaches_with_its_own_signal() -> None:
    vm = vf.VmReading("benchbox-agent", (5,), 9 * GIB, int(8.8 * GIB))
    breaches = hw.evaluate_breaches(GIB, 1, [], policy(budget_gib=4.0), vm)
    assert [b.signal for b in breaches] == ["VM footprint (benchbox-agent)"]
    assert "VM allowance" in breaches[0].limit
    assert "9.00 GiB" in breaches[0].measured


def test_vm_below_allowance_never_breaches_even_above_the_shared_budget() -> None:
    vm = vf.VmReading("benchbox-agent", (5,), 8 * GIB, int(8.8 * GIB))
    assert hw.evaluate_breaches(GIB, 1, [], policy(budget_gib=2.0), vm) == []


def test_vm_footprint_is_not_added_to_the_shared_budget() -> None:
    vm = vf.VmReading("benchbox-agent", (5,), 100 * GIB, None)
    assert hw.evaluate_breaches(GIB, 1, [], policy(budget_gib=2.0), vm) == []


def test_warn_mode_reports_the_vm_line_once_and_keeps_the_tree() -> None:
    vm = vf.VmReading("benchbox-agent", (5,), 9 * GIB, int(8.8 * GIB))
    out = io.StringIO()
    dog = hw.Watchdog(
        policy(budget_gib=4.0, mode="warn"),
        sampler=lambda pid, exclude=(): sample(GIB),
        pressure_reader=lambda: 1,
        disk_reader=lambda roots: [],
        vm_reader=lambda s: vm,
        out=out,
    )
    dog.step()
    dog.step()
    text = out.getvalue()
    assert text.count("BREACH VM footprint (benchbox-agent)") == 1
    assert "warn only" in text
    assert "VM benchbox-agent footprint 9.00 GiB" in text


def test_a_clean_step_with_a_vm_logs_nothing() -> None:
    vm = vf.VmReading("benchbox-agent", (5,), GIB, int(8.8 * GIB))
    out = io.StringIO()
    dog = hw.Watchdog(
        policy(budget_gib=4.0),
        sampler=lambda pid, exclude=(): sample(GIB),
        pressure_reader=lambda: 1,
        disk_reader=lambda roots: [],
        vm_reader=lambda s: vm,
        out=out,
    )
    assert dog.step()
    assert out.getvalue() == ""


def test_a_negative_mapping_is_cached_only_once_the_process_is_old() -> None:
    calls: list[int] = []
    now = {"t": 0.0}

    def open_paths(pid: int) -> list[str]:
        calls.append(pid)
        return ["/usr/lib/x"]

    attr = vf.VmAttributor(process_lister=lambda: [vf.VmProcess(7, 1.0)], open_paths=open_paths, clock=lambda: now["t"])
    attr.machine_pids("benchbox-agent")
    now["t"] = 10.0
    attr.machine_pids("benchbox-agent")
    assert calls == [7, 7]
    now["t"] = 31.0
    attr.machine_pids("benchbox-agent")
    attr.machine_pids("benchbox-agent")
    assert calls == [7, 7, 7]


def test_a_vm_process_that_gains_its_machine_files_later_is_matched() -> None:
    paths: dict[int, list[str]] = {1: ["/usr/lib/x"]}
    attr = vf.VmAttributor(
        process_lister=lambda: [vf.VmProcess(1, 1.0)], open_paths=lambda pid: paths[pid], clock=lambda: 0.0
    )
    assert attr.machine_pids("benchbox-agent") == ()
    paths[1] = listing("benchbox-agent")
    assert attr.machine_pids("benchbox-agent") == (1,)


def test_the_wrapper_matcher_and_machine_name_live_in_the_stdlib_module() -> None:
    assert vf.argv_runs_wrapper(WRAPPER_ARGV, "benchbox-agent")
    assert not vf.argv_runs_wrapper(WRAPPER_ARGV, "benchbox-agent-2")
    assert vf.machine_name_from_env({}) == vf.DEFAULT_MACHINE
    assert vf.machine_name_from_env({vf.MACHINE_ENV: " x "}) == "x"
