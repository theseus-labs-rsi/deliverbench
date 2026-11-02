"""Portable deterministic responder checks. No provider or Docker calls."""
import json
import tempfile
import unittest
from pathlib import Path
from deliverbench_harbor.responses import generate, commit_event, review, ResponseFailure

class ResponseChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task = self.root / 'task'
        (self.task / 'responses').mkdir(parents=True)
        (self.task / 'responses/facts.json').write_text(json.dumps({'version': '1', 'facts': [
            {'id':'start', 'question':'Actual start date', 'answer':'2 October'},
            {'id':'stop', 'question':'Actual stop date', 'answer':'9 November'}]}))
        self.event = {'id':'CUSTOMER', 'stage':'T02', 'kind':'questions',
                      'inputs':['working/checkpoints/T01/request.md'],
                      'request_inputs':['working/checkpoints/T01/request.md'],
                      'outputs':['E02/reply.md'], 'facts_file':'facts.json',
                      'heading':'Customer reply', 'intro':'Reply to the actual request.',
                      'unknown_reply':'Not available.', 'empty_reply':'No request received.', 'language':'en'}
        self.request = self.root / self.event['inputs'][0]
        self.request.parent.mkdir(parents=True)
        self.request.write_text('What was the actual start date?')

    def tearDown(self):
        self.temp.cleanup()

    def caller(self, system, payload, schema, audit, name):
        self.assertNotIn('2 October', json.dumps(payload))
        self.assertNotIn('9 November', json.dumps(payload))
        return {'questions':[{'document':self.event['inputs'][0],
                              'quote':self.request.read_text(), 'fact_ids':['start']}]}

    def fail(self, *args):
        raise AssertionError('Unexpected model call')

    def test_only_requested_fact_is_released(self):
        generate(self.event, self.root, self.task/'responses', self.root/'public', self.root/'audit', self.caller)
        result=(self.root/'public/E02/reply.md').read_text()
        self.assertIn('2 October', result)
        self.assertNotIn('9 November', result)

    def test_empty_is_valid_business_state(self):
        self.request.write_text('')
        generate(self.event, self.root, self.task/'responses', self.root/'public', self.root/'audit', self.fail)
        self.assertIn('No request received.', (self.root/'public/E02/reply.md').read_text())

    def test_committed_reply_reused_without_model(self):
        first, receipt=commit_event(self.event, self.root, self.task, self.root/'job/attempt1', self.caller)
        second, reused=commit_event(self.event, self.root, self.task, self.root/'job/attempt2', self.fail)
        self.assertEqual(receipt, reused)
        self.assertEqual((first/'public/E02/reply.md').read_bytes(), (second/'public/E02/reply.md').read_bytes())
        self.assertTrue((second/'reuse.json').is_file())

    def test_changed_input_cannot_overwrite_commit(self):
        commit_event(self.event, self.root, self.task, self.root/'trial', self.caller)
        self.request.write_text('Changed request')
        with self.assertRaises(ResponseFailure):
            commit_event(self.event, self.root, self.task, self.root/'trial', self.fail)

    def test_timeout_is_not_empty_reply(self):
        def timeout(*args): raise TimeoutError('Synthetic timeout')
        with self.assertRaises(TimeoutError):
            commit_event(self.event, self.root, self.task, self.root/'trial', timeout)
        self.assertFalse((self.root/'trial/responses/CUSTOMER/receipt.json').exists())
        self.assertEqual(json.loads((self.root/'trial/responses/CUSTOMER.pending/status.json').read_text())['status'], 'failed')

    def test_corrupt_input_is_pipeline_error(self):
        self.request.write_bytes(b'\xff\xfe\xf9')
        with self.assertRaises(ResponseFailure):
            generate(self.event, self.root, self.task/'responses', self.root/'public', self.root/'audit', self.fail)
        with self.assertRaises(ResponseFailure):
            review({'checks':[]}, {'d':{'status':'invalid','text':''}}, self.root/'audit', self.fail)

    def test_unsupported_review_is_not_published(self):
        event={'checks':[{'id':'wrong','test':'A contradiction','feedback':'Please check'}], 'role':'Reviewer'}
        docs={'d':{'status':'readable','text':'A valid alternative'}}
        def caller(system,payload,schema,audit,name):
            if name.endswith('_confirm'):return {'checks':[{'id':'wrong','supported':False}]}
            return {'checks':[{'id':'wrong','issue':True,'evidence':[{'document':'d','quote':'A valid alternative'}],'reason':'Alleged error'}]}
        self.assertEqual(review(event,docs,self.root/'audit',caller), [])

if __name__ == '__main__':
    unittest.main()
