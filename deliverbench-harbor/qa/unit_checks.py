"""Shared protocol tests. Supply any migrated project with --project."""
import argparse,copy,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from deliverbench_harbor import __version__
from deliverbench_harbor.contract import load_task,validate_snapshot,trial_contract
from deliverbench_harbor.decisions import validate_unit,validate_step
from deliverbench_harbor.agents import terminal_ok
from deliverbench_harbor.io import inventory
import deliverbench_harbor
p=argparse.ArgumentParser();p.add_argument('--project',required=True,type=Path);args,remaining=p.parse_known_args();ROOT=args.project.resolve();PKG=Path(deliverbench_harbor.__file__).parent

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class Contracts(unittest.TestCase):
 def test_installed_separation(self):
  self.assertFalse(PKG.is_relative_to(ROOT));self.assertFalse((ROOT/'deliverbench_harbor').exists());self.assertFalse((ROOT/'pyproject.toml').exists());self.assertFalse((PKG/'expected_verdicts.json').exists())
 def test_project_contract(self):
  c=load_task(ROOT/'task');self.assertEqual(c['extension_version'],__version__);self.assertTrue(c['expected_verdicts']);self.assertTrue(c['definition_sha256'])
 def test_variable_stages_and_final_names(self):
  for steps in [['alpha'],['draft','review','delivery']]:
   c={'schema_version':1,'steps':steps,'final_step':steps[-1],'expected_verdicts':{s+'/process':[s+'-criterion'] for s in steps}};c['expected_verdicts'][steps[-1]+'/final']=['delivery-criterion'];validate_snapshot(c)
   with tempfile.TemporaryDirectory() as td:
    folder=Path(td);(folder/'process').mkdir();(folder/'final').mkdir()
    for phase in ['process','final']:
     ids=c['expected_verdicts'][steps[-1]+'/'+phase];(folder/phase/'result.json').write_text(json.dumps({'status':'graded','score':0.,'rubric_verdict_count':len(ids),'criteria':[{'rubric_id':i,'status':'graded','hit':False} for i in ids]}))
    validate_step(folder,steps[-1],{'db_score_'+steps[-1]:0.,'db_final_score':0.},c)
 def test_contract_rejects_invalid_stage_sets(self):
  c=load_task(ROOT/'task')
  for mutation in [lambda d:d.update(final_step='unknown'),lambda d:d['steps'].append(d['steps'][0]),lambda d:d['expected_verdicts'].pop(next(iter(d['expected_verdicts']))),lambda d:d['expected_verdicts'].update({'rogue/process':['x']})]:
   altered=copy.deepcopy(c);mutation(altered)
   with self.assertRaises(ValueError):validate_snapshot(altered)
 def test_frozen_contract_tampering(self):
  with tempfile.TemporaryDirectory() as td:
   f=Path(td)/'deliverbench_contract.json';c=load_task(ROOT/'task');f.write_text(json.dumps(c));trial_contract(Path(td));c['final_step']='changed';f.write_text(json.dumps(c))
   with self.assertRaises(ValueError):trial_contract(Path(td))
 def test_decisions_complete(self):
  u={'status':'graded','rubric_verdict_count':2,'criteria':[{'rubric_id':i,'status':'graded','hit':False} for i in ['a','b']]};validate_unit(u,['a','b'])
  for mode in ['missing','duplicate','wrong','non_bool','error']:
   x=copy.deepcopy(u)
   if mode=='missing':x['criteria'].pop()
   if mode=='duplicate':x['criteria'][1]=x['criteria'][0]
   if mode=='wrong':x['criteria'][0]['rubric_id']='other-project'
   if mode=='non_bool':x['criteria'][0]['hit']=1
   if mode=='error':x['criteria'][0]['status']='judge_error'
   with self.assertRaises(ValueError):validate_unit(x,['a','b'])
 def test_native_terminal(self):
  self.assertTrue(terminal_ok('{"type":"turn.completed"}','codex'));self.assertFalse(terminal_ok('{"type":"turn.completed"}\n{"type":"turn.failed"}','codex'));self.assertFalse(terminal_ok('','codex'))
 def test_receipt_and_frozen_bytes(self):
  v=load('verify_test',PKG/'runtime/verify.py')
  with tempfile.TemporaryDirectory() as td:
   w=Path(td);step='draft';b=w/'working/checkpoints'/step;b.mkdir(parents=True);(w/'deliverables/final').mkdir(parents=True);(w/'.checkpoints').mkdir()
   with self.assertRaises(RuntimeError):v.judge_side_certificate(w,step,w/'logs')
   receipt={'step':step,'candidate_completed':True,'attestation':'host_native_success_v1','checkpoint_directory_snapshot':v.directory_snapshot(b),'final_directory_snapshot':v.directory_snapshot(w/'deliverables/final')};(w/'.checkpoints'/f'{step}_deliverbench_output.json').write_text(json.dumps(receipt));v.judge_side_certificate(w,step,w/'logs');(b/'x').write_text('changed')
   with self.assertRaises(RuntimeError):v.judge_side_certificate(w,step,w/'logs')
 def test_secret_preservation(self):
  with tempfile.TemporaryDirectory() as td:
   b=Path(td);(b/'x').write_text('fixture-secret-token')
   with self.assertRaises(RuntimeError):inventory(b,forbidden=(b'fixture-secret-token',))
   self.assertEqual((b/'x').read_text(),'fixture-secret-token')
 def test_no_project_ids_in_runtime(self):
  import re
  for f in PKG.rglob('*.py'):
   if f.name=='testing.py':continue
   self.assertIsNone(re.search(r'P06-|deliverbench-p06|"T06"|\x27T06\x27',f.read_text()),str(f))

if __name__=='__main__':unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)
