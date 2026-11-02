"""Exercise actual package evidence boundaries with synthetic bytes, without a model."""
import argparse, copy, json, shutil, sys, tempfile, unittest
from pathlib import Path
import deliverbench_harbor
parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);args,remaining=parser.parse_known_args();P=args.project
STAGES=json.loads((P/'task/deliverbench.json').read_text())['steps']
sys.path.insert(0,str(Path(deliverbench_harbor.__file__).parent/'runtime'))
from cumulative_evidence import build_view,snapshot,dump,digest


def prepare(w,through=None):
 through=through or STAGES[-1]
 contract=json.loads((P/'task/tests/steps'/through/'runtime_contract.json').read_text())
 shutil.copytree(P/'task/environment/data/source',w/'source')
 (w/'deliverables/final').mkdir(parents=True);(w/'.checkpoints').mkdir()
 for i in range(1,int(through[1:])+1):
  s=f'T{i:02}';delta=P/'task/steps'/s/'workdir/release_materials'
  if delta.is_dir():shutil.copytree(delta,w/'source',dirs_exist_ok=True)
  for event in contract.get('response_events',[]):
   if event['stage']!=s:continue
   definition=next(e for e in json.loads((P/'task/responses/config.json').read_text())['events'] if e['id']==event['id'])
   inputs={rel:digest(w/rel) if (w/rel).is_file() else None for rel in definition['inputs']}
   rows=[]
   for rel in event['outputs']:
    path=w/'source'/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Synthetic response bytes for '+event['id']);rows.append({'path':rel,'sha256':digest(path)})
   dump(w/'.checkpoints/responses'/(event['id']+'.json'),{'id':event['id'],'stage':s,'status':'committed','inputs':inputs,'files':rows})
  files=[]
  for root in ['source','working/checkpoints']:
   for p in (w/root).rglob('*'):
    if p.is_file():files.append({'path':p.relative_to(w).as_posix(),'sha256':digest(p)})
  dump(w/'.checkpoints'/(s+'_input.json'),{'files':files})
  dest=w/'working/checkpoints'/s;dest.mkdir(parents=True)
  for name in contract['outputs'][s]:
   path=dest/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Synthetic stage content '+s)
  (dest/'extra.txt').write_text('An actual extra submitted file from '+s)
  if s==STAGES[-1]:
   for name in contract['final_required_files']:
    path=w/'deliverables/final'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Synthetic final content')
    # A historical file with the same name must never substitute for a missing final file.
    (dest/name).write_text('Synthetic historical content')
  receipt={'step':s,'candidate_completed':True,'attestation':'host_native_success_v1',
           'checkpoint_directory_snapshot':snapshot(dest),'final_directory_snapshot':snapshot(w/'deliverables/final')}
  dump(w/'.checkpoints'/(s+'_deliverbench_output.json'),receipt)
 return contract

