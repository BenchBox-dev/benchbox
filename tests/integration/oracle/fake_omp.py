from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PLACEHOLDER = re.compile(r"<[a-z_]+>")
RECORDED_ROOT = "/workspace"
RECORDED_CWD = "/scratch/omp-cwd"


def _script(home: Path) -> dict[str, Any]:
    return json.loads((home / "script.json").read_text(encoding="utf-8"))


def _next_reply(home: Path, replies: list[dict[str, Any]]) -> dict[str, Any]:
    import fcntl

    with open(home / "calls.lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counter = home / "calls.json"
        calls = json.loads(counter.read_text(encoding="utf-8")) if counter.is_file() else []
        reply = replies[min(len(calls), len(replies) - 1)]
        calls.append({"reply": len(calls)})
        counter.write_text(json.dumps(calls), encoding="utf-8")
    return reply


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


def replay(recorded: list[dict[str, Any]], text: str, workspace: str, cwd: str) -> list[dict[str, Any]]:
    events = [_rewrite(event, workspace, cwd) for event in recorded]
    final_turn = max(index for index, event in enumerate(events) if event["type"] == "turn_start")
    out = []
    for index, event in enumerate(events):
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
    reply = _next_reply(home, script["replies"])
    recorded = [
        json.loads(line) for line in Path(script["stream"]).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if reply.get("kind") == "truncated":
        events = [_rewrite(event, workspace, cwd) for event in recorded]
        final_turn = max(index for index, event in enumerate(events) if event["type"] == "turn_start")
        events = events[:final_turn]
    else:
        events = replay(recorded, json.dumps(fill(response_template(prompt), reply), indent=2), workspace, cwd)
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
