"""Run as root inside the verifier image. No models or external API calls.

Mount the extension's runtime read-only and pass --runtime /path/to/runtime.
Checks the actual batch entry point using deliberately unparseable Office/PDF
bytes and a synthetic Judge response; this does not test model reading quality.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch

p=argparse.ArgumentParser();p.add_argument('--runtime',required=True,type=Path);a=p.parse_args()
sys.dont_write_bytecode=True
sys.path.insert(0,str(a.runtime))
import blind_judge
import batched_judge
from artifact_io import copy_artifacts

assert os.geteuid()==0,'Run inside a disposable verifier container as root'
checks={}
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);src=root/'input';src.mkdir()
    # Parsing these files would fail: preparation must only copy and hash them.
    for name in ['原件.xlsx','原件.docx','原件.pptx','原件.pdf','note.md']:
        (src/name).write_bytes(('raw test bytes '+name).encode())
    outside=root/'private.txt';outside.write_text('not visible')
    (src/'external.docx').symlink_to(outside)
    (src/'.deliverbench_extra.json').write_text('An additional submitted file, not trusted control metadata')
    expected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir() if p.is_file() and not p.is_symlink()}
    (root/'task.md').write_text('Synthetic protocol check.')
    (root/'judge.md').write_text('Read raw evidence with tools as needed.')
    item={'id':'raw-file-check','description':'Synthetic criterion','weight':1,'hurdle':False,'depends_on':[],
          'dimension':'correctness','evaluation_aspect':'accuracy','target':'process_delivery','artifact_scope':['.']}
    (root/'rubric.json').write_text(json.dumps({'schema_version':'1.0.0','evaluation_unit':'T01','rubrics':[item]}))
    observed=[]
    def execute(command,question,env,timeout,prefix):
        work=Path(command[command.index('-C')+1]);uid=int(command[command.index('--reuid')+1])
        files={str(p.relative_to(work/'output')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (work/'output').rglob('*') if p.is_file()}
        assert files==expected
        assert not (work/'views').exists()
        assert all(not p.stat().st_mode & 0o222 for p in (work/'output').rglob('*') if p.is_file())
        assert (work/'scratch').stat().st_uid==uid
        assert 'scratch/' in question and 'artifact_manifest.json' in question
        manifest=json.loads((work/'artifact_manifest.json').read_text())
        assert any(r['path']=='external.docx' and r['kind']=='symlink_not_followed' for r in manifest)
        visible=json.loads((work/'rubric.json').read_text())['rubrics']
        assert all(set(r)=={'id','description','artifact_scope','criterion_type'} for r in visible)
        assert visible[0]['criterion_type']=='positive'
        observed.append(True)
        row={'rubric_id':item['id'],'status':'graded','hit':False,'finding':'criterion_not_satisfied',
             'reason':'Synthetic response; no model evaluation.','evidence':[],'missing_information':[]}
        (work/'result/verdict.json').write_text(json.dumps({'verdicts':[row]}))
        return {'usage':{k:0 for k in batched_judge.USAGE},'successful_terminal':True,'returncode':0}
    env={'JUDGE_API_KEY':'synthetic-only','JUDGE_BASE_URL':'http://127.0.0.1:1/v1','JUDGE_MODEL':'synthetic','JUDGE_MAX_TRIES':'1'}
    with patch.dict(os.environ,env),patch.object(batched_judge,'execute',execute),patch('subprocess.Popen',side_effect=AssertionError('Pre-Judge process launch forbidden')):
        result=blind_judge.evaluate(src,root/'rubric.json',root/'task.md',root/'judge.md',root/'result/result.json')
    checks['raw_bytes_readonly_scope_and_scratch']=observed==[True]
    checks['no_pre_judge_subprocess']=True
    checks['complete_valid_zero']=result['status']=='graded' and result['score']==0 and result['rubric_verdict_count']==1
    checks['explicit_raw_mode']=result['evidence_mode']=='raw_files'
    saved=root/'result'/batched_judge.PROTOCOL/'evidence'
    checks['snapshot_has_no_auxiliary_views']=not (saved/'views').exists() and not (root/'result/artifact_view_manifest.json').exists()
    checks['raw_snapshot_hashes_preserved']={str(p.relative_to(saved/'output')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (saved/'output').rglob('*') if p.is_file()}==expected
    checks['no_extraction_modules']=not any((a.runtime/n).exists() for n in ['artifact_views.py','office_observe.py','workbook_preview.py','markdown_view.py'])
    for failure in ['timeout','missing','ungradable']:
        def failed_execute(command,question,env,timeout,prefix):
            work=Path(command[command.index('-C')+1])
            meta={'usage':{k:0 for k in batched_judge.USAGE},'returncode':0,'successful_terminal':True}
            if failure=='timeout':return {**meta,'error':'CLI timeout/failure or missing successful terminal','timeout':True}
            row={'rubric_id':item['id'],'status':'ungradable','hit':None,'finding':'ungradable',
                 'reason':'Synthetic tool failure.','evidence':[],'missing_information':['Unreadable evidence']}
            payload={'verdicts':[] if failure=='missing' else [row]}
            (work/'result/verdict.json').write_text(json.dumps(payload))
            return meta
        with patch.dict(os.environ,env),patch.object(batched_judge,'execute',failed_execute),patch('subprocess.Popen',side_effect=AssertionError('Pre-Judge process launch forbidden')):
            failed=blind_judge.evaluate(src,root/'rubric.json',root/'task.md',root/'judge.md',root/failure/'result.json')
        checks[failure+'_is_error_not_low_score']=failed['status']=='judge_error' and failed['score'] is None and failed['rubric_verdict_count']==0

print(json.dumps({'passed':all(checks.values()),'no_model_calls':True,'checks':checks},indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
