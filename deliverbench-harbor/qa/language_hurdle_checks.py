"""Chinese-body hurdle polarity, evidence, and actual batch-runner regressions.

No model calls. The batch-runner tests replace only process execution and the
UID-specific temporary view with local fixtures; validation/retries/cache are real.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import deliverbench_harbor
from jsonschema import validate as validate_schema

parser = argparse.ArgumentParser()
parser.add_argument('--project', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(Path(deliverbench_harbor.__file__).parent / 'runtime'))
import blind_judge as native
import batched_judge as batch
from batch_core import accept_rows, run_batches
from language_guard import applies, schema_for_item, judge_instruction, validate_language_assessment
from score_contract import score_unit

RULES = [r for p in (args.project / 'task/tests/steps').glob('*/rubric.json')
         for r in json.loads(p.read_text())['rubrics'] if applies(r)]
BASE = copy.deepcopy(RULES[0])
POSITIVE = {**BASE, 'id': 'positive_fixture', 'description': 'Substantive answer present.',
            'weight': 2, 'hurdle': False}


def observation(artifact, state):
    quotes = {
        'non_chinese_body': ['The refinancing proceeds repay the existing facilities.'],
        'chinese_dominant': ['本次再融资所得款项用于偿还现有融资。'],
        'complete_english_parallel': ['本次再融资所得款项用于偿还现有融资。',
                                     'The refinancing proceeds repay the existing facilities.'],
        'missing_artifact': [], 'unreadable_or_uncertain': []}[state]
    return dict(artifact=artifact, body_language=state, location='body, page 2',
                quotes=quotes, basis='Inspected all substantive sections using the rubric scope and exceptions.')


def decision(item, state='non_chinese_body'):
    uncertain = state == 'unreadable_or_uncertain'
    hit = None if uncertain else state == 'chinese_dominant'
    return dict(rubric_id=item['id'], status='ungradable' if uncertain else 'graded',
                hit=hit, finding='ungradable' if uncertain else 'violation_present' if hit else 'violation_absent',
                evidence=['body, page 2'], reason='Body language observed in the submitted artifact.',
                missing_information=['Body could not be read.'] if uncertain else [],
                language_assessment=[observation(a, state) for a in item['artifact_scope']])


def positive():
    return dict(rubric_id=POSITIVE['id'], status='graded', hit=True,
                finding='criterion_satisfied', evidence=['body'], reason='Substantive answer is present.', missing_information=[])


class LanguageChecks(unittest.TestCase):
    def test_all_fifteen_rules_preserve_real_violation_and_valid_score(self):
        self.assertEqual(len(RULES), 15)
        for item in RULES:
            for state, expected in [('non_chinese_body', 1), ('complete_english_parallel', 1),
                                    ('chinese_dominant', 0), ('missing_artifact', 1)]:
                with self.subTest(id=item['id'], state=state):
                    d = decision(item, state)
                    validate_schema(d, schema_for_item(item, native.SCHEMA))
                    native.validate_verdict(d, item)
                    result = score_unit([POSITIVE, item], [positive(), d])
                    self.assertEqual(result['score'], expected)
                    self.assertEqual(result['hurdles'], [item['id']] if expected == 0 else [])

    def test_old_missing_assessment_and_reversed_english_are_rejected(self):
        for item in RULES:
            old = decision(item); old.pop('language_assessment')
            bad = decision(item); bad.update(hit=True, finding='violation_present')
            for d in (old, bad):
                with self.assertRaises(ValueError): native.validate_verdict(d, item)
                with self.assertRaises(ValueError): score_unit([POSITIVE, item], [positive(), d])

    def test_chinese_cannot_be_blindly_cleared(self):
        d = decision(BASE, 'chinese_dominant'); d.update(hit=False, finding='violation_absent')
        with self.assertRaises(ValueError): native.validate_verdict(d, BASE)

    def test_english_quotes_cannot_prove_chinese_body(self):
        d = decision(BASE, 'chinese_dominant')
        d['language_assessment'][0]['quotes'] = ['English body text only.']
        with self.assertRaises(ValueError): native.validate_verdict(d, BASE)

    def test_incomplete_bilingual_evidence_is_rejected(self):
        for quotes in [['Chinese absent: all English'], ['只有中文正文']]:
            d = decision(BASE, 'complete_english_parallel'); d['language_assessment'][0]['quotes'] = quotes
            with self.assertRaises(ValueError): native.validate_verdict(d, BASE)

    def test_uncertainty_produces_no_numeric_score(self):
        d = decision(BASE, 'unreadable_or_uncertain'); native.validate_verdict(d, BASE)
        self.assertIsNone(score_unit([POSITIVE, BASE], [positive(), d])['score'])
        accepted, errors = accept_rows({'verdicts': [d]}, [BASE], native.validate_verdict)
        self.assertFalse(accepted); self.assertIn(BASE['id'], errors)
        d.update(status='graded', hit=False, finding='violation_absent', missing_information=[])
        with self.assertRaises(ValueError): native.validate_verdict(d, BASE)

    def test_scoped_files_are_independent_and_complete(self):
        item = next(r for r in RULES if len(r['artifact_scope']) > 1)
        d = decision(item); d['language_assessment'][1] = observation(item['artifact_scope'][1], 'chinese_dominant')
        d.update(hit=True, finding='violation_present'); native.validate_verdict(d, item)
        for mutation in ('missing', 'duplicate', 'outside', 'extra', 'empty_quote', 'empty_basis'):
            bad = copy.deepcopy(d)
            if mutation == 'missing': bad['language_assessment'].pop()
            elif mutation == 'duplicate': bad['language_assessment'][1] = copy.deepcopy(bad['language_assessment'][0])
            elif mutation == 'outside': bad['language_assessment'][0]['artifact'] = '../other.docx'
            elif mutation == 'extra': bad['language_assessment'][0]['inferred'] = True
            elif mutation == 'empty_quote': bad['language_assessment'][0]['quotes'] = []
            else: bad['language_assessment'][0]['basis'] = ''
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): native.validate_verdict(bad, item)

    def test_incidental_chinese_does_not_force_a_hit(self):
        d = decision(BASE); d['language_assessment'][0]['quotes'].append('Alpha（阿尔法）')
        native.validate_verdict(d, BASE)
        self.assertEqual(score_unit([POSITIVE, BASE], [positive(), d])['score'], 1)

    def test_missing_artifact_still_triggers_separate_missing_hurdle(self):
        missing = {**BASE, 'id': 'missing_fixture', 'description': 'Required file is absent.'}
        v = {**positive(), 'rubric_id': missing['id'], 'finding': 'violation_present'}
        result = score_unit([POSITIVE, BASE, missing], [positive(), decision(BASE, 'missing_artifact'), v])
        self.assertEqual(result['hurdles'], ['missing_fixture']); self.assertEqual(result['score'], 0)

    def test_scope_of_protocol_is_explicit(self):
        for item in [{**BASE, 'description': 'Chinese language mentioned in a different rule.'}, POSITIVE, {'id': '__batch__'}]:
            self.assertFalse(applies(item)); self.assertEqual(schema_for_item(item, native.SCHEMA), native.SCHEMA)
            self.assertEqual(judge_instruction([item]), '')
        for field, value in [('weight', 1), ('hurdle', False), ('target', 'final_delivery'), ('artifact_scope', [])]:
            with self.assertRaises(ValueError): applies({**BASE, field: value})

    def test_batch_schema_exposes_assessment_only_for_language_item(self):
        schema = batch.batch_schema([POSITIVE, BASE], native.SCHEMA, {}, 'rubric_id')
        validate_schema({'verdicts': [positive(), decision(BASE)]}, schema)
        self.assertEqual(len(schema['properties']['verdicts']['items']['anyOf']), 2)
        self.assertNotIn('language_assessment', schema['properties']['verdicts']['items']['anyOf'][0]['required'])

    def exercise_batch(self, recover):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); master = root/'master'; (master/'output').mkdir(parents=True)
            (master/'output/example.txt').write_text('English submitted body.')
            (master/'artifact_manifest.json').write_text('[]')
            prompt = root/'prompt.md'; prompt.write_text('Synthetic regression task.')
            calls = []
            def view(runner, items):
                work = Path(tempfile.mkdtemp(dir=root))
                for name in ('home/.codex', 'scratch', 'result'): (work/name).mkdir(parents=True)
                shutil.copytree(master/'output', work/'output')
                shutil.copy2(master/'artifact_manifest.json', work/'artifact_manifest.json')
                return work, None
            def execute(command, question, env, timeout, prefix):
                work = Path(command[command.index('-C') + 1])
                visible = json.loads((work/'rubric.json').read_text())['rubrics']
                calls.append([r['id'] for r in visible]); verdicts = []
                self.assertIn('语言违规判据（中文主体）不是要求提交中文', question)
                for item in visible:
                    if item['id'] == POSITIVE['id']: verdicts.append(positive()); continue
                    d = decision(BASE)
                    if not recover or len(calls) == 1: d.update(hit=True, finding='violation_present')
                    verdicts.append(d)
                payload = {'verdicts': verdicts}
                validate_schema(payload, json.loads((work/'schema.json').read_text()))
                (work/'result/verdict.json').write_text(json.dumps(payload))
                return {'usage': {k: 0 for k in batch.USAGE}, 'fixture_no_model_call': True}
            env = dict(JUDGE_MODEL='fixture', JUDGE_API_KEY='fixture', JUDGE_MAX_TRIES='2', JUDGE_SERVICE_TIER='default')
            with patch.dict(os.environ, env), patch.object(batch.BatchRunner, 'create_view', view), patch.object(batch, 'execute', execute):
                runner = batch.BatchRunner(native, master, prompt, 'Judge independently.', root/'results', 'http://unused.invalid', 'fixture')
                self.assertIn('language_guard.py', runner.code)
                accepted, records = run_batches([POSITIVE, BASE], runner, 8, 1)
                self.assertEqual(calls[0], [POSITIVE['id'], BASE['id']])
                self.assertTrue(all(c == [BASE['id']] for c in calls[1:]))
                if recover:
                    self.assertEqual(len(accepted), 2); self.assertEqual(len(calls), 2)
                    result = score_unit([POSITIVE, BASE], [accepted[r['id']]['verdict'] for r in [POSITIVE, BASE]])
                    self.assertEqual(result['score'], 1)
                    before = len(calls); again, _ = run_batches([POSITIVE, BASE], runner, 8, 1)
                    self.assertEqual(len(again), 2); self.assertEqual(len(calls), before)
                else:
                    self.assertEqual(set(accepted), {POSITIVE['id']}); self.assertEqual(len(calls), 3)
                    with self.assertRaises(ValueError): score_unit([POSITIVE, BASE], [accepted[POSITIVE['id']]['verdict']])
                self.assertTrue(any(a.get('validation_errors') for r in records for a in r.get('attempts', [])))

    def test_actual_batch_runner_retries_only_rejected_and_reuses_valid_cache(self): self.exercise_batch(True)
    def test_actual_batch_runner_exhausts_bounded_retries_without_imputing_zero(self): self.exercise_batch(False)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