class EvidenceChecks(unittest.TestCase):
 def test_each_stage_and_final(self):
  for i in range(1,len(STAGES)+1):
   with tempfile.TemporaryDirectory() as td:
    root=Path(td);w=root/'workspace';c=prepare(w,f'T{i:02}');s=c['current_step'];out=root/'view';m=build_view(c,w,s,out)
    self.assertEqual(m['history_stages'],c['steps'][:i-1]);self.assertTrue((out/'submission/extra.txt').is_file())
    self.assertEqual({p.name for p in (out/'instructions').iterdir()},{'project.md'}|{x+'.md' for x in c['steps'][:i]})
    expected={r['candidate_path'] for r in c['input_files'] if c['steps'].index(r['visible_from'])<i}
    expected|={p for event in c.get('response_events',[]) if c['steps'].index(event['stage'])<i for p in event['outputs']}
    actual={p.relative_to(out/'sources').as_posix() for p in (out/'sources').rglob('*') if p.is_file()}
    self.assertEqual(actual,expected)
    for past in c['steps'][:i-1]:self.assertTrue((out/'history'/past/'extra.txt').is_file())
    self.assertFalse(any((out/'history'/future).exists() for future in c['steps'][i:]))
    if i==len(STAGES):
     final=root/'final';m=build_view(c,w,'FINAL',final);self.assertEqual(m['history_stages'],c['steps'])
     for p in (w/'deliverables/final').iterdir():self.assertEqual(digest(p),digest(final/'submission'/p.name))
     self.assertEqual(m['source_count'],len(expected))
 def test_present_future_materials_remain_hidden(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);w=root/'w';c=prepare(w,'T04')
   future=next(s for s in STAGES[4:] if (P/'task/steps'/s/'workdir/release_materials').exists())
   delta=P/'task/steps'/future/'workdir/release_materials'
   shutil.copytree(delta,w/'source',dirs_exist_ok=True)
   future_cp=w/'working/checkpoints'/STAGES[-1];future_cp.mkdir(parents=True);(future_cp/'future.txt').write_text('FUTURE_ARTIFACT')
   (w/'private-rubric.json').write_text('PRIVATE_SENTINEL')
   build_view(c,w,'T04',root/'view')
   for r in c['input_files']:
    if STAGES.index(r['visible_from'])>=4:self.assertFalse((root/'view/sources'/r['candidate_path']).exists())
   self.assertFalse((root/'view/history'/STAGES[-1]).exists())
   self.assertFalse((root/'view/private-rubric.json').exists())
   for event in c.get('response_events',[]):
    if STAGES.index(event['stage'])>=4:
     for path in event['outputs']:self.assertFalse((root/'view/sources'/path).exists())
   c['stage_instructions']['T08']='FUTURE_PROMPT_SENTINEL'
   with self.assertRaisesRegex(ValueError,'visibility mismatch'):build_view(c,w,'T04',root/'bad')
 def test_history_tampering_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);w=root/'w';c=prepare(w,'T04');(w/'working/checkpoints/T01/extra.txt').write_text('tampered')
   with self.assertRaisesRegex(ValueError,'Frozen checkpoint changed'):build_view(c,w,'T04',root/'view')
 def test_source_missing_or_changed_rejected(self):
  for mode in ['missing','changed','symlink']:
   with tempfile.TemporaryDirectory() as td:
    root=Path(td);w=root/'w';c=prepare(w,'T01');p=next(p for p in (w/'source').rglob('*') if p.is_file());p.unlink()
    if mode=='changed':p.write_text('changed')
    if mode=='symlink':p.symlink_to('/etc/passwd')
    with self.assertRaises(ValueError):build_view(c,w,'T01',root/'view')
 def test_failed_receipt_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);w=root/'w';c=prepare(w,'T02');p=w/'.checkpoints/T01_deliverbench_output.json';r=json.loads(p.read_text());r['candidate_completed']=False;dump(p,r)
   with self.assertRaises(ValueError):build_view(c,w,'T02',root/'view')
 def test_dynamic_receipt_and_bytes_are_required(self):
  config=json.loads((P/'task/tests/steps'/STAGES[-1]/'runtime_contract.json').read_text())
  if not config.get('response_events'):self.skipTest('Task has no dynamic response')
  for mode in ['missing_receipt','changed_bytes']:
   with tempfile.TemporaryDirectory() as td:
    root=Path(td);w=root/'w';c=prepare(w);event=c['response_events'][0]
    if mode=='missing_receipt':(w/'.checkpoints/responses'/(event['id']+'.json')).unlink()
    else:(w/'source'/event['outputs'][0]).write_text('tampered')
    with self.assertRaises(ValueError):build_view(c,w,'FINAL',root/'view')
 def test_submission_link_not_followed(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);w=root/'w';c=prepare(w,'T01');cp=w/'working/checkpoints/T01';(cp/'escape').symlink_to('/etc/passwd')
   p=w/'.checkpoints/T01_deliverbench_output.json';r=json.loads(p.read_text());r['checkpoint_directory_snapshot']=snapshot(cp);dump(p,r)
   m=build_view(c,w,'T01',root/'view');self.assertFalse((root/'view/submission/escape').exists());self.assertIn('symlink',[x['type'] for x in m['submission_snapshot']['entries']])
 def test_missing_final_does_not_use_history(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);w=root/'w';c=prepare(w);f=next((w/'deliverables/final').iterdir());name=f.name;f.unlink()
   p=w/'.checkpoints'/(STAGES[-1]+'_deliverbench_output.json');r=json.loads(p.read_text());r['final_directory_snapshot']=snapshot(w/'deliverables/final');dump(p,r)
   build_view(c,w,'FINAL',root/'view');self.assertFalse((root/'view/submission'/name).exists());self.assertTrue(any((root/'view/history').rglob(name)))

if __name__=='__main__':unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)
