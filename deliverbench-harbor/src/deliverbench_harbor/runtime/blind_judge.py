"""One criterion per Codex process, per-UID file view, no hidden context."""
import hashlib
import itertools
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from artifact_io import copy_artifacts,dump
from score_contract import score_unit
from session_router import Router
from language_guard import schema_for_item, judge_instruction, validate_language_assessment

UIDS=itertools.count(31001)
USAGE_FIELDS=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens')


SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'rubric_id':{'type':'string'},'status':{'type':'string','enum':['graded','ungradable']},
    'hit':{'type':['boolean','null']},'evidence':{'type':'array','items':{'type':'string'}},
    'reason':{'type':'string'},'missing_information':{'type':'array','items':{'type':'string'}}},
    'required':['rubric_id','status','hit','evidence','reason','missing_information']}


from verdict_guard import FINDINGS, validate_findings
SCHEMA["properties"]["finding"] = {"type": "string", "enum": FINDINGS}
SCHEMA["required"].append("finding")


def usage_from_trace(text):
    events=[]
    for line in text.splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    terminals=[x for x in events if isinstance(x,dict) and x.get('type')=='turn.completed']
    usage={k:sum(int((x.get('usage') or {}).get(k,0)) for x in terminals)
           for k in USAGE_FIELDS}
    outcomes=[x.get('type') for x in events if isinstance(x,dict) and x.get('type') in ('turn.completed','turn.failed')]
    # A recovered transport warning is not a failed completed Judge turn.
    ok=bool(outcomes) and outcomes[-1]=='turn.completed'
    return ok,usage


def validate_verdict(data,item):
    if set(data)!=set(schema_for_item(item, SCHEMA)['required']) or data['rubric_id']!=item['id']:raise ValueError('Verdict identity/schema mismatch')
    if data['status']=='graded' and type(data['hit']) is not bool:raise ValueError('Graded verdict must be binary')
    if data['status']=='ungradable' and data['hit'] is not None:raise ValueError('Ungradable verdict must have null hit')
    if data['status'] not in ('graded','ungradable'):raise ValueError('Unknown status')
    if not isinstance(data['reason'],str) or not data['reason'].strip():raise ValueError('Reason required')
    for key in ('evidence','missing_information'):
        if not isinstance(data[key],list) or not all(isinstance(x,str) for x in data[key]):raise ValueError('Invalid evidence field')
    if data['status']=='ungradable' and not data['missing_information']:raise ValueError('Specify missing information')
    if item['weight']>0 and data['hit'] is False:
        if any(text in data['reason'] for text in ('未命中该错误标准','未触发本条负向','未命中该负向项')):
            raise ValueError('Positive criterion incorrectly interpreted as a negative violation test')
    if item['weight']>0 and data['hit'] is True:
        if data['reason'].startswith(('部分符合但未完整满足本条','不满足本条','未满足本条')):
            raise ValueError('Positive hit contradicts the explicit unmet-criterion explanation')
    if data['hit'] and (item['hurdle'] or item['weight']<0):
        if any(text in data['reason'] for text in ('未发生所述违规','未命中失败条件','失败条件不存在')):
            raise ValueError('Negative boolean contradicts explanation')
    logical = validate_findings([item], [data])
    if logical: raise ValueError("; ".join(logical))
    validate_language_assessment(item, data)
    return data


def create_view(master,prompt,item,uid,root):
    work=Path(tempfile.mkdtemp(prefix='item-',dir=root))
    shutil.copytree(master/'output',work/'output')
    shutil.copy2(master/'artifact_manifest.json',work/'artifact_manifest.json')
    shutil.copy2(prompt,work/'task_prompt.md');dump(work/'rubric.json',item);dump(work/'schema.json',schema_for_item(item, SCHEMA))
    # Root-owned read-only inputs, group-specific traverse/read. Sibling Judges
    # have different UIDs/GIDs and cannot read this view or the private master.
    for p in [work,*work.rglob('*')]:
        os.chown(p,0,uid);p.chmod(0o550 if p.is_dir() else 0o440)
    work.chmod(0o550)
    for name in ('home','result','scratch'):
        p=work/name;p.mkdir();os.chown(p,uid,uid);p.chmod(0o700)
    (work/'home/.codex').mkdir();os.chown(work/'home/.codex',uid,uid)
    return work


