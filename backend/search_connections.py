"""Provider selection is nonsecret; each provider retains its own credential scope."""
import hashlib,json,os,tempfile,threading
from pathlib import Path
from .aliyun_search import AliyunSearch
from .deepseek_tools import DeepSeekTools
from .common import AppError
from .credential_vault import CredentialVault
from .search import TavilySearch

class SearchConnections:
    def __init__(self,store,new_workspace=False,providers=None):
        namespace=str(store.path.resolve())+'|search'
        vault=CredentialVault(store.path.parent/'credentials'/'search',namespace)
        self.providers=providers or {'deepseek':DeepSeekTools(),'tavily':TavilySearch(vault=vault),'aliyun':AliyunSearch(vault=vault)}
        self.path=store.path.parent/('search-selection-'+hashlib.sha256(str(store.path.resolve()).encode()).hexdigest()[:16]+'.json')
        self.lock=threading.RLock();self.revision=0;self.usage={};self.selected=('deepseek' if 'deepseek' in self.providers else 'aliyun') if new_workspace else 'tavily'
        if self.path.exists():
            try:
                if self.path.is_symlink() or self.path.stat().st_size>2048:raise ValueError()
                saved=json.loads(self.path.read_text(encoding='utf8'))
                if set(saved)!={'provider','engine_type'} or saved['provider'] not in self.providers:raise ValueError()
                from .aliyun_search import TIERS
                if saved['engine_type'] not in ('',*TIERS):raise ValueError()
                self.selected=saved['provider'];self.providers['aliyun'].engine_type=saved['engine_type']
            except (OSError,ValueError,TypeError):raise AppError('本机搜索选择文件无效，未改动原文件或发出请求',500,'search_selection_invalid') from None
        elif new_workspace:self.write_selection(self.selected,'')
    def write_selection(self,provider,engine):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.is_symlink():raise AppError('搜索选择路径无效',400,'invalid_input')
        name=None
        try:
            fd,name=tempfile.mkstemp(prefix='.search-selection-',dir=self.path.parent)
            with os.fdopen(fd,'w',encoding='utf8') as f:json.dump({'provider':provider,'engine_type':engine},f);f.flush();os.fsync(f.fileno())
            os.replace(name,self.path);name=None
        except OSError:raise AppError('搜索选择保存失败，原连接保持不变',500,'search_selection_failed') from None
        finally:
            if name:Path(name).unlink(missing_ok=True)
    @property
    def configured(self):
        with self.lock:return self.providers[self.selected].configured
    def settings(self):
        with self.lock:
            value=self.providers[self.selected].settings();value['revision']=self.revision
            value['providers']=[{'id':key,'label':label} for key,label in [('deepseek','DeepSeek（推荐）'),('aliyun','阿里云 CleverSee'),('tavily','Tavily')] if key in self.providers]
            value['usage']={'scope':'since_service_start','requests_attempted':sum(v['attempts'] for v in self.usage.values()),
                'estimated_cost_cny':round(sum(v['estimated_cost_cny'] for v in self.usage.values()),6),'billing_confirmed':False,
                'by_provider_tier':[{'provider':key[0],'engine_type':key[1],**v} for key,v in self.usage.items()]}
            tools=self.providers['deepseek'].settings()['tool_usage'] if 'deepseek' in self.providers else {}
            value['usage']['deepseek_tools']=tools
            value['usage']['estimated_cost_cny']=round(value['usage']['estimated_cost_cny']+sum(v['estimated_cost_cny'] for v in tools.values()),6)
            value['usage']['requests_attempted']+=tools.get('fetch',{}).get('attempts',0)
            value['usage']['estimated_cost_min_cny']=round(sum(v['estimated_cost_cny'] for v in self.usage.values())+sum(v['estimated_cost_min_cny'] for v in tools.values()),6)
            value['usage']['estimated_cost_max_cny']=round(sum(v['estimated_cost_cny'] for v in self.usage.values())+sum(v['estimated_cost_max_cny'] for v in tools.values()),6)
            value['recommended_provider']='deepseek';value['automatic_fallback']=False;value['usage']['token_cost_included']=False
            return value
    def save(self,data):
        if not isinstance(data,dict):raise AppError('搜索配置格式无效',400,'invalid_input')
        with self.lock:
            if type(data.get('revision')) is not int or data['revision']!=self.revision:raise AppError('搜索连接已变更，请刷新后核对',409,'search_config_changed')
            provider=data.get('provider',self.selected)
            if not isinstance(provider,str) or provider not in self.providers:raise AppError('不支持的搜索供应商',400,'invalid_input')
            credential={k:v for k,v in data.items() if k not in ('provider','engine_type','revision')}
            engine=data.get('engine_type',self.providers['aliyun'].engine_type)
            from .aliyun_search import TIERS
            if engine not in ('',*TIERS) or 'engine_type' in data and provider!='aliyun':raise AppError('请选择受支持的阿里云检索档位',400,'invalid_input')
            if provider!=self.selected or 'engine_type' in data:
                if credential:raise AppError('请先单独保存供应商和档位，再填写该供应商的独立 Key',400,'search_scope_confirmation_required')
                self.write_selection(provider,engine);self.selected=provider
                if self.providers['aliyun'].engine_type!=engine:
                    with self.providers['aliyun'].config_lock:
                        self.providers['aliyun'].verified_at=None;self.providers['aliyun'].revision+=1
                self.providers['aliyun'].engine_type=engine
            else:
                active=self.providers[provider];active.save(dict(credential,revision=active.revision))
            self.revision+=1
            return self.settings()
    def search(self,request):
        with self.lock:
            selected=self.selected;revision=self.revision;active=self.providers[selected]
            tier=active.settings().get('engine_type','');rate=active.settings().get('price_cny_per_request') if selected!='deepseek' else 0
        original_hook=request.get('_attempt_hook')
        def attempt():
            with self.lock:
                if self.revision!=revision:raise AppError('搜索连接已变更，没有发出请求',409,'search_config_changed')
                if original_hook:original_hook()
                value=self.usage.setdefault((selected,tier),{'attempts':0,'estimated_cost_cny':0})
                value['attempts']+=1;value['estimated_cost_cny']+=rate or 0
        result=active.search(dict(request,_attempt_hook=attempt))
        with self.lock:
            if self.revision!=revision:raise AppError('请求期间搜索供应商或凭证已变更，结果已丢弃',409,'search_config_changed')
            result['usage']=self.settings()['usage']
        return result

    def compose_query(self,request):
        from .search import compose_query
        with self.lock:active=self.providers[self.selected]
        return active.compose_query(request) if hasattr(active,'compose_query') else compose_query(request)
    def fetch(self,data):
        with self.lock:
            if self.selected!='deepseek':raise AppError('请明确选择 DeepSeek 后使用 Fetch；不会自动切换供应商',400,'search_provider_selection_required')
            active=self.providers['deepseek'];revision=self.revision
        result=active.fetch(data)
        with self.lock:
            if revision!=self.revision:raise AppError('抓取期间供应商已改变，结果已丢弃',409,'search_config_changed')
            result['usage']=self.settings()['usage']
        return result
