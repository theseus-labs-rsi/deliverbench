"""Final delivery scoring over the published integer-weight rubric."""
from pathlib import Path
import json,shutil,tempfile

def dump(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def safe_copy(src,dst):
 if src.is_symlink()or not src.exists():return
 if src.is_file():dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
 elif src.is_dir():
  dst.mkdir(parents=True,exist_ok=True)
  for p in src.iterdir():safe_copy(p,dst/p.name)
def signals(result):
 if result.get('status')!='graded':raise ValueError('Incomplete final Judge decisions; no final score')
 b=result['breakdown']['target'];out={'db_final_graded':1.,'db_final_hurdle':float(bool(result['hurdles'])),'db_final_score':result['score'],'db_final_raw':result['score_raw']}
 for target,key in [('final_artifact','db_artifact'),('cross_file_relation','db_cross_file'),('package_delivery','db_package_delivery')]:
  if target not in b or not b[target]['possible']:raise ValueError('Missing positive ACP component')
  out[key]=b[target]['score'];out[key+'_earned']=b[target]['earned'];out[key+'_possible']=b[target]['possible']
 for dim,v in result['breakdown']['dimension'].items():
  out['db_final_dim_'+dim+'_earned']=v['earned'];out['db_final_dim_'+dim+'_possible']=v['possible']
 return out

def run_final(tests,logs,workspace,evaluate,groups,source_builder,extra_context=None):
 """Authoritative final directory plus explicitly separated final-source views.

 Source views bind actual final bytes to the source task's filenames. Prior
 non-final artifacts may be context only; they cannot fill missing final files.
 """
 with tempfile.TemporaryDirectory(prefix='delivery-final-')as td:
  view=Path(td)/'output';view.mkdir();safe_copy(workspace/'deliverables/final',view/'delivery')
  inventory=[{'path':str(p.relative_to(workspace/'deliverables/final')),'type':'symlink'if p.is_symlink()else'file'if p.is_file()else'directory'}for p in sorted((workspace/'deliverables/final').rglob('*'))]
  dump(view/'delivery_inventory.json',inventory)
  for group in groups:
   unit=group if isinstance(group,str)else group['step'];dest=view/'artifact_views'/unit;dest.mkdir(parents=True,exist_ok=True);source_builder(group,dest)
  if extra_context:extra_context(view/'business_evidence')
  instruction=tests/'judge_instruction.md' if (tests/'judge_instruction.md').exists() else tests.parent.parent/'judge_instruction.md'
  result=evaluate(view,tests/'final_rubric.json',tests/'final_task_prompt.md',instruction,logs/'final/result.json')
 return signals(result)
