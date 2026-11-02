"""Durable local evidence. Never silently edit Candidate artifact bytes."""
import hashlib
import json
import os
from pathlib import Path

def persistent_job_storage(job):
    """Keep sidecars outside the job directory scanned by stock Harbor resume."""
    job = Path(job).resolve()
    root = job.parent / '.deliverbench' / job.name
    for folder in (root.parent, root):
        if folder.is_symlink():
            raise ValueError('Persistent job storage may not be a symlink')
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
    return root


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".partial")
    with temp.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inventory(root, *, sync=False, forbidden=()):
    """Do not follow links. Reject special files and supplied credential bytes."""
    root = Path(root)
    rows = []
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append({"path": name, "type": "symlink", "target": os.readlink(path)})
        elif path.is_dir():
            rows.append({"path": name, "type": "directory"})
        elif path.is_file():
            digest = hashlib.sha256()
            tail = b""
            overlap = max((len(v) for v in forbidden), default=1) - 1
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    if any(v in tail + chunk for v in forbidden):
                        raise RuntimeError("Credential bytes found in Candidate artifact: " + name)
                    digest.update(chunk)
                    tail = (tail + chunk)[-overlap:] if overlap else b""
                if sync:
                    os.fsync(stream.fileno())
            rows.append({"path": name, "type": "file", "sha256": digest.hexdigest()})
        else:
            raise RuntimeError("Unsupported special artifact: " + name)
    return rows
