from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

PLACEHOLDER = re.compile(r"<[a-z_]+>")
RECORDED_ROOT = "/workspace"
RECORDED_CWD = "/scratch/omp-cwd"
RENDEZVOUS_SECONDS = 20


def _script(home: Path) -> dict[str, Any]:
    return json.loads((home / "script.json").read_text(encoding="utf-8"))


def _next_reply(home: Path, replies: list[dict[str, Any]], prompt: str) -> tuple[dict[str, Any], int]:
    import fcntl

    with open(home / "calls.lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counter = home / "calls.json"
        calls = json.loads(counter.read_text(encoding="utf-8")) if counter.is_file() else []
        index = len(calls)
        reply = replies[min(index, len(replies) - 1)]
        calls.append({"reply": index, "prompt": prompt, "met": None})
        counter.write_text(json.dumps(calls), encoding="utf-8")
    return reply, index


def _record_met(home: Path, index: int, met: bool) -> None:
    import fcntl

    with open(home / "calls.lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counter = home / "calls.json"
        calls = json.loads(counter.read_text(encoding="utf-8"))
        calls[index]["met"] = met
        counter.write_text(json.dumps(calls), encoding="utf-8")


def _rendezvous(home: Path, index: int, peers: int) -> bool:
    arrivals = home / "arrivals"
    arrivals.mkdir(exist_ok=True)
    (arrivals / str(index)).write_text("", encoding="utf-8")
    deadline = time.monotonic() + RENDEZVOUS_SECONDS
    while time.monotonic() < deadline:
        if len(list(arrivals.iterdir())) >= peers:
            return True
        time.sleep(0.05)
    return False


def _parent(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(stat.rsplit(")", 1)[1].split()[1])


def _record_ancestors(home: Path, probe: dict[str, Any]) -> None:
    stop = int(probe["stop"])
    chain = []
    pid = _parent(os.getpid())
    while pid != stop and pid > 1:
        chain.append(pid)
        pid = _parent(pid)
    seen = []
    for ancestor in chain:
        environ = Path(f"/proc/{ancestor}/environ").read_bytes()
        found = sorted(name for name, value in probe["values"].items() if value.encode("utf-8") in environ)
        seen.append({"pid": ancestor, "found": found})
    (home / "ancestors.json").write_text(json.dumps(seen), encoding="utf-8")


def response_template(prompt: str) -> Any:
    start = prompt.rindex('\n{\n  "status"') + 1
    template, _ = json.JSONDecoder().raw_decode(prompt, start)
    return template


def _prose(slot: str, answers: dict[str, Any]) -> Any:
    return answers.get(slot, f"{slot.replace('_', ' ')} as read in the head commit")


def _populate(value: Any, answers: dict[str, Any]) -> Any:
    if isinstance(value, str) and PLACEHOLDER.fullmatch(value):
        return _prose(value[1:-1], answers)
    if isinstance(value, list):
        return [_populate(item, answers) for item in value]
    if isinstance(value, dict):
        return {key: _populate(item, answers) for key, item in value.items()}
    return value


def fill(template: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
    answers = {
        "status": "complete",
        "incomplete_reason": "",
        "decision": reply["decision"],
        "summary": reply["summary"],
    }
    skipped = ("defects", "prior_defects", "files_examined")
    filled = {key: _populate(value, answers) for key, value in template.items() if key not in skipped}
    filled["files_examined"] = list(reply["files_examined"])
    prototype = template["defects"][0] if template["defects"] else {}
    filled["defects"] = []
    for defect in reply.get("defects", []):
        entry = _populate(
            prototype,
            {
                "severity": defect["severity"],
                "file": defect["file"],
                "title": defect["title"],
                "detail": defect["detail"],
            },
        )
        entry["line"] = defect["line"]
        filled["defects"].append(entry)
    priors = reply.get("priors", {})
    filled["prior_defects"] = []
    for entry in template["prior_defects"]:
        answer = priors.get(entry["id"], {})
        slots = {
            "prior_status": answer.get("status", "not_fixed"),
            "evidence": answer.get("evidence", ""),
            "claim": answer.get("claim", ""),
            "concedes": _spoken(answer.get("concedes", "")),
        }
        prior = _populate(entry, slots)
        if "concedes" in entry and entry["concedes"] is None and "concedes" in answer:
            prior["concedes"] = answer["concedes"]
        filled["prior_defects"].append(prior)
    return filled


def _spoken(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes, the rebuttal holds." if value else "No, the rebuttal does not hold."
    return str(value)


def _rewrite(value: Any, workspace: str, cwd: str) -> Any:
    if isinstance(value, str):
        return value.replace(RECORDED_CWD, cwd).replace(RECORDED_ROOT, workspace)
    if isinstance(value, list):
        return [_rewrite(item, workspace, cwd) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, workspace, cwd) for key, item in value.items()}
    return value


def _leaked(event: dict[str, Any], value: str) -> dict[str, Any]:
    if event.get("type") != "tool_execution_end":
        return event
    content = [*event["result"]["content"], {"type": "text", "text": f"token={value}"}]
    return {**event, "result": {**event["result"], "content": content}}


def _answer_message(message: dict[str, Any], text: str) -> dict[str, Any]:
    content = [block for block in message.get("content", []) if block.get("type") != "text"]
    return {**message, "content": [*content, {"type": "text", "text": text}]}


def _extra_reads(events: list[dict[str, Any]], workspace: str, paths: list[str]) -> list[dict[str, Any]]:
    first = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "message_end"
        and any(
            isinstance(block, dict) and block.get("name") == "read" for block in event["message"].get("content", [])
        )
    )
    last = next(
        index
        for index, event in enumerate(events)
        if index > first and event["type"] == "message_end" and event["message"].get("role") == "toolResult"
    )
    turn = events[first : last + 1]
    call = next(block for block in turn[0]["message"]["content"] if block.get("name") == "read")
    old_id, old_path = call["id"], call["arguments"]["path"]
    out: list[dict[str, Any]] = []
    for number, path in enumerate(paths):
        target = f"{workspace}/{path}"
        text = Path(target).read_text(encoding="utf-8", errors="replace")
        clone = json.loads(json.dumps(turn).replace(old_id, f"toolu_extra_{number}").replace(old_path, target))
        for event in clone:
            if event["type"] == "tool_execution_end":
                event["result"]["content"] = [{"type": "text", "text": text}]
                event["result"]["details"]["meta"]["source"]["value"] = target
            elif event["type"] in ("message_start", "message_end") and event["message"].get("role") == "toolResult":
                event["message"]["content"] = [{"type": "text", "text": text}]
        out.extend(clone)
    return out


def _terminal_error(reply: dict[str, Any]) -> list[dict[str, Any]]:
    message = {
        "role": "assistant",
        "content": [],
        "stopReason": "error",
        "errorMessage": reply["message"],
        "errorStatus": reply["status"],
        "provider": reply["provider"],
    }
    return [{"type": "message_end", "message": message}]


def replay(
    recorded: list[dict[str, Any]], text: str, workspace: str, cwd: str, reads: list[str] | None = None
) -> list[dict[str, Any]]:
    events = [_rewrite(event, workspace, cwd) for event in recorded]
    final_turn = max(index for index, event in enumerate(events) if event["type"] == "turn_start")
    out = []
    for index, event in enumerate(events):
        if index == final_turn and reads:
            out.extend(_extra_reads(events, workspace, reads))
        if index <= final_turn:
            out.append(event)
            continue
        if event["type"] == "message_update":
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            event = {**event, "message": _answer_message(message, text)}
        if event["type"] == "agent_end":
            messages = list(event["messages"])
            last = max(i for i, item in enumerate(messages) if item.get("role") == "assistant")
            messages[last] = _answer_message(messages[last], text)
            event = {**event, "messages": messages}
        out.append(event)
    return out


def main(argv: list[str]) -> int:
    home = Path(__file__).resolve().parent
    script = _script(home)
    if argv[:1] == ["--version"]:
        print(script["version"])
        return 0
    workspace = argv[argv.index("--add-dir") + 1]
    cwd = argv[argv.index("--cwd") + 1]
    prompt = argv[-1]
    reply, call = _next_reply(home, script["replies"], prompt)
    if script.get("ancestor_probe"):
        _record_ancestors(home, script["ancestor_probe"])
    if reply.get("rendezvous"):
        _record_met(home, call, _rendezvous(home, call, int(reply["rendezvous"])))
    recorded = [
        json.loads(line) for line in Path(script["stream"]).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if reply.get("kind") == "error":
        for event in _terminal_error(reply):
            sys.stdout.write(json.dumps(event) + "\n")
        return 1
    if reply.get("kind") == "truncated":
        events = [_rewrite(event, workspace, cwd) for event in recorded]
        final_turn = max(index for index, event in enumerate(events) if event["type"] == "turn_start")
        events = events[:final_turn]
    elif reply.get("kind") == "text":
        events = replay(recorded, reply["text"], workspace, cwd)
    else:
        events = replay(
            recorded, json.dumps(fill(response_template(prompt), reply), indent=2), workspace, cwd, reply.get("reads")
        )
    leak = script.get("leak_env")
    if leak:
        events = [_leaked(event, os.environ.get(leak, "")) for event in events]
    for event in events:
        sys.stdout.write(json.dumps(event) + "\n")
    if leak:
        sys.stderr.write(f"gateway session for {os.environ.get(leak, '')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
