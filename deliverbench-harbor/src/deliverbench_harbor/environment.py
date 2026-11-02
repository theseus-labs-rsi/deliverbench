"""Docker lifecycle extension using public Harbor methods only."""
import os
import re
import shutil
import shlex
import json
import tempfile
from pathlib import Path

from harbor.environments.docker.docker import DockerEnvironment
from .io import atomic_json, inventory

# Populated by the CLI plugin before trials are created/run. No Harbor mutation.
ACTIVE_JOBS = set()
ACTIVE_TASKS = {}


class EvidenceDocker(DockerEnvironment):
    async def start(self, *args, **kwargs):
        self.require_protocol()
        from .contract import load_task
        task_root = self.environment_dir.parent.resolve()
        snapshot = load_task(task_root)
        expected = ACTIVE_TASKS.get(self.trial_paths.trial_dir.parent.resolve(), {}).get(str(task_root))
        if expected != snapshot:
            raise RuntimeError("Task definition changed after preflight or task was not registered")
        self.task_contract = snapshot
        await super().start(*args, **kwargs)
        result = await self.exec("cat /opt/deliverbench-role", user="root")
        role = (result.stdout or "").strip()
        if result.return_code or role not in ("candidate", "verifier"):
            raise RuntimeError("Missing trusted environment role marker")
        self.evidence_role = role
        self.evidence_started = True
        # The installed wheel is the sole source of shared runtime code. Deploy only
        # stage control to Candidate; private Judge code stays in the verifier.
        package = Path(__file__).resolve().parent
        result = await self.exec("mkdir -p /opt/deliverbench && chmod 0755 /opt/deliverbench", user="root")
        if result.return_code:
            raise RuntimeError("Unable to prepare extension runtime")
        contract_path = self.trial_paths.trial_dir / "deliverbench_contract.json"
        if contract_path.exists():
            if json.loads(contract_path.read_text()) != snapshot:
                raise RuntimeError("Per-trial task definition changed")
        else:
            atomic_json(contract_path, snapshot)
        with tempfile.TemporaryDirectory() as td:
            public = Path(td) / "stage_contract.json"
            public.write_text(json.dumps({"steps": snapshot["steps"], "final_step": snapshot["final_step"]}))
            await self.upload_file(public, "/opt/deliverbench/stage_contract.json")
        if role == "candidate":
            await self.upload_file(package / "resources/stage.py", "/opt/deliverbench/stage.py")
            await self.upload_file(package / "resources/sudoers-deliverbench", "/etc/sudoers.d/deliverbench")
            command = "chown root:root /etc/sudoers.d/deliverbench && chmod 0440 /etc/sudoers.d/deliverbench && visudo -cf /etc/sudoers.d/deliverbench"
        else:
            await self.upload_dir(package / "runtime", "/opt/deliverbench/runtime")
            command = "chmod 0700 /opt/deliverbench/runtime && cp /opt/deliverbench/runtime/libreoffice_uid_profile.py /usr/local/bin/libreoffice && chmod 0755 /usr/local/bin/libreoffice"
        result = await self.exec("chown -R root:root /opt/deliverbench && chmod 0444 /opt/deliverbench/stage_contract.json && " + command, user="root")
        if result.return_code:
            raise RuntimeError("Unable to install shared container runtime: " + (result.stderr or ""))

    def require_protocol(self):
        if self.trial_paths.trial_dir.parent.resolve() not in ACTIVE_JOBS:
            raise RuntimeError("Required protocol plugin missing: add --plugin deliverbench_harbor.lifecycle:Plugin")

    def evidence_root(self):
        return self.trial_paths.trial_dir / "evidence"

    async def save_checkpoint(self, step):
        self.require_protocol()
        if getattr(self, "evidence_role", None) != "candidate":
            raise RuntimeError("Only Candidate may submit a checkpoint")
        await self.prepare_export()
        await self.export("candidate/" + step, candidate=True)

    async def prepare_export(self):
        # docker cp on macOS cannot populate a newly copied 0555 directory.
        # Only its owner's permission changes; root-owned inputs stay unwritable
        # to Candidate. File bytes and file permissions remain unchanged.
        result = await self.exec("find -P /workspace -type d -exec chmod u+rwx {} +", user="root")
        if result.return_code:
            raise RuntimeError("Unable to prepare exportable directory modes")
        if self.evidence_role == "candidate":
            result = await self.exec("pkill -KILL -u 1000 || test $? = 1", user="root")
            if result.return_code:
                raise RuntimeError("Cannot quiesce native logs")
            source = """
from pathlib import Path
import shutil
locations = {
 'codex': '/tmp/codex-home/sessions',
 'claude': '/home/candidate/.claude/projects',
 'gemini': '/home/candidate/.gemini/tmp',
 'qwen': '/home/candidate/.qwen/projects',
 'kimi': '/home/candidate/.kimi/sessions',
}
destination = Path('/opt/deliverbench-native')
destination.mkdir(mode=0o700, exist_ok=True)
for kind, folder in locations.items():
    base = Path(folder)
    if not base.is_dir() or base.is_symlink(): continue
    for path in base.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.suffix in ('.json', '.jsonl'):
            target = destination / kind / path.relative_to(base)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
"""
            result = await self.exec("python3 -I -c " + shlex.quote(source), user="root")
            if result.return_code:
                raise RuntimeError("Unable to preserve native session files")
            await self.exec("find -P /logs/agent -type d -exec chmod u+rwx {} +", user="root")

    async def export(self, name, *, candidate):
        target = self.evidence_root() / name
        if target.exists():
            raise RuntimeError("Refusing to replace committed evidence " + name)
        temp = target.with_name(target.name + ".partial")
        temp.mkdir(parents=True, exist_ok=True)
        try:
            secrets = tuple(v.encode() for k, v in os.environ.items()
                            if re.search(r"(API_KEY|ACCESS_TOKEN|SECRET)$", k) and len(v) >= 12)
            if candidate:
                # Public download_dir uses docker cp and works after stop_service.
                await self.download_dir("/workspace", temp / "workspace")
                rows = inventory(temp / "workspace", sync=True, forbidden=secrets)
                if rows != inventory(temp / "workspace"):
                    raise RuntimeError("Evidence changed during commit")
                await self.download_dir("/logs/agent", temp / "agent")
                await self.download_dir("/opt/deliverbench-native", temp / "native")
                logs = temp / "agent"
            else:
                # Never export Judge workspace/private view back into Candidate evidence.
                await self.download_dir("/logs/verifier", temp / "verifier")
                logs = temp / "verifier"
                rows = []
            # Redact log copies before committing. Candidate artifact bytes
            # above are never edited; credential detection there fails closed.
            log_paths = list(logs.rglob("*"))
            if candidate:
                log_paths += list((temp / "native").rglob("*"))
            for path in log_paths:
                if path.is_file() and not path.is_symlink():
                    raw = path.read_bytes()
                    clean = raw
                    for value in secrets:
                        clean = clean.replace(value, b"[REDACTED]")
                    if raw != clean:
                        path.chmod(0o600)
                        path.write_bytes(clean)
            log_rows = inventory(logs, sync=True)
            if not candidate:
                rows = log_rows
            atomic_json(temp / "manifest.json", {"role": self.evidence_role, "files": rows,
                                                "logs": log_rows,
                                                "native_logs": inventory(temp / "native", sync=True) if candidate else [],
                                                "status": "committed", "schema": 1})
            os.replace(temp, target)
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            # Retain partial data and the stopped/live container for manual recovery.
            atomic_json(self.evidence_root() / "storage_status.json",
                        {"status": "storage_incomplete", "container_session": self.session_id,
                         "evidence_target": name, "automatic_deletion": False})
            raise

    async def stop(self, delete):
        if getattr(self, "evidence_started", False) and not getattr(self, "evidence_saved", False):
            await self.prepare_export()
            await self.stop_service("main")
            if self.evidence_role == "candidate":
                name = "candidate/at_stop"
            else:
                # Each separate verifier has its own session identity.
                name = "verifier/" + re.sub(r"[^A-Za-z0-9_.-]", "_", self.session_id or "verifier")
            await self.export(name, candidate=self.evidence_role == "candidate")
            self.evidence_saved = True
        # If export failed, this is deliberately not reached.
        await super().stop(delete)