def run_item(index,item,master,prompt,instruction,result_dir,base_url,scope):
    uid=next(UIDS);work=create_view(master,prompt,item,uid,Path('/judge_views'))
    model=os.environ['JUDGE_MODEL'];effort=os.environ.get('JUDGE_REASONING_EFFORT','high')
    tier=os.environ.get('JUDGE_SERVICE_TIER','default')
    if tier!='default':raise ValueError('This experiment is frozen to service_tier=default')
    command=['setpriv','--reuid',str(uid),'--regid',str(uid),'--clear-groups','--no-new-privs',
        'codex','-a','never','-s','danger-full-access','-m',model,
        '-c','model_reasoning_effort='+effort,'-c','model_provider=deliverbench_external',
        '-c','model_providers.deliverbench_external.name="DeliverBenchJudge"',
        '-c','model_providers.deliverbench_external.base_url="'+base_url+'"',
        '-c','model_providers.deliverbench_external.env_key="OPENAI_API_KEY"',
        '-c','model_providers.deliverbench_external.wire_api="responses"',
        '-c','model_providers.deliverbench_external.supports_websockets=false',
        '-c','model_providers.deliverbench_external.http_headers={"X-DeliverBench-Session-ID"="'+scope+'"}',
        '-c','service_tier=default','-c','disable_response_storage=true',
        'exec','-C',str(work),'--skip-git-repo-check','--ephemeral','--ignore-user-config','--ignore-rules',
        '--json','--output-schema',str(work/'schema.json'),'--output-last-message',str(work/'result/verdict.json'),'-']
    env={'PATH':os.environ['PATH'],'HOME':str(work/'home'),'CODEX_HOME':str(work/'home/.codex'),
         'TMPDIR':str(work/'scratch'),'LANG':'C.UTF-8','OPENAI_API_KEY':os.environ['JUDGE_API_KEY']}
    polarity_note=('本条是正向得分项：符合所述正确内容或边界时 hit=true；不符合或缺实质作答时 hit=false。'
                   if item['weight']>0 else
                   '本条是负向错误/失效项：实际发生所述违规时 hit=true；没有发生时 hit=false。')
    question=(instruction+'\n\n'+polarity_note+'\n以下已包含完整任务委托与当前唯一判据，不必重复打开对应副本。'
              '\n<task_prompt>\n'+Path(prompt).read_text()+'\n</task_prompt>\n<rubric>\n'+
              json.dumps(item,ensure_ascii=False)+'\n</rubric>\n'
              '请从 output/ 读取当前产物，优先定位本条涉及的文件与位置，不要无差别输出整张大表。'
              'artifact_manifest.json 记录原文件清单与哈希。自行使用工具读取 output/ 原文件，转换和分析写入 scratch/。'
              '实际清单为 [] 且 output 为空，表示确实没有提交作答，不是读取失败；须按本条的缺失情形作二元判定。'
              '涉及版面时必须查看对应页面图像，不能只凭文字抽取。不要查找其他上下文。最终按 schema 输出 JSON。')
    question += judge_instruction([item])
    timeout=int(os.environ.get('JUDGE_TIMEOUT_SEC','600'));attempts=int(os.environ.get('JUDGE_MAX_TRIES','2'))
    started=time.monotonic();errors=[];usage={k:0 for k in USAGE_FIELDS}
    try:
        for attempt in range(1,attempts+1):
            output=work/'result/verdict.json';output.unlink(missing_ok=True)
            proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,env=env,start_new_session=True)
            retry_note=('\n上一技术尝试未被接受：'+errors[-1]+'。请重新按本条极性和输出合同独立判断。') if errors else ''
            try:stdout,stderr=proc.communicate(question+retry_note,timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGTERM)
                try:stdout,stderr=proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);stdout,stderr=proc.communicate()
                errors.append('timeout')
            finally:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            # Key never goes in args; redact if the CLI happens to echo it.
            key=env['OPENAI_API_KEY'];stdout=stdout.replace(key,'[REDACTED]') if key else stdout
            stderr=stderr.replace(key,'[REDACTED]') if key else stderr
            prefix=result_dir/f'{item["id"]}.attempt-{attempt}'
            prefix.with_suffix(prefix.suffix+'.jsonl').write_text(stdout)
            prefix.with_suffix(prefix.suffix+'.stderr.log').write_text(stderr)
            ok,used=usage_from_trace(stdout)
            for k in usage:usage[k]+=used[k]
            try:
                if proc.returncode!=0 or not ok or not output.exists():raise ValueError('CLI failed or missing successful terminal')
                verdict=validate_verdict(json.loads(output.read_text()),item)
                session={'verdict':verdict,'attempts':attempt,'usage':usage,'seconds':time.monotonic()-started,
                         'input_contract':'task_prompt + one rubric + current artifacts','uid_isolation':True}
                dump(result_dir/(item['id']+'.session.json'),session)
                return session
            except (ValueError,TypeError) as exc:errors.append(str(exc))
        session={'error':errors,'rubric_id':item['id'],'usage':usage,'seconds':time.monotonic()-started}
        dump(result_dir/(item['id']+'.session.json'),session)
        return session
    finally:
        # Exact per-item temporary path, never any source or user directory.
        shutil.rmtree(work)


