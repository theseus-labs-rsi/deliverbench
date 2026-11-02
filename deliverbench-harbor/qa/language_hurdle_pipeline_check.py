"""Run as root in a disposable verifier container, with network disabled.

Exercises the actual evaluate -> batch -> validation -> scoring path. Only the
Judge CLI process is replaced by explicit synthetic responses; no model calls.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--runtime',required=True,type=Path);a=p.parse_args()
sys.dont_write_bytecode=True;sys.path.insert(0,str(a.runtime))
import blind_judge
import batched_judge
from language_guard import MARKER
assert os.geteuid()==0, 'Run in a disposable verifier container as root'
positive=dict(id='body-present',description='Body present.',weight=2,hurdle=False,target='process_delivery',
              dimension='completeness',evaluation_aspect='instruction_and_constraint',artifact_scope=['body.md'],depends_on=[],bonus=False)
language={**positive,'id':'language','description':MARKER+'中文主体为违规；英文正文、完整英文并列不触发。','weight':0,'hurdle':True}
checks={};cases=[]
with tempfile.TemporaryDirectory() as td:
 root=Path(td);src=root/'submission';src.mkdir()
 (root/'rubric.json').write_text(json.dumps(dict(schema_version='1.1.0',evaluation_unit='CHECK',rubrics=[positive,language])))
 (root/'task.md').write_text('Synthetic pipeline regression.');(root/'judge.md').write_text('Read submitted body.')
 for name,state,hit,expect_status,expect_score in [
   ('english-valid','non_chinese_body',False,'graded',1),
   ('english-reversed','non_chinese_body',True,'judge_error',None),
   ('chinese-valid','chinese_dominant',True,'graded',0),
   ('chinese-reversed','chinese_dominant',False,'judge_error',None),
   ('uncertain','unreadable_or_uncertain',None,'judge_error',None)]:
  body='本次再融资用于偿还现有融资。' if state=='chinese_dominant' else 'The refinancing proceeds repay the existing facilities.'
  (src/'body.md').write_text(body);calls=[]
  def execute(command,question,env,timeout,prefix):
   work=Path(command[command.index('-C')+1]);calls.append(True)
   assert (work/'output/body.md').read_text()==body
   assert not (work/'output/body.md').stat().st_mode & 0o222
   assert '语言违规判据（中文主体）不是要求提交中文' in question
   rows=[]
   for item in json.loads((work/'rubric.json').read_text())['rubrics']:
    row=dict(rubric_id=item['id'],status='graded',hit=True,finding='criterion_satisfied',evidence=['body.md: body'],reason='Synthetic evidence observation.',missing_information=[])
    if item['id']=='language':
     row.update(status='ungradable' if hit is None else 'graded',hit=hit,
       finding='ungradable' if hit is None else 'violation_present' if hit else 'violation_absent',
       missing_information=['Unable to read body.'] if hit is None else [],
       language_assessment=[dict(artifact='body.md',body_language=state,location='body.md: body',quotes=[] if hit is None else [body],basis='Synthetic body fixture observed.')])
    rows.append(row)
   (work/'result/verdict.json').write_text(json.dumps({'verdicts':rows}))
   return {'usage':{k:0 for k in batched_judge.USAGE},'successful_terminal':True,'returncode':0}
  env=dict(JUDGE_MODEL='fixture',JUDGE_API_KEY='fixture',JUDGE_BASE_URL='http://127.0.0.1:1/v1',JUDGE_MAX_TRIES='1',JUDGE_SERVICE_TIER='default')
  with patch.dict(os.environ,env),patch.object(batched_judge,'execute',execute),patch('subprocess.Popen',side_effect=AssertionError('Model execution forbidden')):
   result=blind_judge.evaluate(src,root/'rubric.json',root/'task.md',root/'judge.md',root/name/'result.json')
  checks[name]=result['status']==expect_status and result['score']==expect_score
  assert len(calls)==(1 if expect_status=='graded' else 2)
  assert (root/name/'result.json').is_file()
  cases.append(dict(case=name,status=result['status'],score=result['score'],verdict_count=result['rubric_verdict_count'],synthetic_attempts=len(calls)))
print(json.dumps(dict(passed=all(checks.values()),model_calls=0,checks=checks,cases=cases),indent=2))
sys.exit(0 if all(checks.values()) else 1)
