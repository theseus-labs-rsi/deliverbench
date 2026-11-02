"""Validate Judge-declared logic, never infer or replace business verdicts."""
import copy
import re

FINDINGS = ['criterion_satisfied', 'criterion_not_satisfied',
            'violation_present', 'violation_absent', 'ungradable']


def extend_verdict_schema(schema):
    schema = copy.deepcopy(schema)
    item = schema['properties']['details']['properties']['results']['items']
    item['properties']['finding'] = {'type': 'string', 'enum': FINDINGS[:-1]}
    if 'finding' not in item['required']:
        item['required'].append('finding')
    return schema


def validate_findings(items, results):
    errors = []
    by_id = {str(r.get('id', r.get('rubric_id'))): r for r in results}
    for item in items:
        row = by_id.get(item['id'], {})
        hit, finding = row.get('hit'), row.get('finding')
        if row.get('status') == 'ungradable':
            if hit is not None or finding != 'ungradable':
                errors.append(item['id'] + ': ungradable requires null hit and ungradable finding')
            continue
        if type(hit) is not bool:
            errors.append(item['id'] + ': hit must be a JSON boolean')
            continue
        positive = item.get('weight', 0) > 0 and not item.get('hurdle')
        expected = ('criterion_satisfied' if hit else 'criterion_not_satisfied') if positive else (
            'violation_present' if hit else 'violation_absent')
        if finding != expected:
            errors.append(item['id'] + ': finding contradicts hit or criterion polarity')
        reason = str(row.get('justification', row.get('reason', ''))).strip()
        # Narrow terminal conclusion check, not a generic keyword classifier.
        if positive and not hit and re.search(
                r'(?:因此|故)[，,\s]*(?:本条|该项|本项)(?:正向)?(?:一致性)?(?:标准|要求)?(?:已)?满足[^。]*[。.]?$', reason):
            errors.append(item['id'] + ': false contradicts an explicit satisfied conclusion')
    return errors
