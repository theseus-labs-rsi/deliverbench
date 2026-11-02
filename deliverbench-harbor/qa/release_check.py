"""Check independently distributed extension and task; no model calls."""
import argparse,hashlib,json,re,sys,tomllib
from pathlib import Path
from importlib.metadata import version,entry_points
import harbor,deliverbench_harbor
from deliverbench_harbor.contract import load_task
p=argparse.ArgumentParser();p.add_argument('--project',required=True,type=Path);a=p.parse_args();project=a.project.resolve();extension=Path(__file__).resolve().parents[1];checks=[]
def check(name,result):checks.append({'check':name,'passed':bool(result)})
check('stock_harbor_0_20_0',version('harbor')=='0.20.0')
check('extension_version',version('deliverbench-harbor')=='0.7.2')
check('plugin_entrypoint',any(e.name=='deliverbench' and e.value=='deliverbench_harbor.lifecycle:Plugin' for e in entry_points(group='harbor.plugins')))
check('no_harbor_source_in_distributions',not (extension/'harbor').exists() and not (project/'harbor').exists())
check('installed_harbor_outside_distributions',all(not Path(harbor.__file__).resolve().is_relative_to(root) for root in [project,extension]))
check('installed_extension_outside_distributions',all(not Path(deliverbench_harbor.__file__).resolve().is_relative_to(root) for root in [project,extension]))
check('project_has_no_extension',not (project/'deliverbench_harbor').exists() and not (project/'pyproject.toml').exists() and not list(project.rglob('*.whl')))
check('project_has_no_shared_judge_code',not list((project/'task/tests').glob('*.py')) and not (project/'task/environment/deliverbench').exists())
c=load_task(project/'task');check('native_schema_and_task_contract',bool(c['expected_verdicts']))
check('no_fixed_verdict_table',not (extension/'src/deliverbench_harbor/expected_verdicts.json').exists())
installed=Path(deliverbench_harbor.__file__).resolve().parent;source=extension/'src/deliverbench_harbor'
files=[f for f in source.rglob('*') if f.is_file() and '__pycache__' not in f.parts]
check('installed_runtime_matches_source',all((installed/f.relative_to(source)).is_file() and (installed/f.relative_to(source)).read_bytes()==f.read_bytes() for f in files))
check('no_project_ids_or_stage_constants_in_runtime',all(not re.search(rb'P06-|deliverbench-p06|[\"\x27]T06[\"\x27]',f.read_bytes()) for f in files if f.suffix=='.py'))
for label,root in [('extension',extension),('project',project)]:
 checksum=root/'SHA256SUMS';entries=[line.split('  ',1) for line in checksum.read_text().splitlines()] if checksum.exists() else []
 actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=checksum and '__pycache__' not in p.parts and p.name!='.DS_Store'}
 check(label+'_checksums_complete',bool(entries) and len({n for h,n in entries})==len(entries) and {n for h,n in entries}==actual)
 check(label+'_checksums_match',all((root/n).is_file() and hashlib.sha256((root/n).read_bytes()).hexdigest()==h for h,n in entries))
 check(label+'_no_generated_outputs',not any((root/n).exists() for n in ['.venv','build','dist','jobs','.env']) and not list(root.rglob('*.egg-info')))
 check(label+'_no_host_paths_or_keys',not any(re.search(b'/' + rb'Users/|sk' + rb'-[A-Za-z0-9_-]{12,}',f.read_bytes()) for f in root.rglob('*') if f.is_file() and f.suffix in ['.py','.md','.toml','.yaml','.json','.txt']))
r={'passed':all(c['passed'] for c in checks),'no_model_calls':True,'checks':checks,'step_count':len(c['steps'])};print(json.dumps(r,indent=2));sys.exit(0 if r['passed'] else 1)
