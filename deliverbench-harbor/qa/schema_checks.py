"""Schema/projection/dependency regression tests; no model calls."""
import argparse, copy, itertools, json, sys, unittest
from pathlib import Path
from decimal import Decimal
import deliverbench_harbor
parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True)
args,remaining=parser.parse_known_args();PROJECT=args.project
sys.path.insert(0,str(Path(deliverbench_harbor.__file__).parent/'runtime'))
from rubric_schema import require_valid, load_json, ContractError, integer_weight
from score_contract import score_unit
from batched_judge import judge_items
from batch_core import accept_rows, run_batches
from blind_judge import validate_verdict


def row(rid,weight=1,depends=None):
 return dict(id=rid,description='Check actual submitted evidence.',weight=weight,hurdle=weight==0,target='process_delivery',dimension='correctness',evaluation_aspect='accuracy',artifact_scope=['.'],depends_on=depends or [])
def document(rows):return dict(schema_version='1.0.0',evaluation_unit='T01',rubrics=rows)
def decisions(rows,hits):return [dict(rubric_id=r['id'],status='graded',hit=h,evidence=['submission/file.txt: line 1'],reason='Verified actual content.',missing_information=[],finding=('criterion_satisfied' if h else 'criterion_not_satisfied') if r['weight']>0 else ('violation_present' if h else 'violation_absent')) for r,h in zip(rows,hits)]

class SchemaChecks(unittest.TestCase):
 def test_directional_rounding(self):
  for value,expected in [('2.8',2),('0.5',1),('-2.2',-3),('-0.5',-1),('3',3),('-2',-2)]:
   self.assertEqual(integer_weight(Decimal(value)),expected)
  self.assertEqual(integer_weight(0,hurdle=True),0)
  for bad in [True,float('nan'),float('inf'),0]:
   with self.assertRaises(ContractError):integer_weight(bad)
 def test_project_documents(self):
  docs=[require_valid(load_json(p)) for p in (PROJECT/'task/tests/steps').rglob('*rubric.json')]
  stages=json.loads((PROJECT/'task/deliverbench.json').read_text())['steps']
  self.assertEqual(len(docs),len(stages)+1)
  for d in docs:
   folder=PROJECT/'task/tests/steps'/(stages[-1] if d['evaluation_unit']=='FINAL' else d['evaluation_unit'])
   c=json.loads((folder/'runtime_contract.json').read_text())
   denominator=sum(r['weight'] for r in d['rubrics'] if r['weight']>0 and not r.get('bonus',False))
   self.assertEqual(denominator,c['final_positive_denominator' if d['evaluation_unit']=='FINAL' else 'positive_denominator'])
  for d in docs:
   for r in d['rubrics']:self.assertIs(type(r['weight']),int)
 def test_invalid_fields_and_values(self):
  base=document([row('a'),row('b')])
  mutations=[lambda d:d.update(schema_version='2'),lambda d:d.update(extra=0),lambda d:d['rubrics'][0].update(weight=.5),lambda d:d['rubrics'][0].update(weight=True),lambda d:d['rubrics'][0].update(weight=0),lambda d:d['rubrics'][0].update(dimension='unknown'),lambda d:d['rubrics'][0].update(extra='x'),lambda d:d['rubrics'][0].pop('description'),lambda d:d['rubrics'][0].update(artifact_scope=['../x']),lambda d:d['rubrics'][0].update(artifact_scope=['/tmp/x']),lambda d:d['rubrics'][0].update(artifact_scope=['.','x']),lambda d:d['rubrics'][0].update(depends_on=['unknown']),lambda d:d['rubrics'][0].update(depends_on=['a']),lambda d:d['rubrics'][1].update(id='a')]
  for mutation in mutations:
   d=copy.deepcopy(base);mutation(d)
   with self.assertRaises(ContractError):require_valid(d)
  for rows in [[row('a',1,['b']),row('b',1,['a'])],[row('a'),row('b',-1,['a'])],[row('a'),row('b',0,['a'])],[row('a',1,['b']),row('b',-1)]]:
   with self.assertRaises(ContractError):require_valid(document(rows))
 def test_partial_run_has_no_final_score(self):
  from deliverbench_harbor.metric import score_trial
  stages=[f'T{i:02}' for i in range(1,9)]
  rewards={}
  for stage in stages[:3]:
   rewards.update({'db_seen_'+stage:1/3,'db_complete_'+stage:1/3,'db_graded_'+stage:1/3,'db_score_'+stage:0.5/3})
  result=score_trial(rewards,{'steps':stages})
  self.assertEqual(result['observed_steps'],3)
  self.assertEqual(result['infrastructure_complete'],0)
  self.assertEqual(result['scoring_complete'],0)
  self.assertIsNone(result['final_delivery_quality'])
  self.assertIsNone(result['reward'])
 def test_projection(self):
  visible=judge_items([row('a',2),row('b',-3),row('c',0)])
  self.assertEqual([x['criterion_type'] for x in visible],['positive','negative','hurdle'])
  self.assertTrue(all(set(x)=={'id','description','artifact_scope','criterion_type'} for x in visible))
 def test_all_boolean_vectors(self):
  rows=[row('a',3),row('b',2,['a']),row('c',1,['a','b']),row('penalty',-2),row('fatal',0)]
  require_valid(document(rows))
  for a,b,c,n,h in itertools.product([False,True],repeat=5):
   hits=[a,b,c,n,h];result=score_unit(rows,decisions(rows,hits))
   effective=[a,a and b,a and b and c,n,h];earned=3*effective[0]+2*effective[1]+effective[2]-2*n
   raw=max(0,min(1,earned/6))
   self.assertEqual(result['score_raw'],raw);self.assertEqual(result['score'],0 if h else raw)
   self.assertEqual([r['self_hit'] for r in result['criteria']],hits)
   self.assertEqual([r['effective_hit'] for r in result['criteria']],effective)
   self.assertEqual(result['criteria'][2]['blocked_by'],[k for k,v in zip(['a','b'],effective[:2]) if not v])
 def test_incomplete_is_not_zero(self):
  rows=[row('a'),row('b')];vs=decisions(rows,[False,False])
  self.assertEqual(score_unit(rows,vs)['score'],0)
  for bad in [vs[:1],vs+[vs[0]],[vs[0],vs[0]]]:
   with self.assertRaises(ValueError):score_unit(rows,bad)
  vs[0].update(status='ungradable',hit=None)
  self.assertIsNone(score_unit(rows,vs)['score'])
 def test_duplicate_and_missing_verdict(self):
  rows=[row('a'),row('b')];vs=decisions(rows,[True,False])
  accepted,errors=accept_rows({'verdicts':[vs[0],vs[0]]},rows,validate_verdict)
  self.assertFalse(accepted);self.assertEqual(set(errors),{'a','b'})
 def test_failed_batch_never_imputed(self):
  def fail(items,label,fallback):raise TimeoutError('Injected Judge timeout')
  got,records=run_batches([row('a'),row('b')],fail,batch_size=2,concurrency=1)
  self.assertEqual(got,{});self.assertEqual(len(records),3)
 def test_missing_retry_preserves_valid(self):
  rows=[row('a'),row('b')]
  def invoke(items,label,fallback):return ({r['id']:{} for r in items if fallback or r['id']=='a'}, {'fallback':fallback})
  got,records=run_batches(rows,invoke,2,1)
  self.assertEqual(set(got),{'a','b'});self.assertEqual(len(records),2)

if __name__=='__main__':unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)
