"""One unscored project briefing before the first native Candidate stage."""
import asyncio
import hashlib
import json
from pathlib import Path

from .io import atomic_json

PREFIX = "项目开场说明（不评分）"
SUFFIX = "本轮仅阅读并接收上述项目背景与总体要求，请简短确认收到。不要执行子任务、调用工具或创建修改文件；具体阶段任务将在下一条消息给出。本轮回复不参与评分。"


def prompt(kickoff):
    if kickoff.get('language') == 'en':
        return ('Project briefing (unscored)\n\n'+kickoff['text']+
                '\n\nRead the project background and acknowledge receipt briefly. Do not start any task, use tools, '
                'or create or modify files in this turn. The first assignment follows in the next message. '
                'This acknowledgment is not assessed.')
    return PREFIX + "\n\n" + kickoff["text"] + "\n\n" + SUFFIX


LOG_NAMES = {"codex": "codex.txt", "claude-code": "claude-code.txt", "qwen-coder": "qwen-code.txt"}


def native_turn(text, kind="codex"):
    if kind not in LOG_NAMES:
        raise RuntimeError("Unsupported project briefing Agent: " + kind)
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    if kind == "codex":
        ids = {e.get("thread_id") for e in events if e.get("type") == "thread.started"}
        terminal = [e for e in events if e.get("type") in ("turn.completed", "turn.failed")]
        success = bool(terminal) and terminal[-1]["type"] == "turn.completed"
        replies = [e.get("item", {}).get("text", "") for e in events
                   if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
    else:
        ids = {e.get("session_id") for e in events if e.get("type") in ("system", "assistant", "result") and e.get("session_id")}
        terminal = [e for e in events if e.get("type") == "result"]
        success = bool(terminal) and terminal[-1].get("subtype") == "success" and terminal[-1].get("is_error") is False
        replies = [b.get("text", "") for e in events if e.get("type") == "assistant"
                   for b in e.get("message", {}).get("content", []) if isinstance(b, dict) and b.get("type") == "text"]
        if not replies and success:
            replies = [terminal[-1].get("result", "")]
    if len(ids) != 1 or None in ids or not success:
        raise RuntimeError("Project briefing did not complete one native " + kind + " session")
    return next(iter(ids)), replies


def thread_id(text, kind="codex"):
    return native_turn(text, kind)[0]


def skip_oracle(environment, kickoff):
    atomic_json(environment.trial_paths.trial_dir / "kickoff/status.json", {
        "status": "skipped_oracle", "scored": False,
        "instruction_sha256": kickoff["sha256"], "model_called": False})


async def run_kickoff(native_run, environment, context, kickoff, *, kind="codex"):
    folder = environment.trial_paths.trial_dir / "kickoff"
    folder.mkdir(parents=True, exist_ok=True)
    request = prompt(kickoff)
    (folder / "instruction.md").write_text(request)
    status = {"status": "running", "scored": False,
              "instruction_sha256": kickoff["sha256"], "agent_kind": kind,
              "request_sha256": hashlib.sha256(request.encode()).hexdigest()}
    atomic_json(folder / "status.json", status)
    try:
        async with asyncio.timeout(kickoff["timeout_sec"]):
            # The ordinary Harbor execution context applies the Candidate's
            # network policy, credentials and native Agent setup to this call.
            await native_run(request, environment, context)
            result = await environment.exec("cat /logs/agent/" + LOG_NAMES[kind], user="root")
            if result.return_code:
                raise RuntimeError("Missing native project briefing log")
            trace = result.stdout or ""
            (folder / LOG_NAMES[kind]).write_text(trace)
            session, replies = native_turn(trace, kind)
            if not any(r.strip() for r in replies):
                raise RuntimeError("Project briefing returned no acknowledgment")
            (folder / "reply.txt").write_text("\n".join(replies))
            status.update(status="completed", thread_id=session,
                          trace_sha256=hashlib.sha256(trace.encode()).hexdigest(),
                          reply_sha256=hashlib.sha256((folder / "reply.txt").read_bytes()).hexdigest())
            atomic_json(folder / "status.json", status)
    except BaseException as exc:
        status.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                      exception_type=type(exc).__name__)
        atomic_json(folder / "status.json", status)
        raise


def validate(trial, contract, *, oracle=False):
    kickoff = contract.get("kickoff")
    if not kickoff:
        return "not_configured"
    folder = Path(trial) / "kickoff"
    status = json.loads((folder / "status.json").read_text())
    if status.get("scored") is not False or status.get("instruction_sha256") != kickoff["sha256"]:
        raise ValueError("Invalid project briefing receipt")
    if oracle:
        if status.get("status") != "skipped_oracle" or status.get("model_called") is not False:
            raise ValueError("Oracle must not call the briefing model")
        return "skipped_oracle"
    if status.get("status") != "completed":
        raise ValueError("Project briefing did not complete")
    request = (folder / "instruction.md").read_text()
    if request != prompt(kickoff) or hashlib.sha256(request.encode()).hexdigest() != status.get("request_sha256"):
        raise ValueError("Project briefing request mismatch")
    kind = status.get("agent_kind", "codex")
    if kind not in LOG_NAMES:
        raise ValueError("Unsupported project briefing Agent")
    trace = (folder / LOG_NAMES[kind]).read_text()
    reply = (folder / "reply.txt").read_bytes()
    if (hashlib.sha256(trace.encode()).hexdigest() != status.get("trace_sha256")
            or not reply.strip() or hashlib.sha256(reply).hexdigest() != status.get("reply_sha256")):
        raise ValueError("Project briefing evidence mismatch")
    session = thread_id(trace, kind)
    first = (Path(trial) / "steps" / contract["steps"][0] / "agent" / LOG_NAMES[kind]).read_text()
    if session != status.get("thread_id") or thread_id(first, kind) != session:
        raise ValueError("First stage did not resume the project briefing session")
    return "completed"
