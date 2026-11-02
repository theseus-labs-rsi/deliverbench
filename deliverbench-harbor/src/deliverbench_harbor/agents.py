"""Public Agent subclasses: native execution/resume, then host-side attestation."""
import json
import os
import shlex
from pathlib import Path

from harbor.agents.installed.codex import Codex as NativeCodex
from harbor.agents.installed.claude_code import ClaudeCode as NativeClaude
from harbor.agents.installed.gemini_cli import GeminiCli as NativeGemini
from harbor.agents.installed.qwen_code import QwenCode as NativeQwen
from harbor.agents.installed.kimi_cli import KimiCli as NativeKimi
from harbor.agents.oracle import OracleAgent

from . import capture


def terminal_ok(text, kind):
    events = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                events.append(item)
        except ValueError:
            pass
    if kind == "codex":
        terminal = [e["type"] for e in events if e.get("type") in ("turn.completed", "turn.failed")]
        return bool(terminal) and terminal[-1] == "turn.completed"
    if kind in ("claude-code", "qwen-coder"):
        terminal = [e for e in events if e.get("type") == "result"]
        return bool(terminal) and terminal[-1].get("subtype") == "success" and terminal[-1].get("is_error") is False
    if kind == "kimi-cli":
        # Kimi may pretty-print one JSON-RPC frame across several lines.
        frames, buffer = [], ""
        for line in text.splitlines():
            if line.lstrip().startswith('{"jsonrpc"'):
                if buffer:
                    frames.append(buffer)
                buffer = line
            elif buffer:
                buffer += "\n" + line
        if buffer:
            frames.append(buffer)
        replies = []
        for frame in frames:
            try:
                item = json.loads(frame, strict=False)
                if str(item.get("id")) == "1":
                    replies.append(item)
            except ValueError:
                pass
        return bool(replies) and "result" in replies[-1] and "error" not in replies[-1]
    if kind == "gemini-cli":
        return bool(text.strip()) and "An unexpected critical error occurred" not in text
    return False


class Guarded:
    execution_kind = "candidate"

    def __init__(self, *args, **kwargs):
        if self.execution_kind == "candidate":
            kwargs.setdefault("reasoning_effort", "high")
            kwargs.setdefault("service_tier", "default")
            kwargs.setdefault("web_search", "live")
        super().__init__(*args, **kwargs)

    def proxy_env(self, env):
        merged = dict(env or {})
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            if value := os.environ.get("HARBOR_CONTAINER_" + key):
                merged[key] = value
        return merged

    async def exec_as_agent(self, environment, command, env=None, cwd=None, timeout_sec=None):
        return await super().exec_as_agent(environment, command, env=self.proxy_env(env),
                                           cwd=cwd, timeout_sec=timeout_sec)

    async def exec_as_root(self, environment, command, env=None, cwd=None, timeout_sec=None):
        return await super().exec_as_root(environment, command, env=self.proxy_env(env),
                                          cwd=cwd, timeout_sec=timeout_sec)

    async def setup(self, environment):
        if not hasattr(environment, "save_checkpoint"):
            raise RuntimeError("Use harbor_job.yaml with EvidenceDocker and the deliverbench lifecycle plugin")
        environment.require_protocol()
        return await super().setup(environment)

    async def run(self, instruction, environment, context):
        from .responses import publish_for_stage
        await publish_for_stage(environment)
        kickoff = getattr(environment, "task_contract", {}).get("kickoff")
        if kickoff and not getattr(self, "_kickoff_complete", False):
            from .kickoff import run_kickoff, skip_oracle
            if self.execution_kind == "oracle":
                skip_oracle(environment, kickoff)
                self._kickoff_complete = True
            else:
                if super().name() not in ("codex", "claude-code", "qwen-coder"):
                    raise RuntimeError("Unscored kickoff requires a supported native session parser")
                await run_kickoff(super().run, environment, context, kickoff, kind=super().name())
                self._kickoff_complete = True
                # Native resume invokes self.run with the same session. That call
                # performs the sole scored-stage freeze; this outer call must return.
                await super().resume(instruction, environment, context)
                return
        await super().run(instruction, environment, context)
        kind = super().name()
        if kind == "oracle":
            exit_record = self.logs_dir / "exit-code.txt"
            if exit_record.exists() and exit_record.read_text().strip() != "0":
                raise RuntimeError("Oracle solution exited nonzero")
        else:
            filename = {"codex": "codex.txt", "claude-code": "claude-code.txt",
                        "gemini-cli": "gemini-cli.txt", "qwen-coder": "qwen-code.txt",
                        "kimi-cli": "kimi-cli.txt"}[kind]
            # Read from container: host logs may not yet have been synchronized.
            result = await environment.exec("cat " + shlex.quote("/logs/agent/" + filename), user="root")
            if result.return_code or not terminal_ok(result.stdout or "", kind):
                raise RuntimeError("No successful native terminal for " + kind)
        # Prevent lingering child processes from changing evidence while it is frozen/copied.
        result = await environment.exec("pkill -KILL -u 1000 || test $? = 1", user="root")
        if result.return_code:
            raise RuntimeError("Unable to quiesce Candidate before freezing")
        source = Path(capture.__file__).read_text() + "\ncapture(" + repr(self.execution_kind) + ")\n"
        result = await environment.exec("python3 -I -c " + shlex.quote(source), user="root", timeout_sec=120)
        if result.return_code:
            raise RuntimeError("Unable to freeze successful stage: " + (result.stderr or "")[-1000:])
        record = json.loads(result.stdout)
        await environment.save_checkpoint(record["step"])


