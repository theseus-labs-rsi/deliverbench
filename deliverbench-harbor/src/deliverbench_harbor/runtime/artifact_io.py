"""Copy scoped raw evidence and record file hashes; never parse file content."""
import hashlib
import json
import shutil
from pathlib import Path

def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,default=str,indent=2)+'\n')


def copy_artifacts(source,destination):
    """Do not follow Candidate symlinks into undeclared sources/host paths."""
    source=Path(source);destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    manifest=[]
    if not source.exists():return manifest
    for item in sorted(source.rglob('*')):
        rel=item.relative_to(source)
        if item.is_symlink():
            manifest.append({'path':str(rel),'kind':'symlink_not_followed','target':str(item.readlink())})
        elif item.is_dir():
            (destination/rel).mkdir(parents=True,exist_ok=True)
        elif item.is_file():
            target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(item,target)
            manifest.append({'path':str(rel),'kind':'file','size':item.stat().st_size,
                             'sha256':hashlib.sha256(item.read_bytes()).hexdigest()})
    return manifest

