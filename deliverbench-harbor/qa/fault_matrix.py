"""Exercise released extensions via stock CLI and Docker, without model calls.
Usage: python qa/fault_matrix.py --output /absolute/path/out
Synthetic fixture scores are never benchmark results.
"""
import argparse,json,os,shutil,signal,subprocess,sys,time,hashlib
from pathlib import Path

PACKAGE=Path(__file__).resolve().parents[1]
CASES=['zero','empty','candidate_failure','candidate_timeout','oracle_nonzero','judge_timeout','judge_missing','partial','cancel_candidate','cancel_judge','storage_failure']

def criterion(rid,target):
    return dict(id=rid,description='Synthetic transport fixture.',weight=1,hurdle=False,target=target,dimension='correctness',evaluation_aspect='accuracy',artifact_scope=['.'],depends_on=[])

def build(root,case):
    task=root/'fixtures'/case
    files={
        'instruction.md':'Synthetic execution fixture; not a benchmark.',
        'environment/docker-compose.yaml':'services:\n  main:\n    extra_hosts:\n      - "host.docker.internal:host-gateway"\n',
        'environment/Dockerfile':'''FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends sudo procps && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 candidate && mkdir -p /workspace/source/W0 /workspace/.checkpoints /workspace/working/checkpoints /workspace/deliverables/final /logs/agent && chown -R candidate:candidate /workspace/deliverables /logs/agent
RUN echo candidate > /opt/deliverbench-role
WORKDIR /workspace
CMD ["sleep","infinity"]
''',
        'tests/Dockerfile':'''FROM python:3.12-slim-bookworm
COPY . /tests
RUN mkdir -p /workspace /logs/verifier && echo verifier > /opt/deliverbench-role
WORKDIR /workspace
CMD ["sleep","infinity"]
''',
    }
    steps=3 if case=='partial' else 6
    toml='''schema_version = "1.3"
multi_step_reward_strategy = "mean"
artifacts = ["/workspace/working", "/workspace/deliverables", "/workspace/.checkpoints"]
[agent]
user = "candidate"
timeout_sec = 30
[environment]
build_timeout_sec = 600
workdir = "/workspace"
[verifier]
environment_mode = "separate"
timeout_sec = 30
'''
    for i in range(1,steps+1):
        step=f'T{i:02}'
        command={'candidate_failure':'FAIL','candidate_timeout':'CANDIDATE_TIMEOUT','cancel_candidate':'CANCEL_CANDIDATE'}.get(case,'OK') if i==1 else 'OK'
        toml+=f'\n[[steps]]\nname = "{step}"\nmin_reward = {{ db_protocol_valid = 1.0 }}\n[steps.agent]\ntimeout_sec = {2 if case=="candidate_timeout" else 60}\n[steps.verifier]\ntimeout_sec = {2 if case=="judge_timeout" else 60}\n[steps.verifier.env]\nTASK_ID = "{step}"\n'
        files[f'steps/{step}/instruction.md']=command
        files[f'steps/{step}/workdir/deliverbench_stage.json']=json.dumps({'step':step})
        files[f'steps/{step}/workdir/setup.sh']='#!/bin/bash\nset -euo pipefail\nsudo /usr/local/bin/python3 -I /opt/deliverbench/stage.py\n'
        if i in (5,6): files[f'steps/{step}/workdir/release_materials/E0{i}/sentinel.txt']=step
    files['task.toml']=toml
    solve='''#!/bin/bash
set -euo pipefail
step=$(cat /workspace/.checkpoints/deliverbench_current_step)
case "$step" in T01|T02|T03|T04) test ! -e /workspace/source/E05; test ! -e /workspace/source/E06 ;; T05) test -f /workspace/source/E05/sentinel.txt; test ! -e /workspace/source/E06 ;; T06) test -f /workspace/source/E06/sentinel.txt ;; esac
if [ "$step" != T01 ]; then
  if touch /workspace/working/checkpoints/T01/forbidden 2>/dev/null; then exit 91; fi
fi
'''
    if case!='empty': solve+='echo "$step" > /workspace/working/current/output.txt\n'
    if case=='oracle_nonzero': solve+='exit 7\n'
    files['solution/solve.sh']=solve
    judge='''import json,os,time
from pathlib import Path
s=os.environ['TASK_ID']; p=Path('/logs/verifier');p.mkdir(exist_ok=True,parents=True)
(p/'ready').write_text('ready')
r={'reward':0.,'db_complete_'+s:1.,'db_evidence_'+s:1.,'db_graded_'+s:1.,'db_seen_'+s:1.,'db_score_'+s:0.,'db_protocol_valid':1.}
if s=='T06':r.update(db_final_graded=1.,db_final_score=0.)
'''
    if case in ('judge_timeout','cancel_judge'):judge+='time.sleep(600)\n'
    if case=='judge_missing':judge+="r['db_graded_'+s]=0.;r['db_protocol_valid']=0.\n"
    judge+="(p/'reward.json').write_text(json.dumps(r))\n(p/'evaluation_status.json').write_text(json.dumps({'status':'graded'}))\n"
    judge += "\nu={'status':'graded','rubric_verdict_count':1,'score':0.,'criteria':[{'rubric_id':s+'-fixture','status':'graded','hit':False}]}\n(p/'process').mkdir(exist_ok=True);(p/'process/result.json').write_text(json.dumps(u))\nif s=='T06':\n u['rubric_verdict_count']=3;u['criteria']=[{'rubric_id':'final-'+t,'status':'graded','hit':False} for t in ['final_artifact','cross_file_relation','package_delivery']];(p/'final').mkdir(exist_ok=True);(p/'final/result.json').write_text(json.dumps(u))\n"
    files['tests/judge.py']=judge
    files['tests/test.sh']='#!/bin/bash\nset -euo pipefail\nexec python3 /tests/judge.py\n'
    for name,text in files.items():
        p=task/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        if p.suffix=='.sh':p.chmod(0o755)
    stages=[f'T{i:02}' for i in range(1,7)]
    manifest={'schema_version':1,'project_id':'synthetic-fixture','project_version':'1',
              'extension_version':__import__('deliverbench_harbor').__version__,'rubric_schema_version':'1.0.0','evidence_view':'cumulative_raw','harbor_version':'0.20.0','steps':stages,'final_step':stages[-1],
              'continuous_conversation':True,'scoring':'flat_integer','tests_dir':'tests','metric_contract':'metric_contract.json'}
    (task/'deliverbench.json').write_text(json.dumps(manifest))
    (task/'metric_contract.json').write_text(json.dumps({'steps':stages,'scoring':'flat_integer','positive_denominator':3}))
    (task/'tests/judge_instruction.md').write_text('Synthetic transport fixture; no model quality claim.')
    inputs=[{'candidate_path':'E0'+str(i)+'/sentinel.txt','visible_from':f'T{i:02}','sha256':hashlib.sha256(f'T{i:02}'.encode()).hexdigest()} for i in (5,6) if i<=steps]
    for step in stages:
        folder=task/'tests/steps'/step;folder.mkdir(parents=True,exist_ok=True)
        (folder/'runtime_contract.json').write_text(json.dumps({'steps':stages,'current_step':step,'outputs':{x:['output.txt'] for x in stages},'rubric_schema_version':'1.0.0','evidence_view':'cumulative_raw','project_instruction':files['instruction.md'],'stage_instructions':{x:files.get(f'steps/{x}/instruction.md','OK') for x in stages[:stages.index(step)+1]},'positive_denominator':1,'final_positive_denominator':3,'input_files':inputs,'final_rubric_sources':{'final-'+t:'FINAL' for t in ['final_artifact','cross_file_relation','package_delivery']}}))
        (folder/'rubric.json').write_text(json.dumps({'schema_version':'1.0.0','evaluation_unit':step,'rubrics':[criterion(step+'-fixture','process_delivery')]}))
        if step==stages[-1]:(folder/'final_rubric.json').write_text(json.dumps({'schema_version':'1.0.0','evaluation_unit':'FINAL','rubrics':[criterion('final-'+target,target) for target in ['final_artifact','cross_file_relation','package_delivery']]}))

    config={'job_name':case,'jobs_dir':str(root/'jobs'),'n_attempts':1,'retry':{'max_retries':0},
            'tasks':[{'path':str(task)}],
            'agents':[{'import_path':'deliverbench_harbor.testing:FaultOracle','kwargs':{'task_dir':str(task)},'resume_trajectory':False}],
            'environment':{'type':'docker','import_path':'deliverbench_harbor.environment:EvidenceDocker'},
            'verifier':{'import_path':'deliverbench_harbor.verifier:CompleteVerifier'}}
    if case=='storage_failure':config['environment']['import_path']='deliverbench_harbor.testing:DiskFailureDocker'
    path=root/(case+'.json');path.write_text(json.dumps(config));return path

