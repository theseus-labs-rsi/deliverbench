"""Executed as root from host-selected source, never installed in Candidate image."""
import hashlib
import json
import os
import stat
from pathlib import Path


def snapshot(base):
    entries = []
    if base.is_symlink():
        raise RuntimeError("Artifact root must not be a symlink")
    if base.exists() and not base.is_dir():
        raise RuntimeError("Artifact root must be a directory")
    if base.is_dir():
        for p in sorted(base.rglob("*")):
            kind = "symlink" if p.is_symlink() else "directory" if p.is_dir() else "file"
            row = {"path": p.relative_to(base).as_posix(), "type": kind}
            if kind == "file":
                if not stat.S_ISREG(p.lstat().st_mode):
                    raise RuntimeError("Special files cannot be frozen")
                row["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
            entries.append(row)
    return {"exists": base.is_dir(), "entries": entries}


def capture(kind):
    root = Path("/workspace")
    audits = root / ".checkpoints"
    step = (audits / "deliverbench_current_step").read_text().strip()
    if step not in json.loads(Path("/opt/deliverbench/stage_contract.json").read_text())["steps"]:
        raise RuntimeError("Unknown stage")
    base = root / "working/checkpoints" / step
    target = audits / (step + "_deliverbench_output.json")
    if target.exists() or target.is_symlink():
        raise RuntimeError("Never overwrite a completed receipt")
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError("Missing checkpoint")
    # Candidate processes have been terminated by the host before this call.
    for p in [base, *base.rglob("*")]:
        if p.is_symlink():
            os.lchown(p, 0, 0)
        else:
            os.chown(p, 0, 0)
            p.chmod(0o755 if p.is_dir() else 0o444)
    checkpoint = snapshot(base)
    final = snapshot(root / "deliverables/final")
    record = {
        "step": step,
        "candidate_completed": True,
        "attestation": "host_native_success_v1",
        "execution_kind": kind,
        "files": [r for r in checkpoint["entries"] if r["type"] == "file"],
        "entries": checkpoint["entries"],
        "checkpoint_directory_snapshot": checkpoint,
        "final_directory_snapshot": final,
    }
    with target.open("x") as stream:
        json.dump(record, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    target.chmod(0o400)
    print(json.dumps({"step": step, "execution_kind": kind}))
