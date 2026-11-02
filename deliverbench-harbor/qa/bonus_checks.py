"""Exhaustive bonus arithmetic, schema compatibility and metric state checks."""
import itertools,sys,unittest
from pathlib import Path
import deliverbench_harbor
sys.path.insert(0,str(Path(deliverbench_harbor.__file__).parent/'runtime'))
from rubric_schema import require_valid,ContractError
from score_contract import score_unit
from batched_judge import judge_items
from deliverbench_harbor.metric import score_trial

def row(name,weight,bonus=False,parents=None):
 return dict(id=name,description='Inspect the actual file.',weight=weight,hurdle=weight==0,bonus=bonus,depends_on=parents or [],target='process_delivery',dimension='correctness',evaluation_aspect='accuracy',artifact_scope=['.'])
def doc(rows):return dict(schema_version='1.1.0',evaluation_unit='T01',rubrics=rows)

class Checks(unittest.TestCase):
 def test_exhaustive(self):
  rows=[row('a',5),row('b',3,parents=['a']),row('c',2,parents=['a','b']),row('bonus',4,True),row('penalty',-3),row('fatal',0)]
  require_valid(doc(rows))
  for a,b,c,x,n,h in itertools.product([False,True],repeat=6):
   hits=[a,b,c,x,n,h];out=score_unit(rows,[dict(rubric_id=r['id'],hit=hit,status='graded') for r,hit in zip(rows,hits)])
   raw=max(0,min(1,(5*a+3*(a and b)+2*(a and b and c)+4*x-3*n)/10))
   self.assertEqual(out['score_raw'],raw);self.assertEqual(out['score'],0 if h else raw)
   self.assertEqual(out['positive_denominator'],10);self.assertEqual(out['bonus_earned'],4*x);self.assertEqual(out['deductions'],-3*n)
   self.assertEqual(out['breakdown']['dimension']['correctness']['possible'],10)
  self.assertEqual(set(judge_items(rows)[3]),{'id','description','artifact_scope','criterion_type'})
  self.assertEqual(judge_items(rows)[3]['criterion_type'],'positive')
 def test_schema(self):
  require_valid(doc([row('a',2),row('b',1,True)]))
  for rows in [[row('a',1,True)],[row('a',2),row('b',0,True)],[row('a',2),row('b',-1,True)],[row('a',2,1)]]:
   with self.assertRaises(ContractError):require_valid(doc(rows))
  old=doc([row('a',1)]);old['schema_version']='1.0.0';old['rubrics'][0].pop('bonus');require_valid(old)
  old['schema_version']='1.1.0'
  with self.assertRaises(ContractError):require_valid(old)
 def test_metric_states(self):
  c={'steps':['T01','T02']};r={}
  for s in c['steps']:r.update({'db_seen_'+s:.5,'db_complete_'+s:.5,'db_evidence_'+s:.5})
  probe=score_trial({**r,'db_reference_run':1,'db_non_model_probe':1},c)
  self.assertEqual(probe['infrastructure_complete'],1);self.assertEqual(probe['scoring_complete'],0);self.assertIsNone(probe['reward'])
  for s in c['steps']:r.update({'db_graded_'+s:.5,'db_score_'+s:0})
  r['db_final_graded']=.5;r['db_final_score']=0
  actual=score_trial(r,c);self.assertEqual(actual['reward'],0);self.assertEqual(actual['actual_model_evaluation'],1)
  self.assertIsNone(score_trial({**r,'db_non_model_probe':1},c)['reward'])
  del r['db_graded_T01'];self.assertEqual(score_trial(r,c)['scoring_complete'],0);self.assertIsNone(score_trial(r,c)['reward'])

if __name__=='__main__':unittest.main(verbosity=2)
