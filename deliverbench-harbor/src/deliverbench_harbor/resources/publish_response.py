"""Host-invoked publication. Candidate has no sudo permission for this program."""
import hashlib,json,os,shutil
from pathlib import Path
ROOT=Path('/workspace');PENDING=Path('/opt/deliverbench/response-pending')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def safe(base,rel):
    assert rel and not rel.startswith('/') and all(x not in ('','..','.') for x in rel.split('/'))
    p=base
    for x in rel.split('/'):
        p/=x;assert not p.is_symlink()
    return p
def main():
    assert os.geteuid()==0
    record=json.loads((PENDING/'receipt.json').read_text());stage=(ROOT/'.checkpoints/deliverbench_current_step').read_text().strip()
    assert record['stage']==stage and record['status']=='committed'
    for rel,expected in record['inputs'].items():
        p=safe(ROOT,rel);assert (sha(p) if p.is_file() else None)==expected
    for row in record['files']:
        src=safe(PENDING/'files',row['path']);dst=safe(ROOT/'source',row['path'])
        assert sha(src)==row['sha256']
        if dst.exists():assert dst.is_file() and sha(dst)==row['sha256']
    for row in record['files']:
        src=safe(PENDING/'files',row['path']);dst=safe(ROOT/'source',row['path'])
        dst.parent.mkdir(parents=True,exist_ok=True)
        if not dst.exists():shutil.copyfile(src,dst)
        os.chown(dst,0,0);dst.chmod(0o444)
    folder=ROOT/'.checkpoints/responses';folder.mkdir(exist_ok=True)
    receipt=folder/(record['id']+'.json')
    if receipt.exists():assert json.loads(receipt.read_text())==record
    else:receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    os.chown(receipt,0,0);receipt.chmod(0o444)
    inp=ROOT/'.checkpoints'/(stage+'_input.json');manifest=json.loads(inp.read_text())
    for row in record['files']:
        p=ROOT/'source'/row['path'];item={'path':'source/'+row['path'],'size':p.stat().st_size,'sha256':row['sha256']}
        existing=[r for r in manifest['files'] if r['path']==item['path']]
        assert not existing or existing==[item]
        if not existing:manifest['files'].append(item)
    inp.chmod(0o644);inp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');inp.chmod(0o444)
    shutil.rmtree(PENDING)
if __name__=='__main__':main()