def evaluate(output_dir,rubric_path,prompt_path,instruction_path,result_path,calibration_ids=None):
    if os.geteuid()!=0:raise RuntimeError('Verifier orchestrator requires root for per-UID blind views')
    rubric_raw=json.loads(Path(rubric_path).read_text())
    items=rubric_raw['rubrics'] if isinstance(rubric_raw,dict) else rubric_raw
    if calibration_ids is not None:
        items=[i for i in items if i['id'] in calibration_ids]
        if len(items)!=len(set(calibration_ids)):raise ValueError('Unknown calibration ID')
    result_path=Path(result_path);result_path.parent.mkdir(parents=True,exist_ok=True)
    result_path.parent.chmod(0o700)
    for p in (Path('/tests'),Path('/workspace')):
        if p.exists():p.chmod(0o700)
    root=Path('/judge_views');root.mkdir(exist_ok=True);root.chmod(0o711)
    upstream=os.environ['JUDGE_BASE_URL'];router=Router(('127.0.0.1',0),upstream)
    threading.Thread(target=router.serve_forever,daemon=True).start()
    started=time.monotonic();scope='boomerang-judge-'+uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory(prefix='boomerang-master-') as tmp:
            master=Path(tmp);manifest=copy_artifacts(output_dir,master/'output')
            dump(master/'artifact_manifest.json',manifest)
            dump(result_path.parent/'artifact_manifest.json',manifest)
            instruction=Path(instruction_path).read_text()
            with ThreadPoolExecutor(max_workers=int(os.environ.get('JUDGE_MAX_CONCURRENT','4'))) as pool:
                futures=[pool.submit(run_item,i,item,master,Path(prompt_path),instruction,result_path.parent,
                         f'http://127.0.0.1:{router.server_port}/v1',scope) for i,item in enumerate(items,1)]
                sessions=[f.result() for f in futures]
            errors=[x for x in sessions if 'error' in x]
            verdicts=[x['verdict'] for x in sessions if 'verdict' in x]
            if errors:result={'status':'judge_error','score':None,'errors':errors}
            elif calibration_ids is not None:result={'status':'calibration_only','verdicts':verdicts}
            else:result=score_unit(items,verdicts)
            result.update(judge_mode='single_rubric_blind_judge',sessions=sessions,
                          rubric_verdict_count=len(verdicts),elapsed_seconds=time.monotonic()-started,
                          judge_model=os.environ['JUDGE_MODEL'],service_tier='default',
                          usage={key:sum(s['usage'][key] for s in sessions) for key in USAGE_FIELDS},
                          input_sha256={str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in map(Path,(rubric_path,prompt_path))},
                          artifact_manifest=manifest)
            dump(result_path,result)
            return result
    finally:router.shutdown();router.server_close()

# Batch protocol v1: native views/validation retained; independent hits, external scoring.
import sys as _batch_sys
from batched_judge import install as _install_batch
_install_batch(_batch_sys.modules[__name__])
