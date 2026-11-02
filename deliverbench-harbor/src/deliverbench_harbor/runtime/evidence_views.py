"""Construct explicit current/history/decision evidence without score decisions."""
import hashlib,json,shutil
from pathlib import Path


def read(p):return json.loads(Path(p).read_text())
def dump(p,x):p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None
def copy(src,dst):
    src=Path(src);dst=Path(dst)
    if src.is_symlink() or not src.exists():return
    if src.is_file():dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    elif src.is_dir():
        dst.mkdir(parents=True,exist_ok=True)
        for p in src.iterdir():copy(p,dst/p.name)
def inventory(root):
    root=Path(root)
    return {'exists':root.is_dir() and not root.is_symlink(),'entries':[
        {'path':str(p.relative_to(root)),'type':'symlink' if p.is_symlink() else 'file' if p.is_file() else 'directory',
         **({'sha256':sha(p)} if p.is_file() and not p.is_symlink() else {})}
        for p in sorted(root.rglob('*'))]} if root.is_dir() and not root.is_symlink() else {'exists':False,'entries':[]}


def build_view(contract,workspace,unit,destination,final_source=False):
    workspace=Path(workspace);destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    final=workspace/'deliverables/final';checkpoints=workspace/'working/checkpoints'
    current_files=[];lineage=[]
    if unit=='FINAL':
        copy(final,destination/'current')
        current_files=['current/'+x for x in contract['outputs'][contract['steps'][-1]]]
        # Existence evidence only: these retained files are not formal deliverables.
        # Retained records remain outside the formal release directory.
        records=[]
        for prefix in ['working/checkpoints','source']:
            for path in sorted((workspace/prefix).rglob('*')):
                if path.is_file() and not path.is_symlink():
                    records.append({'workspace_path':'/workspace/'+str(path.relative_to(workspace)),
                                    'sha256':sha(path),'bytes':path.stat().st_size})
        dump(destination/'retained_workspace_records.json',{
            'scope':'Captured original workspace records; existence metadata only, not release-directory members.',
            'path_semantics':'workspace_path names refer to the evaluated original workspace, not the isolated Judge filesystem. Absence from the isolated view is not evidence of absence from that workspace.',
            'records':records})
    elif final_source:
        source=contract['source_groups'][unit]
        for role,name in source['final_files'].items():
            copy(final/name,destination/'current'/name);current_files.append('current/'+name)
            old=checkpoints/unit/source['process_files'][role]
            lineage.append({'role':role,'process_path':str(old.relative_to(workspace)),
                            'final_path':str((final/name).relative_to(workspace)),
                            'process_sha256':sha(old),'final_sha256':sha(final/name),
                            'same_bytes':bool(sha(old) and sha(old)==sha(final/name))})
        # These are current supporting documents, never substitutes for the
        # group's main file. In particular they do not satisfy a missing gate.
        for name in contract['outputs'][contract['steps'][-1]]:
            if 'current/'+name not in current_files:copy(final/name,destination/'related_current'/name)
    else:
        for name in contract['outputs'][unit]:
            copy(checkpoints/unit/name,destination/'current'/name);current_files.append('current/'+name)
    # Historical evidence is always a captured stage, not a rebased final file.
    stages=[] if unit=='FINAL' else list(contract['history_steps'][unit])
    if final_source:stages=sorted(set(stages+[unit]))
    for stage in stages:
        for name in contract['outputs'][stage]:copy(checkpoints/stage/name,destination/'history'/stage/name)
    visible_stage=contract['steps'][-1] if unit=='FINAL' else unit
    if unit!='FINAL':
        for row in contract['input_files']:
            if contract['steps'].index(row['visible_from'])<=contract['steps'].index(visible_stage):copy(workspace/'source'/row['candidate_path'],destination/'sources'/row['candidate_path'])
        dump(destination/'source_catalog.json',[r for r in contract['input_files'] if contract['steps'].index(r['visible_from'])<=contract['steps'].index(visible_stage)])
    if final_source:
        dump(destination/'decisions/stage_decisions.json',contract['final_decisions'])
    if unit in contract.get('protected_history', {}):
        before=read(workspace/'.checkpoints'/f'{unit}_input.json') if (workspace/'.checkpoints'/f'{unit}_input.json').is_file() else None
        protected_stages=contract['protected_history'][unit]
        expected={r['path']:r['sha256'] for r in before['files']} if before else {}
        records=[]
        for stage in protected_stages:
            for name in contract['outputs'][stage]:
                relative=f'working/checkpoints/{stage}/{name}';digest=sha(workspace/relative)
                records.append({'path':relative,'before_sha256':expected.get(relative),'captured_sha256':digest,
                                'same_bytes':digest==expected[relative] if relative in expected else None})
        dump(destination/'observations'/f'{unit}_readonly_history.json',{'records':records,'no_claim_when_before_missing':True})
    if unit in {contract['steps'][-1],'FINAL'} or final_source:dump(destination/'final_directory_inventory.json',inventory(final))
    mode='final_source' if final_source else 'native_final' if unit=='FINAL' else 'process'
    manifest={'unit':unit,'mode':mode,'current_scored_files':current_files,
              'missing_current_files':[p for p in current_files if not (destination/p).is_file()],
              'history_stages':stages,'historical_source_visibility_through':visible_stage,
              'current_related_files_are_context_only':True,'lineage':lineage,
              'reuse_eligible':False if final_source else None,
              'reuse_reason':'Final decisions/context differ; full source rubric regrade required.' if final_source else None}
    dump(destination/'evaluation_manifest.json',manifest)
    return manifest