class Codex(Guarded, NativeCodex):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("version", "0.142.4")
        kwargs.setdefault("reasoning_effort", "high")
        kwargs.setdefault("service_tier", "default")
        kwargs.setdefault("web_search", "live")
        super().__init__(*args, **kwargs)


class ClaudeCode(Guarded, NativeClaude):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("version", "2.1.280")
        super().__init__(*args, **kwargs)


class GeminiCli(Guarded, NativeGemini):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("version", "0.60.0")
        super().__init__(*args, **kwargs)


class QwenCode(Guarded, NativeQwen):
    async def install(self, environment):
        result = await environment.exec("qwen --version", user="candidate")
        if self._version and result.return_code == 0 and (result.stdout or "").strip() == self._version:
            return
        return await super().install(environment)

    async def exec_as_agent(self, environment, command, env=None, cwd=None, timeout_sec=None):
        command = command.replace(". ~/.nvm/nvm.sh; qwen ", "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; qwen ", 1)
        if "qwen --yolo " in command:
            command = command.replace("qwen --yolo ", "qwen --yolo --output-format stream-json ", 1)
        return await super().exec_as_agent(environment, command, env=env, cwd=cwd, timeout_sec=timeout_sec)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("version", "0.24.1")
        super().__init__(*args, **kwargs)

    def get_version_command(self):
        return "qwen --version"


class KimiCli(Guarded, NativeKimi):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("version", "1.50.0")
        super().__init__(*args, **kwargs)


class Oracle(Guarded, OracleAgent):
    execution_kind = "oracle"

    def __init__(self, logs_dir, task_dir=None, **kwargs):
        from harbor.models.trial.paths import TrialPaths
        if task_dir is None:
            from .environment import ACTIVE_TASKS
            registered = ACTIVE_TASKS.get(Path(logs_dir).parent.parent.resolve(), {})
            if len(registered) != 1:
                raise ValueError("Oracle requires one registered task, or explicit task_dir in Agent kwargs")
            task_dir = next(iter(registered))
        super().__init__(logs_dir=logs_dir, task_dir=Path(task_dir),
                         trial_paths=TrialPaths(trial_dir=Path(logs_dir).parent), **kwargs)
