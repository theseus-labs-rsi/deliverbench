"""Fixed arithmetic over complete Agent verdicts, never business verification."""
from collections import defaultdict
from statistics import mean
try:
    from batch_core import dependency_gates
except ModuleNotFoundError as exc:
    if exc.name != 'batch_core': raise
    import importlib.util as _batch_import
    from pathlib import Path as _BatchPath
    _batch_spec = _batch_import.spec_from_file_location('_deliverbench_batch_core', _BatchPath(__file__).with_name('batch_core.py'))
    _batch_module = _batch_import.module_from_spec(_batch_spec)
    _batch_spec.loader.exec_module(_batch_module)
    dependency_gates = _batch_module.dependency_gates


try:
    from language_guard import validate_language_assessment
except ModuleNotFoundError as exc:
    if exc.name != 'language_guard': raise
    import importlib.util as _language_import
    from pathlib import Path as _LanguagePath
    _language_spec = _language_import.spec_from_file_location('_deliverbench_language_guard', _LanguagePath(__file__).with_name('language_guard.py'))
    _language_module = _language_import.module_from_spec(_language_spec)
    _language_spec.loader.exec_module(_language_module)
    validate_language_assessment = _language_module.validate_language_assessment


def clipped(value):return max(0.0,min(1.0,value))


def score_unit(rubrics,verdicts):
    if not rubrics or len({r['id'] for r in rubrics})!=len(rubrics):
        raise ValueError('Empty or duplicate Rubric IDs')
    if any(type(r.get('bonus',False)) is not bool or (r.get('bonus',False) and (r['weight']<=0 or r['hurdle'])) for r in rubrics):
        raise ValueError('Invalid bonus criterion')
    denominator=sum(r['weight'] for r in rubrics if r['weight']>0 and not r.get('bonus',False))
    if denominator<=0:raise ValueError('Positive denominator required')
    if len(verdicts)!=len(rubrics) or {v['rubric_id'] for v in verdicts}!={r['id'] for r in rubrics}:
        raise ValueError('Incomplete or mismatched Judge decisions; never impute misses')
    by_id={v['rubric_id']:v for v in verdicts}
    if len(by_id)!=len(verdicts):raise ValueError('Duplicate verdict')
    for item in rubrics:
        validate_language_assessment(item, by_id[item['id']])
    if any(v['status']!='graded' or type(v['hit']) is not bool for v in verdicts):
        return dict(status='ungradable',score=None,score_raw=None,criteria=[],hurdles=[])
    gates=dependency_gates(rubrics,{rid:v['hit'] for rid,v in by_id.items()})
    rows=[];hurdles=[]
    for item in rubrics:
        verdict=by_id[item['id']]
        if item['hurdle'] and verdict['hit']:hurdles.append(item['id'])
        blocked=[parent for parent in item.get('depends_on',[]) if not (by_id[parent]['hit'] and gates[parent])] if item['weight']>0 and item.get('dependency_mode')!='analytic_only' else []
        rows.append({**item,**verdict,'self_hit':verdict['hit'],
                     'effective_hit':verdict['hit'] and gates[item['id']], 'blocked_by':blocked,
                     'gate_ok':gates[item['id']],'gained':item['weight'] if verdict['hit'] and gates[item['id']] else 0})
    raw=clipped(sum(r['gained'] for r in rows)/denominator)
    return dict(status='graded',score_raw=raw,score=0.0 if hurdles else raw,
                strict_fail=bool(hurdles),hurdles=hurdles,criteria=rows,positive_denominator=denominator,earned=sum(r['gained'] for r in rows),
                base_earned=sum(r['gained'] for r in rows if r['weight']>0 and not r.get('bonus',False)),
                bonus_earned=sum(r['gained'] for r in rows if r.get('bonus',False)),
                deductions=sum(r['gained'] for r in rows if r['weight']<0),
                breakdown={key:breakdown(rows,key) for key in
                           ('dimension','evaluation_aspect','target')})


def breakdown(rows,key):
    groups=defaultdict(lambda:dict(earned=0,possible=0,count=0))
    for row in rows:
        if row['hurdle']:continue
        group=groups[row[key]];group['count']+=1
        group['earned']+=row['gained']
        group['possible']+=max(row['weight'],0) if not row.get('bonus',False) else 0
    return {name:{**group,'score':clipped(group['earned']/group['possible']) if group['possible'] else None}
            for name,group in groups.items()}

