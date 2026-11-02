"""One-shot structured responder calls. No Candidate session or encrypted history."""
import hashlib
import json
import os
import time
from pathlib import Path
import httpx

class ResponseFailure(RuntimeError):
    pass

def encoded(value): return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
def digest(value): return hashlib.sha256(encoded(value)).hexdigest()

def effective_config():
    def value(key, default=''):
        return os.environ.get('RESPONDER_'+key) or os.environ.get('JUDGE_'+key) or default
    return {'base_url':value('BASE_URL').rstrip('/'), 'model':value('MODEL'),
            'api_key':value('API_KEY'), 'api':value('API','chat'),
            'reasoning_effort':value('REASONING_EFFORT','high'),
            'timeout_sec':int(value('TIMEOUT_SEC','1200')), 'max_tries':int(value('MAX_TRIES','2'))}

def call_json(system, payload, schema, audit_dir, name):
    audit_dir=Path(audit_dir);audit_dir.mkdir(parents=True,exist_ok=True)
    request={'system':system,'payload':payload,'schema':schema}
    fingerprint=digest(request)
    cached=audit_dir/(name+'.json')
    failures=[]
    def record_failure(category,attempt=0,status=None,config=None):
        failures.append({'category':category,'attempt':attempt,'http_status':status})
        file=audit_dir/(name+'-failure.json')
        file.write_bytes(encoded({'request_sha256':fingerprint,'configuration':config or {},'failures':failures}))
        file.chmod(0o600)
    if cached.exists():
        saved=json.loads(cached.read_text())
        if saved['request_sha256']!=fingerprint: raise ResponseFailure('Responder request changed during recovery')
        import jsonschema
        jsonschema.validate(saved['result'],schema)
        return saved['result']
    fixture=os.environ.get('DELIVERBENCH_RESPONSE_FIXTURES')
    if fixture:
        source=Path(fixture)/(fingerprint+'.json')
        if not source.is_file():
            (audit_dir/(name+'-fixture-request.json')).write_bytes(encoded(request))
            raise ResponseFailure('Missing exact-input response fixture '+fingerprint)
        result=json.loads(source.read_text());config={'backend':'fixture'};usage={};attempt=1
    else:
        cfg=effective_config()
        if not all(cfg[k] for k in ('base_url','api_key','model')):raise ResponseFailure('Missing RESPONDER or JUDGE configuration')
        if cfg['api'] not in ('chat','responses'):raise ResponseFailure('RESPONDER_API must be chat or responses')
        config={k:v for k,v in cfg.items() if k!='api_key'}
        if cfg['timeout_sec']<=0 or cfg['max_tries'] not in (1,2):
            raise ResponseFailure('Responder timeout must be positive; max tries must be 1 or 2')
        text=json.dumps(payload,ensure_ascii=False)
        body={'model':cfg['model']}
        if cfg['api']=='responses':
            body.update(input=[{'role':'system','content':system},{'role':'user','content':text}],store=False,
                        text={'format':{'type':'json_schema','name':name,'schema':schema,'strict':True}},
                        reasoning={'effort':cfg['reasoning_effort']})
            endpoint='/responses'
        else:
            body.update(messages=[{'role':'system','content':system},{'role':'user','content':text}],
                        response_format={'type':'json_schema','json_schema':{'name':name,'schema':schema,'strict':True}},
                        reasoning_effort=cfg['reasoning_effort'])
            endpoint='/chat/completions'
        result=None
        for attempt in range(1,min(2,cfg['max_tries'])+1):
            try:
                with httpx.Client(timeout=cfg['timeout_sec']) as client:
                    r=client.post(cfg['base_url']+endpoint,headers={'Authorization':'Bearer '+cfg['api_key']},json=body)
                if r.status_code>=400:
                    # Provider refusals are not retried or routed around.
                    if r.status_code not in (408,429,500,502,503,504):raise ResponseFailure('Responder HTTP '+str(r.status_code))
                    raise httpx.HTTPStatusError('Transient responder error',request=r.request,response=r)
                data=r.json()
                if cfg['api']=='responses':
                    outputs=data.get('output',[])
                    if any(c.get('type')=='refusal' for m in outputs for c in m.get('content',[])):
                        raise ResponseFailure('Provider refused responder request')
                    output=''.join(c.get('text','') for m in outputs for c in m.get('content',[]) if c.get('type')=='output_text')
                else:
                    choice=data['choices'][0];message=choice['message']
                    if message.get('refusal') or choice.get('finish_reason')=='content_filter':raise ResponseFailure('Provider refused responder request')
                    output=message.get('content','')
                result=json.loads(output);usage=data.get('usage',{});break
            except ResponseFailure as exc:
                record_failure(str(exc),attempt,config=config)
                raise
            except (httpx.TimeoutException,httpx.TransportError,httpx.HTTPStatusError) as exc:
                record_failure(type(exc).__name__,attempt,getattr(getattr(exc,'response',None),'status_code',None),config)
                if attempt>=min(2,cfg['max_tries']):raise ResponseFailure(type(exc).__name__) from None
                time.sleep(min(2**attempt,4))
            except (ValueError,KeyError,IndexError,TypeError):
                record_failure('invalid_structured_output',attempt,config=config)
                raise ResponseFailure('Invalid structured responder output') from None
        if result is None:raise ResponseFailure('Responder did not produce a result')
    import jsonschema
    try:jsonschema.validate(result,schema)
    except jsonschema.ValidationError:
        record_failure('schema_validation_error',attempt,config=config)
        raise ResponseFailure('Responder output does not match schema') from None
    receipt={'request_sha256':fingerprint,'configuration':config,'attempts':attempt,'usage':usage,'result':result}
    cached.write_bytes(encoded(receipt));cached.chmod(0o600)
    return result
