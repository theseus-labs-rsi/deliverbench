"""Evidence/polarity contract for explicitly tagged Chinese-body hurdles.

This validates the Judge's observations, not document language from a keyword
search. The rubric still defines dominance, counting rules and exceptions.
Contradictions are rejected; this module never flips or supplies a verdict.
"""
import copy
import re

MARKER = '语言违规判据（中文主体）：'
STATES = ('non_chinese_body', 'complete_english_parallel', 'chinese_dominant',
          'missing_artifact', 'unreadable_or_uncertain')
FIELDS = {'artifact', 'body_language', 'location', 'quotes', 'basis'}
CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]')


def applies(item):
    if not item.get('description', '').startswith(MARKER):
        return False
    scope = item.get('artifact_scope')
    if (item.get('weight') != 0 or item.get('hurdle') is not True
            or item.get('target') != 'process_delivery'
            or not isinstance(scope, list) or not scope
            or not all(isinstance(x, str) and x for x in scope)
            or len(scope) != len(set(scope))):
        raise ValueError('Invalid Chinese-body language hurdle contract')
    return True


def schema_for_item(item, base):
    schema = copy.deepcopy(base)
    if applies(item):
        schema['properties']['language_assessment'] = {
            'type': 'array', 'minItems': len(item['artifact_scope']),
            'maxItems': len(item['artifact_scope']),
            'items': {'type': 'object', 'additionalProperties': False,
                      'properties': {
                          'artifact': {'type': 'string', 'enum': item['artifact_scope']},
                          'body_language': {'type': 'string', 'enum': list(STATES)},
                          'location': {'type': 'string'},
                          'quotes': {'type': 'array', 'items': {'type': 'string'}},
                          'basis': {'type': 'string'}},
                      'required': sorted(FIELDS)}}
        schema['required'].append('language_assessment')
    return schema


def judge_instruction(items):
    if not any(applies(item) for item in items):
        return ''
    return (
        '\n语言违规判据（中文主体）不是要求提交中文。须先独立读取每份 artifact_scope 原文件，'
        '按本条原有主体范围、占比规则及例外判断，再填写 language_assessment；每个文件恰好一项。'
        'non_chinese_body 表示正文未达到本条中文主体违规条件（包括英文正文、少量中文标签）；'
        'complete_english_parallel 表示中英并列且英文内容完整；chinese_dominant 表示确实达到本条中文主体违规条件且无适用例外。'
        '不得仅凭出现汉字、中文文件名、双语标题、数值或币种代码判为 chinese_dominant。'
        'location 指出正文页、章节、幻灯片或工作表/单元格；quotes 引用代表性的原文正文，basis 说明覆盖范围、占比及例外。'
        '中文主体需引用中文正文，完整英文并列需同时引用对应中文和英文，不能用英文原文证明中文主体。'
        '确实缺文件用 missing_artifact 并说明清单依据，缺文件本身不等于中文违规，仍由缺交付判据独立处理。'
        '读取失败或无法确定主体语言用 unreadable_or_uncertain，本条 status=ungradable、hit=null、finding=ungradable 并说明缺失信息。'
        '所有文件均可确定时，任一 chinese_dominant 才使 hit=true、finding=violation_present；'
        '否则 hit=false、finding=violation_absent。英文完整不构成“缺少中文”的违规。'
    )


def validate_language_assessment(item, verdict):
    if not applies(item):
        return
    rows = verdict.get('language_assessment')
    if not isinstance(rows, list) or len(rows) != len(item['artifact_scope']):
        raise ValueError('Language assessment must cover every scoped artifact')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('Invalid language assessment schema')
        artifact = row['artifact']
        if not isinstance(artifact, str) or artifact not in item['artifact_scope'] or artifact in seen:
            raise ValueError('Duplicate or unknown language assessment artifact')
        seen.add(artifact)
        state = row['body_language']
        if not isinstance(state, str) or state not in STATES:
            raise ValueError('Unknown body language observation')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ('location', 'basis')):
            raise ValueError('Language observation requires location and basis')
        quotes = row['quotes']
        if not isinstance(quotes, list) or any(not isinstance(q, str) or not q.strip() for q in quotes):
            raise ValueError('Invalid language evidence quotes')
        if state not in ('missing_artifact', 'unreadable_or_uncertain') and not quotes:
            raise ValueError('Readable body requires quoted evidence')
        text = '\n'.join(quotes)
        if state in ('chinese_dominant', 'complete_english_parallel') and not CJK.search(text):
            raise ValueError('Chinese observation contradicts quoted body evidence')
        if state == 'complete_english_parallel' and not re.search('[A-Za-z]', text):
            raise ValueError('English parallel observation requires English evidence')
    uncertain = any(r['body_language'] == 'unreadable_or_uncertain' for r in rows)
    if uncertain:
        if (verdict.get('status') != 'ungradable' or verdict.get('hit') is not None
                or verdict.get('finding') != 'ungradable' or not verdict.get('missing_information')):
            raise ValueError('Uncertain language evidence cannot produce a graded verdict')
        return
    hit = any(r['body_language'] == 'chinese_dominant' for r in rows)
    finding = 'violation_present' if hit else 'violation_absent'
    if (verdict.get('status') != 'graded' or verdict.get('hit') is not hit
            or verdict.get('finding') != finding):
        raise ValueError('Language violation polarity contradicts per-artifact observations')
