#!/usr/bin/env python3
"""Root-only ordered material release. Completion is attested by the host Agent.
The fixed sudo entry may consume one host-uploaded stage request only.
It never manufactures a success receipt for the previous stage.
"""
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path('/workspace')
REQUEST = ROOT / 'deliverbench_stage.json'
AUDITS = ROOT / '.checkpoints'
MARKER = AUDITS / 'deliverbench_current_step'
CANDIDATE_UID = 1000
STEPS = json.loads(Path('/opt/deliverbench/stage_contract.json').read_text())['steps']


def fail(message, code=1):
    print('deliverbench stage: ' + message, file=sys.stderr)
    raise SystemExit(code)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(folder):
    rows = []
    if folder.is_dir() and not folder.is_symlink():
        for p in sorted(folder.rglob('*')):
            kind = 'symlink' if p.is_symlink() else 'file' if p.is_file() else 'directory'
            row = {'path': str(p.relative_to(folder)), 'type': kind}
            if kind == 'file':
                row['sha256'] = sha256(p)
            rows.append(row)
    return rows


def lock_tree(root, uid=0):
    """chown/chmod a tree; uid=0 -> root read-only, uid=1000 -> candidate writable."""
    for p in [root, *root.rglob('*')]:
        if p.is_symlink():
            os.lchown(p, uid, uid)
            continue
        os.chown(p, uid, uid)
        if uid == 0:
            p.chmod(0o755 if p.is_dir() else 0o444)
        else:
            p.chmod(0o755 if p.is_dir() else 0o644)


def require_frozen(step):
    base = ROOT / 'working/checkpoints' / step
    target = AUDITS / (step + '_deliverbench_output.json')
    if not target.is_file() or target.is_symlink():
        fail('missing host success receipt for ' + step)
    record = json.loads(target.read_text())
    if record.get('step') != step or record.get('candidate_completed') is not True:
        fail('invalid host success receipt')
    if record.get('attestation') != 'host_native_success_v1':
        fail('unsupported attestation')
    if inventory(base) != record['entries']:
        fail('frozen checkpoint bytes changed')
    for p in [base, *base.rglob('*')]:
        if not p.is_symlink() and (p.stat().st_uid != 0 or p.stat().st_mode & 0o022):
            fail('frozen checkpoint is writable')


def release_materials(incoming, source):
    """Merge a staged event without replacing any previously released bytes."""
    incoming, source = Path(incoming), Path(source)
    entries = sorted(incoming.rglob('*'))
    for item in entries:
        relative = item.relative_to(incoming)
        target = source / relative
        if item.is_symlink() or any(p.is_symlink() for p in [target, *target.parents]):
            fail('source symlink refused: ' + str(relative))
        if target.exists():
            if item.is_dir() != target.is_dir():
                fail('source path type conflict: ' + str(relative))
            if item.is_file() and sha256(item) != sha256(target):
                fail('refusing replacement of released source: ' + str(relative))
    for item in entries:
        target = source / item.relative_to(incoming)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    shutil.rmtree(incoming)


def stage(step):
    """Make <step> the current stage using the declared ordered protocol."""
    for parent in [ROOT / 'working', ROOT / 'working/checkpoints']:
        parent.mkdir(parents=True, exist_ok=True)
        os.chown(parent, 0, 0)
        parent.chmod(0o755)
    current = ROOT / 'working/current'
    if current.is_symlink():
        current.unlink()
    elif current.exists():
        fail('working/current must be a runtime symlink; refusing to delete a real directory')
    checkpoint = ROOT / 'working/checkpoints' / step
    if checkpoint.exists():
        fail('checkpoint directory for ' + step + ' already exists')
    checkpoint.mkdir(parents=True)
    os.chown(checkpoint, CANDIDATE_UID, CANDIDATE_UID)
    checkpoint.chmod(0o755)
    current.symlink_to(checkpoint)
    os.lchown(current, 0, 0)
    # Release this stage's new source materials (uploaded with the step workdir).
    incoming = ROOT / 'release_materials'
    source = ROOT / 'source'
    source.mkdir(exist_ok=True)
    if incoming.is_dir():
        release_materials(incoming, source)
    for p in [source, *source.rglob('*')]:
        if not p.is_symlink():
            os.chown(p, 0, 0)
            p.chmod(0o555 if p.is_dir() else 0o444)
    final = ROOT / 'deliverables/final'
    final.mkdir(parents=True, exist_ok=True)
    os.chown(ROOT / 'deliverables', CANDIDATE_UID, CANDIDATE_UID)
    os.chown(final, CANDIDATE_UID, CANDIDATE_UID)
    AUDITS.mkdir(exist_ok=True)
    os.chown(AUDITS, 0, 0)
    AUDITS.chmod(0o755)
    if MARKER.exists():
        MARKER.chmod(0o644)
    MARKER.write_text(step + '\n')
    os.chown(MARKER, 0, 0)
    MARKER.chmod(0o444)
    files = []
    for base in [source, ROOT / 'working/checkpoints']:
        for path in sorted(base.rglob('*')):
            if path.is_file() and not path.is_symlink():
                files.append({'path': str(path.relative_to(ROOT)), 'size': path.stat().st_size,
                              'sha256': sha256(path)})
    manifest = AUDITS / (step + '_input.json')
    manifest.write_text(json.dumps({'task_id': step, 'files': files}, ensure_ascii=False, indent=2) + '\n')
    os.chown(manifest, 0, 0)
    manifest.chmod(0o444)


def main():
    if os.geteuid() != 0:
        fail('must run as root (via the fixed sudoers rule)')
    if not REQUEST.is_file() or REQUEST.is_symlink():
        fail('no pending stage request; nothing to do', 2)
    # docker cp keeps the *host* uid of uploaded files, so root ownership cannot be
    # required. What matters: /workspace is root-owned and not writable by others,
    # so only Harbor (docker cp) can place a request there, and the request is not
    # group/world writable.
    ws = ROOT.stat()
    if ws.st_uid != 0 or stat.S_IMODE(ws.st_mode) & 0o022:
        fail('workspace root must be root-owned and not group/world writable')
    st = REQUEST.stat()
    if stat.S_IMODE(st.st_mode) & 0o022:
        fail('untrusted stage request (group/world writable)')
    request = json.loads(REQUEST.read_text())
    step = request.get('step')
    if step not in STEPS:
        fail('unknown step in request')
    index = STEPS.index(step)
    if MARKER.exists():
        current = MARKER.read_text().strip()
        if current not in STEPS or STEPS.index(current) != index - 1:
            fail('stage order violated: current=' + current + ' requested=' + step)
        require_frozen(current)
    elif index != 0:
        fail('continuous workspace must start at ' + STEPS[0])
    stage(step)
    REQUEST.unlink()
    setup = ROOT / 'setup.sh'
    if setup.is_file():
        setup.unlink()
    print('deliverbench stage: ' + step + ' ready')


if __name__ == '__main__':
    main()
