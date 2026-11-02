"""Separate verifier: host-attested inputs, complete Judge results, unchanged business scoring."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
import sys
# -I excludes the script directory; only this root-owned runtime is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

def read(p): return json.loads(p.read_text())
def dump(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.partial')
    with tmp.open('w') as f:
        f.write(json.dumps(v,ensure_ascii=False,indent=2)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None
def safe_copy(source,target):
    if source.is_symlink():return
    if source.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    elif source.is_dir():
        for p in source.rglob('*'):
            if p.is_file() and not p.is_symlink():safe_copy(p,target/p.relative_to(source))

def directory_snapshot(base):
    entries=[]
    if base.is_dir() and not base.is_symlink():
        for p in sorted(base.rglob('*')):
            kind='symlink' if p.is_symlink() else 'file' if p.is_file() else 'directory'
            row={'path':str(p.relative_to(base)),'type':kind}
            if kind=='file':row['sha256']=sha(p)
            entries.append(row)
    return {'exists':base.is_dir() and not base.is_symlink(),'entries':entries}

def final_context(final_dir,output):
    safe_copy(final_dir,output/'正式交付目录')
    # Inventory includes empty folders and symlinks without following links.
    # It is reading evidence, never an automatic business-quality verdict.
    dump(output/'正式目录清单.json',directory_snapshot(final_dir))

def current_view(checkpoint,names,output):
    output.mkdir(parents=True,exist_ok=True)
    # Same-stage executable/model helpers are Candidate artifacts, not hidden
    # upstream context. Their hashes also govern final source-score reuse.
    names=set(names)
    if checkpoint.is_dir():
        names.update(str(p.relative_to(checkpoint)) for p in checkpoint.rglob('*')
            if p.is_file() and p.suffix.lower() in ('.py','.json','.yaml','.yml','.npy','.npz','.pkl','.joblib')
            and '__pycache__' not in p.parts)
    for name in sorted(names):safe_copy(checkpoint/name,output/name)

def group_view(group,final_dir,checkpoints,audits,output):
    """Return hash equality to the actually completed source version, not a later checkpoint mutation."""
    step=group['step'];audit=audits/(step+'_deliverbench_output.json')
    frozen=read(audit) if audit.is_file() else {}
    hashes={r['path']:r['sha256'] for r in frozen.get('files',[])}
    output.mkdir(parents=True,exist_ok=True);records=[]
    mappings=dict(group['files']) # source filename -> actual final filename
    source_outputs=list(group['source_outputs'])
    # Auxiliary executable/model dependencies also govern byte-identical reuse.
    for name in hashes:
        if name not in source_outputs and Path(name).suffix.lower() in ('.py','.json','.yaml','.yml','.npy','.npz','.pkl','.joblib'):
            source_outputs.append(name);mappings[name]=name
    for name in source_outputs:
        original=checkpoints/step/name
        if name in mappings:
            final=final_dir/mappings[name];h=sha(final)
            safe_copy(final,output/name)
            records.append({'source':name,'final':mappings[name],'frozen_sha256':hashes.get(name),'final_sha256':h,
                'same_bytes':bool(h and h==hashes.get(name) and frozen.get('candidate_completed'))})
        elif original.is_file() and sha(original)==hashes.get(name):safe_copy(original,output/name)
    match=bool(records) and all(r['same_bytes'] for r in records)
    if group.get('include_final_directory'):
        final_context(final_dir,output)
        context_match=frozen.get('final_directory_snapshot')==directory_snapshot(final_dir)
        records.append({'context':'final_directory','same_bytes':context_match})
        match=match and context_match
    if group.get('include_tool_records'):
        safe_copy(audits/(step+'_actual_tool_calls.json'),output/'实际工具执行记录.json')
    return match,records

def judge_side_certificate(workspace,step,logs):
    """Read and validate a host receipt; a directory never proves native success."""
    audit=workspace/'.checkpoints'/(step+'_deliverbench_output.json')
    if not audit.is_file() or audit.is_symlink():
        raise RuntimeError('Missing host completion receipt')
    record=read(audit)
    if record.get('step')!=step or record.get('candidate_completed') is not True or record.get('attestation')!='host_native_success_v1':
        raise RuntimeError('Invalid host completion receipt')
    if record.get('checkpoint_directory_snapshot')!=directory_snapshot(workspace/'working/checkpoints'/step):
        raise RuntimeError('Checkpoint changed after completion')
    if record.get('final_directory_snapshot')!=directory_snapshot(workspace/'deliverables/final'):
        raise RuntimeError('Final directory changed after completion')
    dump(logs/(step+'_deliverbench_output.json'),record)
    return record

def require_judge_env():
    missing=[k for k in ('JUDGE_BASE_URL','JUDGE_API_KEY','JUDGE_MODEL') if not os.environ.get(k)]
    if missing:
        raise SystemExit('verify.py: missing required judge configuration: '+', '.join(missing)+
                         '. Export them on the Harbor host (they are forwarded through [verifier.env] in task.toml), '
                         'or set DELIVERBENCH_INFRA_PROBE=true for a non-scoring infrastructure probe.')

def main():
    tests=Path('/tests');step=os.environ.get('TASK_ID','').strip()
    steps_root=tests/'steps'
    if not step or not (steps_root/step).is_dir():
        raise SystemExit('verify.py: TASK_ID must name a step directory under /tests/steps (got %r)'%step)
    step_dir=steps_root/step;contract=read(step_dir/'runtime_contract.json')
    if contract['current_step']!=step:raise SystemExit('verify.py: runtime_contract.json does not match TASK_ID')
    workspace=Path('/workspace');logs=Path('/logs/verifier')
    reward={'reward':0.,'db_seen_'+step:1.,'db_complete_'+step:0.,'db_evidence_'+step:0.,'db_graded_'+step:0.,'db_protocol_valid':0.}
    record=judge_side_certificate(workspace,step,logs)
    complete=record.get('candidate_completed') is True and record.get('step')==step
    reward['db_complete_'+step]=float(complete)
    reward['db_reference_run']=float(record.get('execution_kind')=='oracle')
    from rubric_schema import load_json, require_valid
    for unit, filename in [(step,'rubric.json')]+([('FINAL','final_rubric.json')] if step==contract['steps'][-1] else []):
        document=require_valid(load_json(step_dir/filename))
        if document['evaluation_unit']!=unit:raise ValueError('Rubric evaluation unit mismatch')
    if os.environ.get('DELIVERBENCH_INFRA_PROBE','false').lower()=='true':
        if contract.get('evidence_view')=='cumulative_raw':
            from cumulative_evidence import build_view
            with tempfile.TemporaryDirectory(prefix='db-probe-') as td:
                manifest=build_view(contract,workspace,step,Path(td)/'process')
                dump(logs/'probe_evidence_contract.json',manifest)
                if step==contract['steps'][-1]:
                    manifest=build_view(contract,workspace,'FINAL',Path(td)/'final')
                    dump(logs/'probe_final_evidence_contract.json',manifest)
        reward['db_evidence_'+step]=1.
        reward['db_non_model_probe']=1.;reward['db_protocol_valid']=1.;dump(logs/'reward.json',reward)
        dump(logs/'evaluation_status.json',{'status':'infra_probe','step':step,'model_called':False})
        dump(logs/'probe.json',{'model_called':False,'candidate_completed':complete,'scoring_complete':False,
                                'checkpoint_files':len(record.get('files',[])),'attestation':record.get('attestation')});return
    require_judge_env()
    # Always preserve an infrastructure record even if a subsequent Judge call fails.
    dump(logs/'reward.json',reward)
    from rubric_schema import load_json, require_valid
    document=require_valid(load_json(step_dir/'rubric.json'))
    if document['evaluation_unit']!=step:raise ValueError('Rubric stage mismatch')
    from blind_judge import evaluate
    with tempfile.TemporaryDirectory(prefix='db-current-') as td:
        output=Path(td)/'output';output.mkdir()
        if contract.get('evidence_view') == 'cumulative_raw':
            from cumulative_evidence import build_view
            manifest=build_view(contract,workspace,step,output)
            dump(logs/'process/evidence_contract.json',manifest)
        elif contract.get('evidence_view') == 'history':
            from evidence_views import build_view
            manifest=build_view(contract,workspace,step,output)
            dump(logs/'process/evidence_contract.json',manifest)
        else:
            current_view(workspace/'working/checkpoints'/step,contract['outputs'][step],output)
        if contract.get('tool_records'):
            safe_copy(workspace/'.checkpoints'/(step+'_actual_tool_calls.json'),output/'实际工具执行记录.json')
        if step in contract.get('final_context_steps',[]):
            final_context(workspace/'deliverables/final',output)
        reward['db_evidence_'+step]=1.
        dump(logs/'reward.json',reward)
        result=evaluate(output,step_dir/'rubric.json',step_dir/'task_prompt.md',tests/'judge_instruction.md',logs/'process/result.json')
        if result.get('status')!='graded':raise RuntimeError('Current checkpoint Judge incomplete')
        validate_incompatible_hits(result,contract.get('incompatible_hits',[]),logs/'process/result.json')
        reward.update({'reward':result['score'],'db_score_'+step:result['score'],'db_hurdle_'+step:float(bool(result['hurdles'])),'db_graded_'+step:1.})
        for dim,v in result['breakdown']['dimension'].items():
            reward['db_dim_'+step+'_'+dim+'_earned']=v['earned'];reward['db_dim_'+step+'_'+dim+'_possible']=v['possible']
        if step==contract['steps'][-1]:
            from final_scoring import run_final
            if contract.get('evidence_view') == 'cumulative_raw':
                from final_scoring import signals
                final_output=Path(td)/'final-output'
                manifest=build_view(contract,workspace,'FINAL',final_output)
                dump(logs/'final/evidence_contract.json',manifest)
                final_doc=require_valid(load_json(step_dir/'final_rubric.json'))
                if final_doc['evaluation_unit']!='FINAL':raise ValueError('Final rubric unit mismatch')
                final_result=evaluate(final_output,step_dir/'final_rubric.json',step_dir/'final_task_prompt.md',tests/'judge_instruction.md',logs/'final/result.json')
                validate_incompatible_hits(final_result,contract.get('final_incompatible_hits',[]),logs/'final/result.json')
                reward.update(signals(final_result))
            elif contract.get('evidence_view') == 'history':
                builder=lambda g,o:build_view(contract,workspace,g,o,True)
            else:
                builder=lambda g,o:group_view(g,workspace/'deliverables/final',workspace/'working/checkpoints',workspace/'.checkpoints',o)
            if contract.get('evidence_view') != 'cumulative_raw':
                reward.update(run_final(step_dir,logs,workspace,evaluate,contract['source_groups'],builder))
        reward['db_protocol_valid']=1.
        dump(logs/'reward.json',reward)
        dump(logs/'evaluation_status.json',{'status':'graded','step':step,'model_called':True})

def validate_incompatible_hits(result,groups,path):
    """Contradictory verdicts are a Judge error, never an extra business penalty."""
    hits={r['rubric_id'] for r in result.get('criteria',[]) if r.get('hit') is True}
    conflicts=[g for g in groups if len(hits.intersection(g))>1]
    if conflicts:
        result.update(status='judge_error',score=None,score_raw=None,
                      error='incompatible_rubric_hits',incompatible_hits=conflicts)
        dump(path,result)
        raise RuntimeError('Judge returned mutually incompatible criterion hits')


if __name__=='__main__':
    status=Path('/logs/verifier/evaluation_status.json')
    dump(status,{'status':'running','step':os.environ.get('TASK_ID')})
    try:
        main()
    except BaseException as exc:
        dump(status,{'status':'judge_error','step':os.environ.get('TASK_ID'),'error_type':type(exc).__name__})
        raise
