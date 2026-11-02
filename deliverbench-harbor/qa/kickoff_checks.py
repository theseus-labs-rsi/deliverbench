"""Unscored briefing, native resume and fail-closed regression checks."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from deliverbench_harbor.agents import Guarded
from deliverbench_harbor.kickoff import run_kickoff, validate, prompt
import hashlib

BRIEF = {'text':'项目背景：交付可复核的研究工程。','timeout_sec':1}
BRIEF['sha256'] = hashlib.sha256(BRIEF['text'].encode()).hexdigest()

def trace(ok=True, reply='收到', session='same-session'):
 return '\n'.join(json.dumps(x,ensure_ascii=False) for x in [
  {'type':'thread.started','thread_id':session},
  {'type':'item.completed','item':{'type':'agent_message','text':reply}},
  {'type':'turn.completed' if ok else 'turn.failed'}])

class Environment:
 def __init__(self, root):
  self.trial_paths=SimpleNamespace(trial_dir=root)
  self.task_contract={'kickoff':BRIEF}
  self.trace=trace();self.saved=[]
 async def exec(self, command, **kwargs):
  out=self.trace if command.startswith('cat ') else json.dumps({'step':'stage'}) if command.startswith('python3') else ''
  return SimpleNamespace(return_code=0,stdout=out)
 async def save_checkpoint(self, step):self.saved.append(step)

class Native:
 def __init__(self, **kwargs):self.messages=[];self.resumed=[];self.fail=False
 def name(self):return 'codex'
 async def run(self, instruction, environment, context):
  self.messages.append(instruction)
  if self.fail:raise RuntimeError('Injected provider failure')
 async def resume(self, instruction, environment, context):
  self.resumed.append(instruction)
  await self.run(instruction, environment, context)

class Candidate(Guarded, Native):pass

class Checks(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.env=Environment(self.root)
 async def asyncTearDown(self):self.tmp.cleanup()
 async def test_one_briefing_then_native_resume_and_one_freeze(self):
  agent=Candidate();await agent.run('FIRST',self.env,object());await agent.resume('SECOND',self.env,object())
  self.assertEqual(agent.messages,[prompt(BRIEF),'FIRST','SECOND'])
  self.assertEqual(agent.resumed,['FIRST','SECOND']);self.assertEqual(self.env.saved,['stage','stage'])
  first=self.root/'steps/stage/agent/codex.txt';first.parent.mkdir(parents=True);first.write_text(trace())
  contract={'kickoff':BRIEF,'steps':['stage']}
  self.assertEqual(validate(self.root,contract),'completed')
  first.write_text(trace(session='different'))
  with self.assertRaises(ValueError):validate(self.root,contract)
 async def test_provider_failure_blocks_first_stage(self):
  agent=Candidate();agent.fail=True
  with self.assertRaises(RuntimeError):await agent.run('FIRST',self.env,object())
  self.assertEqual(agent.messages,[prompt(BRIEF)]);self.assertFalse(agent.resumed);self.assertFalse(self.env.saved)
  self.assertEqual(json.loads((self.root/'kickoff/status.json').read_text())['status'],'failed')
 async def test_empty_reply_and_failed_terminal_block_first_stage(self):
  for t in [trace(reply=''),trace(ok=False)]:
   self.env.trace=t;agent=Candidate()
   with self.assertRaises(RuntimeError):await agent.run('FIRST',self.env,object())
   self.assertFalse(agent.resumed);self.assertFalse(self.env.saved)
 async def test_timeout(self):
  async def slow(*args):await asyncio.sleep(10)
  with self.assertRaises(TimeoutError):await run_kickoff(slow,self.env,object(),{**BRIEF,'timeout_sec':.01})
  self.assertEqual(json.loads((self.root/'kickoff/status.json').read_text())['exception_type'],'TimeoutError')
 async def test_cancel(self):
  async def cancelled(*args):raise asyncio.CancelledError()
  with self.assertRaises(asyncio.CancelledError):await run_kickoff(cancelled,self.env,object(),BRIEF)
  self.assertEqual(json.loads((self.root/'kickoff/status.json').read_text())['status'],'cancelled')
 async def test_tampered_reply_rejected(self):
  await Candidate().run('FIRST',self.env,object())
  first=self.root/'steps/stage/agent/codex.txt';first.parent.mkdir(parents=True);first.write_text(trace())
  (self.root/'kickoff/reply.txt').write_text('changed')
  with self.assertRaises(ValueError):validate(self.root,{'kickoff':BRIEF,'steps':['stage']})
 async def test_oracle_skips_model(self):
  class OracleCandidate(Candidate):execution_kind='oracle'
  agent=OracleCandidate();await agent.run('REFERENCE',self.env,object())
  self.assertEqual(agent.messages,['REFERENCE'])
  self.assertEqual(validate(self.root,{'kickoff':BRIEF},oracle=True),'skipped_oracle')

if __name__=='__main__':unittest.main(verbosity=2)
