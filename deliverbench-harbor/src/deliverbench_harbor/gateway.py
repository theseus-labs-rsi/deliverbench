"""Optional legacy gateway compatibility, with no private endpoint defaults."""
import os,re,shlex,threading
from urllib.parse import urlsplit
from .agents import Codex,ClaudeCode,GeminiCli
from .session_router import Router

_ROUTERS={}

def close_router(context_id):
    router=_ROUTERS.pop(str(context_id),None)
    if router:
        router.shutdown();router.server_close()

class CodexGateway(Codex):
    def __init__(self,*args,sticky_session=True,**kwargs):
        self.sticky_session=sticky_session
        super().__init__(*args,**kwargs)

    async def setup(self,environment):
        self.upstream=self.extra_env.get('OPENAI_BASE_URL') or os.environ.get('OPENAI_BASE_URL','')
        u=urlsplit(self.upstream)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Set a credential-free OPENAI_BASE_URL for the optional gateway adapter')
        if self.sticky_session:
            router=Router(('0.0.0.0',0),self.upstream)
            _ROUTERS[str(self.context_id)]=router
            threading.Thread(target=router.serve_forever,daemon=True).start()
            self.gateway_url=f'http://host.docker.internal:{router.server_port}/v1'
        else:self.gateway_url=self.upstream
        await super().setup(environment)

    async def exec_as_agent(self,environment,command,env=None,cwd=None,timeout_sec=None):
        if 'codex exec ' in command:
            token='--model '+self.model_name.split('/')[-1]+' '
            if token not in command:raise RuntimeError('Unexpected native Codex invocation')
            values=['model_provider=deliverbench_external',
                    'model_providers.deliverbench_external.name="deliverbench"',
                    'model_providers.deliverbench_external.base_url="'+self.gateway_url+'"',
                    'model_providers.deliverbench_external.env_key="OPENAI_API_KEY"',
                    'model_providers.deliverbench_external.wire_api="responses"',
                    'model_providers.deliverbench_external.supports_websockets=false',
                    'model_providers.deliverbench_external.http_headers={"X-DeliverBench-Session-ID"="'+str(self.context_id)+'"}',
                    'service_tier="default"','disable_response_storage=true']
            command=command.replace(token,'--model '+shlex.quote(self.model_name)+' '+ ' '.join('-c '+shlex.quote(v) for v in values)+' ',1)
        return await super().exec_as_agent(environment,command,env=env,cwd=cwd,timeout_sec=timeout_sec)

class ClaudeGateway(ClaudeCode):
    async def exec_as_agent(self,environment,command,env=None,cwd=None,timeout_sec=None):
        merged=dict(env or {});base=self.extra_env.get('ANTHROPIC_BASE_URL') or os.environ.get('ANTHROPIC_BASE_URL')
        if base and self.model_name:
            merged['ANTHROPIC_BASE_URL']=base
            for k in ('ANTHROPIC_MODEL','ANTHROPIC_DEFAULT_SONNET_MODEL','ANTHROPIC_DEFAULT_OPUS_MODEL','ANTHROPIC_DEFAULT_HAIKU_MODEL','CLAUDE_CODE_SUBAGENT_MODEL'):
                merged[k]=self.model_name
        return await super().exec_as_agent(environment,command,env=merged,cwd=cwd,timeout_sec=timeout_sec)

class GeminiGateway(GeminiCli):
    # Version-pinned provider settings hook on the native Agent subclass.
    def _build_settings_config(self,model=None,auth_type=None):
        if self.extra_env.get('GOOGLE_GEMINI_BASE_URL') or os.environ.get('GOOGLE_GEMINI_BASE_URL'):
            native_model=self.model_name or model
            config,_=super()._build_settings_config(None,auth_type);config=config or {}
            config.setdefault('experimental',{})['dynamicModelConfiguration']=True
            alias='deliverbench-'+re.sub(r'[^A-Za-z0-9_-]+','-',native_model).strip('-')
            model_config={'model':native_model}
            if self._reasoning_effort:
                model_config['generateContentConfig']={'thinkingConfig':{'includeThoughts':True,'thinkingLevel':self._reasoning_effort.upper()}}
            config['modelConfigs']={'customAliases':{alias:{'modelConfig':model_config},'chat-compression-default':{'modelConfig':{'model':native_model}}}}
            return config,alias
        return super()._build_settings_config(model,auth_type)

    async def exec_as_agent(self,environment,command,env=None,cwd=None,timeout_sec=None):
        merged=dict(env or {});base=self.extra_env.get('GOOGLE_GEMINI_BASE_URL') or os.environ.get('GOOGLE_GEMINI_BASE_URL')
        if base:merged['GOOGLE_GEMINI_BASE_URL']=base
        return await super().exec_as_agent(environment,command,env=merged,cwd=cwd,timeout_sec=timeout_sec)