def run(root,case):
    cp=build(root,case)
    command=[str(Path(sys.executable).with_name('harbor')),'run','-c',str(cp),'--plugin','deliverbench_harbor.lifecycle:Plugin','-y']
    env=os.environ.copy();env.pop('PYTHONPATH',None)
    env.update(PYTHONDONTWRITEBYTECODE='1',LITELLM_LOCAL_MODEL_COST_MAP='True',JUDGE_API_KEY='synthetic-fixture',JUDGE_BASE_URL='http://fixture.invalid/v1',JUDGE_MODEL='fixture',DELIVERBENCH_INFRA_PROBE='false')
    with (root/(case+'.log')).open('w') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True,cwd=PACKAGE)
        cancelled=False
        for _ in range(900):
            if proc.poll() is not None:break
            if case.startswith('cancel_') and not cancelled:
                kind='agent' if case=='cancel_candidate' else 'verifier'
                ready=list((root/'jobs'/case).glob(f'*/{kind}/ready'))
                if ready:os.killpg(proc.pid,signal.SIGINT);cancelled=True
            time.sleep(1)
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGINT)
        code=proc.wait(timeout=60)
    paths=list((root/'jobs'/case).glob('*/result.json'))
    if case=='partial' and not paths:
        passed=code!=0 and 'must exactly match' in (root/(case+'.log')).read_text()
        return {'case':case,'passed':passed,'exit_code':code,'checks':{'partial_task_rejected_before_execution':passed},'synthetic':True}
    if len(paths)!=1:return {'case':case,'passed':False,'error':'missing trial result','exit_code':code}
    result=json.loads(paths[0].read_text());trial=paths[0].parent;steps=result.get('step_results') or []
    from deliverbench_harbor.acceptance import check_job
    accepted=check_job(root/'jobs'/case,verify_evidence=True)
    expected=6 if case in ('zero','empty') else 3 if case=='partial' else 1
    check={'step_count':len(steps)==expected,'acceptance':accepted['complete']==(case in ('zero','empty'))}
    if case in ('zero','empty'):
        check['valid_zero']=all((s.get('verifier_result') or {}).get('rewards',{}).get('reward')==0 for s in steps)
    if case=='empty':
        check['no_outputs']=not list((trial/'evidence/candidate/T01/workspace/working/checkpoints/T01').iterdir())
    if case.startswith('cancel_'):
        check['cancelled']=bool((trial/'cancellation.json').exists()) and code==130
    if case in ('candidate_failure','candidate_timeout','cancel_candidate'):
        check['partial_preserved']=(trial/'evidence/candidate/at_stop/workspace/working/checkpoints/T01/partial.txt').is_file()
        check['no_receipt']=not (trial/'evidence/candidate/T01/manifest.json').exists()
    if case=='storage_failure':
        check['storage_status']=(trial/'evidence/storage_status.json').is_file()
        check['no_delete']=bool(subprocess.check_output(['docker','ps','-a','--filter',f'label=com.docker.compose.project={trial.name.lower()}__env','--format','{{.ID}}'],text=True).strip())
    if case in ('judge_timeout','judge_missing','cancel_judge'):
        check['candidate_committed']=(trial/'evidence/candidate/T01/manifest.json').is_file()
    row={'case':case,'passed':all(check.values()),'checks':check,'exit_code':code,'synthetic':True,'acceptance':accepted}
    print(json.dumps({'case':case,'passed':row['passed'],'checks':check}),flush=True)
    return row

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);parser.add_argument('--cases',nargs='+',default=CASES);args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    rows=[]
    for case in args.cases:
        rows.append(run(root,case));(root/'fault_results.json').write_text(json.dumps({'passed':all(r['passed'] for r in rows),'no_model_calls':True,'results':rows},indent=2))
    sys.exit(0 if all(r['passed'] for r in rows) else 1)
